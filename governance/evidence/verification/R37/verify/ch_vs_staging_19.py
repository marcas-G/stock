#!/usr/bin/env python3
"""R37 独立复查 E2：19 行修复后 CH canonical vs staging clean parquet 逐列一致性。

两后端（CH daily / staging parquet）对 19 个 (code, trade_date) 的
open/high/low/close/vol/amount 逐列相等（浮点精确 bit 比较用 == 0 差）。
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import clickhouse_connect
import polars as pl

CSV = Path("/data/students/gaolei/stock/governance/evidence/verification/R37/"
           "residual-55/scoped-55-rows.csv")
CLEAN = "/data/students/gaolei/stock/data/staging/ashare_daily/scope20260920/daily_fact.parquet"
OUT_LOG = Path(__file__).with_name("E2-ch-vs-staging.log")

log_lines: list[str] = []


def log(msg: str) -> None:
    print(msg)
    log_lines.append(msg)


def main() -> int:
    rows = [r for r in csv.DictReader(CSV.open(encoding="utf-8"))
            if r["class"] == "unit_like"]
    keys = [f"{r['code']}|{r['trade_date']}" for r in rows]
    client = clickhouse_connect.get_client(host="127.0.0.1", port=8123,
                                           username="default", password="")
    res = client.query("""
        SELECT ts_code, toString(trade_date), open, high, low, close, vol, amount
        FROM factorlab.daily
        WHERE concat(ts_code, '|', toString(trade_date)) IN {keys:Array(String)}
    """, parameters={"keys": keys})
    ch = {f"{r[0]}|{r[1]}": r[2:] for r in res.result_rows}

    clean = pl.scan_parquet(CLEAN)
    keyexpr = pl.col("code") + "|" + pl.col("trade_date").cast(pl.Utf8)
    st = clean.filter(keyexpr.is_in(keys)).collect()
    stg = {f"{r['code']}|{r['trade_date']}": r for r in st.iter_rows(named=True)}

    fails = []
    for k in keys:
        if k not in ch or k not in stg:
            fails.append(f"{k}: missing ch={k in ch} staging={k in stg}")
            continue
        c = ch[k]
        s = stg[k]
        pairs = [("open", c[0], s["open"]), ("high", c[1], s["high"]),
                 ("low", c[2], s["low"]), ("close", c[3], s["close"]),
                 ("vol", c[4], s["volume"]), ("amount", c[5], s["amount"])]
        for name, cv, sv in pairs:
            if cv != sv:
                fails.append(f"{k}: {name} CH={cv!r} staging={sv!r}")
    log(f"rows checked={len(keys)} both-backend present={len(ch)}/{len(stg)}")
    log(f"column mismatches: {len(fails)}")
    for f in fails:
        log(f"  {f}")
    log("VERDICT: " + ("IDENTICAL" if not fails else "MISMATCH"))
    OUT_LOG.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
