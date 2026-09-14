#!/usr/bin/env python
"""Wind 逐笔数据 → tick 事实库转换器。

源:      /data/students/gaolei/stock/data/raw/quark_downloaded/YYYYMMDD/<code>.zip
         每 zip 含 3 个 GBK CSV member: 逐笔成交.csv / 逐笔委托.csv / 行情.csv
输出:    /data/students/gaolei/stock/data/fact/tick_fact/{trades,orders,snapshots}/year=YYYY/month=MM/part-000.parquet
Manifest: tick_fact/_manifest/conversion_manifest.parquet + conversion_errors.csv

过滤规则:
  1. 哨兵行: 自然日 == '0' (Wind 开盘前虚拟行, 各表均有) → 过滤, 计 n_sentinel
  2. 零价行: 逐笔成交价格 == 0 (深交所集合竞价虚拟撮合, 仅 SZ 有) → 过滤, 计 n_zero
  3. 周末错位目录: 目录日 weekday>=5 (20260801/02/15/16/22/23) → 整目录隔离 date_shifted_directory
  4. zip 内自然日 != 目录日 → 整 zip 隔离 date_shifted

单位 (2026-08 摸底已三方锚定 E~1e-8 vs TDX/1m):
  price_x10000 = 源价格整数原值 (元 ×10000); volume = 股; time_ms = HHMMSSss → ms-of-day
  amt(元) = Σ(price_x10000 × volume) / 10000

用法:
  python convert_tick_to_parquet.py --workers 16
  python convert_tick_to_parquet.py --only-day 20260821   # 单日验证
"""
import os
# 线程限制必须在 pyarrow import 前设置 (2026-08-26 教训: 8 worker × 40 线程 pyarrow
# 默认线程池 = 360+ 线程 → 与共享机器 4684 线程竞争, 调度过载 → 吞吐掉 100 倍)
os.environ.setdefault('ARROW_NUM_THREADS', '2')
os.environ.setdefault('OMP_NUM_THREADS', '2')
# 2026-08-26 死锁修复: pyarrow 17 默认 jemalloc 内存池长生命周期累积损坏嫌疑
# (12 worker 满 CPU 死循环 5.5h) → 切系统 malloc, 规避分配器层问题
os.environ.setdefault('PYARROW_JEMALLOC', '0')
import io, glob, json, time, argparse, zipfile, signal, multiprocessing, fcntl
import pandas as pd, numpy as np, pyarrow as pa, pyarrow.parquet as pq
from concurrent.futures import ProcessPoolExecutor

ROOT = '/data/students/gaolei/stock/data/raw/quark_downloaded/'
OUT = '/data/students/gaolei/stock/data/fact/tick_fact/'
DAY_RE = __import__('re').compile(r'^(\d{8})$')

# ---------------- Schema (冻结) ----------------
TRADES_SCHEMA = pa.schema([
    pa.field('code', pa.string()), pa.field('trade_date', pa.date32()),
    pa.field('time_ms', pa.int32()), pa.field('trade_no', pa.int64()),
    pa.field('bs', pa.uint8()), pa.field('price_x10000', pa.int32()),
    pa.field('volume', pa.int32()), pa.field('ask_seq', pa.int64()),
    pa.field('bid_seq', pa.int64())])
ORDERS_SCHEMA = pa.schema([
    pa.field('code', pa.string()), pa.field('trade_date', pa.date32()),
    pa.field('time_ms', pa.int32()), pa.field('order_no', pa.int64()),
    pa.field('exch_order_no', pa.int64()), pa.field('order_type', pa.string()),
    pa.field('bs', pa.string()), pa.field('price_x10000', pa.int32()),
    pa.field('volume', pa.int32())])
