"""Probe 2: kernel vs independent recompute on adversarial cases:
ties (row-order sensitivity), degenerate weeks (t denominator), turnover bucket-last.
"""
import datetime
import math

import numpy as np
import polars as pl

import quant_core

NAN = float("nan")


def _rank_avg(xs):
    xs = np.asarray(xs, dtype=np.float64)
    order = np.argsort(xs, kind="stable")
    ranks = np.empty(len(xs))
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        ranks[order[i:j + 1]] = avg
        i = j + 1
    return ranks


def _rank_ordinal(xs):
    xs = np.asarray(xs, dtype=np.float64)
    order = np.argsort(xs, kind="stable")
    ranks = np.empty(len(xs))
    ranks[order] = np.arange(1, len(xs) + 1)
    return ranks


def _spear_avg(x, y):
    rx, ry = _rank_avg(x), _rank_avg(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return NAN
    return float(np.corrcoef(rx, ry)[0, 1])


def _spear_ord(x, y):
    rx, ry = _rank_ordinal(x), _rank_ordinal(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return NAN
    return float(np.corrcoef(rx, ry)[0, 1])


def run(rows, label="", direction=1):
    df = pl.DataFrame(rows)
    r = quant_core.evaluate_factor(
        df["date"].dt.strftime("%Y-%m-%d").to_list(), df["code"].to_list(),
        df["signal"].to_list(), df["fwd"].to_list(), "_factor", direction)
    return r


# ---------- (a) tie sensitivity: same multiset, shuffled row order ----------
print("=== (a) tie handling: decile groups under row-order shuffle ===")
base_rows = []
for s in range(10):
    # signal values with heavy ties: only 5 distinct values
    sig = [1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0, 5.0, 5.0][s]
    fwd = [0.01, 0.05, 0.02, 0.06, 0.03, 0.07, 0.04, 0.08, 0.05, 0.09][s]
    base_rows.append({"date": datetime.date(2024, 1, 5), "code": f"{s:06d}",
                      "signal": sig, "fwd": fwd})
r_a = run(base_rows, direction=1)
rows_rev = list(reversed(base_rows))
r_b = run(rows_rev, direction=1)
groups_a = [g["mean_ret"] for g in r_a["decile_returns"]["groups"]]
groups_b = [g["mean_ret"] for g in r_b["decile_returns"]["groups"]]
print("row order A groups:", groups_a)
print("row order B groups:", groups_b)
print("same? ", groups_a == groups_b)
print("spread A/B:", r_a["decile_returns"]["spread"]["ret"],
      r_b["decile_returns"]["spread"]["ret"])
# independent ordinal (input-order tied), and average-rank reference
sig = [x["signal"] for x in base_rows]; fwd = [x["fwd"] for x in base_rows]
print("indep ordinal-rank spread (avg-rank corr):", _spear_ord(sig, fwd))
print("weekly_ic-style average-rank corr (avg ranks):", _spear_avg(sig, fwd))
print("NOTE: kernel uses pl.corr(spearman) for IC (average ranks) but ordinal ranks for deciles.")

# ---------- (b) degenerate weeks: t denominator ----------
print("\n=== (b) degenerate weeks (constant fwd) vs t_stat denominator ===")
rows = []
dates = [datetime.date(2024, 1, 5) + datetime.timedelta(weeks=w) for w in range(5)]
# weeks 0,1,2: varying fwd (distinct ICs); weeks 3,4: constant fwd (degenerate)
import random as _rnd
for w, d in enumerate(dates):
    rngw = _rnd.Random(w)
    for s in range(10):
        fwd = float(s) * 0.01 + rngw.uniform(-0.01, 0.01) if w < 3 else 0.01
        rows.append({"date": d, "code": f"{s:06d}", "signal": float(s), "fwd": fwd})
r = run(rows, direction=1)
print("n_weeks:", r["n_weeks"], " ic.mean:", r["ic"]["mean"], " ic.std:", r["ic"]["std"],
      " t_stat:", r["ic"]["t_stat"])
ics = []
for d in dates[:3]:
    sub = [x for x in rows if x["date"] == d]
    ics.append(_spear_avg([x["signal"] for x in sub], [x["fwd"] for x in sub]))
mean = float(np.mean(ics)); std = float(np.std(ics, ddof=1))
print("independent: ok weeks ic =", ics, " mean/std:", mean, std)
print("t with denominator n_ok=3:", mean / (std / math.sqrt(3)))
print("t with denominator n_weeks=5:", mean / (std / math.sqrt(5)))
print("kernel t       :", r["ic"]["t_stat"])
print("sign_consistent: kernel", r["ic"]["sign_consistent"], " (count>0 / n_weeks; "
      "n_ok-based would be", sum(1 for x in ics if x > 0), "/ 3 =",
      sum(1 for x in ics if x > 0) / 3, ")")
# pearson too
print("pearson mean:", r["pearson_ic"]["mean"], " t:", r["pearson_ic"]["t_stat"])

# ---------- (c) turnover: bucket-last ordering ----------
print("\n=== (c) turnover: does bucket .last() pick the true last week? ===")
# 8 weeks => monthly window=4 computable. One stock (000000) oscillates between
# decile 0 and 9 between week index 2 and 3 (both in bucket 0). True last week
# of bucket 0 is week index 3; a wrong pick (week 2) flips its bucket assignment.
rows = []
for w in range(8):
    d = datetime.date(2024, 1, 5) + datetime.timedelta(weeks=w)
    for s in range(20):
        sig = float(s)
        if s == 0:
            sig = 0.0 if w % 2 == 0 else 100.0   # oscillates each week
        rows.append({"date": d, "code": f"{s:06d}", "signal": sig,
                     "fwd": float(s) * 0.001})
df = pl.DataFrame(rows)
r = run(rows, direction=1)
print("kernel turnover monthly:", r["turnover"]["monthly"])

# independent turnover per contract formula with explicit last-week selection
def indep_turnover(rows, window):
    dates = sorted({x["date"] for x in rows})
    if len(dates) < window * 2:
        return NAN
    # per week deciles (ordinal ranks clipped), 20 stocks -> 10 groups of 2
    dec = {}
    for d in dates:
        sub = [x for x in rows if x["date"] == d]
        sub_sorted = sorted(sub, key=lambda x: x["signal"])  # ordinal by value; no ties here
        n = len(sub_sorted)
        for rank, x in enumerate(sub_sorted, start=1):
            dec[(x["code"], d)] = min(9, rank * 10 // (n + 1))
    # bucket last week per code
    nbuckets = len(dates) // window
    changes = []
    per_code_last = {}
    for b in range(nbuckets):
        bdates = dates[b * window:(b + 1) * window]
        lastd = bdates[-1]
        for code in {x["code"] for x in rows}:
            if (code, lastd) in dec:
                per_code_last[(code, b)] = dec[(code, lastd)]
    for b in range(nbuckets - 1):
        common = [c for c in {x["code"] for x in rows}
                  if (c, b) in per_code_last and (c, b + 1) in per_code_last]
        if common:
            ch = sum(1 for c in common if per_code_last[(c, b)] != per_code_last[(c, b + 1)])
            changes.append(ch / len(common))
    return float(np.mean(changes)) if changes else NAN


print("independent turnover monthly (true last week):", indep_turnover(rows, 4))

# now force polars group_by ordering issue: check with the same data but shuffled row order
import random
rng = random.Random(0)
shuffled = list(rows)
rng.shuffle(shuffled)
r2 = run(shuffled, direction=1)
print("kernel turnover monthly under row shuffle:", r2["turnover"]["monthly"])
print("kernel turnover quarterly under row shuffle:", r2["turnover"]["quarterly"])

# ---------- (d) NaN rows in kernel ----------
print("\n=== (d) NaN handling in kernel ===")
rows = []
for w in range(3):
    d = datetime.date(2024, 1, 5) + datetime.timedelta(weeks=w)
    for s in range(10):
        rows.append({"date": d, "code": f"{s:06d}", "signal": float(s),
                     "fwd": float(s) * 0.01})
rows[0]["signal"] = NAN   # one NaN signal in week 0
rows[3]["fwd"] = NAN      # one NaN fwd in week 1
r = run(rows, direction=1)
print("coverage:", r["coverage"], " n_weeks:", r["n_weeks"])
print("ic.mean vs independent with NaN rows dropped and remaining rows per week:")
import copy
clean = [dict(x) for x in rows]
clean = [x for x in clean if np.isfinite(x["signal"]) and np.isfinite(x["fwd"])]
ics = []
for w in range(3):
    d = datetime.date(2024, 1, 5) + datetime.timedelta(weeks=w)
    sub = [x for x in clean if x["date"] == d]
    ics.append(_spear_avg([x["signal"] for x in sub], [x["fwd"] for x in sub]))
print("           independent NaN-dropped per-week ic:", ics,
      "mean:", float(np.mean(ics)))
