#!/usr/bin/env python
"""R08 抽验：用 scipy.stats.spearmanr/pearsonr 对照 recompute_metrics.py 的逐周值。

在独立解释器（anaconda3, scipy 1.10.0）运行，与平台 venv 无关：
  /data/students/gaolei/anaconda3/bin/python scipy_spotcheck.py

- 样本：intraday_high_time 全周；另两个因子按 ok 周等距抽 12 周。
- 读 weekly.parquet 只取 date/code/signal/forward_return_5d，有效性过滤与复算脚本一致：
  signal/fwd 非 null 且有限。
- 逐周 scipy.spearmanr(signal, fwd)（默认 average 并列语义）与
  scipy.pearsonr 对照 recompute_metrics.py 落盘的 weekly_ic_<factor>.json。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

RUNS = Path("/data/students/gaolei/stock/runs/platform")
OUT = Path("/data/students/gaolei/stock/governance/evidence/reviews/"
           "r08-2026-09-16-metrics-audit/evidence/recompute")
FACTORS = ["low_vol_20d", "max_effect_20d_high", "intraday_high_time"]
TARGET = "forward_return_5d"
TOL = 1e-12


def sample_dates(per_week: list[dict], factor: str) -> list[str]:
    ok = [r["date"] for r in per_week if r["ic"] is not None]
    if factor == "intraday_high_time" or len(ok) <= 12:
        return ok
    idx = sorted({round(i * (len(ok) - 1) / 11) for i in range(12)})
    return [ok[i] for i in idx]


def main() -> None:
    bad = 0
    print("=" * 92)
    print("R08 scipy 抽验：scipy.stats.spearmanr / pearsonr vs 独立复算逐周值")
    print(f"scipy {stats.__name__} / numpy {np.__version__} / pandas {pd.__version__}")
    print("=" * 92)
    for factor in FACTORS:
        per_week = json.loads((OUT / f"weekly_ic_{factor}.json").read_text())
        want = {r["date"]: r for r in per_week}
        dates = sample_dates(per_week, factor)
        df = pd.read_parquet(RUNS / factor / "weekly.parquet",
                             columns=["date", "code", "signal", TARGET])
        df["date"] = df["date"].astype(str)
        df = df[["date", "signal", TARGET]].dropna()
        df = df[np.isfinite(df["signal"]) & np.isfinite(df[TARGET])]
        print(f"\n### {factor}: sampled {len(dates)} / ok weeks "
              f"{sum(1 for r in per_week if r['ic'] is not None)}")
        print(f"{'date':<12} {'n':>6} {'scipy_spearman':>18} {'mine_spearman':>18} "
              f"{'absdiff':>10} {'scipy_pearson':>18} {'mine_pearson':>18} {'absdiff':>10}")
        for d in dates:
            sub = df[df["date"] == d]
            x = sub["signal"].to_numpy(dtype=float)
            y = sub[TARGET].to_numpy(dtype=float)
            sp = stats.spearmanr(x, y).statistic
            pe = stats.pearsonr(x, y).statistic
            mine = want[d]
            dsp = abs(sp - mine["ic"]) if mine["ic"] is not None else float("nan")
            dpe = abs(pe - mine["pearson"]) if mine["pearson"] is not None else float("nan")
            flag = ""
            if not (dsp <= TOL and dpe <= TOL):
                flag = "  <-- MISMATCH"
                bad += 1
            print(f"{d:<12} {len(x):>6} {sp:>18.12f} {mine['ic']:>18.12f} {dsp:>10.2e} "
                  f"{pe:>18.12f} {mine['pearson']:>18.12f} {dpe:>10.2e}{flag}")
    print("\n" + "=" * 92)
    print(f"scipy spotcheck mismatches (> {TOL:g}): {bad}")
    print("=" * 92)
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
