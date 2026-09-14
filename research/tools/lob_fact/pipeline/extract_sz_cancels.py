#!/usr/bin/env python
"""W0: SZ 撤单行增补抽取 → tick_fact/cancels (additive, 不动现有 3 表/schema)


import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # lob_fact/
背景 (2026-09-09 实测): 深交所撤单发布在逐笔成交流 (成交代码='C', BS标志=空,
价格=0, 数量=撤单量, 引用被撤订单号), 占 SZ 逐笔成交 25.7-29.3%;
convert_tick_to_parquet.py 的"零价行过滤"(px==0) 把它们连同集合竞价虚拟行一起滤掉,
tick_fact trades 因此缺失 SZ 撤单流 (已逐日精确对账证实: tick_fact == raw 正常行).

本抽取器与转换器零价过滤完全正交: 只按 BS标志空白 且 成交代码=='C' 取行,
逐 code-day 双射对账 (n_blank==n_C, 空BS非C==0, C非空BS==0, ref 必>0, 量必>0),
任何违规则该 zip 整包隔离入 cancels_errors.csv (不静默吞).

复用 convert_tick_to_parquet.py 的 MonthWriter(唯一 tmp+fsync+st_blocks+schema 校验
+os.replace 原子提交)/flock 单实例/stall 看门狗(spawn ProcessPool, 900s) 架构。

输出: tick_fact/cancels/year=YYYY/month=MM/part-000.parquet (+_SUCCESS)
Schema (冻结): code str / trade_date date32 / time_ms int32 / trade_no int64 /
               side uint8 (0=B 1=S) / order_ref int64 (被撤单交易所委托号) /
               volume int32 (撤单量)
  不含价: 价格由消费引擎自身 resting 簿解析 (撤单时订单必在簿).
断点: 月目录 _SUCCESS 存在 = 该月已完成 → 重启自动跳过 (比 state.json 更强:
      MonthWriter.close 已做字节级校验才落 _SUCCESS); 半写月 (final 无 _SUCCESS)
      自动整月重建.

用法:
  python extract_sz_cancels.py --workers 6
  python extract_sz_cancels.py --only-day 20260803
"""
import os
# 线程/jemalloc env 必须在任何 pyarrow import 前 (2026-08-26 教训, 见转换器头注释)
os.environ.setdefault('ARROW_NUM_THREADS', '2')
os.environ.setdefault('OMP_NUM_THREADS', '2')
os.environ.setdefault('PYARROW_JEMALLOC', '0')
os.environ.setdefault('POLARS_MAX_THREADS', '4')
import sys
# 研究工具间引用：复用转换器（其 import 链已设 env 并导入 pyarrow）。
# WS6c：不再用 `cvt.SCHEMAS['cancels']=...` 模块级注册——MonthWriter 显式吃
# schema=CANCELS_SCHEMA（见下方实例化处）。
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'converters'))
import convert_tick_to_parquet as cvt
import io, glob, json, time, argparse, zipfile, signal, multiprocessing, fcntl
import numpy as np, pandas as pd
import datetime as dt
import pyarrow as pa
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED

CANCELS_SCHEMA = pa.schema([
    pa.field('code', pa.string()), pa.field('trade_date', pa.date32()),
    pa.field('time_ms', pa.int32()), pa.field('trade_no', pa.int64()),
    pa.field('side', pa.uint8()), pa.field('order_ref', pa.int64()),
    pa.field('volume', pa.int32())])

OUT = os.path.join(cvt.OUT, 'cancels')
FLUSH_ZIPS = 12   # 每 N 个 code-day 写一个 row group (~19 万行, SZ 撤单日均 ~1.6 万行)
STALL_S = 900
MAX_INFLIGHT_MUL = 3

# 抽取用列 (dtype 与转换器一致; 只要需要的列省内存)
READ_COLS = ['自然日', '时间', '成交编号', '成交代码', 'BS标志',
             '成交数量', '叫卖序号', '叫买序号']
READ_DTYPES = {'自然日': 'str', '时间': 'str', '成交编号': 'int64',
               '成交代码': 'str', 'BS标志': 'str', '成交数量': 'float64',
               '叫卖序号': 'float64', '叫买序号': 'float64'}


