#!/usr/bin/env python3
"""第一层 parity：本地 Top300 vs golden（A9）逐项对比（Jaccard / rank / 数值差）。

R20 收编自 ashare_alpha3 的 `11_validate_layer1_parity.py`（逻辑逐行保留）。
golden 是**上游 jqdata 口径的一次性交付**（生成链路未留存，见 pending #9），
只读对照。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import universe_paths  # noqa: E402
from validation.parity import compare_top300  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default=None)
    p.add_argument('--scan-date', required=True)
    a = p.parse_args()
    cfg = universe_paths.load_config(a.config)
    local = pd.read_parquet(universe_paths.out_dir(cfg, 'universes') / 'v4_top300_local.parquet')
    golden = pd.read_parquet(universe_paths.golden_universe())
    print(json.dumps(compare_top300(local, golden, a.scan_date), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
