#!/usr/bin/env python3
"""第三层：tick 微观结构特征（仅 20260817 全市场逐笔成交可用）。

R20 收编自 ashare_alpha3 的 `40_run_layer3_tick.py`（逻辑逐行保留）：改动只有
路径经 `universe_paths`、读取层改经 `readers.tick`、输出默认落
`<工具>/outputs/research`。

流程：layer2 特征（sas_features_*.parquet）→ 候选前 k → 每股 shock 事件
（sas_events_*.parquet 的 datetime）→ WindTickStore 读逐笔成交 →
事件前/后窗口特征（signed flow、大单占比、反转）→ 输出。

R01-TOOLS-I8 纪律：只处理请求日（scan-date = tick 数据日）的事件，非当日事件跳过
并记入 `layer3_tick_issues_<date>.csv`；特征异常显式写 `feature_error` 列，不静默吞。
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
    """读单股 tick 并逐事件算特征；返回 (code, df, err, issues)。

    R01-TOOLS-I8 纪律：
    - 事件日期 ≠ 请求日 → 不产出（跳过并把原因记进 issues），绝不拿别日 tick 编窗口；
    - 特征异常 → `feature_error` 显式落在结果行 + issues 记录，不得静默 `r={}`。
    """
    code, trade_date, events, tick_root = args
    try:
        ts = WindTickStore(tick_root)
        df = ts.read_trade_csv(code, trade_date)
    except FileNotFoundError:
        return code, None, 'no tick file', []
    if df.empty:
        return code, None, 'empty ticks', []
    rows, issues = [], []
    for ev in events:
        try:
            ev_ts = pd.Timestamp(ev)
        except (ValueError, TypeError):
            issues.append({'code': code, 'event_time': str(ev),
                           'kind': 'invalid_event_time', 'detail': ''})
            continue
        if pd.isna(ev_ts):
            issues.append({'code': code, 'event_time': str(ev),
                           'kind': 'invalid_event_time', 'detail': ''})
            continue
        if ev_ts.strftime('%Y%m%d') != trade_date:
            issues.append({'code': code, 'event_time': ev_ts,
                           'kind': 'skipped_not_on_trade_date',
                           'detail': f'trade_date={trade_date}'})
            continue
        try:
            r = event_reversal_features(df, ev_ts, pre_seconds=PRE_SEC,
                                        post_seconds=POST_SEC)
        except Exception as e:
            detail = f'{type(e).__name__}: {e}'
            r = {'feature_error': detail}
            issues.append({'code': code, 'event_time': ev_ts,
                           'kind': 'feature_error', 'detail': detail})
        row = {'code': code, 'event_time': ev_ts}
        row.update(r)
        rows.append(row)
    return code, pd.DataFrame(rows), None, issues


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default=None, help='工具配置（默认 <工具>/config.yaml）')
    p.add_argument('--scan-date', required=True)
    p.add_argument('--top-k', type=int)
    p.add_argument('--workers', type=int, default=16)
    a = p.parse_args()
    cfg = universe_paths.load_config(a.config)
    sd = pd.Timestamp(a.scan_date).normalize()
    trade_date = sd.strftime('%Y%m%d')
    try:  # R01-TOOLS-I9：tick 源缺失 fail fast（含解包指引）
        universe_paths.preflight_layer3(trade_date)
    except universe_paths.MissingInput as e:
        raise SystemExit(f'universe_stages layer3 前置数据缺失：\n{e}')
    out = universe_paths.out_dir(cfg, 'research')
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
    tick_root = universe_paths.ticks_root(trade_date)

    per_code = {}
    for code in cands['code']:
        ev = events[(events['code'] == code)]['datetime'].tolist()
        if ev:
            per_code[code] = ev
    print(f'candidates: {len(cands)}, codes with events: {len(per_code)}')

    if not per_code:
        raise SystemExit('no events; nothing to do')

    tasks = [(code, trade_date, evs, tick_root) for code, evs in per_code.items()]
    ok, missing, errs, issues = [], 0, 0, []
    with mp.Pool(a.workers) as pool:
        for code, df, err, ev_issues in pool.imap_unordered(_worker, tasks):
            issues.extend(ev_issues)
            if err or df is None:
                if err and 'no tick file' in str(err):
                    missing += 1
                else:
                    errs += 1
                continue
            if df.empty:
                continue
            ok.append(df)
    issues_path = out / f'layer3_tick_issues_{sd.date()}.csv'
    if issues:
        pd.DataFrame(issues, columns=['code', 'event_time', 'kind', 'detail']).to_csv(
            issues_path, index=False)
        print(f'event issues: {len(issues)} -> {issues_path}')
    print(f'processed: {len(ok)}, no-tick-file: {missing}, other errors: {errs}')
    if not ok:
        detail = f'event issues={len(issues)}' + (f' (see {issues_path})' if issues else '')
        raise SystemExit(
            f'nothing processed for {trade_date}: {detail}, '
            f'no-tick-file={missing}, other errors={errs}')
    out_df = pd.concat(ok, ignore_index=True)
    out_path = out / f'layer3_tick_features_{sd.date()}.parquet'
    out_df.to_parquet(out_path, index=False)
    print(f'wrote {out_path} ({len(out_df)} event-rows, {out_df["code"].nunique()} codes)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
