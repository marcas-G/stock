"""事件原生日聚合（event_daily）：lob_events/lob_sweep_meta → 每 code-day 一行

口径权威 = tests/test_event_daily.py（撤单洪峰/挂单存活/逐笔间隔/扫单强度）。
不经过分钟栅格、不重建簿状态——单遍事件流 + polars 向量化聚合。

编排（同 panel_batch 纪律）：day-major、spawn Pool(maxtasksperchild=1)、
幂等跳过、MemAvailable 节流。
产出：tick_daily_event/year=YYYY/month=MM/YYYYMMDD.parquet
"""
import os
import sys

os.environ.setdefault('ARROW_NUM_THREADS', '2')
os.environ.setdefault('OMP_NUM_THREADS', '2')
os.environ.setdefault('PYARROW_JEMALLOC', '0')
os.environ.setdefault('POLARS_MAX_THREADS', '2')

import time
from datetime import date

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # lob_fact/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))                                                 # tools/

from core import config as C

MEM_FLOOR_MB = 8 * 1024
_EV_COLS = ['code', 'time_ms', 'seq', 'kind', 'side', 'qty', 'id']


def _log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def _nan(x):
    return pl.col(x).cast(pl.Float64).is_nan() | pl.col(x).is_null()


def event_day_stats(ev, sw):
    """事件流 + sweep 表 → 日统计（每 code 一行；列口径见测试）。"""
    ev = ev.select([c for c in _EV_COLS if c in ev.columns])
    base = (ev.group_by('code')
            .agg([
                (pl.col('qty').filter(pl.col('kind') == 'add').count()).alias('n_add'),
                (pl.col('qty').filter(pl.col('kind') == 'cancel').count()).alias('n_cancel'),
                (pl.col('qty').filter(pl.col('kind') == 'trade').count()).alias('n_trade'),
                pl.col('qty').filter(pl.col('kind') == 'add').sum().alias('add_vol'),
                pl.col('qty').filter(pl.col('kind') == 'cancel').sum().alias('cancel_vol'),
                pl.col('qty').filter(pl.col('kind') == 'trade').sum().alias('trade_vol'),
            ]))
    base = base.with_columns(
        pl.when(pl.col('add_vol') > 0)
        .then(pl.col('cancel_vol') / pl.col('add_vol'))
        .otherwise(None).alias('cancel_rate'))

    # burst：1 秒桶内最大撤单量/挂单量
    buckets = (ev.filter(pl.col('kind').is_in(['add', 'cancel']))
               .with_columns((pl.col('time_ms') // 1000).alias('sec'))
               .group_by(['code', 'sec'])
               .agg([
                   pl.col('qty').filter(pl.col('kind') == 'cancel').sum().alias('cv'),
                   pl.col('qty').filter(pl.col('kind') == 'add').sum().alias('av'),
               ])
               .group_by('code')
               .agg([pl.col('cv').max().fill_null(0).alias('max_cancel_vol_1s'),
                     pl.col('av').max().fill_null(0).alias('max_add_vol_1s')]))

    # lifetime：cancel ↔ 唯一 add（歧义 id 剔除）
    adds = (ev.filter(pl.col('kind') == 'add')
            .group_by(['code', 'id'])
            .agg([pl.len().alias('n_add_id'), pl.col('time_ms').min().alias('add_ms')])
            .filter(pl.col('n_add_id') == 1))
    cks = (ev.filter(pl.col('kind') == 'cancel')
           .select(['code', 'id', 'time_ms'])
           .join(adds.select(['code', 'id', 'add_ms']), on=['code', 'id'], how='left'))
    cks = cks.with_columns((pl.col('time_ms') - pl.col('add_ms')).alias('life'))
    lt = (cks.group_by('code').agg([
        pl.col('add_ms').is_not_null().sum().alias('n_cancel_matched'),
        pl.col('life').filter(pl.col('life').is_not_null()).mean().alias('lifetime_mean'),
        pl.col('life').filter(pl.col('life').is_not_null()).median().alias('lifetime_med'),
        pl.col('life').filter(pl.col('life').is_not_null()).quantile(0.9, interpolation='linear').alias('lifetime_p90'),
        pl.col('life').filter(pl.col('life').is_not_null() & (pl.col('life') < 1000))
          .count().cast(pl.Float64).alias('n_flash'),
    ]))

    # trade 间隔（同 code 按 seq 升序）
    tr = (ev.filter(pl.col('kind') == 'trade')
          .sort(['code', 'seq'])
          .with_columns(pl.col('time_ms').diff().over('code').alias('gap')))
    gaps = (tr.group_by('code').agg([
        pl.col('gap').median().alias('trade_gap_med'),
        pl.col('gap').quantile(0.9, interpolation='linear').alias('trade_gap_p90'),
    ]))
    tstats = tr.group_by('code').agg([
        (pl.col('qty').mean()).alias('avg_trade_qty'),
        pl.col('qty').max().alias('max_trade_qty'),
    ])

    # sweep
    swg = (sw.group_by('code').agg([
        pl.len().alias('n_sweep_rows'),
        pl.col('vol_before').sum().alias('sweep_vol_sum'),
        pl.col('vol_before').max().alias('sweep_vol_max'),
        pl.col('vol_before').filter(pl.col('side') == 'S').sum().alias('sweep_buy_vol'),
        pl.col('vol_before').filter(pl.col('side') == 'B').sum().alias('sweep_sell_vol'),
    ]))

    ltr = (lt.with_columns(
        pl.when(pl.col('n_cancel_matched') > 0)
        .then(pl.col('n_flash') / pl.col('n_cancel_matched'))
        .otherwise(None).alias('flash_cancel_share'))
        .drop('n_flash'))

    # 时段列：am(<11:30) / pm(>13:00) / close30(>=14:30，pm 子段)
    seg_def = [('am', pl.col('time_ms') < C.LUNCH_START),
               ('pm', pl.col('time_ms') > C.LUNCH_END),
               ('close30', pl.col('time_ms') >= C.CLOSE - 1_800_000)]
    seg_ev_agg = []
    for s, m in seg_def:
        seg_ev_agg += [
            pl.col('qty').filter(m & (pl.col('kind') == 'add')).count().alias(f'n_add_{s}'),
            pl.col('qty').filter(m & (pl.col('kind') == 'add')).sum().alias(f'add_vol_{s}'),
            pl.col('qty').filter(m & (pl.col('kind') == 'cancel')).count().alias(f'n_cancel_{s}'),
            pl.col('qty').filter(m & (pl.col('kind') == 'cancel')).sum().alias(f'cancel_vol_{s}'),
        ]
    segs_ev = ev.group_by('code').agg(seg_ev_agg)
    seg_lt_agg = []
    for s, m in seg_def:
        seg_lt_agg += [
            pl.col('add_ms').filter(m & pl.col('add_ms').is_not_null())
              .count().cast(pl.Float64).alias(f'n_matched_{s}'),
            pl.col('life').filter(m & pl.col('add_ms').is_not_null() & (pl.col('life') < 1000))
              .count().cast(pl.Float64).alias(f'n_flash_{s}'),
        ]
    segs_lt = cks.group_by('code').agg(seg_lt_agg)
    for s, _ in seg_def:
        segs_lt = segs_lt.with_columns(
            pl.when(pl.col(f'n_matched_{s}') > 0)
            .then(pl.col(f'n_flash_{s}') / pl.col(f'n_matched_{s}'))
            .otherwise(None).alias(f'flash_share_{s}'))
    segs_lt = segs_lt.drop([f'n_matched_{s}' for s, _ in seg_def]
                           + [f'n_flash_{s}' for s, _ in seg_def])
    seg_sw_agg = []
    for s, m in seg_def:
        seg_sw_agg += [
            pl.col('vol_before').filter(m & (pl.col('side') == 'S')).sum().alias(f'sweep_buy_vol_{s}'),
            pl.col('vol_before').filter(m & (pl.col('side') == 'B')).sum().alias(f'sweep_sell_vol_{s}'),
            pl.col('vol_before').filter(m).sum().alias(f'sweep_vol_sum_{s}'),
            pl.col('vol_before').filter(m).count().alias(f'n_sweep_rows_{s}'),
        ]
    segs_sw = sw.group_by('code').agg(seg_sw_agg)

    out = (base
           .join(buckets, on='code', how='left')
           .join(ltr, on='code', how='left')
           .join(gaps, on='code', how='left')
           .join(tstats, on='code', how='left')
           .join(swg, on='code', how='left'))
    out = out.with_columns(
        (pl.col('n_cancel_matched') / pl.col('n_cancel').cast(pl.Float64))
        .alias('cancel_match_ratio'),
        (pl.when((pl.col('sweep_buy_vol') + pl.col('sweep_sell_vol')) > 0)
         .then((pl.col('sweep_buy_vol') - pl.col('sweep_sell_vol'))
               / (pl.col('sweep_buy_vol') + pl.col('sweep_sell_vol')))
         .otherwise(None).cast(pl.Float64).alias('sweep_imb')),
    )
    for c in ['n_cancel_matched', 'n_sweep_rows', 'sweep_vol_sum',
              'sweep_buy_vol', 'sweep_sell_vol']:
        out = out.with_columns(pl.col(c).cast(pl.Float64).fill_null(0))
    out = out.join(segs_ev, on='code', how='left')
    out = out.join(segs_lt, on='code', how='left')
    out = out.join(segs_sw, on='code', how='left')
    for s, _ in seg_def:
        out = out.with_columns(
            pl.when(pl.col(f'add_vol_{s}') > 0)
            .then(pl.col(f'cancel_vol_{s}') / pl.col(f'add_vol_{s}'))
            .otherwise(None).alias(f'cancel_rate_{s}'),
        )
        for c in ([f'n_add_{s}', f'n_cancel_{s}', f'add_vol_{s}', f'cancel_vol_{s}',
                   f'n_sweep_rows_{s}', f'sweep_buy_vol_{s}', f'sweep_sell_vol_{s}',
                   f'sweep_vol_sum_{s}'] ):
            out = out.with_columns(pl.col(c).cast(pl.Float64).fill_null(0))
    return out


# ---------- 按日批算编排 ----------

def _out_path(lob_root, day):
    return f'{lob_root}/tick_daily_event/year={day:%Y}/month={day:%m}/{day:%Y%m%d}.parquet'


def _input_path(lob_root, table, day):
    return f'{lob_root}/{table}/year={day:%Y}/month={day:%m}/{day:%Y%m%d}.parquet'


def run_event_day(day_str, lob_root):
    day = date(int(day_str[:4]), int(day_str[4:6]), int(day_str[6:8]))
    out = _out_path(lob_root, day)
    if os.path.exists(out):
        return dict(day=day_str, skipped=True)
    t0 = time.time()
    ev = pl.read_parquet(_input_path(lob_root, 'lob_events', day),
                         columns=_EV_COLS)
    sw = pl.read_parquet(_input_path(lob_root, 'lob_sweep_meta', day))
    stats = event_day_stats(ev, sw).with_columns(
        pl.lit(day_str).str.to_date("%Y%m%d").alias("trade_date"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + '.tmp.parquet'
    stats.write_parquet(tmp)
    os.replace(tmp, out)
    return dict(day=day_str, codes=stats.height, secs=round(time.time() - t0, 1))


def _mem_available_mb():
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable'):
                return int(line.split()[1]) // 1024
    return 1 << 20


def _worker(a):
    fn, day_str, lob_root = a
    while _mem_available_mb() < MEM_FLOOR_MB:
        time.sleep(30)
    return globals()[fn](day_str, lob_root)


def main(argv=None):
    import argparse
    import multiprocessing as mp
    ap = argparse.ArgumentParser(description='事件原生日聚合批算')
    ap.add_argument('--lob-root', default=C.LOB_FACT_ROOT.rstrip('/'))
    ap.add_argument('--workers', type=int, default=16)
    args = ap.parse_args(argv)
    import pathlib
    days = sorted(p.stem for p in
                  pathlib.Path(args.lob_root, 'lob_events').rglob('*.parquet'))
    _log(f'事件日聚合批算: {len(days)} 日 workers={args.workers}')
    ctx = mp.get_context('spawn')
    with ctx.Pool(args.workers, maxtasksperchild=1) as pool:
        todo = [( 'run_event_day', d, args.lob_root) for d in days]
        done = 0
        for res in pool.imap_unordered(_worker, todo):
            done += 1
            _log(f'{done}/{len(todo)} {res}')
    _log('批算完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
