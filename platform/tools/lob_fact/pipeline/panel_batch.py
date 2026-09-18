"""面板批算（A 路直折 1m）+ 日聚合 + 按日流式批算编排

语义源：tests/test_panel_batch.py（聚合口径唯一权威）+ W6 冻结列契约
（factor_panel.PANEL_COLS）；金样全等 = panel_1m A/B 双路过门产物（20250812/
20260803 各 4 code）。直折 1m 与 W6「1s 超集派生」路径逐列全等（探针实证），
故生产走 A 路直折，不跑 B 路重放（M7 门只属 W6 验证期）。

编排纪律（对齐 run_lob_batch 风格）：
  - day-major：一天一任务，worker 读当日三表各一次（谓词按 code 过滤切片）
  - multiprocessing.Pool(maxtasksperchild=1)：进程级隔离，内存不跨日累积
  - 断点续跑：输出文件存在即跳过（幂等）
  - MemAvailable 节流：低于阈值则等待
产出：
  panel_1m/year=YYYY/month=MM/YYYYMMDD.parquet   （全 code × 239 样本）
  tick_daily/year=YYYY/month=MM/YYYYMMDD.parquet （日聚合，每 code 一行）
"""
import os
import sys

os.environ.setdefault('ARROW_NUM_THREADS', '2')
os.environ.setdefault('OMP_NUM_THREADS', '2')
os.environ.setdefault('PYARROW_JEMALLOC', '0')
os.environ.setdefault('POLARS_MAX_THREADS', '2')

import json
import time
from datetime import date

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # lob_fact/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))                                                 # tools/

from core import config as C
from core import factor_panel as FP

MEM_FLOOR_MB = 8 * 1024   # MemAvailable 低于此则等待（同 run_lob_batch 精神）


def _log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def chunk_codes(codes, size):
    """按序切块（不重叠不遗漏）。"""
    codes = list(codes)
    return [codes[i:i + size] for i in range(0, len(codes), size)] if codes else []


def _lob_table_path(lob_root, table, day):
    return f'{lob_root}/{table}/year={day:%Y}/month={day:%m}/{day:%Y%m%d}.parquet'


def _read_day_table(lob_root, table, day):
    p = _lob_table_path(lob_root, table, day)
    if not os.path.exists(p):
        raise FileNotFoundError(f'缺 {table} 当日文件: {p}')
    return p


def _slice(df, code):
    out = df.filter(pl.col('code') == code)
    return out if out.height else None


def fold_code_day(code, day, lob_root):
    """单 code-day：A 路 BandFold 直折 1m 栅格 → 面板行（列序 = PANEL_COLS）。

    缺当日该 code 的 lob_events → FileNotFoundError（不静默返回空）。
    """
    if isinstance(day, str):
        day = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
    ev_df = pl.read_parquet(_read_day_table(lob_root, 'lob_events', day))
    sw_df = pl.read_parquet(_read_day_table(lob_root, 'lob_sweep_meta', day))
    ck_df = pl.read_parquet(_read_day_table(lob_root, 'lob_checkpoints', day))
    panels = fold_codes_from(ev_df, sw_df, ck_df, [code], day)
    if not panels:
        raise FileNotFoundError(f'lob_fact 缺表行: {code} {day:%Y%m%d}')
    return panels[0]


def fold_codes_from(ev_df, sw_df, ck_df, codes, day):
    """从当日三表切出 codes 并逐只直折（worker 复用：三表只读一次）。"""
    out = []
    for code in codes:
        ev = _slice(ev_df, code)
        if ev is None:
            continue
        sw = _slice(sw_df, code)
        ck = _slice(ck_df, code)
        rows = ev.sort('seq').to_dicts()
        sweeps = sw.sort('seq').to_dicts() if sw is not None else []
        ckpts = ck.sort(['time_ms', 'seq']).to_dicts() if ck is not None else []
        ck_ms = sorted({int(c['time_ms']) for c in ckpts})
        first = ck_ms[0] if ck_ms else C.OPEN + C.MINUTE_MS
        src = [x for x in FP.sample_times(FP.GRID_1M) if x >= first]
        fold = FP.BandFold().run(rows, sweeps, ckpts, src)
        out.append(pl.DataFrame(FP.panel_rows(fold.states, fold.nqs, fold.flows,
                                              src, code, day, '1m'),
                                schema_overrides={'trade_date': pl.Date}))
    return out


# ---------- 日聚合（口径权威 = tests/test_panel_batch.py） ----------

