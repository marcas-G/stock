#!/usr/bin/env python3
"""第一层：V4 月度粗筛 → `v4_top300_local` / `v4_top100_local` / `v4_eligible_local`。

R20 收编自 ashare_alpha3 的 `10_run_layer1.py`（**筛选逻辑逐行保留**）：改动只有
路径经 `universe_paths`、模块级代码收进 `main()`、输出默认落 `<工具>/outputs/universes`。

用法：
  python scripts/run_layer1.py --scan-date 2026-07-01
  python scripts/run_layer1.py --from-golden --start 2020-01-01 --end 2026-07-31
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

# ---- R20：真正的路径自举（工具根 + tools/；`_env` 之外的平台注入仍走 tools/_env.py）----
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))            # universe_stages/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))                                                          # tools/

import universe_paths  # noqa: E402
from layer1.v4 import run_v4_snapshot  # noqa: E402
from readers.daily import DailyStore, IndexDailyStore  # noqa: E402
from readers.fundamentals import FundamentalsStore  # noqa: E402


def run_one(cfg, store, fstore, istore, scan, factor=None):
    scan = pd.Timestamp(scan).normalize()
    factor = (pd.Timestamp(factor).normalize() if factor is not None
              else store.previous_trade_date(scan))
    fundamentals = fstore.snapshot(factor)
    index_close = istore.close_window(factor, count=int(cfg['layer1'].get('history_count', 520)))
    return (*run_v4_snapshot(store, fundamentals, index_close, scan, factor, cfg['layer1']), factor)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default=None, help='工具配置（默认 <工具>/config.yaml）')
    p.add_argument('--scan-date')
    p.add_argument('--factor-date')
    p.add_argument('--from-golden', action='store_true',
                   help='按 golden 输出里的全部历史 scan/factor 日期批量跑')
    p.add_argument('--start')
    p.add_argument('--end')
    a = p.parse_args()
    cfg = universe_paths.load_config(a.config)
    out = universe_paths.out_dir(cfg, 'universes')

    store = DailyStore(universe_paths.daily_fact())
    fstore = FundamentalsStore(universe_paths.fundamentals())
    istore = IndexDailyStore(universe_paths.index_daily())

    if a.from_golden:
        g = pd.read_parquet(universe_paths.golden_universe())
        g['scan_date'] = pd.to_datetime(g['scan_date']).dt.normalize()
        g['factor_date'] = pd.to_datetime(g['factor_date']).dt.normalize()
        dates = g[['scan_date', 'factor_date']].drop_duplicates().sort_values('scan_date')
        if a.start:
            dates = dates[dates['scan_date'] >= pd.Timestamp(a.start)]
        if a.end:
            dates = dates[dates['scan_date'] <= pd.Timestamp(a.end)]
        all300, all100, summaries = [], [], []
        for r in dates.itertuples(index=False):
            t300, t100, ranked, summary, _ = run_one(cfg, store, fstore, istore, r.scan_date, r.factor_date)
            all300.append(t300)
            all100.append(t100)
            summaries.append(summary)
            print('SCAN', summary)
        pd.concat(all300, ignore_index=True).to_parquet(out / 'v4_top300_local.parquet', index=False)
        pd.concat(all100, ignore_index=True).to_parquet(out / 'v4_top100_local.parquet', index=False)
        (out / 'v4_scan_summary.json').write_text(
            json.dumps(summaries, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        if not a.scan_date:
            raise SystemExit('--scan-date is required unless --from-golden')
        t300, t100, ranked, summary, factor = run_one(
            cfg, store, fstore, istore, a.scan_date, a.factor_date)
        t300.to_parquet(out / 'v4_top300_local.parquet', index=False)
        t100.to_parquet(out / 'v4_top100_local.parquet', index=False)
        ranked.to_parquet(out / 'v4_eligible_local.parquet', index=False)
        (out / 'v4_scan_summary.json').write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(summary)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
