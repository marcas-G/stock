#!/usr/bin/env python3
"""R08 CH 抽笔对拍：M8 成交价语义（NEXT_OPEN / raw open）——只读限流小查询。

抽查（确定性选取 6 笔，覆盖 buy/sell/部分成交/大单）：
  - reference_price == CH factorlab.daily.open（raw，未复权；同一 (ts_code, date)）
  - execution_price == reference_price ×(1±slippage_bps/1e4)
  - calibration：execution_date 为 decision_date 之后**第一个** trade_cal 开放日
    （区间内无其他 is_open=1）

CH 限流：全部对拍合并为 2 条小查询（6 行 daily + 一段 trade_cal），HTTP 逐条，
不扫大表（主键过滤 (ts_code, trade_date)）。
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import polars as pl
import yaml

STRAT = Path("/data/students/gaolei/stock/runs/platform/strategies/"
             "low_lottery_top30_weekly")
SPEC = Path("/data/students/gaolei/stock/research/strategy/"
            "low_lottery_top30_weekly.yaml")
CH = "http://127.0.0.1:8123/"


def ch(query: str, *, max_threads: int = 1) -> list[list[str]]:
    """只读 CH HTTP 查询（TSV 解析；显式限线程）。"""
    body = query + f"\nSETTINGS max_threads={max_threads} FORMAT TSV"
    req = urllib.request.Request(CH, data=body.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode("utf-8")
    rows = [line.split("\t") for line in text.splitlines() if line.strip()]
    return rows


def pick_samples(fills: pl.DataFrame) -> list[dict]:
    """确定性抽样：events 0..4 各 1 笔 + 全局最大名义额 1 笔（去重）。"""
    picks: list[dict] = []

    def first(pred):
        sub = fills.filter(pred)
        return sub.row(0, named=True) if sub.height else None

    candidates = [
        first(pl.col("event_index") == 0),                     # e0 buy
        first((pl.col("event_index") == 1) & (pl.col("side") == "sell")),
        first(pl.col("order_quantity") > pl.col("filled_quantity")),  # 部分成交
        first((pl.col("event_index") == 3) & (pl.col("side") == "sell")),
        first((pl.col("event_index") == 4) & (pl.col("side") == "sell")
              & (pl.col("filled_quantity") >= 10_000)),
        fills.sort("gross_notional", descending=True).row(0, named=True),
    ]
    seen = set()
    for c in candidates:
        if c is None:
            continue
        key = (c["event_index"], c["code"], c["side"])
        if key in seen:
            continue
        seen.add(key)
        picks.append(c)
    return picks


def main() -> int:
    spec = yaml.safe_load(SPEC.read_text())
    slip = spec["execution"]["cost_model"]["slippage_bps"] / 10_000.0
    fills = pl.read_parquet(STRAT / "artifacts/fills.parquet")
    nav = pl.read_parquet(STRAT / "nav/nav_series.parquet")
    exec_art = pl.read_parquet(STRAT / "artifacts/execution_artifact.parquet")

    picks = pick_samples(fills)
    print(f"抽笔 {len(picks)} 笔（event/code/side/filled）:")
    for p in picks:
        print(f"  e{p['event_index']} {p['code']:10s} {p['side']:4s} "
              f"filled={p['filled_quantity']:>7d} ref={p['reference_price']}")

    # ---- 查询 1：daily.open（6 个主键元组，单条限流）----
    keys = ",".join(
        f"('{p['code']}','{exec_art.filter(pl.col('event_index') == p['event_index'])['execution_date'][0]}')"
        for p in picks)
    q1 = ("SELECT ts_code, trade_date, open FROM factorlab.daily "
          f"WHERE (ts_code, trade_date) IN ({keys}) "
          "ORDER BY ts_code, trade_date")
    rows = ch(q1)
    time.sleep(0.2)
    ch_open = {(r[0], r[1]): float(r[2]) for r in rows}
    print(f"\nCH daily.open 返回 {len(rows)} 行")

    # ---- 查询 2：trade_cal 区间（decision→execution，验证 next open day）----
    d0 = min(str(exec_art["decision_date"][0]) for _ in [0])
    cal_rows = ch("SELECT cal_date, is_open FROM factorlab.trade_cal "
                  f"WHERE cal_date >= '{d0}' AND cal_date <= "
                  f"'{max(str(d) for d in nav['execution_date'])}' "
                  "ORDER BY cal_date")
    is_open = {r[0]: int(r[1]) for r in cal_rows}
    print(f"trade_cal 返回 {len(cal_rows)} 行")

    results = []
    ok_all = True
    for p in picks:
        e = p["event_index"]
        d = str(exec_art.filter(pl.col("event_index") == e)["execution_date"][0])
        dec = str(exec_art.filter(pl.col("event_index") == e)["decision_date"][0])
        got = ch_open.get((p["code"], d))
        exp_exec = (p["reference_price"] * (1.0 + slip) if p["side"] == "buy"
                    else p["reference_price"] * (1.0 - slip))
        price_ok = got is not None and abs(got - p["reference_price"]) <= 1e-12
        exec_ok = abs(exp_exec - p["execution_price"]) <= 1e-12
        # next open day：(dec, d) 内除 d 外无 is_open=1
        between = [c for c, o in is_open.items()
                   if dec < c < d and o == 1]
        next_ok = is_open.get(d) == 1 and not between
        ok_all &= price_ok and exec_ok and next_ok
        results.append({
            "event_index": e, "code": p["code"], "side": p["side"],
            "filled_quantity": p["filled_quantity"],
            "decision_date": dec, "execution_date": d,
            "reference_price": p["reference_price"],
            "ch_daily_open": got,
            "ref_vs_open_diff": None if got is None else got - p["reference_price"],
            "execution_price": p["execution_price"],
            "expected_execution_price": exp_exec,
            "price_ok": price_ok, "exec_ok": exec_ok, "next_open_ok": next_ok,
            "between_open_days": between,
        })
        print(f"  e{e} {p['code']:10s} {p['side']:4s} ref={p['reference_price']} "
              f"CH.open={got} Δ={None if got is None else got - p['reference_price']:.2e} "
              f"exec_formula={'OK' if exec_ok else 'FAIL'} "
              f"next_open={'OK' if next_ok else 'FAIL'}")

    out = {"spot_checks": results, "all_ok": bool(ok_all),
           "ch_queries": 2}
    Path(__file__).with_name("r08_ch_spotcheck.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nCH 抽笔对拍: {'PASS' if ok_all else 'FAIL'} "
          f"({sum(1 for r in results if r['price_ok'])}/{len(results)} price, "
          f"{sum(1 for r in results if r['next_open_ok'])}/{len(results)} "
          f"next-open)")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