_FLOATS = list(FP.FLOAT_COLS)
_FLOWS = list(FP._FLOW_COLS)
_NQS = list(FP.NQ_COLS)


def day_agg(panel):
    """面板行 → 每 (code, trade_date) 一行日聚合。"""
    key = ['code', 'trade_date']
    exprs = [pl.len().alias('n_samples')]
    floats = [c for c in _FLOATS if c in panel.columns]
    flows = [c for c in _FLOWS if c in panel.columns]
    nqs = [c for c in _NQS if c in panel.columns]
    for c in floats:
        exprs += [
            pl.col(c).mean().alias(f'{c}_mean'),
            pl.col(c).std(0).alias(f'{c}_std'),
            pl.col(c).last().alias(f'{c}_last'),
            pl.col(c).max().alias(f'{c}_max'),
            pl.col(c).min().alias(f'{c}_min'),
            pl.col(c).filter(pl.col('time_ms') < C.LUNCH_START).mean().alias(f'{c}_am_mean'),
            pl.col(c).filter(pl.col('time_ms') > C.LUNCH_END).mean().alias(f'{c}_pm_mean'),
            pl.col(c).slice(-30).mean().alias(f'{c}_last30_mean'),
        ]
    for f in flows:
        exprs.append(pl.col(f).sum().alias(f'{f}_sum'))
    for n in nqs:
        exprs.append(pl.col(n).mean().alias(f'{n}_mean'))
    return panel.sort(key + ['time_ms']).group_by(key).agg(exprs)


# ---------- 按日流式编排 ----------

def process_day(day_str, lob_root):
    """一天：读三表 → 全 code 直折 → 写 panel_1m + tick_daily（存在即跳过）。"""
    day = date(int(day_str[:4]), int(day_str[4:6]), int(day_str[6:8]))
    out_panel = _lob_table_path(lob_root, 'panel_1m', day)
    out_agg = _lob_table_path(lob_root, 'tick_daily', day)
    if os.path.exists(out_panel) and os.path.exists(out_agg):
        return dict(day=day_str, skipped=True)
    t0 = time.time()
    ev = pl.read_parquet(_read_day_table(lob_root, 'lob_events', day))
    sw = pl.read_parquet(_read_day_table(lob_root, 'lob_sweep_meta', day))
    ck = pl.read_parquet(_read_day_table(lob_root, 'lob_checkpoints', day))
    codes = ev['code'].unique().sort().to_list()
    panels = []
    for chunk in chunk_codes(codes, 40):
        panels.extend(fold_codes_from(ev, sw, ck, chunk, day))
    if not panels:
        return dict(day=day_str, codes=0)
    panel = pl.concat(panels)
    agg = day_agg(panel)
    for path, df in ((out_panel, panel), (out_agg, agg)):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp.parquet'
        df.write_parquet(tmp)
        os.replace(tmp, path)
    return dict(day=day_str, codes=len(codes), n_panel_rows=panel.height,
                secs=round(time.time() - t0, 1))


def _mem_available_mb():
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable'):
                return int(line.split()[1]) // 1024
    return 1 << 20


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='W-P 面板批算（A 直折 1m）+ 日聚合')
    ap.add_argument('--lob-root', default=C.LOB_FACT_ROOT.rstrip('/'))
    ap.add_argument('--dates', default=None, help='逗号分隔 YYYYMMDD（缺省=扫描 lob_events）')
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args(argv)

    if args.dates:
        days = args.dates.split(',')
    else:
        days = sorted(p.stem for p in
                      __import__('pathlib').Path(args.lob_root, 'lob_events').rglob('*.parquet'))
    import multiprocessing as mp
    todo = []
    for d in days:
        dd = date(int(d[:4]), int(d[4:6]), int(d[6:8]))
        if not os.path.exists(_lob_table_path(args.lob_root, 'lob_events', dd)):
            continue
        todo.append(d)
    _log(f'批算开始: {len(todo)} 日 × workers={args.workers} root={args.lob_root}')
    ctx = mp.get_context('spawn')
    with ctx.Pool(args.workers, maxtasksperchild=1) as pool:
        done = 0
        for res in pool.imap_unordered(
                lambda d: _throttle(process_day, d, args.lob_root), todo):
            done += 1
            _log(f'{done}/{len(todo)} {res}')
    _log('批算完成')
    return 0


def _throttle(fn, day_str, lob_root):
    while _mem_available_mb() < MEM_FLOOR_MB:
        time.sleep(30)
    return fn(day_str, lob_root)


if __name__ == '__main__':
    sys.exit(main())
