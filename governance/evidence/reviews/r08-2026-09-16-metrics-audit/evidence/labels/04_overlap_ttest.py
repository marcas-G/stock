#!/usr/bin/env python
"""R08 标签核对 04：频率口径 — 简单 t vs Newey-West(lag=4) t（20d 重叠）。

数据：runs/platform/low_vol_20d/weekly.parquet（934,236 行，已 align_weekly）。
独立复算周频 RankIC（Spearman，逐周，signal/target 非 null 且有限，有效股票数
>=3，退化周 null）→ 简单 t = mean/(std/sqrt(n)) 与 NW 修正 t。

NW（Bartlett，L 阶）：x̄ = mean；γ_l = (1/n)Σ_{t=l+1..n}(x_t−x̄)(x_{t−l}−x̄)；
se² = (1/n)(γ0 + 2Σ_{l=1..L}(1−l/(L+1))γ_l)；t_nw = x̄/se。

先用 forward_return_5d 对 summary 的 ic.mean/std/t_stat 校准复算口径，
再对 forward_return_20d 给两口径数值与虚高倍数；记录 summary 是否有重叠校正字段。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import polars as pl

RUN = Path("/data/students/gaolei/stock/runs/platform/low_vol_20d")
OUT = Path("/data/students/gaolei/stock/governance/evidence/reviews/"
           "r08-2026-09-16-metrics-audit/evidence/labels")
OUT.mkdir(parents=True, exist_ok=True)

MIN_STOCKS = 3


def weekly_ic_series(panel: pl.DataFrame, target: str) -> pl.DataFrame:
    valid = panel.drop_nulls(["signal", target]).filter(
        pl.col("signal").is_finite() & pl.col(target).is_finite())
    stats = valid.group_by("date").agg(
        ic=pl.corr(pl.col("signal"), pl.col(target), method="spearman"),
        n_valid=pl.len())
    return (panel.select("date").unique().join(stats, on="date", how="left")
            .with_columns(
                pl.when((pl.col("n_valid").fill_null(0) < MIN_STOCKS)
                        | pl.col("ic").is_null() | ~pl.col("ic").is_finite())
                .then(None).otherwise(pl.col("ic")).alias("ic"))
            .sort("date"))


def stats(x: np.ndarray, lags: int) -> dict:
    n = len(x)
    mean = float(np.mean(x))
    std_pop = float(np.std(x, ddof=0))
    std_sam = float(np.std(x, ddof=1))
    gamma0 = float(np.mean((x - mean) ** 2))
    var_nw = gamma0
    gammas = {}
    for l in range(1, lags + 1):
        gl = float(np.mean((x[l:] - mean) * (x[:-l] - mean)))
        gammas[l] = gl
        var_nw += 2.0 * (1.0 - l / (lags + 1.0)) * gl
    se_nw = math.sqrt(var_nw / n) if var_nw > 0 else float("nan")
    return {
        "n": n, "mean": mean,
        "std_ddof0": std_pop, "std_ddof1": std_sam,
        "t_simple_ddof0": mean / (std_pop / math.sqrt(n)) if std_pop > 0 else float("nan"),
        "t_simple_ddof1": mean / (std_sam / math.sqrt(n)) if std_sam > 0 else float("nan"),
        "gamma": gammas,
        "var_nw": var_nw,
        "se_nw": se_nw,
        "t_nw": mean / se_nw if se_nw and not math.isnan(se_nw) and se_nw > 0 else float("nan"),
    }


def main() -> int:
    panel = pl.read_parquet(RUN / "weekly.parquet")
    summary = json.loads((RUN / "summary.json").read_text())
    report: dict = {
        "weekly_rows": panel.height,
        "summary_target": summary["evaluation"]["target"],
        "summary_ic_existing": summary["evaluation"]["ic"],
        "overlap_correction_fields_in_summary": sorted(
            k for k in summary["evaluation"].keys()
            if any(s in k.lower() for s in ("nw", "overlap", "newey", "adj"))),
        "overlap_correction_fields_in_ic": sorted(
            k for k in summary["evaluation"]["ic"].keys()
            if any(s in k.lower() for s in ("nw", "overlap", "newey", "adj"))),
    }

    for target, lags in (("forward_return_5d", (0, 1)), ("forward_return_20d", (0, 4))):
        ic = weekly_ic_series(panel, target)
        x = ic["ic"].drop_nulls().to_numpy()
        full = stats(x, lags=4)
        rec = {
            "n_ic_weeks": int(len(x)),
            "n_weeks_total": ic.height,
            "mean": full["mean"],
            "std_ddof0": full["std_ddof0"],
            "std_ddof1": full["std_ddof1"],
            "t_simple_ddof0": full["t_simple_ddof0"],
            "t_simple_ddof1": full["t_simple_ddof1"],
        }
        for L in lags:
            s = stats(x, lags=L)
            rec[f"nw_lag{L}"] = {
                "gamma_l": {str(k): v for k, v in s["gamma"].items()},
                "se_nw": s["se_nw"],
                "t_nw": s["t_nw"],
            }
        if target == "forward_return_5d":
            rec["summary_ic"] = summary["evaluation"]["ic"]
        else:
            tnw = stats(x, lags=4)["t_nw"]
            rec["overlap_inflation_t_simple_over_t_nw_ddof0"] = \
                full["t_simple_ddof0"] / tnw if tnw else None
            rec["overlap_inflation_t_simple_over_t_nw_ddof1"] = \
                full["t_simple_ddof1"] / tnw if tnw else None
        report[target] = rec

    # simple t for 20d using same ddof as kernel (calibrated by 5d match)
    t5_summary = summary["evaluation"]["ic"]["t_stat"]
    s5_0 = report["forward_return_5d"]["t_simple_ddof0"]
    s5_1 = report["forward_return_5d"]["t_simple_ddof1"]
    report["calibration_5d"] = {
        "summary_t_stat": t5_summary,
        "recomputed_t_ddof0": s5_0,
        "recomputed_t_ddof1": s5_1,
        "abs_diff_ddof0": abs(t5_summary - s5_0),
        "abs_diff_ddof1": abs(t5_summary - s5_1),
        "summary_std": summary["evaluation"]["ic"]["std"],
        "recomputed_std_ddof0": report["forward_return_5d"]["std_ddof0"],
        "recomputed_std_ddof1": report["forward_return_5d"]["std_ddof1"],
    }

    (OUT / "04_overlap_ttest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
