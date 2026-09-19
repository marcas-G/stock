#!/usr/bin/env python
"""R08 标签核对 01：independent forward_return recompute vs panel.parquet.

Independent path: CH factorlab.daily ⨝ adj_factor → trade_cal skeleton →
hfq = close*adj_factor → shift(-h)/hfq − 1 (h=5,20). Two variants:
  (a) f32 ops (mirror platform load_daily(float32=True) arithmetic/storage)
  (b) f64 ops (formula-truth independent of storage)
Compare vs runs/platform/low_vol_20d/panel.parquet forward_return_5d/20d on
30 codes x 2025-03 rows (all rows in window) + 100-row random sample dump.

Read-only. CH queries are single-window + LIMIT.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, "/data/students/gaolei/stock/platform/src")
from factorlab.adapters.ch_read import in_clause, query_df, query_rows  # noqa: E402

RUN = Path("/data/students/gaolei/stock/runs/platform/low_vol_20d")
OUT = Path("/data/students/gaolei/stock/governance/evidence/reviews/"
           "r08-2026-09-16-metrics-audit/evidence/labels")
OUT.mkdir(parents=True, exist_ok=True)

WIN_START, WIN_END = "2025-03-01", "2025-03-31"
DATA_START, DATA_END = "2025-02-01", "2025-05-31"  # enough right tail for h=20


def rel_breakdown(mine: pl.Series, ref: pl.Series) -> dict:
    """mismatch counts at rtol 1e-9 / 1e-6 + max rel/abs diff (non-null pairs)."""
    both = mine.is_not_null() & ref.is_not_null()
    a = mine.filter(both).cast(pl.Float64)
    b = ref.filter(both).cast(pl.Float64)
    if len(a) == 0:
        return {"n": 0}
    ad = (a - b).abs()
    rd = ad / b.abs().clip(lower_bound=1e-12)
    return {
        "n": int(len(a)),
        "mismatch_rtol_1e-9": int((rd > 1e-9).sum()),
        "mismatch_rtol_1e-6": int((rd > 1e-6).sum()),
        "mismatch_abs_gt_1e-6": int((ad > 1e-6).sum()),
        "mismatch_abs_gt_1e-7": int((ad > 1e-7).sum()),
        "max_abs_diff": float(ad.max()),
        "max_rel_diff": float(rd.max()),
    }


def main() -> int:
    panel = pl.read_parquet(RUN / "panel.parquet")
    labels = pl.read_parquet(RUN / "labels.parquet")

    # deterministic 30-code selection: codes alive in first week of 2025-03, every k-th
    alive = (panel.filter((pl.col("date") >= pl.date(2025, 3, 3))
                          & (pl.col("date") <= pl.date(2025, 3, 7))
                          & pl.col("signal").is_not_null())
             .select("code").unique().sort("code")["code"].to_list())
    k = max(1, len(alive) // 30)
    sel = alive[::k][:30]
    assert len(sel) == 30, f"sample size {len(sel)}"

    ph, params = in_clause(sel)
    px = query_df(
        f"""SELECT d.ts_code, d.trade_date, d.close, a.adj_factor
            FROM factorlab.daily d
            JOIN factorlab.adj_factor a
              ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date
            WHERE d.ts_code IN ({ph})
              AND d.trade_date BETWEEN '{DATA_START}' AND '{DATA_END}'
            ORDER BY d.ts_code, d.trade_date
            LIMIT 100000""",
        params,
    )
    cal = query_df(
        f"""SELECT cal_date FROM factorlab.trade_cal
            WHERE cal_date BETWEEN '{DATA_START}' AND '{DATA_END}'
            ORDER BY cal_date LIMIT 1000""")
    sb = query_df(
        f"""SELECT ts_code, list_date, delist_date FROM factorlab.stock_basic
            WHERE ts_code IN ({ph}) LIMIT 1000""",
        params)

    trade_dates = cal["cal_date"]
    grid = pl.DataFrame({"ts_code": pl.Series([], dtype=pl.String),
                         "trade_date": pl.Series([], dtype=pl.Date)})
    grid = sb.join(pl.DataFrame({"trade_date": trade_dates}), how="cross")
    grid = grid.filter(
        (pl.col("trade_date") >= pl.col("list_date"))
        & (pl.col("delist_date").is_null() | (pl.col("trade_date") < pl.col("delist_date")))
    ).select(["ts_code", "trade_date"])

    sk = (grid.join(px, on=["ts_code", "trade_date"], how="left")
          .sort(["ts_code", "trade_date"]))
    # platform arithmetic: load_daily(float32=True) -> f32 inputs, f32 mul/div/sub
    sk = sk.with_columns(
        (pl.col("close").cast(pl.Float32) * pl.col("adj_factor").cast(pl.Float32)
         ).alias("hfq32"),
        (pl.col("close").cast(pl.Float64) * pl.col("adj_factor").cast(pl.Float64)
         ).alias("hfq64"),
    )
    for h in (5, 20):
        for tag, col in (("32", "hfq32"), ("64", "hfq64")):
            sk = sk.with_columns(
                ((pl.col(col).shift(-h).over("ts_code") / pl.col(col)) - 1)
                .alias(f"mine{h}_{tag}"))
    mine = sk.filter(
        (pl.col("trade_date") >= datetime.date.fromisoformat(WIN_START))
        & (pl.col("trade_date") <= datetime.date.fromisoformat(WIN_END))
    ).select(["ts_code", "trade_date", "mine5_32", "mine20_32",
              "mine5_64", "mine20_64"]).rename(
        {"ts_code": "code", "trade_date": "date"})

    p = panel.filter(
        (pl.col("date") >= datetime.date.fromisoformat(WIN_START))
        & (pl.col("date") <= datetime.date.fromisoformat(WIN_END))
        & pl.col("code").is_in(sel)
    ).select(["date", "code", "signal", "forward_return_5d", "forward_return_20d"])
    lab = labels.filter(
        (pl.col("date") >= datetime.date.fromisoformat(WIN_START))
        & (pl.col("date") <= datetime.date.fromisoformat(WIN_END))
        & pl.col("code").is_in(sel)
    ).select(["date", "code", "forward_return_5d", "forward_return_20d"])

    j = p.join(mine, on=["date", "code"], how="full", coalesce=True).sort(["date", "code"])

    report: dict = {
        "window": [WIN_START, WIN_END],
        "data_window": [DATA_START, DATA_END],
        "n_codes": len(sel),
        "codes": sel,
        "panel_rows_in_window": p.height,
        "label_rows_in_window": lab.height,
        "recompute_rows_in_window": mine.height,
        "ch_daily_rows_fetched": px.height,
        "trade_cal_days": cal.height,
        "checks": {},
        "key_alignment": {
            "panel_rows_not_in_recompute": int(j.filter(
                pl.col("mine5_32").is_null() & pl.col("signal").is_not_null()).height),
            "recompute_rows_not_in_panel": int(j.filter(
                pl.col("signal").is_null() & pl.col("mine5_32").is_not_null()).height),
        },
    }

    # panel vs labels artifact equality (same key, same values)
    pl_join = p.join(lab, on=["date", "code"], how="inner", suffix="_lab")
    d5 = (pl_join["forward_return_5d"] - pl_join["forward_return_5d_lab"]).abs()
    d20 = (pl_join["forward_return_20d"] - pl_join["forward_return_20d_lab"]).abs()
    report["panel_vs_labels_artifact"] = {
        "n": pl_join.height,
        "n_diff_5d": int((d5 > 0).sum()),
        "n_diff_20d": int((d20 > 0).sum()),
        "max_abs_diff_5d": float(d5.max()) if d5.len() else None,
        "max_abs_diff_20d": float(d20.max()) if d20.len() else None,
    }

    for h in (5, 20):
        for tag in ("32", "64"):
            mcol, rcol = f"mine{h}_{tag}", f"forward_return_{h}d"
            r = rel_breakdown(j[mcol], j[rcol])
            report["checks"][f"fwd{h}_{tag}"] = r
            both_null = (j[mcol].is_null() & j[rcol].is_null()).sum()
            panel_null_mine_val = (j[mcol].is_not_null() & j[rcol].is_null()).sum()
            mine_null_panel_val = (j[mcol].is_null() & j[rcol].is_not_null()).sum()
            report["checks"][f"fwd{h}_{tag}"].update({
                "both_null": int(both_null),
                "panel_null_mine_nonnull": int(panel_null_mine_val),
                "mine_null_panel_nonnull": int(mine_null_panel_val),
            })

    # 100-row deterministic random sample dump (seeded)
    samp = j.filter(pl.col("signal").is_not_null()).sample(n=100, seed=20260916).sort(["date", "code"])
    samp.write_csv(OUT / "01_forward_recompute_sample100.csv")
    report["sample100"] = {
        "file": "01_forward_recompute_sample100.csv",
        "n": samp.height,
    }

    # f32 vs f64 internal consistency (formula unaffected by float64 in independent recompute)
    for h in (5, 20):
        a = j[f"mine{h}_32"].cast(pl.Float64)
        b = j[f"mine{h}_64"]
        ok = a.is_not_null() & b.is_not_null()
        rd = ((a - b).abs() / b.abs().clip(lower_bound=1e-12)).filter(ok)
        report["checks"][f"internal_f32_vs_f64_fwd{h}"] = {
            "n": int(ok.sum()),
            "mismatch_rtol_1e-9": int((rd > 1e-9).sum()),
            "max_rel_diff": float(rd.max()) if rd.len() else None,
        }

    (OUT / "01_forward_recompute.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
