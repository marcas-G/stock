"""Probe 4: layered_backtest vs kernel n_weeks; NaN rows; cost alignment; join order."""
import datetime
import random

import numpy as np
import polars as pl

import quant_core
from factorlab.core.eval.alignment import align_weekly
from factorlab.core.eval.layered import layered_backtest

NAN = float("nan")
D0 = datetime.date(2024, 1, 5)


def kern(panel, direction=1):
    w = align_weekly(panel)
    w = w.filter(pl.col("signal").is_not_null() & pl.col("forward_return_5d").is_not_null())
    return quant_core.evaluate_factor(
        w["date"].dt.strftime("%Y-%m-%d").to_list(), w["code"].to_list(),
        w["signal"].to_list(), w["forward_return_5d"].to_list(), "_factor", direction)


# ---------- (1) periods vs n_weeks: 1-stock week ----------
print("=== (1) one-valid-stock week: bt.periods vs kernel.n_weeks ===")
rows = []
for w in range(2):
    d = D0 + datetime.timedelta(weeks=w)
    n = 5 if w == 0 else 1
    for s in range(n):
        rows.append({"date": d, "code": f"{s:06d}", "signal": float(s + 1),
                     "forward_return_5d": 0.01 * (s + 1)})
panel = pl.DataFrame(rows)
bt = layered_backtest(panel, 1)
k = kern(panel)
print("bt periods:", bt["periods"], " kernel n_weeks:", k["n_weeks"],
      " dates(bt):", len(bt["dates"]), " net_values D1 len:", len(bt["net_values"]["D1"]))

# ---------- (2) NaN rows: kernel drops (is_finite), layered keeps ----------
print("\n=== (2) NaN signal / fwd rows ===")
rows = []
for w in range(3):
    d = D0 + datetime.timedelta(weeks=w)
    for s in range(10):
        sig = float(s)
        fwd = float(s) * 0.01
        if w == 1 and s == 0:
            sig = NAN
        if w == 2 and s == 0:
            fwd = NAN
        rows.append({"date": d, "code": f"{s:06d}", "signal": sig, "forward_return_5d": fwd})
panel = pl.DataFrame(rows)
bt = layered_backtest(panel, 1)
k = kern(panel)
print("bt periods:", bt["periods"], " kernel n_weeks:", k["n_weeks"])
print("bt D1 net last:", bt["net_values"]["D1"][-1], " D10 net last:", bt["net_values"]["D10"][-1])
print("kernel spread:", k["decile_returns"]["spread"]["ret"], " coverage:", k["coverage"])
print("NaN signal rank (polars):",
      panel.filter(pl.col("date") == D0 + datetime.timedelta(weeks=1))
      .with_columns(pl.col("signal").rank("ordinal", descending=True).alias("r"))
      .select("code", "signal", "r").head(3).to_dicts())
print("NaN fwd group mean (polars mean over 10 with one NaN):",
      panel.filter(pl.col("date") == D0 + datetime.timedelta(weeks=2))
      .group_by("date").agg(pl.col("forward_return_5d").mean()).to_dicts())

# ---------- (3) cost alignment vs manual computation ----------
print("\n=== (3) cost alignment: net = gross - rate*turnover ===")
rows = []
members = [("A1", "A2"), ("B1", "B2"), ("C1", "C2"), ("D1", "D2")]
for w, codes in enumerate(members):
    d = D0 + datetime.timedelta(weeks=w)
    for i, c in enumerate(codes):
        rows.append({"date": d, "code": c, "signal": 1.0 - i * 0.1,
                     "forward_return_5d": 0.02 if c.startswith("A") else 0.01})
panel = pl.DataFrame(rows)
bt_free = layered_backtest(panel, 1, n_groups=1)
bt_cost = layered_backtest(panel, 1, n_groups=1, cost_rate=0.5)
print("turnover D1:", bt_cost["turnover"]["D1"])
print("gross returns D1:", [round(v, 6) for v in
      bt_free["net_values"]["D1"]])
print("costed net D1:", [round(v, 6) for v in bt_cost["net_values"]["D1"]])
# manual: ret_t = gross_t - 0.5*turnover_t
gross = [0.02, 0.01, 0.01, 0.01]  # group D1 (n_groups=1 -> all stocks)
turn = bt_cost["turnover"]["D1"]
manual = 1.0
for r, t in zip(gross, turn):
    manual *= (1.0 + r - 0.5 * t)
print("manual net last:", round(manual, 8),
      "kernel:", round(bt_cost["net_values"]["D1"][-1], 8))

# ---------- (4) join order: sparse group (empty weeks) ----------
print("\n=== (4) right-join order with an empty middle week for one group ===")
rows = []
for w in range(4):
    d = D0 + datetime.timedelta(weeks=w)
    if w != 1:  # week 1 has no stock in the top group
        rows.append({"date": d, "code": "TOP", "signal": 10.0, "forward_return_5d": 0.10})
    rows.append({"date": d, "code": "LOW", "signal": 0.0, "forward_return_5d": -0.05})
panel = pl.DataFrame(rows)
bt = layered_backtest(panel, 1, n_groups=2)
print("turnover D1:", bt["turnover"]["D1"])
print("D1 net:", [round(v, 6) for v in bt["net_values"]["D1"]])
print("D2 net:", [round(v, 6) for v in bt["net_values"]["D2"]])

# ---------- (5) summary long_short drawdown semantics ----------
print("\n=== (5) long_short summary when the spread only loses ===")
rows = []
for w in range(3):
    d = D0 + datetime.timedelta(weeks=w)
    rows.append({"date": d, "code": "LOW", "signal": 0.0, "forward_return_5d": 0.05})
    rows.append({"date": d, "code": "TOP", "signal": 1.0, "forward_return_5d": -0.02})
panel = pl.DataFrame(rows)
bt = layered_backtest(panel, 1, n_groups=2)
print("D1:", bt["net_values"]["D1"], "D2:", bt["net_values"]["D2"])
print("long_short:", bt["net_values"]["long_short"])
print("summary long_short:", bt["summary"]["long_short"])