SNAP_SCHEMA = pa.schema(
    [pa.field('code', pa.string()), pa.field('trade_date', pa.date32()),
     pa.field('time_ms', pa.int32()), pa.field('price', pa.float64()),
     pa.field('volume', pa.float64()), pa.field('amount', pa.float64()),
     pa.field('n_trades', pa.float64()), pa.field('iopv', pa.float64()),
     pa.field('trade_flag', pa.string()), pa.field('bs', pa.string()),
     pa.field('cum_volume', pa.float64()), pa.field('cum_amount', pa.float64()),
     pa.field('high', pa.float64()), pa.field('low', pa.float64()),
     pa.field('open', pa.float64()), pa.field('prev_close', pa.float64())]
    + [pa.field(f'ask_p{i}', pa.float64()) for i in range(1, 11)]
    + [pa.field(f'ask_v{i}', pa.float64()) for i in range(1, 11)]
    + [pa.field(f'bid_p{i}', pa.float64()) for i in range(1, 11)]
    + [pa.field(f'bid_v{i}', pa.float64()) for i in range(1, 11)]
    + [pa.field('wavg_ask', pa.float64()), pa.field('wavg_bid', pa.float64()),
       pa.field('ask_total', pa.float64()), pa.field('bid_total', pa.float64()),
       pa.field('unweighted_index', pa.float64()), pa.field('n_issues', pa.float64()),
       pa.field('n_up', pa.float64()), pa.field('n_down', pa.float64()),
       pa.field('n_flat', pa.float64())])
assert len(SNAP_SCHEMA) == 65, len(SNAP_SCHEMA)  # 源 66 列, 冗余的"交易所代码"丢弃

MANIFEST_SCHEMA = pa.schema([
    pa.field('code', pa.string()), pa.field('trade_date', pa.date32()),
    pa.field('n_trades', pa.int64()), pa.field('sum_vol', pa.int64()),
    pa.field('sum_amt', pa.float64()), pa.field('first_price', pa.int32()),
    pa.field('last_price', pa.int32()), pa.field('first_time_ms', pa.int32()),
    pa.field('last_time_ms', pa.int32()), pa.field('n_zero', pa.int64()),
    pa.field('n_sentinel', pa.int64()), pa.field('n_orders', pa.int64()),
    pa.field('n_snap', pa.int64()), pa.field('last_cum_vol', pa.float64()),
    pa.field('last_cum_amt', pa.float64()), pa.field('source_zip_size', pa.int64())])

TRADE_DTYPES = {'成交价格': 'float64', '成交数量': 'float64', '成交编号': 'int64',
                'BS标志': 'str', '成交代码': 'str', '委托代码': 'str',
                '自然日': 'str', '时间': 'str'}
ORDER_DTYPES = {'委托编号': 'int64', '交易所委托号': 'int64', '委托类型': 'str',
                '委托代码': 'str', '委托价格': 'float64', '委托数量': 'float64',
                '自然日': 'str', '时间': 'str'}
SNAP_STR = ['万得代码', '交易所代码', '自然日', '时间', '成交标志', 'BS标志']
SNAP_FLOAT = [c for c in [
    '成交价', '成交量', '成交额', '成交笔数', 'IOPV', '当日累计成交量', '当日成交额',
    '最高价', '最低价', '开盘价', '前收盘',
    '申卖价1', '申卖价2', '申卖价3', '申卖价4', '申卖价5',
    '申卖价6', '申卖价7', '申卖价8', '申卖价9', '申卖价10',
    '申卖量1', '申卖量2', '申卖量3', '申卖量4', '申卖量5',
    '申卖量6', '申卖量7', '申卖量8', '申卖量9', '申卖量10',
    '申买价1', '申买价2', '申买价3', '申买价4', '申买价5',
    '申买价6', '申买价7', '申买价8', '申买价9', '申买价10',
    '申买量1', '申买量2', '申买量3', '申买量4', '申买量5',
    '申买量6', '申买量7', '申买量8', '申买量9', '申买量10',
    '加权平均叫卖价', '加权平均叫买价', '叫卖总量', '叫买总量',
    '不加权指数', '品种总数', '上涨品种数', '下跌品种数', '持平品种数']]
