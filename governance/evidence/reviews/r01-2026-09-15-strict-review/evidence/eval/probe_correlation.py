"""Probe 3: correlation.py rank-corr correctness vs average-rank Spearman + NaN + tie cases."""
import random
import tempfile
from pathlib import Path

import numpy as np
import polars as pl

from factorlab.app.analysis.correlation import (
    MAX_JOINED_ROWS, WEEKLY_SAMPLE_STOCKS, factor_correlation, factor_svd, _join_panels)


def _rank_avg(xs):
    xs = np.asarray(xs, dtype=np.float64)
    order = np.argsort(xs, kind="stable")
    ranks = np.empty(len(xs))
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def spear_avg(x, y):
    rx, ry = _rank_avg(x), _rank_avg(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def write_panel(root, name, rows):
    p = Path(root) / name
    p.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(p / "panel.parquet")


def mkrows(dates, codes, sig, fwd=None, code_prefix=""):
    rows = []
    for d in dates:
        for i, c in enumerate(codes):
            row = {"date": d, "code": code_prefix + c, "signal": float(sig[i])}
            rows.append(row)
    return rows


with tempfile.TemporaryDirectory() as td:
    dates = [f"2024-01-{i:02d}" for i in range(1, 4)]
    codes = [f"{j:06d}" for j in range(1, 41)]  # 40 stocks/day >= 30

    # ---------- case 1: ties in both factors ----------
    # a = [1,1,2,2,...] blocks; b = pattern; compare to average-rank Spearman
    a_vals = [1, 1, 2, 2, 3, 3, 4, 4] * 5
    b_vals = ([1, 2, 1, 2, 3, 4, 3, 4] * 5)
    write_panel(td, "a", mkrows(dates, codes, a_vals))
    write_panel(td, "b", mkrows(dates, codes, b_vals))
    m = factor_correlation(["a", "b"], td)
    ref = spear_avg(a_vals, b_vals)
    print("=== case 1 ties ===")
    print("correlation.py rank_corr:", m["rank_corr"][0])
    print("average-rank Spearman  :", ref)
    print("MATCH:", abs(m["rank_corr"][0] - ref) < 1e-9)

    # ---------- case 2: NaN in one factor (real panels contain NaN) ----------
    a2 = [float(j) for j in range(40)]
    b2 = [float(j) for j in range(40)]
    b2[0] = float("nan"); b2[1] = float("nan")   # 2 NaN rows among 40
    b2[2] = 100.0; b2[3] = -100.0                # extreme outliers on the 2 NaN? no - on rows 2,3
    write_panel(td, "a2", mkrows(dates, codes, a2))
    write_panel(td, "b2", mkrows(dates, codes, b2))
    m2 = factor_correlation(["a2", "b2"], td)
    # correct reference: pairwise deletion
    am = np.array(a2); bm = np.array(b2)
    mask = ~(np.isnan(am) | np.isnan(bm))
    ref2 = spear_avg(am[mask], bm[mask])
    print("\n=== case 2 NaN rows ===")
    print("correlation.py rank_corr:", m2["rank_corr"][0], " n_weeks:", m2["n_weeks"][0])
    print("pairwise-deletion ref  :", ref2)
    print("MATCH:", abs(m2["rank_corr"][0] - ref2) < 1e-9)

    # ---------- case 3: week with only 2 valid pairs but >=30 rows ----------
    a3 = [float("nan")] * 38 + [1.0, 2.0]
    b3 = [float("nan")] * 38 + [1.0, 2.0]
    write_panel(td, "a3", mkrows(dates, codes, a3))
    write_panel(td, "b3", mkrows(dates, codes, b3))
    m3 = factor_correlation(["a3", "b3"], td)
    print("\n=== case 3 only 2 valid rows (30+ raw rows, 38 NaN) ===")
    print("rank_corr:", m3["rank_corr"][0], " pearson:", m3["pearson"][0],
          " n_weeks:", m3["n_weeks"][0])
    print("(per-week raw height = 40 >= 30 -> NOT skipped; truth undefined)")

    # ---------- case 4: 'weeks' are actually daily rows ----------
    # 10 trading days in 2 calendar weeks -> n_weeks should be 2 under ISO weekly,
    # code counts 10.
    dd = [f"2024-01-{i:02d}" for i in range(1, 11)]  # Jan 1..10 2024
    rows_a = [{"date": d, "code": f"{j:06d}", "signal": float(j)}
              for d in dd for j in range(40)]
    write_panel(td, "da", rows_a)
    write_panel(td, "db", [{"date": r["date"], "code": r["code"],
                            "signal": 2 * r["signal"]} for r in rows_a])
    m4 = factor_correlation(["da", "db"], td)
    print("\n=== case 4 daily vs weekly accounting ===")
    print("n_weeks reported:", m4["n_weeks"][0], "(10 daily dates -> counted as 10 'weeks')")

    # ---------- case 5: 20M guard sampling bias (monkeypatch constants) ----------
    import factorlab.app.analysis.correlation as corr
    old_rows, old_samp = corr.MAX_JOINED_ROWS, corr.WEEKLY_SAMPLE_STOCKS
    corr.MAX_JOINED_ROWS, corr.WEEKLY_SAMPLE_STOCKS = 10, 5
    try:
        # panel row order: sorted by code (like parquet written by pipeline?)
        codes_sorted = [f"{j:06d}" for j in range(20, 0, -1)]
        write_panel(td, "sa", mkrows(["2024-01-01"], codes_sorted, list(range(20, 0, -1))))
        write_panel(td, "sb", mkrows(["2024-01-01"], codes_sorted, list(range(1, 21))))
        joined = _join_panels(["sa", "sb"], Path(td))
        print("\n=== case 5 guard subsampling ===")
        print("joined height after >10-row guard (limit 5/week):", joined.height)
        print("codes kept:", joined["code"].to_list())
        print("(selection is first-in-frame-order, not random)")
    finally:
        corr.MAX_JOINED_ROWS, corr.WEEKLY_SAMPLE_STOCKS = old_rows, old_samp
