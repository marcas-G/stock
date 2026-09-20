"""R37 T4：19 行单位 bug 的 vendor 源 xlsx 直查（只读，zip 内选择性读取）。

背景：bars_1m 自 2020-01-02 起才有覆盖 → 4 个 2015 行无分钟三角。本脚本从
**vendor 源**（`data/raw/daily/19910101至20260831A股日k线.zip` 全包 xlsx，只读
zip 成员）直查 19 行的 amount/volume 与源自带换手率，并给出相邻交易日连续性统计：

- ``volume ×100 后换手率`` 应回到同码邻近日常规区间（源换手率随 volume 塌缩 ~100×）；
- ``amount`` 在异常日与相邻日平滑（体积/金额二选一：amount 破坏会是 100× 断崖）；
- vendor 值 == daily_fact 值（bug 在源头持久化，两个全包快照一致，非导出事故）。

输出：``vendor-source-19.csv``（逐行）+ stdout JSON 摘要。
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import polars as pl

ZIP = "data/raw/daily/19910101至20260831A股日k线.zip"
INNER = "19910101至上月底A股日k线/{code}.xlsx"


def _vendor_frame(z: zipfile.ZipFile, code6: str) -> pd.DataFrame:
    with z.open(INNER.format(code=code6)) as f:
        df = pd.read_excel(io.BytesIO(f.read()))
    dcol = df.columns[0]
    df[dcol] = pd.to_datetime(df[dcol]).dt.date
    return df.rename(columns={dcol: "date"})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True, help="scoped-55-rows.csv")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rows = (pl.read_csv(args.rows, try_parse_dates=True)
            .filter(pl.col("class") == "unit_like")
            .sort(["trade_date", "code"]))
    z = zipfile.ZipFile(ZIP)
    cache: dict[str, pd.DataFrame] = {}
    recs = []
    for r in rows.iter_rows(named=True):
        code6 = r["code"].split(".")[0]
        if code6 not in cache:
            cache[code6] = _vendor_frame(z, code6)
        v = cache[code6]
        hit = v[v["date"] == r["trade_date"]]
        assert len(hit) == 1, f"vendor 源缺行：{r['code']} {r['trade_date']}"
        h = hit.iloc[0]
        prev = v[v["date"] < r["trade_date"]].tail(1)
        nxt = v[v["date"] > r["trade_date"]].head(1)
        rec = {
            "trade_date": r["trade_date"].isoformat(), "code": r["code"],
            "vendor_volume": float(h["volume"]), "vendor_amount": float(h["amount"]),
            "vendor_turnover_pct": float(h["换手率"]),
            "daily_fact_volume": r["volume"], "daily_fact_amount": r["amount"],
            "vendor_eq_daily": bool(float(h["volume"]) == r["volume"]
                                    and float(h["amount"]) == r["amount"]),
            "turnover_x100": float(h["换手率"]) * 100,
            "prev_volume": float(prev["volume"].iloc[0]) if len(prev) else None,
            "next_volume": float(nxt["volume"].iloc[0]) if len(nxt) else None,
            "prev_amount": float(prev["amount"].iloc[0]) if len(prev) else None,
            "next_amount": float(nxt["amount"].iloc[0]) if len(nxt) else None,
            "prev_turnover_pct": float(prev["换手率"].iloc[0]) if len(prev) else None,
            "next_turnover_pct": float(nxt["换手率"].iloc[0]) if len(nxt) else None,
        }
        rec["vol_ratio_vs_prev_x100"] = (rec["vendor_volume"] * 100
                                         / rec["prev_volume"]) if rec["prev_volume"] else None
        rec["amt_ratio_vs_prev"] = (rec["vendor_amount"]
                                    / rec["prev_amount"]) if rec["prev_amount"] else None
        recs.append(rec)
    out = pl.DataFrame(recs)
    out.write_csv(args.csv)
    with_m = rows.filter(pl.col("trade_date") >= dt.date(2020, 1, 2))
    summary = {
        "rows": out.height,
        "vendor_eq_daily_fact_all": bool(out["vendor_eq_daily"].all()),
        "bars_1m_covered": with_m.height,
        "no_bars_1m": out.filter(
            pl.col("trade_date") < "2020-01-02")
        .select(["code", "trade_date"]).to_dicts(),
        "turnover_before_pct_median": round(
            float(out["prev_turnover_pct"].drop_nulls().median()), 4),
        "turnover_after_x100_median": round(
            float(out["turnover_x100"].median()), 4),
        "vol_ratio_vs_prev_x100_median": round(
            float(out["vol_ratio_vs_prev_x100"].drop_nulls().median()), 4),
        "amt_ratio_vs_prev_median": round(
            float(out["amt_ratio_vs_prev"].drop_nulls().median()), 4),
        "csv": args.csv,
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
