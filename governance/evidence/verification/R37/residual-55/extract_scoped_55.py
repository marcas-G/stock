"""R37 T4：从 R33 隔离证据提取 scope 内 55 行（只读）。

输入：``--quarantine data/quarantine/ashare_daily/<run>/rows.parquet``（R33=T2b 20260919c）。
scope := trade_date >= 1996-01-01 且 code 非 .BJ（factorlab.core.scope）。

输出：
- CSV：逐行 vwap=amount/volume、ratio=vwap/close、OHLCV；
- stdout JSON：行数构成（unit_like ratio∈[90,110] / other）+ 逐码计数。

只读：不写 data/；CSV 落证据目录。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import polars as pl

UNIT_LIKE_LO, UNIT_LIKE_HI = 90.0, 110.0


def build(df: pl.DataFrame) -> pl.DataFrame:
    scoped = df.filter(
        (pl.col("trade_date") >= dt.date(1996, 1, 1))
        & (~pl.col("code").str.ends_with(".BJ")))
    out = scoped.with_columns(
        (pl.col("amount") / pl.col("volume")).alias("vwap"),
        (pl.col("amount") / pl.col("volume") / pl.col("close")).alias("ratio"),
    ).with_columns(
        pl.when((pl.col("ratio") >= UNIT_LIKE_LO) & (pl.col("ratio") <= UNIT_LIKE_HI))
        .then(pl.lit("unit_like"))
        .otherwise(pl.lit("other")).alias("class"),
    )
    cols = ["trade_date", "code", "class", "open", "high", "low", "close",
            "volume", "amount", "vwap", "ratio", "adj_factor", "fq_factor",
            "float_shares", "total_shares"]
    return out.select(cols).sort(["trade_date", "code"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quarantine", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    df = pl.read_parquet(args.quarantine)
    scoped = build(df)
    scoped.write_csv(args.csv)
    classes = dict(sorted(scoped.group_by("class").len()
                          .iter_rows()))
    report = {
        "quarantine_total": df.height,
        "scoped_total": scoped.height,
        "classes": classes,
        "by_code": dict(sorted(scoped.group_by("code").len().iter_rows())),
        "ratio_min": float(scoped["ratio"].min()),
        "ratio_max": float(scoped["ratio"].max()),
        "csv": str(args.csv),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
