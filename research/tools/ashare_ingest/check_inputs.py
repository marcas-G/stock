#!/usr/bin/env python3
"""三资产存在性 + 列检查（数据侧入口自检）。

路径经 `datapaths.py` → `factorlab.core.factio.paths` 单点（R19：原脚本从 config.yaml
读 `paths.*`，那是第二份路径真相）。
"""
from __future__ import annotations

import argparse

import pandas as pd

import datapaths
from contracts import DAILY_REQUIRED, FUNDAMENTALS_REQUIRED, INDEX_DAILY_REQUIRED


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()   # --help 即可
    checks = []
    for key, path, required in [
            ('daily_fact', datapaths.daily_fact(), DAILY_REQUIRED),
            ('benchmark_daily_pre', datapaths.index_daily(), INDEX_DAILY_REQUIRED),
            ('fundamentals_pti', datapaths.fundamentals(), FUNDAMENTALS_REQUIRED)]:
        ok = path.exists()
        missing: list[str] = []
        if ok:
            df = pd.read_parquet(path).head(20)
            missing = sorted(required - set(df.columns))
            ok = not missing
        checks.append((key, str(path), ok, missing))
    print('INPUT CHECK')
    for x in checks:
        print(x)
    print('IMPORTANT: daily_fact must represent a complete market trading calendar window; '
          'suspended rows may be absent because windowing is market-date based.')
    print('IMPORTANT: adj_factor is the causal cumulative adjustment factor; pre/hf prices '
          'are derived at read time by the pool-stage reader (universe_stages.data.daily).')
    return 0 if all(c[2] for c in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
