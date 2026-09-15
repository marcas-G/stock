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
  python convert_tick_to_parquet.py --only-day 20260821
      # 单日验证：默认落 calib/tick_fact_validation，**绝不覆盖** tick_fact 月产物
"""
import os
# 线程限制必须在 pyarrow import 前设置 (2026-08-26 教训: 8 worker × 40 线程 pyarrow
# 默认线程池 = 360+ 线程 → 与共享机器 4684 线程竞争, 调度过载 → 吞吐掉 100 倍)
os.environ.setdefault('ARROW_NUM_THREADS', '2')
os.environ.setdefault('OMP_NUM_THREADS', '2')
# 2026-08-26 死锁修复: pyarrow 17 默认 jemalloc 内存池长生命周期累积损坏嫌疑
# (12 worker 满 CPU 死循环 5.5h) → 切系统 malloc, 规避分配器层问题
os.environ.setdefault('PYARROW_JEMALLOC', '0')
import io, glob, json, time, argparse, zipfile, signal   # R10：multiprocessing 随编排收敛删除
import sys as _sys
from pathlib import Path as _Path

# R4c：时间解析收敛到 core.factio.timeparse（此前本文件自写一份 numpy 版同规则实现）
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))      # tools/
from _env import ensure_platform as _ensure_platform  # noqa: E402

_ensure_platform()
from lib import writekit as W  # noqa: E402  （R8c 漏接线：锁/标记单点；R9 由 end-to-end 测试抓回）
from lib import tickkit as K  # noqa: E402  （R11：共享小件单点，含时间解析薄封装）
from lib.monthflow import MonthPartitionSink  # noqa: E402  （R11：月分片写入骨架）
import pandas as pd, numpy as np, pyarrow as pa, pyarrow.parquet as pq

from factorlab.core.factio import partitions, paths  # noqa: E402  （R8c：路径/分区单点）
from factorlab.adapters.batch_flock import BatchFlock  # noqa: E402  （R10：P-5 编排单点）
from factorlab.ports.batch import Task  # noqa: E402
ROOT = f'{paths.quark_root()}/'
OUT = f'{paths.tick_fact_root()}/'
# R01-TOOLS-I10：单日验证的独立根（与生产月产物物理隔离；对标 bars_1m_validation）
VALIDATION_ROOT = str(paths.CALIB_ROOT / 'tick_fact_validation')
DAY_RE = K.DAY_RE               # R11：单点在 lib/tickkit

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


parse_ms = K.parse_ms          # R11：实现单点在 lib/tickkit（平台 timeparse 薄封装）


def to_int64_nullable(v: np.ndarray) -> pa.Array:
    return pa.array(pd.array(v, dtype='Int64'), type=pa.int64())


def to_int32_nullable(v: np.ndarray) -> pa.Array:
    return pa.array(pd.array(v, dtype='Int32'), type=pa.int32())


date_arr = K.date_arr           # R11：实现单点在 lib/tickkit


def _run_one_zip(task):
    """BatchFlock 的 worker 适配器（模块级 → spawn 可 pickle）：只算不回写。

    R11 注记：一次批量替换按"首个 `def date_arr` 到 `def process_zip`"切片，把这个函数
    一并切掉了（`NameError` 由真跑暴露）——批量改写必须逐段校验，这就是一例。
    """
    d, z = task.payload
    return process_zip(z, d)


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


# R9：原 `MonthWriter`（唯一 tmp + fsync + st_blocks 完整性 + schema/行数校验）已移入
# `lib/writekit`（研究侧**唯一**落盘实现）；此处保留 `SCHEMAS` 与 `FLUSH_ZIPS`。


SCHEMAS = {'trades': TRADES_SCHEMA, 'orders': ORDERS_SCHEMA, 'snapshots': SNAP_SCHEMA}
FLUSH_ZIPS = 6  # 累积多少个 zip 写一个 row group (~250k 行 trades)
STALL_S = 900        # 停滞判定窗（R10：作为 BatchFlock 的 stall_s）
_TMP_SEQ = 0  # 同进程内 tmp 路径唯一化 (pid+序号, 任何重复创建都不共享路径)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--only-day', default=None,
                    help='单日验证（默认落 tick_fact_validation，绝不覆盖月产物）')
    ap.add_argument('--out-root', default=None,
                    help='显式输出根（默认：全量 → tick_fact；--only-day → tick_fact_validation）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    # ---- R01-TOOLS-I10：单日验证与生产月产物物理隔离（此前 --only-day 会把整月
    # part-000.parquet 原子替换成单日文件 —— 一个"单日验证"就毁掉整月）----
    out_root = args.out_root or (VALIDATION_ROOT if args.only_day else OUT)
    if args.only_day and os.path.abspath(out_root) == os.path.abspath(OUT):
        raise SystemExit(
            f'拒绝: --only-day 不得写入生产根 {OUT}（会原子替换整月产物为单日文件）。\n'
            f'单日验证请省略 --out-root（默认 {VALIDATION_ROOT}），或指定另一个 --out-root。')

    # ---- 单实例锁 (2026-08-26 事故: 4 进程并发写同一输出路径, O_TRUNC 互清,
    # 35/35 文件 100% blocks 丢失, 全量数据被销毁). flock 不阻塞, 已有实例则退出. ----
    os.makedirs(out_root, exist_ok=True)
    try:   # R8c：单写者锁收敛到 lib.writekit（原为第 4 份自写 flock）
        _LOCK = W.acquire_lock(os.path.join(out_root, '.converter.lock'))  # 局部变量：函数返回即放锁，勿删
    except W.LockBusy:
        print(f'另一个转换实例正在运行 (锁 {out_root}/.converter.lock 被占用) → 退出',
              flush=True)
        return
    # 清理上次异常退出遗留的 tmp 文件 (正常关闭已 os.replace, 残留必为孤儿)
    for d0 in os.listdir(out_root):
        if d0.startswith('.') or d0 == '_manifest':
            continue
        for root, _, files in os.walk(os.path.join(out_root, d0)):
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
    # R11：缓冲/阈值 flush/月分片写入收进 lib/monthflow（单一实现 + 两条事故回归测试）
    sink = MonthPartitionSink(out_root, schema_of=lambda n: SCHEMAS[n],
                              flush_units=FLUSH_ZIPS, kind_of=lambda key: key)
    errors, man_rows = [], []
    # 限流提交: in-flight <= MAX_INFLIGHT, 完成一个才提交下一个
    # (2026-08-25 教训: 74k future 全量提交 + 主进程写盘慢 → 已完成结果堆积 OOM 122GB)
    MAX_INFLIGHT = max(args.workers * 3, 12)
    pending = [(d, z) for d in workdays
               for z in sorted(glob.glob(os.path.join(ROOT, d, '*', '*.zip')))]
    n_done = 0
    last_milestone = 0
    # ---- 编排收敛到平台 P-5（R10）----
    # 原先本文件自建的"spawn 进程池自愈循环"整段删掉：2026-08-26 的死锁兜底
    # （wait 900s 无完成 → 杀 worker → 未完成 zip 重新入队 → 重建 executor）
    # 正是 `BatchFlock(stall_policy='requeue', stall_strikes=3)` 的语义。
    # 逐条对齐：spawn（fork 会复制主进程缓冲；2026-08-25 OOM 122GB 教训）✓、
    # in-flight=max(workers*3,12) 限流（74k future 全量提交 → OOM 教训）✓、
    # 停滞 900s 重启+重试 ✓、失败记账不中断 ✓、结果按完成顺序回调 ✓。
    def on_result(task, payload):
        """主进程侧消费（缓冲 + 记账）；worker 只回传表与回执行。"""
        nonlocal n_done, last_milestone
        d, z = task.payload
        n_done += 1
        if payload is None:
            errors.append((d, os.path.basename(z)[:-4], 'date_shifted', ''))
        elif isinstance(payload, tuple) and payload[0] == 'error':
            errors.append((d, os.path.basename(z)[:-4], 'parse_error', payload[1]))
        else:
            tt, ot, snt, mrow = payload
            ym = d[:6]
            for name, tab in [('trades', tt), ('orders', ot), ('snapshots', snt)]:
                sink.add((name, ym), tab)   # R11：骨架负责缓冲/阈值/事故修法
            man_rows.append(mrow)
        if n_done % 5000 == 0:
            print(f'  {n_done}/{len(tasks)} zips done, elapsed '
                  f'{time.time()-t0:.0f}s', flush=True)
        else:
            last_print = (n_done // 5000) * 5000
            if last_print != last_milestone:
                print(f'  {n_done}/{len(tasks)} zips done (milestone {last_print} '
                      f'skipped), elapsed {time.time()-t0:.0f}s', flush=True)
                last_milestone = last_print

    tasks = [Task(key=f'{d}|{os.path.basename(z)}', payload=(d, z)) for d, z in pending]
    rep = BatchFlock().run(
        tasks, _run_one_zip, workers=args.workers, stall_s=STALL_S,
        mp_context='spawn', max_inflight=MAX_INFLIGHT,
        stall_policy='requeue', stall_strikes=3, on_result=on_result)
    if rep.failed:
        for r in rep.failures:
            if r.error and r.error.startswith('stall'):
                errors.append(('', '', 'converter_stall', r.error))
        print(f'转换失败 {rep.failed} 个单元（记账见 conversion_errors.csv；可重跑）',
              flush=True)
    # R11：尾部 flush + close + _SUCCESS 都是骨架的收尾（历史逐字节同形）
    closed = sink.close_all()
    summary = {}
    for key, (p, rows) in closed.items():
        summary[key] = {'rows': rows, 'bytes': os.path.getsize(p)}
        print(f'{key[0]} {key[1]}: {rows:,} rows -> {os.path.getsize(p)/1e9:.2f} GB')
        # _SUCCESS 标记：分区目录整体写完（Spark 惯例；ingest 侧 discover_tasks
        # 以 _SUCCESS 存在为准，防半写分区入库）——writer.close() 已 fsync，
        # 此处补空标记即可
        W.mark_success(os.path.dirname(p))   # R8c：标记语义单点（lib.writekit）

    # manifest
    mdir = os.path.join(out_root, '_manifest')
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
            'mode': 'validation' if args.only_day else 'production',
            'out_root': out_root,
            'workdays': len(workdays), 'weekend_isolated_dirs': weekend,
            'tables': {'trades': {'rows': summary.get(('trades', ''),
                                                      {}).get('rows', 0)}, },
            'n_errors': len(errors), 'elapsed_s': round(time.time() - t0, 1)}
    with open(os.path.join(mdir, 'conversion_summary.json'), 'w') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f'errors={len(errors)} total_elapsed_s={time.time()-t0:.0f}')


if __name__ == '__main__':
    main()
