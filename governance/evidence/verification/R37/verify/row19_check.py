#!/usr/bin/env python3
"""R37 独立复查 E：19 行单位修复抽核 + 36 行残余缺失核（真 CH 只读）。

- 从 scoped-55-rows.csv 取 class=unit_like 19 行：CH volume 应 = CSV volume ×100
  （修复后），vwap=amount/volume ∈ [low,high]（含 1% 宽容带）；
- 从 class=other 36 行：应不在 CH canonical（保持隔离）；
- 输出 3 行重点抽核明细（含 2015 类与 2024+ 分钟确证类各至少 1 行）。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import clickhouse_connect

CSV = Path("/data/students/gaolei/stock/governance/evidence/verification/R37/"
           "residual-55/scoped-55-rows.csv")
OUT_JSON = Path(__file__).with_name("E-19row-check.json")
OUT_LOG = Path(__file__).with_name("E-19row-check.log")
FOCUS = [("2015-05-28", "000725.SZ"), ("2015-06-09", "601988.SH"),
         ("2026-03-12", "601868.SH")]

log_lines: list[str] = []


def log(msg: str) -> None:
    print(msg)
    log_lines.append(msg)


def main() -> int:
    rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
    fixed = [r for r in rows if r["class"] == "unit_like"]
    residual = [r for r in rows if r["class"] == "other"]
    log(f"CSV rows total={len(rows)} unit_like={len(fixed)} other={len(residual)}")
    assert len(fixed) == 19 and len(residual) == 36, "CSV composition mismatch"

    client = clickhouse_connect.get_client(host="127.0.0.1", port=8123,
                                           username="default", password="")
    keys = [f"{r['code']}|{r['trade_date']}" for r in fixed]
    res = client.query("""
        SELECT ts_code, toString(trade_date), open, high, low, close,
               vol, amount
        FROM factorlab.daily
        WHERE concat(ts_code, '|', toString(trade_date)) IN {keys:Array(String)}
    """, parameters={"keys": keys})
    ch = {f"{r[0]}|{r[1]}": r for r in res.result_rows}
    log(f"CH found {len(ch)}/19 unit_like rows")
    assert len(ch) == 19, "not all repaired rows present in canonical CH"

    fails: list[str] = []
    details: list[dict] = []
    for r in fixed:
        key = f"{r['code']}|{r['trade_date']}"
        _c, _d, o, h, lo, cl, vol, amt = ch[key]
        csv_vol = float(r["volume"])
        vwap = amt / vol
        lo_f, hi_f = float(lo), float(h)
        in_band = lo_f <= vwap <= hi_f
        in_band_1pct = lo_f * 0.99 <= vwap <= hi_f * 1.01
        vol_ratio = vol / csv_vol
        rec = {"code": r["code"], "trade_date": r["trade_date"],
               "ch_volume": vol, "csv_volume": csv_vol, "vol_ratio": vol_ratio,
               "ch_amount": amt, "vwap": vwap, "low": lo_f, "high": hi_f,
               "vwap_in_band": in_band, "vwap_in_band_1pct": in_band_1pct}
        details.append(rec)
        if abs(vol_ratio - 100.0) > 0.01 * 100.0:
            fails.append(f"{key}: CH volume {vol} != csv×100 "
                         f"({csv_vol * 100:.0f}), ratio={vol_ratio:.6f}")
        if not in_band:
            fails.append(f"{key}: vwap {vwap:.6f} not in [{lo_f},{hi_f}]")

    log("")
    log("== all 19: CH volume / csv volume ratio + vwap band ==")
    for rec in details:
        log(f"  {rec['trade_date']} {rec['code']}  vol_ratio={rec['vol_ratio']:.6f}  "
            f"vwap={rec['vwap']:.6f} in [{rec['low']},{rec['high']}] "
            f"band={rec['vwap_in_band']} band1pct={rec['vwap_in_band_1pct']}")

    log("")
    log("== focus 3 rows (per task: 2x 2015 class + 1 minute-confirmed) ==")
    focus_recs = []
    for fd, fc in FOCUS:
        rec = next((x for x in details
                    if x["trade_date"] == fd and x["code"] == fc), None)
        assert rec is not None, f"focus row missing {fc} {fd}"
        focus_recs.append(rec)
        log(f"  {fd} {fc}: CH volume={rec['ch_volume']:.0f} "
            f"(csv {rec['csv_volume']:.0f} ×100) amount={rec['ch_amount']:.0f} "
            f"vwap={rec['vwap']:.6f} OHLC=[{rec['low']},{rec['high']}] "
            f"in_band={rec['vwap_in_band']}")

    log("")
    log("== residual 36 rows must be absent from CH canonical ==")
    rkeys = [f"{r['code']}|{r['trade_date']}" for r in residual]
    rres = client.query("""
        SELECT concat(ts_code, '|', toString(trade_date))
        FROM factorlab.daily
        WHERE concat(ts_code, '|', toString(trade_date)) IN {keys:Array(String)}
    """, parameters={"keys": rkeys})
    present = [r[0] for r in rres.result_rows]
    log(f"  residual rows found in CH: {len(present)} (expect 0)")
    if present:
        fails.append(f"residual rows leaked into canonical: {present}")

    log("")
    log(f"FAILS: {len(fails)}")
    for f in fails:
        log(f"  {f}")

    OUT_JSON.write_text(json.dumps({
        "csv_total": len(rows), "fixed_n": len(fixed), "residual_n": len(residual),
        "ch_found_fixed": len(ch), "residual_in_ch": present,
        "details": details, "focus": focus_recs, "fails": fails,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_LOG.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
