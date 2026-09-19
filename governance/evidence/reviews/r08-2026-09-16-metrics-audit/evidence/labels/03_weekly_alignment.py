#!/usr/bin/env python
"""R08 标签核对 03：align_weekly 周对齐独立验证。

从 panel.parquet（逐日）独立构造「每个 ISO 周(iso_year, week)的全局最后交易日」，
与 weekly.parquet 的 date 集合逐一对拍：
  - weekly date 集合 == 全局周最后交易日集合；
  - weekly 中每个 ISO 周恰一个 date（分布 min/max/n_distinct）；
  - 每 (code, ISO 周) 恰一行；
  - 每行的 signal/target 值 == 该 code 在本周内的最后一条观测（`_code_week_end`）；
  - relabel 跨度：code 周最后观测日 → 全局周末日 的距离分布。

同样检查分钟链产物（intraday_* 有 weekly.parquet 的 run；无停牌骨架补行——
R05-I4 修复的原始场景）。Read-only。
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

RUNS = Path("/data/students/gaolei/stock/runs/platform")
OUT = Path("/data/students/gaolei/stock/governance/evidence/reviews/"
           "r08-2026-09-16-metrics-audit/evidence/labels")
OUT.mkdir(parents=True, exist_ok=True)


def week_keys(df: pl.DataFrame, date_col: str = "date") -> pl.DataFrame:
    return df.with_columns(
        pl.col(date_col).dt.iso_year().alias("_iso_year"),
        pl.col(date_col).dt.week().alias("_week"))


def check_run(name: str, run_dir: Path) -> dict:
    panel = pl.read_parquet(run_dir / "panel.parquet")
    weekly = pl.read_parquet(run_dir / "weekly.parquet")
    rec: dict = {"run": name, "panel_rows": panel.height, "weekly_rows": weekly.height}

    pw = week_keys(panel)
    # global last trade date per ISO week (independent construction)
    expect = (pw.group_by(["_iso_year", "_week"])
              .agg(global_week_end=pl.col("date").max())
              .sort(["_iso_year", "_week"]))
    ww = week_keys(weekly)
    actual = (ww.group_by(["_iso_year", "_week"])
              .agg(dates=pl.col("date").unique().sort(), n_dates=pl.col("date").n_unique(),
                   n_rows=pl.len())
              .sort(["_iso_year", "_week"]))

    cmp = expect.join(actual, on=["_iso_year", "_week"], how="full", coalesce=True)
    dates_set_equal = (
        set(weekly["date"].unique().to_list()) == set(expect["global_week_end"].to_list()))
    rec["n_iso_weeks_panel"] = expect.height
    rec["n_iso_weeks_weekly"] = actual.height
    rec["dates_set_equal"] = bool(dates_set_equal)
    rec["weeks_with_n_dates_ne_1"] = int((cmp["n_dates"].fill_null(0) != 1).sum())
    rec["weeks_with_n_rows_lt_1"] = int((cmp["n_rows"].fill_null(0) < 1).sum())
    rec["n_dates_per_week_distribution"] = {
        str(k): int(v) for k, v in
        actual.group_by("n_dates").len().sort("n_dates").iter_rows()
    }
    # weekly.date must equal global week end for its ISO week
    wrong_weekend = cmp.filter(
        (pl.col("global_week_end").is_not_null()) & (pl.col("n_dates") == 1)
        & (~pl.col("dates").list.contains(pl.col("global_week_end"))))
    rec["weeks_weekly_date_ne_global_week_end"] = int(wrong_weekend.height)

    # per (code, week) exactly one weekly row
    per_code_week = (ww.group_by(["code", "_iso_year", "_week"]).len()
                     .filter(pl.col("len") != 1))
    rec["code_weeks_with_ne1_weekly_rows"] = int(per_code_week.height)
    rec["n_code_week_groups_panel"] = int(pw.select(
        ["code", "_iso_year", "_week"]).n_unique())

    # value check: weekly row == code's last panel observation within the week,
    # at the same signal/forward values (relabel must not alter values)
    target_cols = [c for c in ("signal", "forward_return_5d", "forward_return_20d", "close")
                   if c in weekly.columns]
    last_obs = (pw.with_columns(
        pl.col("date").max().over(["code", "_iso_year", "_week"]).alias("_code_week_end"))
        .filter(pl.col("date") == pl.col("_code_week_end")))
    joined = ww.select(["date", "code", "_iso_year", "_week", *target_cols]).join(
        last_obs.select(["date", "code", "_iso_year", "_week",
                         *[pl.col(c).alias(f"{c}_obs") for c in target_cols]]),
        on=["date", "code", "_iso_year", "_week"], how="left")
    value_mismatch = {}
    for c in target_cols:
        a, b = joined[c], joined[f"{c}_obs"]
        both = a.is_not_null() & b.is_not_null()
        d = (a.filter(both).cast(pl.Float64) - b.filter(both).cast(pl.Float64)).abs()
        value_mismatch[c] = {
            "missing_last_obs_row": int(joined[f"{c}_obs"].is_null().sum()),
            "null_mismatch": int((a.is_null() != b.is_null()).sum()),
            "max_abs_diff": float(d.max()) if d.len() else None,
        }
    rec["weekly_vs_panel_last_obs"] = value_mismatch

    # relabel distance: original code-week-end -> global week end (trade rows apart)
    dist = (joined.filter(pl.col(f"{target_cols[0]}_obs").is_not_null())
            .with_columns(
                pl.col("date").alias("relabeled_date")))
    # original date is _code_week_end; attach via last_obs keyed by code+week
    orig = last_obs.select(["code", "_iso_year", "_week", "date"]).rename({"date": "orig_date"})
    dist = (ww.select(["date", "code", "_iso_year", "_week"])
            .join(orig, on=["code", "_iso_year", "_week"], how="left")
            .with_columns((pl.col("date") - pl.col("orig_date")).dt.total_days()
                          .alias("relabel_days")))
    rec["relabel_distance_days"] = {
        "n": int(dist.height),
        "n_zero": int((dist["relabel_days"] == 0).sum()),
        "n_gt0": int((dist["relabel_days"] > 0).sum()),
        "max": int(dist["relabel_days"].max()) if dist.height else None,
        "dist": {str(k): int(v) for k, v in
                 dist.group_by("relabel_days").len().sort("relabel_days").iter_rows()},
    }
    return rec


def main() -> int:
    runs = [
        ("low_vol_20d", RUNS / "low_vol_20d"),
        ("momentum_20d", RUNS / "momentum_20d"),
    ]
    for d in sorted(RUNS.glob("intraday_*")):
        if (d / "weekly.parquet").exists():
            runs.append((d.name, d))

    report = {"runs": []}
    for name, d in runs:
        if not (d / "panel.parquet").exists():
            continue
        report["runs"].append(check_run(name, d))
    (OUT / "03_weekly_alignment.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
