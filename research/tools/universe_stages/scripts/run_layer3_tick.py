#!/usr/bin/env python3
"""第三层：tick 微观结构特征（仅 20260817 全市场逐笔成交可用）。

R20 收编自 ashare_alpha3 的 `40_run_layer3_tick.py`（逻辑逐行保留）：改动只有
路径经 `universe_paths`、读取层改经 `readers.tick`、输出默认落
`<工具>/outputs/research`。

流程：layer2 特征（sas_features_*.parquet）→ 候选前 k → 每股 shock 事件
（sas_events_*.parquet 的 datetime）→ WindTickStore 读逐笔成交 →
事件前/后窗口特征（signed flow、大单占比、反转）→ 输出。
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import universe_paths  # noqa: E402
from layer3.tick_features import event_reversal_features  # noqa: E402
from readers.tick import WindTickStore  # noqa: E402

PRE_SEC = 1800
POST_SEC = 3600


def _worker(args):
    code, trade_date, events, tick_root = args
    try:
        ts = WindTickStore(tick_root)
        df = ts.read_trade_csv(code, trade_date)
    except FileNotFoundError:
        return code, None, 'no tick file'
    if df.empty:
        return code, None, 'empty ticks'
    rows = []
    for ev in events:
        try:
            r = event_reversal_features(df, ev, pre_seconds=PRE_SEC, post_seconds=POST_SEC)
        except Exception:
            r = {}
        row = {'code': code, 'event_time': ev}
        row.update(r)
        rows.append(row)
    return code, pd.DataFrame(rows), None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default=None, help='工具配置（默认 <工具>/config.yaml）')
    p.add_argument('--scan-date', required=True)
    p.add_argument('--top-k', type=int)
    p.add_argument('--workers', type=int, default=16)
    a = p.parse_args()
    cfg = universe_paths.load_config(a.config)
    out = universe_paths.out_dir(cfg, 'research')
    sd = pd.Timestamp(a.scan_date).normalize()
    k = a.top_k or int(cfg['layer3']['default_top_k'])

    feats_path = out / f'sas_features_{sd.date()}.parquet'
    events_path = out / f'sas_events_{sd.date()}.parquet'
    if not feats_path.exists() or not events_path.exists():
        raise SystemExit(f'run layer2 first (missing {feats_path} or {events_path})')

    sas = pd.read_parquet(feats_path)
    cols = [c for c in ['hv_pi12', 'shock_drop_mean'] if c in sas.columns]
    if not cols:
        raise SystemExit('no layer2 research columns available')
    cands = sas.sort_values(cols, ascending=[False] * len(cols)).head(k)
    events = pd.read_parquet(events_path)
    tick_root = universe_paths.ticks_root()
    trade_date = sd.strftime('%Y%m%d')

    per_code = {}
    for code in cands['code']:
        ev = events[(events['code'] == code)]['datetime'].tolist()
        if ev:
            per_code[code] = ev
    print(f'candidates: {len(cands)}, codes with events: {len(per_code)}')

    if not per_code:
        raise SystemExit('no events; nothing to do')

    tasks = [(code, trade_date, evs, tick_root) for code, evs in per_code.items()]
    ok, missing, errs = [], 0, 0
    with mp.Pool(a.workers) as pool:
        for code, df, err in pool.imap_unordered(_worker, tasks):
            if err or df is None:
                if err and 'no tick file' in str(err):
                    missing += 1
                else:
                    errs += 1
                continue
            ok.append(df)
    print(f'processed: {len(ok)}, no-tick-file: {missing}, other errors: {errs}')
    if not ok:
        raise SystemExit('nothing processed')
    out_df = pd.concat(ok, ignore_index=True)
    out_path = out / f'layer3_tick_features_{sd.date()}.parquet'
    out_df.to_parquet(out_path, index=False)
    print(f'wrote {out_path} ({len(out_df)} event-rows, {out_df["code"].nunique()} codes)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