def extract_cancel_rows(df, trade_date):
    """从原始逐笔成交 DataFrame 提取撤单行 (纯函数, TDD 目标)。

    返回 (out, guard):
      out: dict of list, 键 = CANCELS_SCHEMA 列名 (不含 code/trade_date)
      guard: dict of 对账计数 — n_blank / n_C / n_blank_not_C / n_C_not_blank /
             n_zero_ref / n_bad_vol
    守卫违规不在此抛错 (调用层决定整包隔离), 但违规行绝不进入 out。
    """
    # NaN→'' 规范化: 源存在两代下载批次 (2026-09-10 实测) — 撤单行 BS 列
    # A 格式=' '(单空格), B 格式=真空 → pandas 读成 NaN → astype(str)='nan'.
    # 不规范化会把 B 格式整日 guard 误隔离 (26 工作日 3,661 code-day 全丢事故).
    bs = df['BS标志'].fillna('').astype(str).str.strip()
    cc = df['成交代码'].astype(str).str.strip()
    blank = bs.eq('').to_numpy()
    n_blank = int(blank.sum())
    is_c = cc.eq('C').to_numpy()
    n_C = int(is_c.sum())
    n_blank_not_C = int((blank & ~is_c).sum())
    n_C_not_blank = int((~blank & is_c).sum())
    # 撤单行 = 空BS 且 C (双射已实测 0 例外; 若未来格式漂移 → 计数暴露, zip 隔离)
    keep = blank & is_c
    sub = df[keep]
    bid = sub['叫买序号'].to_numpy(dtype=np.float64)
    ask = sub['叫卖序号'].to_numpy(dtype=np.float64)
    vol = sub['成交数量'].to_numpy(dtype=np.float64)
    has_bid = bid > 0
    has_ask = ask > 0
    n_zero_ref = int((~(has_bid | has_ask)).sum())
    good_ref = has_bid | has_ask
    n_bad_vol = int((vol <= 0).sum())
    ok = good_ref & (vol > 0)
    tm = cvt.parse_ms(sub['时间'][ok])
    out = {
        'time_ms': [int(x) for x in tm],
        'trade_no': sub['成交编号'].to_numpy()[ok].astype(np.int64).tolist(),
        'side': np.where(has_bid[ok], 0, 1).astype(np.uint8).tolist(),
        'order_ref': np.where(has_bid, bid, ask)[ok].astype(np.int64).tolist(),
        'volume': np.rint(vol[ok]).astype(np.int32).tolist(),
    }
    guard = {'n_blank': n_blank, 'n_C': n_C, 'n_blank_not_C': n_blank_not_C,
             'n_C_not_blank': n_C_not_blank, 'n_zero_ref': n_zero_ref,
             'n_bad_vol': n_bad_vol}
    return out, guard


def _dkey(v):
    """trade_date 键规范化: parquet 读回 date32 → 'YYYY-MM-DD'(len10); 运行中
    行是 dir 字符串 'YYYYMMDD'(len8). 双格式并存 = 同 code-day 双行 → 计数双计
    (2026-09-10 事故: manifest 15,271 重复键). 统一为 'YYYY-MM-DD'."""
    v = str(v)
    return v if len(v) == 10 else f'{v[:4]}-{v[4:6]}-{v[6:]}'


def merge_manifest_rows(hist, new_rows):
    """(code, trade_date) 键合并: 同键新行覆盖旧行, 历史行(不在本次)原样保留。

    only-day/月重建皆幂等 — 覆盖式重跑不丢其他 code-day 的索引行。
    (2026-09-09 事故: 曾在 only-day 时跳过 hist 读取 → manifest 被覆盖成仅当日;
    之后全量运行各月 _SUCCESS 全跳过 → 索引永久丢行。合并恒为 hist ∪ 新行。)
    """
    key = lambda r: (r['code'], _dkey(r['trade_date']))
    merged = {key(r): r for r in hist}
    for r in new_rows:
        merged[key(r)] = r
    return list(merged.values())


def manifest_row(code, trade_date, out, guard, zip_size):
    """逐 code-day manifest 摘要行 (双射 QA 数据)"""
    return {'code': code, 'trade_date': trade_date,
            'n_cancels': len(out['trade_no']),
            'sum_vol': int(np.sum(out['volume'])) if out['volume'] else 0,
            'n_blank': guard['n_blank'], 'n_C': guard['n_C'],
            'n_blank_not_C': guard['n_blank_not_C'],
            'n_C_not_blank': guard['n_C_not_blank'],
            'n_zero_ref': guard['n_zero_ref'], 'n_bad_vol': guard['n_bad_vol'],
            'source_zip_size': int(zip_size)}


