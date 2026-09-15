#!/usr/bin/env python3
"""第二层：SAS/Activation —— 1m→5m 动态聚合 + 卖压冲击特征。

R20 收编自 ashare_alpha3 的 `30_run_layer2_sas.py`（逻辑逐行保留）：改动只有
读取层改经 `universe_paths`/`readers`，MinuteStore 现在按 `partitions` 派生的月文件清单读
（原为 config 里的 `bars_1m_glob` 字符串）。
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import universe_paths  # noqa: E402
from layer2.sas import build_event_features  # noqa: E402
from readers.daily import DailyStore  # noqa: E402
from readers.minute import MinuteStore  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default=None)
    p.add_argument('--scan-date', required=True)
    p.add_argument('--top-k', type=int, default=300)
    a = p.parse_args()
    cfg = universe_paths.load_config(a.config)
    out = universe_paths.out_dir(cfg, 'research')

    ranked = pd.read_parquet(universe_paths.golden_universe())
    sd = pd.Timestamp(a.scan_date).normalize()
    s = ranked[pd.to_datetime(ranked['scan_date']).dt.normalize() == sd] \
        .sort_values('rank').head(a.top_k)
    if s.empty:
        raise SystemExit('snapshot not found')

    daily = DailyStore(universe_paths.daily_fact()).read(codes=s['code'].tolist(), end=sd)
    cal = sorted(pd.to_datetime(daily['trade_date']).dt.normalize().unique())
    dates = [d for d in cal if d < sd][-int(cfg['layer2']['lookback_trade_days']):]
    if not dates:
        raise SystemExit('no lookback dates')

    b5 = MinuteStore(universe_paths.bars_1m_root()).query_5m(
        s['code'].tolist(), dates[0], dates[-1])
    feat, events = build_event_features(b5, cfg['layer2'])
    feat['scan_date'] = sd
    feat = feat.merge(s[['code', 'rank', 'score']], on='code', how='left')
    feat.to_parquet(out / f'sas_features_{sd.date()}.parquet', index=False)
    events.to_parquet(out / f'sas_events_{sd.date()}.parquet', index=False)
    print('features', len(feat), 'events', len(events))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
