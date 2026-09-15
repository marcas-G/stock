#!/usr/bin/env python3
"""尾部捕获审计：MFE 标签（60/120 日）+ Precision@K / Recall@K / Lift@K。

R20 收编自 ashare_alpha3 的 `20_tail_capture_audit.py`（逻辑逐行保留）。
默认以 golden 股池排序为输入（`--ranked` 可换成自有产物）。
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
from readers.daily import DailyStore  # noqa: E402
from tail.audit import audit_ranked_pool  # noqa: E402
from tail.labels import future_mfe_labels  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default=None)
    p.add_argument('--ranked', default=None, help='排序输入（默认 golden 股池）')
    a = p.parse_args()
    cfg = universe_paths.load_config(a.config)
    out = universe_paths.out_dir(cfg, 'validation')

    ranked = pd.read_parquet(a.ranked or universe_paths.golden_universe())
    daily = DailyStore(universe_paths.daily_fact()).read()
    labels = future_mfe_labels(daily, ranked, horizons=(60, 120))
    audit = audit_ranked_pool(ranked, labels, 'tail_120_50', ks=(50, 100, 200, 300))
    labels.to_parquet(out / 'tail_labels.parquet', index=False)
    audit.to_parquet(out / 'tail_capture_audit.parquet', index=False)
    print(audit.groupby('k')[['precision', 'recall', 'lift']].mean())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