SNAP_DTYPES = {c: 'str' for c in SNAP_STR} | {c: 'float64' for c in SNAP_FLOAT}
# SNAP_FLOAT 顺序与 SNAP_SCHEMA 数值列顺序完全一致 (price..iopv, cum_volume.., high..)
# 唯一例外: schema 的 trade_flag/bs 两 str 列插在 iopv(第5) 与 cum_volume(第6) 之间
SNAP_NUM_BEFORE_STR = 5  # price, volume, amount, n_trades, iopv


def parse_ms(t: pd.Series) -> np.ndarray:
    t = t.astype(np.int64).to_numpy()
    h = t // 10000000
    mm = (t // 100000) % 100
    ss = (t // 1000) % 100
    sub = t % 1000
    return ((h * 3600 + mm * 60 + ss) * 1000 + sub).astype(np.int32)


def to_int64_nullable(v: np.ndarray) -> pa.Array:
    return pa.array(pd.array(v, dtype='Int64'), type=pa.int64())


def to_int32_nullable(v: np.ndarray) -> pa.Array:
    return pa.array(pd.array(v, dtype='Int32'), type=pa.int32())


def date_arr(day, n):
    # numpy>=2 把无分隔符 'YYYYMMDD' 当整数天（1970+20251103 天）→ date32 溢出为负值
    # （tick_fact 全库 trade_date 列因此损坏，2026-08-26 定位）。显式加分隔符。
    iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    return pa.array(np.full(n, np.datetime64(iso, 'D')), type=pa.date32())


def process_zip(z, day):
    """处理一个 zip (带 1200s SIGALRM 超时兜底)。

    2026-08-26 教训: worker 曾全部满 CPU 死循环 5.5h。本 wrapper 兜住单 zip 的
    Python 层死循环 → 降级为 ('error', timeout) 跳过, 不阻塞整体。
    """
    def _timeout(sig, frm):
        raise TimeoutError(f'process_zip 超时 1200s: {os.path.basename(z)}')
    signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(1200)
    try:
        return _process_zip(z, day)
    finally:
        signal.alarm(0)


def _process_zip(z, day):
    """处理一个 zip: 返回 (trades_tab, orders_tab, snaps_tab, manifest_row) 或 None(隔离) 或 ('error', msg)"""
    code = os.path.basename(z)[:-4]
    st = dict(n_trades=0, sum_vol=0, sum_amt=0.0, first_price=None, last_price=None,
              first_time_ms=None, last_time_ms=None, n_zero=0, n_sentinel=0,
              n_orders=0, n_snap=0, last_cum_vol=None, last_cum_amt=None)
    try:
        with zipfile.ZipFile(z) as zf:
            # ---------- 逐笔成交 ----------
            df = pd.read_csv(io.BytesIO(zf.read('逐笔成交.csv')), encoding='gbk',
                             dtype=TRADE_DTYPES)
            sentinel = (df['自然日'] == '0').to_numpy()
            rest = df[~sentinel]
            if len(rest) == 0 or (rest['自然日'] != day).any():
                return None  # 空或日期错位 → 上层隔离
            px = rest['成交价格'].to_numpy(dtype=np.float64)
            zero_mask = px == 0.0
            keep = ~zero_mask
            px = px[keep]
            if len(px) == 0:
                return None
            vol = rest['成交数量'].to_numpy()[keep]
            tm = parse_ms(rest['时间'][keep])
            st['n_sentinel'] = int(sentinel.sum())
            st['n_zero'] = int(zero_mask.sum())
            st['n_trades'] = int(len(px))
            st['sum_vol'] = int(vol.sum())
            st['sum_amt'] = float(np.dot(px, vol) / 10000.0)
            st['first_price'] = int(px[0]); st['last_price'] = int(px[-1])
            st['first_time_ms'] = int(tm[0]); st['last_time_ms'] = int(tm[-1])
            if (np.diff(tm) < 0).any() or (vol <= 0).any():
                raise ValueError('trade time/vol ordering violated')
            trades_tab = pa.Table.from_arrays([
                pa.array(np.repeat(code, len(px))),
                date_arr(day, len(px)),
                pa.array(tm, pa.int32()),
                rest['成交编号'].to_numpy()[keep],
                rest['BS标志'].fillna('').map({'B': 0, 'S': 1}).fillna(2).astype(np.uint8).to_numpy()[keep],
                np.rint(px).astype(np.int32), np.rint(vol).astype(np.int32),
                to_int64_nullable(rest['叫卖序号'].to_numpy(dtype=np.float64)[keep]),
                to_int64_nullable(rest['叫买序号'].to_numpy(dtype=np.float64)[keep])],
                schema=TRADES_SCHEMA)
            del df

            # ---------- 逐笔委托 ----------
            df = pd.read_csv(io.BytesIO(zf.read('逐笔委托.csv')), encoding='gbk',
                             dtype=ORDER_DTYPES)
            keep = (df['自然日'] != '0').to_numpy()
            if keep.any():
                # 源排序: SH 按委托编号序 / SZ 按时间序 → 规范化为时间序
                # (stable sort 保持同时间戳源序, 保证同一委托号的 A/D 记录 A 在前)
                df = df[keep].copy()
                tm = parse_ms(df['时间'])
                if (tm < 0).any() or (tm >= 86400000).any():
                    raise ValueError('order time out of range')
                df = df.assign(_tm=tm).sort_values('_tm', kind='stable')
                tm = df['_tm'].to_numpy(dtype=np.int32)
                oprice = df['委托价格'].to_numpy(dtype=np.float64)
                ovol = df['委托数量'].to_numpy(dtype=np.float64)
                st['n_orders'] = int(len(df))
                orders_tab = pa.Table.from_arrays([
                    pa.array(np.repeat(code, len(tm))), date_arr(day, len(tm)),
                    pa.array(tm, pa.int32()),
                    df['委托编号'].to_numpy(),
                    to_int64_nullable(df['交易所委托号'].to_numpy(dtype=np.float64)),
                    pa.array(df['委托类型'].fillna(''), pa.string()),
                    pa.array(df['委托代码'].fillna(''), pa.string()),
                    to_int32_nullable(np.rint(oprice)),
                    to_int32_nullable(np.rint(ovol))], schema=ORDERS_SCHEMA)
            else:
                orders_tab = pa.Table.from_arrays(
                    [pa.array([], pa.string()), pa.array([], pa.date32()),
                     pa.array([], pa.int32()), pa.array([], pa.int64()),
                     pa.array([], pa.int64()), pa.array([], pa.string()),
                     pa.array([], pa.string()), pa.array([], pa.int32()),
                     pa.array([], pa.int32())], schema=ORDERS_SCHEMA)
            del df

            # ---------- 行情快照 ----------
            df = pd.read_csv(io.BytesIO(zf.read('行情.csv')), encoding='gbk',
                             dtype=SNAP_DTYPES)
            keep = (df['自然日'] != '0').to_numpy()
            tm = parse_ms(df['时间'][keep])
            st['n_snap'] = int(keep.sum())
            if keep.any():
                cv = pd.to_numeric(df['当日累计成交量'][keep], errors='coerce').dropna()
                ca = pd.to_numeric(df['当日成交额'][keep], errors='coerce').dropna()
                if len(cv):
                    st['last_cum_vol'] = float(cv.iloc[-1])
                if len(ca):
                    st['last_cum_amt'] = float(ca.iloc[-1])
            num_cols = SNAP_FLOAT
            snaps_tab = pa.Table.from_arrays(
                [pa.array(np.repeat(code, len(tm))), date_arr(day, len(tm)),
                 pa.array(tm, pa.int32())]
                + [pa.array(pd.to_numeric(df[c][keep], errors='coerce')
                            .to_numpy(dtype=np.float64)) for c in num_cols[:SNAP_NUM_BEFORE_STR]]
                + [pa.array(df['成交标志'][keep].fillna(''), pa.string()),
                   pa.array(df['BS标志'][keep].fillna(''), pa.string())]
                + [pa.array(pd.to_numeric(df[c][keep], errors='coerce')
                            .to_numpy(dtype=np.float64)) for c in num_cols[SNAP_NUM_BEFORE_STR:]],
                schema=SNAP_SCHEMA)
    except Exception as e:
        return ('error', str(e))
    mrow = {c: st[c] for c in ['n_trades', 'sum_vol', 'sum_amt', 'first_price',
                               'last_price', 'first_time_ms', 'last_time_ms',
                               'n_zero', 'n_sentinel', 'n_orders', 'n_snap',
                               'last_cum_vol', 'last_cum_amt']}
    mrow.update({'code': code, 'trade_date': day,
                 'source_zip_size': os.path.getsize(z)})
    return (trades_tab, orders_tab, snaps_tab, mrow)


class MonthWriter:
    """每月每表一个 parquet 文件, 累积到一定行数 flush row group。

    2026-08-26 互踩修复: 写入唯一 tmp 路径 (part-000.parquet.tmp.<pid>), 全部写完后
    校验通过才 os.replace 到最终路径。write_table 返回时 pyarrow 内部缓冲可能尚未
    全部落 fd (实测误报), 因此 append 只做尺寸单调性检查 (外部 O_TRUNC 必致 size
    骤降); 文件静止期 (writer.close() + fsync 后) 做物理块完整性校验
    (st_blocks*512 >= st_size*0.95) 与结构/行数校验, 通过才原子提交——
    防止多实例并发写同一路径时静默毁数据 (4 进程互踩事故, 100% blocks 丢失)。"""

    def __init__(self, base, name, y, m, schema=None):
        """schema 显式传入（None → 模块级 SCHEMAS[name]）。

        WS6c：抽取类工具（extract_sz_cancels）不再靠 `SCHEMAS['cancels']=...`
        的模块级注册副作用——显式参数化，去隐式全局耦合。
        """
        self.name = name
        self.schema = schema
        ddir = os.path.join(base, name, f'year={y}', f'month={m}')
        os.makedirs(ddir, exist_ok=True)
        self.final_path = os.path.join(ddir, 'part-000.parquet')
        # 唯一 tmp 路径: pid+序号, 任何来源的重复创建都绝不共享路径 (O_TRUNC
        # 互踩的物理前提是共享路径); 校验通过后 os.replace 原子提交
        global _TMP_SEQ
        _TMP_SEQ += 1
        self.path = os.path.join(ddir, f'part-000.parquet.tmp.{os.getpid()}.{_TMP_SEQ}')
        self.writer = pq.ParquetWriter(self.path, self.schema or SCHEMAS[name],
                                       compression='zstd', compression_level=3,
                                       use_dictionary=['code', 'order_type', 'bs'])
        self.rows = 0
        self.max_size = os.path.getsize(self.path)  # 活跃写者文件只增不减

    def append(self, tab):
        if tab.num_rows:
            # 截断检测必须在 write_table 之前 (2026-08-26 漏洞实测): 外部 O_TRUNC
            # 后受害进程 fd offset 不变, 继续写会让 size 从 0 恢复到 >= 原大小 —
            # 写后检查完全看不到异常 (事故精确模式: 截断到 1024/5532 后 1 秒内
            # 稀疏恢复到 1.6GB). 单写者下写前 size 必须 == max_size, 截断必破坏
            # 该等式, 且此刻尚未写新数据, 检测无竞态.
            st = os.stat(self.path)
            if st.st_size != self.max_size:
                # 触发即取证: 列出所有转换相关进程 + 持有本文件 fd 的进程 + 锁状态
                import subprocess
                diag = [f'  本进程 pid={os.getpid()}']
                try:
                    out = subprocess.run(
                        ['ps', '-eo', 'pid,ppid,etime,cmd'], capture_output=True,
                        text=True, timeout=10).stdout
                    for line in out.splitlines():
                        if 'convert_tick' in line or 'spawn_main' in line:
                            diag.append('  ' + line.strip())
                except Exception as e:
                    diag.append(f'  ps 失败: {e}')
                diag.append(f'  持有 {self.path} fd 的进程:')
                for p in sorted(os.listdir('/proc'), key=int):
                    if not p.isdigit():
                        continue
                    try:
                        for fd in os.listdir(f'/proc/{p}/fd'):
                            tgt = os.readlink(f'/proc/{p}/fd/{fd}')
                            if self.path.split('/')[-1] in tgt:
                                diag.append(f'    pid {p}: fd {fd} → {tgt}')
                    except OSError:
                        pass
                raise RuntimeError(
                    f'文件被外部截断/修改! {self.path} size={st.st_size} 期望={self.max_size} '
                    f'(pid={os.getpid()}) — 诊断:\n' + '\n'.join(diag))
            self.writer.write_table(tab)
            self.rows += tab.num_rows
            self.max_size = os.path.getsize(self.path)

    def close(self):
        self.writer.close()  # 所有内部缓冲落 fd, 文件此刻静止
        fd = os.open(self.path, os.O_RDONLY)
        try:
            os.fsync(fd)     # 强制写回 → st_blocks 反映真实物理分配
        finally:
            os.close(fd)
        # 提交校验: 结构可读 + schema 一致 + 行数一致 + 物理分配完整
        pf = pq.ParquetFile(self.path)
        if pf.schema_arrow != (self.schema or SCHEMAS[self.name]):
            raise RuntimeError(f'schema 不一致: {self.path}')
        if pf.metadata.num_rows != self.rows:
            raise RuntimeError(f'行数不一致: {self.path} '
                               f'metadata={pf.metadata.num_rows} 期望={self.rows}')
        st = os.stat(self.path)
        got = st.st_blocks * 512
        if got < st.st_size * 0.95:
            raise RuntimeError(
                f'提交前稀疏化检测失败: {self.path} size={st.st_size} '
                f'blocks={st.st_blocks} (仅 {got/st.st_size:.1%} 物理分配) → '
                f'文件有空洞, 放弃提交, 该月将重转')
        os.replace(self.path, self.final_path)
        dfd = os.open(os.path.dirname(self.final_path), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        return self.final_path, self.rows


SCHEMAS = {'trades': TRADES_SCHEMA, 'orders': ORDERS_SCHEMA, 'snapshots': SNAP_SCHEMA}
FLUSH_ZIPS = 6  # 累积多少个 zip 写一个 row group (~250k 行 trades)
_TMP_SEQ = 0  # 同进程内 tmp 路径唯一化 (pid+序号, 任何重复创建都不共享路径)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--only-day', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    # ---- 单实例锁 (2026-08-26 事故: 4 进程并发写同一输出路径, O_TRUNC 互清,
    # 35/35 文件 100% blocks 丢失, 全量数据被销毁). flock 不阻塞, 已有实例则退出. ----
    os.makedirs(OUT, exist_ok=True)
    lock_f = open(os.path.join(OUT, '.converter.lock'), 'w')
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f'另一个转换实例正在运行 (锁 {OUT}/.converter.lock 被占用) → 退出',
              flush=True)
        return
    # 清理上次异常退出遗留的 tmp 文件 (正常关闭已 os.replace, 残留必为孤儿)
    for d0 in os.listdir(OUT):
        if d0.startswith('.') or d0 == '_manifest':
            continue
        for root, _, files in os.walk(os.path.join(OUT, d0)):
            for f in files:
                if '.tmp.' in f:
                    p = os.path.join(root, f)
                    print(f'清理 stale tmp: {p}', flush=True)
                    os.unlink(p)

    import datetime as dt, re
    all_dirs = sorted(d for d in os.listdir(ROOT) if DAY_RE.match(d))
    weekend = [d for d in all_dirs
               if dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])).weekday() >= 5]
    workdays = [d for d in all_dirs if d not in weekend]
    print(f'dirs total={len(all_dirs)} weekend_isolated={len(weekend)} '
          f'workdays={len(workdays)}', flush=True)
    if args.only_day:
        workdays = [d for d in workdays if d == args.only_day]
    if not workdays:
        print('no workdays to process'); return
    if args.dry_run:
        print('dry-run: %d workdays, %d zips' % (
            len(workdays),
            sum(len(glob.glob(os.path.join(ROOT, d, '*', '*.zip'))) for d in workdays)))
        return

    t0 = time.time()
    from concurrent.futures import wait, FIRST_COMPLETED
    writers = {}   # (name, ym) -> MonthWriter
    buffers = {}   # (name, ym) -> [tab]
    n_buf = {}     # (name, ym) -> zip count
    errors, man_rows = [], []
    # 限流提交: in-flight <= MAX_INFLIGHT, 完成一个才提交下一个
    # (2026-08-25 教训: 74k future 全量提交 + 主进程写盘慢 → 已完成结果堆积 OOM 122GB)
    MAX_INFLIGHT = max(args.workers * 3, 12)
    pending = [(d, z) for d in workdays
               for z in sorted(glob.glob(os.path.join(ROOT, d, '*', '*.zip')))]
    n_done = 0
    last_milestone = 0
    # ---- executor 自愈循环 (2026-08-26 死锁修复) ----
    # 12 worker 曾全部满 CPU 卡死 5.5h (n_done 永久停滞)。fork 继承/系统事件/单 zip
    # 数据均已实验排除; jemalloc 累积损坏为最强嫌疑 (PYARROW_JEMALLOC=0 已规避)。
    # 本循环兜底: wait 900s 无任何完成 → 杀全部 worker → 未完成 zip 重新入队 →
    # 重建 executor 继续。无论根因为何, 转换必然完成 (卡死 zip 自动重试)。
    ctx = multiprocessing.get_context('spawn')  # spawn: 全新解释器, 排除 fork 变量
    STALL_S = 900
    stall_streak = 0
    while True:
        stall = False
        ex = ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx)
        futs = {}
        try:
            for _ in range(MAX_INFLIGHT):
                if not pending:
                    break
                d, z = pending.pop()
                futs[ex.submit(process_zip, z, d)] = (d, z)
            while futs:
                done, _ = wait(futs, timeout=STALL_S, return_when=FIRST_COMPLETED)
                if not done:
                    stall = True
                    print(f'STALL: {STALL_S}s 无任何完成 → 重启 worker '
                          f'(in-flight={len(futs)}, pending={len(pending)})',
                          flush=True)
                    break
                for fu in done:
                    d, z = futs.pop(fu)
                    n_done += 1
                    r = fu.result()
                    if r is None:
                        errors.append((d, os.path.basename(z)[:-4], 'date_shifted', ''))
                    elif isinstance(r, tuple) and r[0] == 'error':
                        errors.append((d, os.path.basename(z)[:-4], 'parse_error', r[1]))
                    else:
                        tt, ot, snt, mrow = r
                        ym = d[:6]
                        for name, tab in [('trades', tt), ('orders', ot), ('snapshots', snt)]:
                            key = (name, ym)
                            buffers.setdefault(key, []).append(tab)
                            n_buf[key] = n_buf.get(key, 0) + 1
                            if n_buf[key] >= FLUSH_ZIPS:
                                # 2026-08-26 修复: 绝不能用 setdefault(key, MonthWriter(...))
                                # —— setdefault 对已存在 key 仍会求值第二个参数, 每次 flush
                                # 都新建一个 MonthWriter 并 O_TRUNC 截断活跃 tmp 文件
                                # (run4 守卫抓到 1024≠490143; run5 活跃文件 blocks=1% 证实
                                # 截断后稀疏恢复, 写后检查看不见). 显式 if, 零副作用.
                                if key not in writers:
                                    writers[key] = MonthWriter(OUT, name, ym[:4], ym[4:])
                                big = pa.concat_tables(buffers.pop(key))
                                writers[key].append(big)
                                n_buf[key] = 0  # 2026-08-26 bugfix: 不重置则每 zip 都 flush
                        man_rows.append(mrow)
                if n_done % 5000 == 0:
                    total = n_done + len(futs) + len(pending)
                    print(f'  {n_done}/{total} zips done, elapsed '
                          f'{time.time()-t0:.0f}s', flush=True)
                else:
                    last_print = (n_done // 5000) * 5000
                    if last_print != last_milestone:
                        total = n_done + len(futs) + len(pending)
                        print(f'  {n_done}/{total} zips done (milestone {last_print} '
                              f'skipped), elapsed {time.time()-t0:.0f}s', flush=True)
                        last_milestone = last_print
                # 补提交, 维持 in-flight 窗口 (2026-08-25 bugfix: 之前漏了这行导致
                # 只处理初始 MAX_INFLIGHT 个 zip 就退出)
                while len(futs) < MAX_INFLIGHT and pending:
                    d2, z2 = pending.pop()
                    futs[ex.submit(process_zip, z2, d2)] = (d2, z2)
        finally:
            # 不等待卡死 worker (shutdown(wait=True) 会挂死); cancel 未启动任务
            ex.shutdown(wait=False, cancel_futures=True)
        if stall:
            stall_streak += 1
            # 卡死 worker 还活着 (shutdown(wait=False) 不等它们) → 杀掉回收
            for pid in list(getattr(ex, '_processes', {})):
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except (OSError, ChildProcessError):
                    pass
            if stall_streak >= 3:
                print(f'连续 {stall_streak} 次 STALL → 放弃剩余 {len(pending)} zip',
                      flush=True)
                errors.append(('', '', 'converter_stall',
                               f'abandoned {len(pending)} zips after {stall_streak} stalls'))
                break
            # 未完成任务重新入队 (卡死 zip 自动重试)
            for d, z in futs.values():
                pending.append((d, z))
            print(f'  worker 已清理, 剩余 {len(pending)} zip, 重建 executor', flush=True)
            continue
        stall_streak = 0
        break
    # flush 尾部缓冲 + close
    for key, tabs in buffers.items():
        if tabs:
            name, ym = key
            if key not in writers:  # 同 flush 处: 显式 if, 禁用 setdefault 副作用
                writers[key] = MonthWriter(OUT, name, ym[:4], ym[4:])
            writers[key].append(pa.concat_tables(tabs))
    summary = {}
    for key, w in sorted(writers.items()):
        p, rows = w.close()
        summary[key] = {'rows': rows, 'bytes': os.path.getsize(p)}
        print(f'{key[0]} {key[1]}: {rows:,} rows -> {os.path.getsize(p)/1e9:.2f} GB')
        # _SUCCESS 标记：分区目录整体写完（Spark 惯例；ingest 侧 discover_tasks
        # 以 _SUCCESS 存在为准，防半写分区入库）——writer.close() 已 fsync，
        # 此处补空标记即可
        sdir = os.path.dirname(p)
        with open(os.path.join(sdir, '_SUCCESS'), 'w') as f:
            f.write('')

    # manifest
    mdir = os.path.join(OUT, '_manifest')
    os.makedirs(mdir, exist_ok=True)
    mdf = pd.DataFrame(man_rows)
    mdf['trade_date'] = pd.to_datetime(mdf['trade_date'], format='%Y%m%d').dt.date
    for c in MANIFEST_SCHEMA.names:
        if c not in mdf:
            mdf[c] = None
    mdf = mdf[[c.name for c in MANIFEST_SCHEMA]]
    pq.write_table(pa.Table.from_pandas(mdf, preserve_index=False,
                                        schema=MANIFEST_SCHEMA),
                   os.path.join(mdir, 'conversion_manifest.parquet'),
                   compression='zstd', compression_level=3)
    pd.DataFrame(errors, columns=['day', 'code', 'error_type', 'detail']).to_csv(
        os.path.join(mdir, 'conversion_errors.csv'), index=False)
    meta = {'dataset': 'A_share_tick_fact', 'schema_version': '1.0',
            'code_format': 'XXXXXX.SH/.SZ/.BJ', 'source': 'Wind quark_downloaded',
            'workdays': len(workdays), 'weekend_isolated_dirs': weekend,
            'tables': {'trades': {'rows': summary.get(('trades', ''),
                                                      {}).get('rows', 0)}, },
            'n_errors': len(errors), 'elapsed_s': round(time.time() - t0, 1)}
    with open(os.path.join(mdir, 'conversion_summary.json'), 'w') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f'errors={len(errors)} total_elapsed_s={time.time()-t0:.0f}')


if __name__ == '__main__':
    main()
