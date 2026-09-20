#!/usr/bin/env python3
"""R37 独立复查 B：范围/账本独立核数（只读）。

- CH（FACTORLAB_DATA_BACKEND=ch 等效直连 127.0.0.1:8123）查 daily 及 canonical 各表：
  行数/日期范围/.BJ 行数/pre-1996 行数/不同代码数；
- 直读 raw daily_fact.parquet（lazy）复算：全量/scope/JB/pre-1996；
- 直读 clean staging parquet 复算：行数/日期数/代码数/越界行。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import polars as pl
import clickhouse_connect

RAW = "/data/students/gaolei/stock/data/fact/daily_fact/daily_fact.parquet"
CLEAN = "/data/students/gaolei/stock/data/staging/ashare_daily/scope20260920/daily_fact.parquet"
MIN = dt.date(1996, 1, 1)
OUT_JSON = Path(__file__).with_name("B-scope-ledger.json")
OUT_LOG = Path(__file__).with_name("B-scope-ledger.log")

log_lines: list[str] = []


def log(msg: str) -> None:
    print(msg)
    log_lines.append(msg)


def main() -> int:
    out: dict = {}
    client = clickhouse_connect.get_client(host="127.0.0.1", port=8123,
                                           username="default", password="")
    log("# CH queries (read-only) via clickhouse-connect 127.0.0.1:8123")
    for table in ("daily", "daily_basic", "adj_factor", "stk_limit",
                  "adj_detail", "adj_event"):
        row = client.query(f"""
            SELECT count() AS n,
                   countIf(ts_code LIKE '%.BJ') AS bj,
                   countIf(trade_date < toDate('1996-01-01')) AS pre1996,
                   min(trade_date) AS dmin, max(trade_date) AS dmax,
                   uniqExact(ts_code) AS codes
            FROM factorlab.{table}
        """).first_row
        rec = {"rows": row[0], "bj_rows": row[1], "pre1996_rows": row[2],
               "min_date": str(row[3]), "max_date": str(row[4]),
               "distinct_codes": row[5]}
        out[f"ch:{table}"] = rec
        log(f"CH {table:<12} rows={rec['rows']} .BJ={rec['bj_rows']} "
            f"pre1996={rec['pre1996_rows']} date={rec['min_date']}..{rec['max_date']} "
            f"codes={rec['distinct_codes']}")
    row = client.query("""
        SELECT count() AS n, countIf(cal_date < toDate('1996-01-01')) AS pre1996,
               min(cal_date) AS dmin, max(cal_date) AS dmax
        FROM factorlab.trade_cal
    """).first_row
    out["ch:trade_cal"] = {"rows": row[0], "pre1996_rows": row[1],
                           "min_date": str(row[2]), "max_date": str(row[3])}
    log(f"CH trade_cal    rows={row[0]} pre1996={row[1]} date={row[2]}..{row[3]}")
    row = client.query("""
        SELECT count() AS n, countIf(ts_code LIKE '%.BJ') AS bj,
               uniqExact(ts_code) AS codes
        FROM factorlab.stock_basic
    """).first_row
    out["ch:stock_basic"] = {"rows": row[0], "bj_rows": row[1], "distinct_codes": row[2]}
    log(f"CH stock_basic  rows={row[0]} .BJ={row[1]} codes={row[2]}")

    bj_share = client.query(
        "SELECT countIf(ts_code LIKE '%.BJ')/count() FROM factorlab.daily").first_item
    out["ch:daily_bj_share"] = bj_share
    log(f"CH daily .BJ share = {bj_share!r}")

    # 全 scope 谓词版计数（与 read 层 scope_expr 同真值）
    row = client.query("""
        SELECT count() FROM factorlab.daily
        WHERE trade_date >= toDate('1996-01-01') AND ts_code NOT LIKE '%.BJ'
    """).first_row
    out["ch:daily_scoped_rows"] = row[0]
    log(f"CH daily scoped(>=1996 & non-BJ) rows = {row[0]}")

    log("")
    log("# raw daily_fact.parquet lazy scan (independent recount)")
    lf = pl.scan_parquet(RAW)
    total = lf.select(pl.len()).collect().item()
    scope = lf.filter((pl.col("trade_date") >= pl.lit(MIN)) &
                      ~pl.col("code").str.to_uppercase().str.ends_with(".BJ")
                      ).select(pl.len()).collect().item()
    bj = lf.filter(pl.col("code").str.to_uppercase().str.ends_with(".BJ")
                   ).select(pl.len()).collect().item()
    pre = lf.filter(pl.col("trade_date") < pl.lit(MIN)).select(pl.len()).collect().item()
    rmin = lf.select(pl.col("trade_date").min()).collect().item()
    rmax = lf.select(pl.col("trade_date").max()).collect().item()
    out["raw"] = {"total": total, "scope": scope, "bj_rows": bj,
                  "pre1996_rows": pre, "min_date": str(rmin), "max_date": str(rmax)}
    log(f"raw total={total} scope={scope} (.BJ={bj}, pre1996={pre}) "
        f"date={rmin}..{rmax}")
    log(f"raw scope + bj + pre1996 = {scope + bj + pre} (expect total={total})")

    log("")
    log("# clean staging parquet lazy scan")
    lc = pl.scan_parquet(CLEAN)
    c_total = lc.select(pl.len()).collect().item()
    c_bj = lc.filter(pl.col("code").str.to_uppercase().str.ends_with(".BJ")
                     ).select(pl.len()).collect().item()
    c_pre = lc.filter(pl.col("trade_date") < pl.lit(MIN)).select(pl.len()).collect().item()
    c_days = lc.select(pl.col("trade_date").n_unique()).collect().item()
    c_codes = lc.select(pl.col("code").n_unique()).collect().item()
    c_min = lc.select(pl.col("trade_date").min()).collect().item()
    c_max = lc.select(pl.col("trade_date").max()).collect().item()
    out["clean"] = {"total": c_total, "bj_rows": c_bj, "pre1996_rows": c_pre,
                    "trade_dates": c_days, "codes": c_codes,
                    "min_date": str(c_min), "max_date": str(c_max)}
    log(f"clean total={c_total} .BJ={c_bj} pre1996={c_pre} "
        f"trade_dates={c_days} codes={c_codes} date={c_min}..{c_max}")
    log(f"raw_scope - clean = {scope - c_total} (summary.json quarantine=36, "
        f"deduped=0)")

    # scope 日数（raw 内 scope 日）应等于 clean 日数 + 全隔离日
    scope_days = lf.filter((pl.col("trade_date") >= pl.lit(MIN)) &
                           ~pl.col("code").str.to_uppercase().str.ends_with(".BJ")
                           ).select(pl.col("trade_date").n_unique()).collect().item()
    out["raw_scope_days"] = scope_days
    log(f"raw scope trade_dates={scope_days} vs clean={c_days} "
        f"(差 = 全隔离日)")

    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    OUT_LOG.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
