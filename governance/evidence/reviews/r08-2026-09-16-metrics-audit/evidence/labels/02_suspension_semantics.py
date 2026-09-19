#!/usr/bin/env python
"""R08 标签核对 02：停牌缺行时 shift(-h) 语义验证（h=5/20）。

平台 label runtime：align_to_listing 生成 is_listed 交易日骨架（停牌日保留 null 行）
→ compute_forward_returns 在骨架上 shift(-h)/over(code)。问题：h 是「骨架行数
（=交易日）」还是「有行情行数」？

独立验证：取 2024 年有 >=3 日连续停牌（daily 缺行）的退市候选股，独立构造
  (a) 骨架语义：每个 listed 交易日一行，停牌日 hfq=null，shift(-h) 行距
  (b) 有行情语义：仅实际 daily 行，shift(-h) 行距（跳过停牌）
分别与 runs/platform/low_vol_20d/panel.parquet 对拍（f32 同算子重建 + f64）。

Read-only. CH queries windowed + LIMIT.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, "/data/students/gaolei/stock/platform/src")
from factorlab.adapters.ch_read import in_clause, query_df  # noqa: E402

RUN = Path("/data/students/gaolei/stock/runs/platform/low_vol_20d")
OUT = Path("/data/students/gaolei/stock/governance/evidence/reviews/"
           "r08-2026-09-16-metrics-audit/evidence/labels")
OUT.mkdir(parents=True, exist_ok=True)

Q_START, Q_END = "2024-01-01", "2026-08-21"   # gaps in 2024; tail covers post-gap 20d
MIN_GAP = 3


def with_forwards(df: pl.DataFrame, tag: str) -> pl.DataFrame:
    """hfq = close*adj (Float<tag>) then fwd5 = shift(-5)/hfq-1, fwd20 = shift(-20)/hfq-1."""
    ftype = pl.Float32 if tag == "32" else pl.Float64
    out = df.sort(["ts_code", "trade_date"]).with_columns(
        (pl.col("close").cast(ftype) * pl.col("adj_factor").cast(ftype)).alias(f"hfq{tag}"))
    for h in (5, 20):
        out = out.with_columns(
            ((pl.col(f"hfq{tag}").shift(-h).over("ts_code") / pl.col(f"hfq{tag}")) - 1)
            .alias(f"fwd{h}_{tag}"))
    return out


def main() -> int:
    panel = pl.read_parquet(RUN / "panel.parquet")

    # 1) candidates: codes with <238 daily rows in 2024 (242 trade days) — temporary
    #    suspension gaps (delisted-late codes included; still in panel while listed)
    cand = query_df(
        """SELECT ts_code, count() AS n FROM factorlab.daily
           WHERE trade_date BETWEEN '2024-01-01' AND '2024-12-31'
           GROUP BY ts_code HAVING n BETWEEN 100 AND 238 ORDER BY n LIMIT 400""")
    codes = cand["ts_code"].to_list()
    ph, params = in_clause(codes)
    sb = query_df(
        f"""SELECT ts_code, list_date, delist_date FROM factorlab.stock_basic
            WHERE ts_code IN ({ph}) LIMIT 1000""",
        params)
    px = query_df(
        f"""SELECT d.ts_code, d.trade_date, d.close, a.adj_factor
            FROM factorlab.daily d
            LEFT JOIN factorlab.adj_factor a
              ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date
            WHERE d.ts_code IN ({ph})
              AND d.trade_date BETWEEN '{Q_START}' AND '{Q_END}'
            ORDER BY d.ts_code, d.trade_date
            LIMIT 300000""",
        params,
    )
    cal = query_df(
        f"SELECT cal_date FROM factorlab.trade_cal "
        f"WHERE cal_date BETWEEN '{Q_START}' AND '{Q_END}' ORDER BY cal_date LIMIT 2000")

    grid = (sb.join(pl.DataFrame({"trade_date": cal["cal_date"]}), how="cross")
            .filter((pl.col("trade_date") >= pl.col("list_date"))
                    & (pl.col("delist_date").is_null()
                       | (pl.col("trade_date") < pl.col("delist_date"))))
            .select(["ts_code", "trade_date"]))
    skel = (grid.join(px.select(["ts_code", "trade_date", "close", "adj_factor"]),
                      on=["ts_code", "trade_date"], how="left")
            .sort(["ts_code", "trade_date"])
            .with_columns(pl.col("close").is_null().alias("missing")))

    # 2) consecutive missing-run detection per code, gap >= MIN_GAP inside 2024
    g = skel.with_columns(
        (pl.col("missing") != pl.col("missing").shift(1).over("ts_code"))
        .fill_null(True).alias("_brk"))
    g = g.with_columns(pl.col("_brk").cum_sum().over("ts_code").alias("_run"))
    runs = (g.filter(pl.col("missing"))
            .group_by(["ts_code", "_run"])
            .agg(start=pl.col("trade_date").min(), end=pl.col("trade_date").max(),
                 days=pl.len())
            .filter((pl.col("days") >= MIN_GAP)
                    & (pl.col("start") >= dt.date(2024, 1, 1))
                    & (pl.col("start") <= dt.date(2024, 12, 31)))
            .sort(["days", "start"], descending=[True, False]))
    n_codes_with_gap = runs["ts_code"].n_unique()

    # 3) pick a code: valid signal/labels in panel just before the gap (universe member
    #    with real prices+adj_factor) & gap 3..40 days
    chosen = None
    for row in runs.filter((pl.col("days") >= 3) & (pl.col("days") <= 40)).iter_rows(named=True):
        c = row["ts_code"]
        around = panel.filter((pl.col("code") == c)
                              & (pl.col("date") >= row["start"] - dt.timedelta(days=60))
                              & (pl.col("date") <= row["start"] - dt.timedelta(days=1)))
        if around.height >= 10 and around["signal"].is_not_null().sum() >= 10 \
                and around["forward_return_5d"].is_not_null().sum() >= 10:
            chosen = {"ts_code": c, **{k: row[k] for k in ("start", "end", "days")}}
            break
    if chosen is None:
        raise SystemExit("no suitable suspended candidate found")
    c = chosen["ts_code"]

    # 4) independent forward returns for chosen code: skeleton vs traded-rows semantics
    d = skel.filter(pl.col("ts_code") == c)
    skel32 = with_forwards(d, "32")
    skel64 = with_forwards(d, "64")
    px_c = px.filter(pl.col("ts_code") == c)
    tr32 = with_forwards(px_c, "32")
    tr64 = with_forwards(px_c, "64")

    p = panel.filter((pl.col("code") == c)
                     & (pl.col("date") >= dt.date.fromisoformat(Q_START))
                     & (pl.col("date") <= dt.date.fromisoformat(Q_END))
                     ).select(
        ["date", "code", "signal", "forward_return_5d", "forward_return_20d"])
    j = p.join(skel32.select(["trade_date", "fwd5_32", "fwd20_32", "missing"]),
               left_on="date", right_on="trade_date", how="left")
    j = j.join(tr32.select(["trade_date", "fwd5_32", "fwd20_32"]),
               left_on="date", right_on="trade_date", how="left", suffix="_tr")
    j = j.join(skel64.select(["trade_date", "fwd5_64", "fwd20_64"]),
               left_on="date", right_on="trade_date", how="left", suffix="_s64")
    j = j.join(tr64.select(["trade_date", "fwd5_64", "fwd20_64"]),
               left_on="date", right_on="trade_date", how="left", suffix="_t64")
    j = j.sort("date")

    # platform label window ends 2026-07-31 (spec.date.end); t+h beyond => label null.
    # cut comparison at the same right edge for a like-for-like value check.
    cal_list = cal["cal_date"].to_list()

    def cutoff(h: int) -> dt.date:
        valid = [d for i, d in enumerate(cal_list)
                 if i + h < len(cal_list) and cal_list[i + h] <= dt.date(2026, 7, 31)]
        return max(valid)

    cutoff5, cutoff20 = cutoff(5), cutoff(20)
    j5 = j.filter(pl.col("date") <= cutoff5)
    j20 = j.filter(pl.col("date") <= cutoff20)

    def cmp(a: pl.Series, b: pl.Series) -> dict:
        both_nn = a.is_not_null() & b.is_not_null()
        ad = (a.filter(both_nn).cast(pl.Float64) - b.filter(both_nn).cast(pl.Float64)).abs()
        return {
            "n": int(len(a)),
            "null_mismatch": int((a.is_null() != b.is_null()).sum()),
            "value_diff_gt_1e-6": int((ad > 1e-6).sum()) if ad.len() else 0,
            "max_abs_diff": float(ad.max()) if ad.len() else None,
        }

    report = {
        "candidate_pool_codes": len(codes),
        "codes_with_gap_ge3_in_2024": int(n_codes_with_gap),
        "chosen": {k: str(v) for k, v in chosen.items()},
        "chosen_gap_len_trade_days": int(chosen["days"]),
        "panel_rows_for_chosen": p.height,
        "right_edge_cutoff_5d": str(cutoff5),
        "right_edge_cutoff_20d": str(cutoff20),
        "checks": {
            "panel_vs_skeleton_f32_5d": cmp(j5["fwd5_32"], j5["forward_return_5d"]),
            "panel_vs_skeleton_f32_20d": cmp(j20["fwd20_32"], j20["forward_return_20d"]),
            "panel_vs_traded_f32_5d": cmp(j5["fwd5_32_tr"], j5["forward_return_5d"]),
            "panel_vs_traded_f32_20d": cmp(j20["fwd20_32_tr"], j20["forward_return_20d"]),
            "panel_vs_skeleton_f64_5d": cmp(j5["fwd5_64"], j5["forward_return_5d"]),
            "panel_vs_traded_f64_5d": cmp(j5["fwd5_64_t64"], j5["forward_return_5d"]),
            "panel_vs_traded_f64_20d": cmp(j20["fwd20_64_t64"], j20["forward_return_20d"]),
        },
    }

    # 5) focused table around the gap: rows where the two semantics differ
    gs, ge = chosen["start"], chosen["end"]
    focus = j20.filter((pl.col("date") >= gs - dt.timedelta(days=45))
                       & (pl.col("date") <= ge + dt.timedelta(days=45)))
    skel_vs_tr = ((pl.col("fwd5_32").is_null() != pl.col("fwd5_32_tr").is_null())
                  | ((pl.col("fwd5_32") - pl.col("fwd5_32_tr")).abs().fill_null(0) > 1e-6)
                  | (pl.col("fwd20_32").is_null() != pl.col("fwd20_32_tr").is_null())
                  | ((pl.col("fwd20_32") - pl.col("fwd20_32_tr")).abs().fill_null(0) > 1e-6))
    diff = focus.filter(skel_vs_tr)
    focus.write_csv(OUT / f"02_suspension_focus_{c.replace('.', '_')}.csv")
    diff.write_csv(OUT / f"02_suspension_diff_{c.replace('.', '_')}.csv")
    report["focus_rows"] = focus.height
    report["diff_rows_skeleton_vs_traded"] = diff.height
    report["focus_file"] = f"02_suspension_focus_{c.replace('.', '_')}.csv"
    report["diff_file"] = f"02_suspension_diff_{c.replace('.', '_')}.csv"
    # explicit t where t+5 lands inside the gap (skeleton null, traded not)
    t_in_gap = focus.filter(pl.col("forward_return_5d").is_null()
                            & pl.col("fwd5_32_tr").is_not_null())
    report["t_with_t_plus_5_inside_gap"] = t_in_gap.height
    if t_in_gap.height:
        report["example_t_plus5_inside_gap"] = [
            {"date": str(r["date"]),
             "panel_fwd5": None if r["forward_return_5d"] is None else float(r["forward_return_5d"]),
             "traded_shift_fwd5": None if r["fwd5_32_tr"] is None else float(r["fwd5_32_tr"])}
            for r in t_in_gap.head(5).iter_rows(named=True)
        ]
    # explicit t where t+5 is a real trade day but window spans the gap (values differ)
    t_span = focus.filter(
        pl.col("forward_return_5d").is_not_null() & pl.col("fwd5_32_tr").is_not_null()
        & ((pl.col("forward_return_5d") - pl.col("fwd5_32_tr")).abs() > 1e-6))
    report["t_spanning_gap_value_diff"] = t_span.height
    if t_span.height:
        report["example_t_spanning_gap"] = [
            {"date": str(r["date"]),
             "panel_fwd5": float(r["forward_return_5d"]),
             "traded_shift_fwd5": float(r["fwd5_32_tr"])}
            for r in t_span.head(5).iter_rows(named=True)
        ]

    (OUT / "02_suspension_semantics.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