def make_table(code, day, out):
    """out dict → pa.Table (CANCELS_SCHEMA)"""
    n = len(out['trade_no'])
    return pa.Table.from_arrays([
        pa.array(np.repeat(code, n)),
        cvt.date_arr(day, n),
        pa.array(out['time_ms'], pa.int32()),
        pa.array(out['trade_no'], pa.int64()),
        pa.array(out['side'], pa.uint8()),
        pa.array(out['order_ref'], pa.int64()),
        pa.array(out['volume'], pa.int32())], schema=CANCELS_SCHEMA)


def process_zip(z, day):
    """处理一个 zip: (tab, manifest_row) | None(隔离) | ('error', msg)。SIGALRM 兜底。"""
    def _timeout(sig, frm):
        raise TimeoutError(f'process_zip 超时 1200s: {os.path.basename(z)}')
    signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(1200)
    try:
        return _process_zip(z, day)
    finally:
        signal.alarm(0)


def _process_zip(z, day):
    code = os.path.basename(z)[:-4]  # '000155.SZ' (镜像转换器, 万得代码带后缀)
    try:
        with zipfile.ZipFile(z) as zf:
            df = pd.read_csv(io.BytesIO(zf.read('逐笔成交.csv')), encoding='gbk',
                             usecols=READ_COLS, dtype=READ_DTYPES)
        sentinel = (df['自然日'] == '0').to_numpy()
        rest = df[~sentinel]
        if len(rest) == 0 or (rest['自然日'] != day).any():
            return None  # 空/日期错位 → 隔离 (镜像转换器)
        out, guard = extract_cancel_rows(rest, day)
        # 严格守卫: 任何双射/身份违规 → 整包隔离 (绝不静默写残缺撤单流)
        if (guard['n_blank_not_C'] or guard['n_C_not_blank']
                or guard['n_zero_ref'] or guard['n_bad_vol']):
            return ('error', 'guard_violation ' + json.dumps(guard))
        tab = make_table(code, day, out)
        return (tab, manifest_row(code, day, out, guard, os.path.getsize(z)))
    except Exception as e:
        return ('error', f'{type(e).__name__}: {e}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--only-day', default=None)
    ap.add_argument('--days-file', default=None,
                    help='每行一个 YYYYMMDD 的补跑清单 (语义同 only-day: 不落月 _SUCCESS)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    # ---- 单实例锁 (复用转换器教训: 并发写 = O_TRUNC 互清毁数据) ----
    os.makedirs(OUT, exist_ok=True)
    lock_f = open(os.path.join(OUT, '.cancels_extract.lock'), 'w')
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f'另一抽取实例正在运行 (锁占用) → 退出', flush=True)
        return
    # 清理孤儿 tmp (正常关闭已 os.replace; .tmp. 残留必为异常退出)
    for root, _, files in os.walk(OUT):
        for f in files:
            if '.tmp.' in f:
                p = os.path.join(root, f)
                print(f'清理 stale tmp: {p}', flush=True)
                os.unlink(p)

    all_dirs = sorted(d for d in os.listdir(cvt.ROOT) if cvt.DAY_RE.match(d))
    weekend = [d for d in all_dirs
               if dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])).weekday() >= 5]
    workdays = [d for d in all_dirs if d not in weekend]
    print(f'dirs total={len(all_dirs)} weekend_isolated={len(weekend)} '
          f'workdays={len(workdays)}', flush=True)
    if args.only_day:
        workdays = [d for d in workdays if d == args.only_day]
    if args.days_file:
        with open(args.days_file) as f:
            sel = {l.strip() for l in f if l.strip()}
        workdays = [d for d in workdays if d in sel]
        args.only_day = args.days_file  # 语义同 only-day: 不落月 _SUCCESS
    if not workdays:
        print('no workdays to process'); return
    if args.dry_run:
        n = sum(len(glob.glob(os.path.join(cvt.ROOT, d, '*', '*.SZ.zip')))
                for d in workdays)
        print(f'dry-run: {len(workdays)} days, {n} SZ zips'); return

    # 完成标记: 月目录 _SUCCESS 存在 → 跳过 (MonthWriter.close 校验后才写)
    pending = []
    for d in workdays:
        ym = d[:6]
        sdir = os.path.join(OUT, f'year={ym[:4]}', f'month={ym[4:]}')
        if os.path.exists(os.path.join(sdir, '_SUCCESS')):
            continue
        # 半写月 (final 无 _SUCCESS) → 整月重建
        final = os.path.join(sdir, 'part-000.parquet')
        if os.path.exists(final):
            print(f'半写月重建: unlink {final}', flush=True)
            os.unlink(final)
        for z in sorted(glob.glob(os.path.join(cvt.ROOT, d, '*', '*.SZ.zip'))):
            pending.append((d, z))
    print(f'pending zips = {len(pending)}', flush=True)

    t0 = time.time()
    writers = {}   # ym -> MonthWriter
    buffers = {}   # ym -> [tab]
    n_buf = {}     # ym -> zip count
    man_rows, errors = [], []
    ctx = multiprocessing.get_context('spawn')
    MAX_INFLIGHT = max(args.workers * MAX_INFLIGHT_MUL, 12)
    n_done = 0
    stall_streak = 0
    while True:
        stall = False
        ex = ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx)
        futs = {}
        try:
            for _ in range(MAX_INFLIGHT):
                if not pending: break
                d, z = pending.pop()
                futs[ex.submit(process_zip, z, d)] = (d, z)
            while futs:
                done, _ = wait(futs, timeout=STALL_S, return_when=FIRST_COMPLETED)
                if not done:
                    stall = True
                    print(f'STALL: {STALL_S}s 无完成 → 重启 worker '
                          f'(in-flight={len(futs)} pending={len(pending)})', flush=True)
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
                        tab, mrow = r
                        ym = d[:6]
                        buffers.setdefault(ym, []).append(tab)
                        n_buf[ym] = n_buf.get(ym, 0) + 1
                        if n_buf[ym] >= FLUSH_ZIPS:
                            if ym not in writers:  # 显式 if (setdefault 副作用教训)
                                writers[ym] = cvt.MonthWriter(
                                    os.path.join(cvt.OUT), 'cancels', ym[:4], ym[4:],
                                    schema=CANCELS_SCHEMA)
                            writers[ym].append(pa.concat_tables(buffers.pop(ym)))
                            n_buf[ym] = 0
                        man_rows.append(mrow)
                if n_done % 2000 == 0:
                    print(f'  {n_done} zips done (total {len(pending)+len(futs)+n_done}), '
                          f'elapsed {time.time()-t0:.0f}s', flush=True)
                while len(futs) < MAX_INFLIGHT and pending:
                    d2, z2 = pending.pop()
                    futs[ex.submit(process_zip, z2, d2)] = (d2, z2)
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        if stall:
            stall_streak += 1
            for pid in list(getattr(ex, '_processes', {})):
                try:
                    os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
                except (OSError, ChildProcessError):
                    pass
            if stall_streak >= 3:
                print(f'连续 {stall_streak} 次 STALL → 放弃剩余 {len(pending)} zip',
                      flush=True)
                errors.append(('', '', 'extractor_stall',
                               f'abandoned {len(pending)} zips after {stall_streak} stalls'))
                break
            for d, z in futs.values():
                pending.append((d, z))
            print(f'  worker 已清理, 剩余 {len(pending)} zip, 重建 executor', flush=True)
            continue
        stall_streak = 0
        break

    # flush 尾部 + close (写 _SUCCESS = 完成标记)
    for ym, tabs in buffers.items():
        if tabs:
            if ym not in writers:
                writers[ym] = cvt.MonthWriter(os.path.join(cvt.OUT), 'cancels',
                                              ym[:4], ym[4:],
                                              schema=CANCELS_SCHEMA)
            writers[ym].append(pa.concat_tables(tabs))
    summary = {}
    # _SUCCESS = 整月完成标记 (resume 依据)。仅两条件全满足才写:
    #   (1) 全量模式 (only_day 会误导后续全量跳过整月 —— 2026-09-09 事故实录);
    #   (2) 该月无任何错误/隔离 zip (有错 → 不落标记 → 下次全量自动整月重建修复)
    err_ym = {d[:6] for d, c, e, x in errors if len(d) == 8}
    full_month = set()
    for ym, w in sorted(writers.items()):
        p, rows = w.close()
        summary[ym] = {'rows': rows, 'bytes': os.path.getsize(p)}
        print(f'cancels {ym}: {rows:,} rows -> {os.path.getsize(p)/1e9:.3f} GB', flush=True)
        if not args.only_day and ym not in err_ym:
            with open(os.path.join(os.path.dirname(p), '_SUCCESS'), 'w') as f:
                f.write('')
            full_month.add(ym)
        else:
            print(f'  {ym}: 不落 _SUCCESS '
                  f'({"only-day 模式" if args.only_day else f"月内有 {sum(1 for d in err_ym if d==ym)} 错误, 待全量重建"})',
                  flush=True)

    # ---- manifest: 新行 + 已 _SUCCESS 月的历史行 (跳过月不回读则索引缺失) ----
    mdir = os.path.join(cvt.OUT, '_manifest')
    os.makedirs(mdir, exist_ok=True)
    hist = []
    mpath = os.path.join(mdir, 'cancels_manifest.parquet')
    if os.path.exists(mpath):
        # 历史 manifest 无条件读取: only_day 若跳过历史, 会把 manifest 覆盖成仅当日,
        # 之后全量运行因各月 _SUCCESS 全跳过 → 读取截断 hist 重写 → 索引永久丢行.
        # 合并恒为 hist ∪ 本次新行 (key 覆盖), only_day 下历史行原样保留 (幂等).
        try:
            hist = pa.parquet.read_table(mpath).to_pylist()
        except Exception as e:
            print(f'历史 manifest 读取失败 (忽略, 重写): {e}', flush=True)
    merged_rows = merge_manifest_rows(hist, man_rows)
    mdf = pd.DataFrame(merged_rows)
    mdf['trade_date'] = pd.to_datetime(mdf['trade_date']).dt.date
    MANIFEST_SCHEMA = pa.schema([
        pa.field('code', pa.string()), pa.field('trade_date', pa.date32()),
        pa.field('n_cancels', pa.int64()), pa.field('sum_vol', pa.int64()),
        pa.field('n_blank', pa.int64()), pa.field('n_C', pa.int64()),
        pa.field('n_blank_not_C', pa.int64()), pa.field('n_C_not_blank', pa.int64()),
        pa.field('n_zero_ref', pa.int64()), pa.field('n_bad_vol', pa.int64()),
        pa.field('source_zip_size', pa.int64())])
    for c in MANIFEST_SCHEMA.names:
        if c not in mdf:
            mdf[c] = None
    mdf = mdf[[c.name for c in MANIFEST_SCHEMA]]
    pa.parquet.write_table(
        pa.Table.from_pandas(mdf, preserve_index=False, schema=MANIFEST_SCHEMA),
        mpath, compression='zstd', compression_level=3)
    # errors 合并 (day,code) 去重, 新覆盖旧
    epath = os.path.join(mdir, 'cancels_errors.csv')
    old = {}
    if os.path.exists(epath):
        # 0 行 errors 运行会写出 0 字节文件 → read_csv EmptyDataError 崩溃 (2026-09-10 事故)
        if os.path.getsize(epath) == 0:
            print('旧 errors csv 为空 (0 字节) → 忽略', flush=True)
        else:
            try:
                old = {(r['day'], r['code']): (r['error_type'], r['detail'])
                       for r in pd.read_csv(epath).to_dict('records')}
            except Exception as e:
                print(f'旧 errors csv 读取失败 (忽略): {e}', flush=True)
    for day, code, etype, det in errors:
        old[(day, code)] = (etype, det)
    # 显式 columns: 0 错误时也写表头 (空 DataFrame 写 0 字节 → 下次读崩溃)
    pd.DataFrame([{'day': d, 'code': c, 'error_type': e, 'detail': x}
                  for (d, c), (e, x) in sorted(old.items())],
                 columns=['day', 'code', 'error_type', 'detail']
                 ).to_csv(epath, index=False)
    meta = {'dataset': 'A_share_tick_cancels', 'schema_version': '1.0',
            'source': 'Wind quark_downloaded SZ 逐笔成交 撤单行(C)',
            'workdays': len(workdays), 'n_zips': n_done,
            'n_codes': mdf['code'].nunique(),
            'n_rows': int(mdf['n_cancels'].sum()),
            'sum_vol': int(mdf['sum_vol'].sum()),
            'weeks_months': sorted(set(writers) | set(summary)),
            'n_errors': len(errors), 'elapsed_s': round(time.time() - t0, 1)}
    with open(os.path.join(mdir, 'cancels_summary.json'), 'w') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f'zips_done={n_done} errors={len(errors)} rows={meta["n_rows"]:,} '
          f'elapsed_s={time.time()-t0:.0f}', flush=True)


if __name__ == '__main__':
    main()
