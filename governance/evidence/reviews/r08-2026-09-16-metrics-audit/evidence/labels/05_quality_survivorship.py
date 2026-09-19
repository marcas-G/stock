#!/usr/bin/env python
"""R08 标签核对 05：数据质量字段重算 + 死信号行为 + 幸存者偏差线索。

A. 抽 2 个因子（low_vol_20d / vol_run_energy_symrun）重算：
   - signal_null_ratio = panel.signal null / panel 行数（summary 口径）；
   - coverage = weekly 过滤前口径（signal/target 非 null 且有限）/ weekly 行数。
B. 死信号/死列行为事实（代码路径 + 存量 artifact + R08 实证 CLI run）。
C. forward 标签 null 的分布（窗口末端 / 退市尾部 / 停牌），给数字。
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

RUNS = Path("/data/students/gaolei/stock/runs/platform")
OUT = Path("/data/students/gaolei/stock/governance/evidence/reviews/"
           "r08-2026-09-16-metrics-audit/evidence/labels")
OUT.mkdir(parents=True, exist_ok=True)

FACTORS = ["low_vol_20d", "vol_run_energy_symrun"]
DEAD_FACTORS = ["value_bp", "small_cap", "cap_real2",
                "crash_bottom_leader_adv20", "r07_idx_circmv_probe"]


def quality_check(name: str) -> dict:
    d = json.loads((RUNS / name / "summary.json").read_text())
    panel = pl.read_parquet(RUNS / name / "panel.parquet")
    weekly = pl.read_parquet(RUNS / name / "weekly.parquet")
    ev = d["evaluation"]
    target = ev["target"]
    rec: dict = {"factor": name, "target": target}

    null_ratio = panel["signal"].null_count() / panel.height
    rec["signal_null_ratio"] = {
        "summary": d["signal_null_ratio"],
        "recomputed": round(null_ratio, 4),
        "recomputed_raw": null_ratio,
        "match": abs(round(null_ratio, 4) - d["signal_null_ratio"]) < 1e-9,
        "panel_rows": panel.height,
        "signal_null_rows": panel["signal"].null_count(),
    }

    mask = weekly["signal"].is_not_null() & weekly["signal"].is_finite()
    mask = mask & weekly[target].is_not_null() & weekly[target].is_finite()
    valid = int(mask.sum())
    cov = {
        "weekly_rows": weekly.height,
        "valid_rows": valid,
        "pct_valid": round(valid / weekly.height, 4) if weekly.height else 0.0,
    }
    sum_cov = ev["coverage"]
    cov["summary"] = sum_cov
    cov["match"] = (cov["pct_valid"] == sum_cov["pct_valid"]
                    and cov["weekly_rows"] == sum_cov["total_rows"]
                    and valid == sum_cov["valid_rows"])
    rec["coverage"] = cov
    rec["summary_keys"] = sorted(ev.keys())
    rec["dead_signal_field_present"] = any(
        "dead" in k.lower() for k in list(ev.keys()) + list(d.keys()))
    return rec


def survivorship() -> dict:
    d = json.loads((RUNS / "low_vol_20d" / "summary.json").read_text())
    panel = pl.read_parquet(RUNS / "low_vol_20d" / "panel.parquet")
    dates = panel["date"].unique().sort()
    last = dates[-1]
    rec: dict = {
        "panel_rows": panel.height,
        "date_max": str(last),
        "fwd5_null_ratio_overall": panel["forward_return_5d"].null_count() / panel.height,
        "fwd20_null_ratio_overall": panel["forward_return_20d"].null_count() / panel.height,
    }
    # per-date null ratio, last 25 dates
    by_date = (panel.group_by("date").agg(
        n=pl.len(),
        fwd5_null=pl.col("forward_return_5d").is_null().sum(),
        fwd20_null=pl.col("forward_return_20d").is_null().sum())
        .sort("date"))
    tail = by_date.tail(25).with_columns(
        (pl.col("fwd5_null") / pl.col("n")).alias("fwd5_null_ratio"),
        (pl.col("fwd20_null") / pl.col("n")).alias("fwd20_null_ratio"))
    rec["last_25_dates"] = [
        {"date": str(r["date"]), "n": r["n"],
         "fwd5_null_ratio": round(r["fwd5_null_ratio"], 4),
         "fwd20_null_ratio": round(r["fwd20_null_ratio"], 4)}
        for r in tail.iter_rows(named=True)]
    head = by_date.head(10).with_columns(
        (pl.col("fwd5_null") / pl.col("n")).alias("fwd5_null_ratio"),
        (pl.col("fwd20_null") / pl.col("n")).alias("fwd20_null_ratio"))
    rec["first_10_dates"] = [
        {"date": str(r["date"]), "n": r["n"],
         "fwd5_null_ratio": round(r["fwd5_null_ratio"], 4),
         "fwd20_null_ratio": round(r["fwd20_null_ratio"], 4)}
        for r in head.iter_rows(named=True)]

    # end-of-window concentration
    cut20 = dates[-21] if len(dates) > 21 else dates[0]
    end_rows = panel.filter(pl.col("date") >= cut20)
    mid_rows = panel.filter(pl.col("date") < cut20)
    total_null20 = panel["forward_return_20d"].null_count()
    rec["fwd20_null_window_end"] = {
        "cut_date": str(cut20),
        "nulls_in_last_20_dates": int(end_rows["forward_return_20d"].null_count()),
        "rows_in_last_20_dates": end_rows.height,
        "nulls_before": int(mid_rows["forward_return_20d"].null_count()),
        "share_of_all_nulls_at_end": (
            end_rows["forward_return_20d"].null_count() / total_null20
            if total_null20 else None),
    }
    cut5 = dates[-6] if len(dates) > 6 else dates[0]
    end5 = panel.filter(pl.col("date") >= cut5)
    rec["fwd5_null_window_end"] = {
        "cut_date": str(cut5),
        "nulls_in_last_5_dates": int(end5["forward_return_5d"].null_count()),
        "rows_in_last_5_dates": end5.height,
    }

    # per-code nulls: delisted tail vs suspension
    per_code = (panel.group_by("code").agg(
        rows=pl.len(),
        first=pl.col("date").min(), last=pl.col("date").max(),
        sn=pl.col("signal").null_count(),
        null20=pl.col("forward_return_20d").is_null().sum(),
        null5=pl.col("forward_return_5d").is_null().sum())
        .with_columns((pl.col("null20") / pl.col("rows")).alias("null20_ratio")))
    rec["codes_total"] = per_code.height
    rec["codes_left_before_end"] = int(
        (per_code["last"] < dates[-2]).sum())
    left = per_code.filter(pl.col("last") < dates[-2])
    still = per_code.filter(pl.col("last") >= dates[-2])
    rec["nulls_from_codes_left_before_end"] = int(left["null20"].sum())
    rec["nulls_from_codes_present_at_end"] = int(still["null20"].sum())
    rec["codes_with_any_fwd20_null"] = int((per_code["null20"] > 0).sum())
    rec["top10_codes_by_fwd20_null"] = [
        {"code": r["code"], "rows": r["rows"], "null20": r["null20"],
         "null20_ratio": round(r["null20_ratio"], 4),
         "first": str(r["first"]), "last": str(r["last"])}
        for r in per_code.sort("null20", descending=True).head(10).iter_rows(named=True)]
    # codes present at end with >30% null20 (suspension-driven candidates)
    susp = still.filter(pl.col("null20_ratio") > 0.3).sort("null20", descending=True)
    rec["codes_present_at_end_with_gt30pct_fwd20_null"] = susp.height
    rec["top5_suspension_like"] = [
        {"code": r["code"], "rows": r["rows"], "null20": r["null20"],
         "null20_ratio": round(r["null20_ratio"], 4),
         "null5": r["null5"], "last": str(r["last"])}
        for r in susp.head(5).iter_rows(named=True)]

    # mid-sample nulls (dates < cut20) = not right-censoring: suspension/delist driven
    mid = panel.filter(pl.col("date") < cut20)
    mid_per_code = (mid.group_by("code").agg(
        rows=pl.len(), mid_null20=pl.col("forward_return_20d").is_null().sum())
        .with_columns((pl.col("mid_null20") / pl.col("rows")).alias("mid_null_ratio")))
    mid_codes = (mid_per_code.filter(pl.col("mid_null20") > 0)
                 .join(per_code.select(["code",
                                        pl.col("last").alias("last_full"),
                                        pl.col("sn").alias("signal_null_full"),
                                        pl.col("rows").alias("rows_full")]),
                       on="code", how="left")
                 .sort("mid_null20", descending=True))
    rec["mid_sample_fwd20_nulls_total"] = int(mid_codes["mid_null20"].sum())
    rec["mid_sample_codes_with_nulls"] = mid_codes.height
    rec["top10_codes_mid_sample_fwd20_null"] = [
        {"code": r["code"], "rows_before_cut": r["rows"],
         "mid_null20": r["mid_null20"],
         "mid_null_ratio": round(r["mid_null_ratio"], 4),
         "signal_null_full": r["signal_null_full"],
         "rows_full": r["rows_full"],
         "last_full": str(r["last_full"])}
        for r in mid_codes.head(10).iter_rows(named=True)]
    rec["mid_null_nullcodes_left_before_end"] = int(
        (mid_codes["last_full"] < dates[-2]).sum())
    rec["mid_null_nullcodes_present_at_end"] = int(
        (mid_codes["last_full"] >= dates[-2]).sum())
    # split mid nulls: dead-code adj holes vs delisted-tail vs live suspension
    dead_set = per_code.filter(pl.col("sn") == pl.col("rows"))["code"]
    mc = mid_codes.with_columns(pl.col("code").is_in(dead_set).alias("is_dead"))
    rec["mid_nulls_from_dead_codes"] = int(mc.filter("is_dead")["mid_null20"].sum())
    rec["mid_nulls_from_delisted_nondead"] = int(
        mc.filter(~pl.col("is_dead") & (pl.col("last_full") < dates[-2]))["mid_null20"].sum())
    live = mc.filter(~pl.col("is_dead") & (pl.col("last_full") >= dates[-2]))
    rec["mid_nulls_from_live_suspension_like"] = int(live["mid_null20"].sum())
    rec["mid_live_codes_count"] = live.height
    rec["top10_live_codes_mid_fwd20_null"] = [
        {"code": r["code"], "rows_before_cut": r["rows"],
         "mid_null20": r["mid_null20"],
         "mid_null_ratio": round(r["mid_null_ratio"], 4)}
        for r in live.sort("mid_null20", descending=True).head(10).iter_rows(named=True)]

    # "dead" codes: entire panel history signal null (adj_factor hole => no signal/label)
    dead = per_code.filter(pl.col("sn") == pl.col("rows")).sort(
        "rows", descending=True)
    rec["dead_codes_signal_all_null"] = {
        "n_codes": dead.height,
        "rows": int(dead["rows"].sum()),
        "share_panel_rows": dead["rows"].sum() / panel.height,
        "last_year_hist": {str(k): int(v) for k, v in
                           dead.with_columns(pl.col("last").dt.year().alias("y"))
                           .group_by("y").len().sort("y").iter_rows()},
        "codes_present_at_end": int((dead["last"] >= dates[-2]).sum()),
        "codes_present_at_end_list": dead.filter(pl.col("last") >= dates[-2])["code"].to_list(),
    }
    # CH adj_factor coverage for dead codes (single bounded query, <=1500 codes)
    try:
        import sys as _sys
        _sys.path.insert(0, "/data/students/gaolei/stock/platform/src")
        from factorlab.adapters.ch_read import in_clause, query_df
        codes = dead["code"].to_list()
        if codes:
            ph, params = in_clause(codes)
            q = query_df(
                f"""SELECT ts_code, count() AS adj_rows, count(adj_factor) AS adj_nn
                    FROM factorlab.adj_factor WHERE ts_code IN ({ph})
                    GROUP BY ts_code LIMIT 5000""", params)
            rec["dead_codes_adj_factor_all_null"] = int((q["adj_nn"] == 0).sum())
            rec["dead_codes_adj_factor_nonnull"] = int((q["adj_nn"] > 0).sum())
    except Exception as exc:  # noqa: BLE001
        rec["dead_codes_adj_coverage_error"] = repr(exc)

    per_code.write_csv(OUT / "05_survivorship_per_code.csv")
    return rec


def main() -> int:
    report = {
        "quality": [quality_check(f) for f in FACTORS],
        "dead_signal_existing_artifacts": [],
        "survivorship_low_vol_20d": survivorship(),
    }
    for f in DEAD_FACTORS:
        p = RUNS / f / "summary.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        ev = d.get("evaluation") or {}
        report["dead_signal_existing_artifacts"].append({
            "factor": f,
            "signal_null_ratio": d.get("signal_null_ratio"),
            "coverage": ev.get("coverage"),
            "n_weeks": ev.get("n_weeks"),
            "ic_mean": ev.get("ic", {}).get("mean"),
            "dead_signal_field": any("dead" in k.lower() for k in ev.keys()),
        })
    report["r07_d5_gitignore_status"] = (
        "evidence/ 下 data 目录已改名（R07 证据为 data-audit/；R29 为 data-t4/），"
        "git check-ignore 未命中（见 05_r07_d5_check.txt）")
    report["r07_d6_dead_signal_status"] = (
        "仍开放：run.py 仅写 signal_null_ratio，无 dead_signal 字段/非零退出；"
        "R08 实证 run（pb 全空）exit 0 且 n_weeks=0 ic=nan（05_dead_signal_cli.out.txt）；"
        "v2 计划 Task 2 未实现（plan 未勾选）")
    (OUT / "05_quality_survivorship.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
