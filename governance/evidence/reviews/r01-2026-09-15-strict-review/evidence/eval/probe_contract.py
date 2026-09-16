"""Probe 1: contract §4 vectors vs shim + independent numpy recomputation.

Independent stat helpers deliberately avoid polars ranking/corr paths:
- _rank: average ranks implemented by hand (sort + grouping)
- _spearman: Pearson on average ranks
"""
import datetime
import math

import numpy as np
import polars as pl

import quant_core

NAN = float("nan")


def _rank(xs):
    xs = np.asarray(xs, dtype=np.float64)
    order = np.argsort(xs, kind="stable")
    ranks = np.empty(len(xs), dtype=np.float64)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(x, y):
    rx, ry = _rank(x), _rank(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return NAN
    return float(np.corrcoef(rx, ry)[0, 1])


def _pearson(x, y):
    if np.std(x) == 0 or np.std(y) == 0:
        return NAN
    return float(np.corrcoef(x, y)[0, 1])


def build_vector(filter_weeks_1based=()):
    rows, weeks = [], []
    for w in range(12):
        d = datetime.date(2024, 1, 5) + datetime.timedelta(weeks=w)
        weeks.append(d)
        for s in range(10):
            signal = 10 * (w + 1) + (s + 1)
            fwd = 0.1 * signal + 0.01 * w * (s % 3)
            if (w + 1) in filter_weeks_1based:
                signal = NAN
            rows.append({"date": d, "code": f"{s:06d}", "signal": signal, "fwd": fwd})
    return rows


def indep_full(dates, codes, signals, fwd, direction=1):
    """Independent recompute straight from contract §3.1 (average-rank spearman)."""
    arr = np.array(signals)
    fwd = np.array(fwd)
    valid = np.isfinite(arr) & np.isfinite(fwd)
    df_dates = np.array(dates)
    weeks = sorted(set(df_dates[valid]))
    ics, pears, nstocks = [], [], []
    deciles_by_week = {}
    n_degenerate = 0
    for d in weeks:
        m = valid & (df_dates == d)
        sig, f = arr[m], fwd[m]
        if len(sig) < 2:
            continue
        nstocks.append(len(sig))
        ic = _spearman(sig, f)
        if ic == ic:
            ics.append(ic)
        else:
            n_degenerate += 1
        pr = _pearson(sig, f)
        if pr == pr:
            pears.append(pr)
        # ordinal-rank decile per contract (ordinal, not average)
        order = np.argsort(sig, kind="stable")
        ordr = np.empty(len(sig))
        ordr[order] = np.arange(1, len(sig) + 1)
        dec = np.clip(ordr * 10 // (len(sig) + 1), 0, 9).astype(int)
        rets = {}
        for g in range(10):
            sel = dec == g
            if sel.any():
                rets[g] = float(f[sel].mean())
        deciles_by_week[d] = rets
    n_weeks = len(nstocks)
    out = {"n_weeks": n_weeks, "n_valid": int(valid.sum()), "total": len(arr),
           "n_degenerate": n_degenerate}
    if ics:
        out["ic_mean"] = float(np.mean(ics))
        out["ic_std_ddof1"] = float(np.std(ics, ddof=1)) if len(ics) > 1 else NAN
        out["ic_t_nweeks"] = (out["ic_mean"] / (out["ic_std_ddof1"] / math.sqrt(n_weeks))
                              if len(ics) > 1 and out["ic_std_ddof1"] > 0 else NAN)
        out["sign_consistent_nweeks"] = sum(1 for x in ics if x > 0) / n_weeks
        out["sign_consistent_nok"] = sum(1 for x in ics if x > 0) / len(ics)
    gmeans = {}
    for g in range(10):
        vals = [deciles_by_week[d][g] for d in deciles_by_week if g in deciles_by_week[d]]
        gmeans[g] = float(np.mean(vals)) if vals else NAN
    out["groups"] = [gmeans[g] for g in range(10)]
    out["spread"] = ((gmeans[0] - gmeans[9]) * direction
                     if gmeans[0] == gmeans[0] and gmeans[9] == gmeans[9] else NAN)
    return out


def shim(rows):
    df = pl.DataFrame(rows)
    return quant_core.evaluate_factor(
        df["date"].dt.strftime("%Y-%m-%d").to_list(), df["code"].to_list(),
        df["signal"].to_list(), df["fwd"].to_list(), "_factor", 1)


# ---------------- vector 1 ----------------
r1 = shim(build_vector())
exp1 = {
    "n_weeks": 12, "n_stocks_avg": 10.0,
    "ic.mean": 0.9810571308693253, "ic.std": 0.018368207210560795,
    "ic.t_stat": 185.01977643375477, "ic.ir": 53.41060886471636,
    "ic.recent_26w_mean": 0.9810571308693253, "ic.recent_26w_t": 185.01977643375477,
    "ic.sign_consistent": 1.0,
    "pearson_ic.mean": 0.9837347038396591, "pearson_ic.t_stat": 220.15672375517144,
    "spread": -0.8999999999999995, "turnover.monthly": 0.0,
    "coverage.pct_valid": 1.0, "coverage.valid_rows": 120,
}
got1 = {
    "n_weeks": r1["n_weeks"], "n_stocks_avg": r1["n_stocks_avg"],
    "ic.mean": r1["ic"]["mean"], "ic.std": r1["ic"]["std"],
    "ic.t_stat": r1["ic"]["t_stat"], "ic.ir": r1["ic"]["ir"],
    "ic.recent_26w_mean": r1["ic"]["recent_26w_mean"],
    "ic.recent_26w_t": r1["ic"]["recent_26w_t"],
    "ic.sign_consistent": r1["ic"]["sign_consistent"],
    "pearson_ic.mean": r1["pearson_ic"]["mean"], "pearson_ic.t_stat": r1["pearson_ic"]["t_stat"],
    "spread": r1["decile_returns"]["spread"]["ret"],
    "turnover.monthly": r1["turnover"]["monthly"],
    "coverage.pct_valid": r1["coverage"]["pct_valid"],
    "coverage.valid_rows": r1["coverage"]["valid_rows"],
}
print("### vector1 field-by-field vs §4.3")
for k, exp in exp1.items():
    got = got1[k]
    ok = (got == got and abs(got - exp) <= 1e-9) if exp == exp else got != got
    print(f"  {'FAIL' if not ok else 'ok  '} {k:24s} expected={exp!r:24} got={got!r:24}")
groups1_exp = [6.6000000000000005, 6.755, 6.91, 6.900000000000001, 7.055,
               7.210000000000001, 7.2, 7.355, 7.510000000000001, 7.5]
groups1_got = [g["mean_ret"] for g in r1["decile_returns"]["groups"]]
print("  groups match:", all(abs(a - b) <= 1e-9 for a, b in zip(groups1_exp, groups1_got)),
      groups1_got)

# independent recompute of vector 1
df1 = pl.DataFrame(build_vector())
ind1 = indep_full(df1["date"].dt.strftime("%Y-%m-%d").to_list(), df1["code"].to_list(),
                  df1["signal"].to_list(), df1["fwd"].to_list())
print("independent vector1: ic.mean", ind1["ic_mean"], "std", ind1["ic_std_ddof1"],
      "t(n_weeks)", ind1["ic_t_nweeks"], "sign", ind1["sign_consistent_nweeks"],
      "spread", ind1["spread"])
print("independent groups:",
      [round(v, 10) if v == v else None for v in ind1["groups"]])
print("shim        groups:",
      [round(g["mean_ret"], 10) if g["mean_ret"] == g["mean_ret"] else None
       for g in r1["decile_returns"]["groups"]])

# ---------------- vector 2: two readings ----------------
for label, fw, exp_ic, exp_pearson in (
        ("1-based {3,7} -> NaN on w1,w6 (2024-01-12/02-16)", (3, 7),
         0.9809049206795543, 0.9827829304187394),
        ("0-based {3,7} -> NaN on w3,w7 (2024-01-26/02-23)", (4, 8),
         0.9820824, None)):
    r2 = shim(build_vector(fw))
    print(f"\n### vector2 {label}")
    print(f"    n_weeks={r2['n_weeks']} ic.mean={r2['ic']['mean']!r} "
          f"pearson.mean={r2['pearson_ic']['mean']!r} coverage={r2['coverage']}")
    print(f"    documented: n_weeks=10 ic.mean={exp_ic!r} "
          f"pearson.mean={exp_pearson!r} coverage pct_valid=0.8333")
    ok_ic = abs(r2["ic"]["mean"] - exp_ic) <= 1e-9
    ok_p = exp_pearson is None or abs(r2["pearson_ic"]["mean"] - exp_pearson) <= 1e-9
    print(f"    match: n_weeks={r2['n_weeks'] == 10} ic={ok_ic} pearson={ok_p}")

# ---------------- vector 3 ----------------
r3 = quant_core.evaluate_factor([], [], [], [], "_factor", 1)
print("\n### vector3 empty: ic/pearson/turnover all-NaN:",
      all(v != v for v in r3["ic"].values()),
      "pearson", all(v != v for v in r3["pearson_ic"].values()),
      "spread", r3["decile_returns"]["spread"]["ret"] != r3["decile_returns"]["spread"]["ret"],
      "groups", r3["decile_returns"]["groups"],
      "turnover", r3["turnover"],
      "coverage", r3["coverage"])
