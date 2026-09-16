"""Probe 7: kernel _turnover bucket-last correctness + weekly_ic vs kernel on NaN."""
import datetime
import random

import numpy as np
import polars as pl

import quant_core
from factorlab.core.eval.ic_series import weekly_ic

D0 = datetime.date(2024, 1, 5)
NAN = float("nan")


# ---------- (1) _turnover bucket-last semantics ----------
print("=== (1) _turnover: uses correctly the LAST week of each bucket? ===")
# 8 weeks, 4 codes. Hand-set deciles: for code A, bucket0 weeks [3,0,0,2] -> last=2;
# bucket1 [2,1,1,0] -> last=0 => change A: 1. Others constant.
dec_by_code_week = {
    "A": [3, 0, 0, 2, 2, 1, 1, 0],   # last b0=2, last b1=0 -> changed
    "B": [1, 1, 1, 1, 1, 1, 1, 1],   # unchanged
    "C": [0, 2, 2, 2, 2, 2, 2, 2],   # last b0=2, last b1=2 -> unchanged
    "D": [4, 4, 4, 4, 4, 4, 4, 4],   # unchanged
}
rows = []
for w in range(8):
    d = D0 + datetime.timedelta(weeks=w)
    for c, decs in dec_by_code_week.items():
        rows.append({"date": d, "code": c, "_decile": decs[w], "signal": 0.0})
df = pl.DataFrame(rows).with_columns(pl.col("_decile").cast(pl.Int64))
print("sorted-order turnover:", quant_core._turnover(df, 4), " expected 0.25 (1 of 4 changed)")
rng = random.Random(0)
sh = rows[:]
rng.shuffle(sh)
df_sh = pl.DataFrame(sh).with_columns(pl.col("_decile").cast(pl.Int64))
print("shuffled-order turnover:", quant_core._turnover(df_sh, 4))

# also: first-week vs last-week pick (if .last() were .first())
first_last = {"A": [3, 0, 0, 2, 2, 1, 1, 0]}
# expected if picking FIRST week of bucket: b0 first=3, b1 first=2 -> changed too.
# construct case distinguishing first from last: A bucket0 [3,0,0,3] last=3; bucket1 [3,...] last=3
# if first-pick: b0=3,b1=2 -> change. Make explicit:
dec2 = {"A": [3, 0, 0, 3, 3, 2, 2, 3],  # last b0=3, last b1=3 -> no change; first would b0=3,b1=3 no change
        "B": [3, 0, 0, 3, 2, 2, 2, 2]}  # last b0=3, last b1=2 -> change
rows2 = []
for w in range(8):
    d = D0 + datetime.timedelta(weeks=w)
    for c, decs in dec2.items():
        rows2.append({"date": d, "code": c, "_decile": decs[w], "signal": 0.0})
df2 = pl.DataFrame(rows2).with_columns(pl.col("_decile").cast(pl.Int64))
print("distinguishing case (expected 0.5):", quant_core._turnover(df2, 4))

# ---------- (2) weekly_ic vs kernel on NaN rows ----------
print("\n=== (2) weekly_ic (Web curve) vs kernel (summary) with NaN rows ===")
rows = []
for w in range(4):
    d = D0 + datetime.timedelta(weeks=w)
    for s in range(12):
        rows.append({"date": d, "code": f"{s:06d}", "signal": float(s),
                     "forward_return_5d": float(s) * 0.1})
# inject NaNs: one in signal (week 0), one in fwd (week 1)
rows[0]["signal"] = NAN
rows[13]["forward_return_5d"] = NAN
df = pl.DataFrame(rows)
k = quant_core.evaluate_factor(
    df["date"].dt.strftime("%Y-%m-%d").to_list(), df["code"].to_list(),
    df["signal"].to_list(), df["forward_return_5d"].to_list(), "_factor", 1)
wic = weekly_ic(df)
wic_nan_filtered = weekly_ic(df.filter(
    pl.col("signal").is_finite() & pl.col("forward_return_5d").is_finite()))
print("kernel ic.mean/std:", k["ic"]["mean"], k["ic"]["std"], " n_weeks:", k["n_weeks"])
print("weekly_ic raw:      ", wic["ic"].to_list(),
      " mean:", wic["ic"].mean())
print("weekly_ic NaN-filt: ", wic_nan_filtered["ic"].to_list(),
      " mean:", wic_nan_filtered["ic"].mean())
print("Web curve (raw weekly_ic) vs summary:", 
      "DIVERGES" if abs((wic['ic'].mean() or float('nan')) - k['ic']['mean']) > 1e-9 else "same")

# ---------- (3) weekly_ic MIN_STOCKS 3 vs kernel 2 ----------
print("\n=== (3) 2-stock week: weekly_ic null vs kernel counts ===")
df2 = pl.DataFrame({
    "date": [D0] * 2, "code": ["a", "b"], "signal": [1.0, 2.0],
    "forward_return_5d": [0.1, 0.2]})
w = weekly_ic(df2)
k = quant_core.evaluate_factor(*[[D0.isoformat()] * 2, ["a", "b"], [1.0, 2.0],
                                 [0.1, 0.2]], "_factor", 1)
print("weekly_ic ic:", w["ic"].to_list(), " kernel n_weeks:", k["n_weeks"],
      " kernel ic.mean:", k["ic"]["mean"])

# ---------- (4) `weekly_ic` infinite values ----------
print("\n=== (4) inf signal: weekly_ic vs kernel ===")
df3 = pl.DataFrame({
    "date": [D0] * 4, "code": ["a", "b", "c", "d"],
    "signal": [1.0, 2.0, float("inf"), 4.0],
    "fwd": [0.1, 0.2, 0.3, 0.4]})
try:
    w3 = weekly_ic(df3)
    print("weekly_ic ic:", w3["ic"].to_list())
except Exception as e:
    print("weekly_ic raised:", type(e).__name__, e)
k3 = quant_core.evaluate_factor([D0.isoformat()] * 4, ["a", "b", "c", "d"],
                                [1.0, 2.0, float("inf"), 4.0], [0.1, 0.2, 0.3, 0.4],
                                "_factor", 1)
print("kernel coverage:", k3["coverage"], " n_weeks:", k3["n_weeks"], " ic:", k3["ic"]["mean"])
