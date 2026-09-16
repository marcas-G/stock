"""Probe 5: cross_section (resIC) vs independent numpy recomputation + spec semantics."""
import datetime
import math

import numpy as np
import polars as pl

from factorlab.app.analysis.cross_section import (
    cs_r2, joint_diagnostics, orthogonalized_ic)

D0 = datetime.date(2024, 1, 5)


def rank_avg(x):
    o = np.argsort(x, kind="stable")
    r = np.empty(len(x))
    i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and x[o[j + 1]] == x[o[i]]:
            j += 1
        r[o[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spear(x, y):
    rx, ry = rank_avg(x), rank_avg(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def wide(series, fwd, weeks):
    n = next(iter(series.values())).shape[1]
    rows = []
    for w, d in enumerate(weeks):
        for s in range(n):
            row = {"date": d, "code": f"{s:06d}"}
            row.update({k: float(v[w, s]) for k, v in series.items()})
            row["forward_return_5d"] = float(fwd[w, s])
            rows.append(row)
    return pl.DataFrame(rows)


rng = np.random.default_rng(123)
W, N = 6, 40
weeks = [D0 + datetime.timedelta(weeks=w) for w in range(W)]
x1 = rng.normal(size=(W, N))
x2 = rng.normal(size=(W, N))
x3 = 0.5 * x1 + rng.normal(size=(W, N)) * 0.1       # near-collinear with x1
fwd = 0.3 * x1 + 0.2 * x2 + rng.normal(size=(W, N)) * 0.5

# ---------- cs_r2 independent ----------
print("=== cs_r2 independent check ===")
wk = wide({"x1": x1, "x2": x2}, fwd, weeks)
r_expr = cs_r2(wk, ["x1", "x2"])
ref_r2 = []
for w in range(W):
    Z = np.column_stack([np.ones(N), x1[w], x2[w]])
    y = fwd[w]
    beta, *_ = np.linalg.lstsq(Z, y, rcond=None)
    e = y - Z @ beta
    ref_r2.append(1 - float(e @ e) / float(((y - y.mean()) ** 2).sum()))
print("kernel mean:", r_expr["mean"], " indep mean:", float(np.mean(ref_r2)),
      " max abs weekly diff:",
      max(abs(a - b) for a, b in zip(r_expr["weekly"]["r2"].to_list(), ref_r2)))
print("n_weeks:", r_expr["n_weeks"], "obs:", r_expr["obs"])

# ---------- orthogonalized_ic independent ----------
print("\n=== orthogonalized_ic independent check (target x1, base [x2, x3]) ===")
wk3 = wide({"x1": x1, "x2": x2, "x3": x3}, fwd, weeks)
r_oi = orthogonalized_ic(wk3, "x1", ["x2", "x3"])
ref_resic = []
ref_absorbed = []
for w in range(W):
    Z = np.column_stack([np.ones(N), x2[w], x3[w]])
    y = x1[w]
    beta, *_ = np.linalg.lstsq(Z, y, rcond=None)
    e = y - Z @ beta
    sst = float(((y - y.mean()) ** 2).sum())
    ref_absorbed.append(1 - float(e @ e) / sst)
    ref_resic.append(spear(e, fwd[w]))
mean = float(np.mean(ref_resic))
std = float(np.std(ref_resic, ddof=1))
t = mean / (std / math.sqrt(len(ref_resic)))
print("kernel:", {k: r_oi[k] for k in ("mean", "std", "t_stat", "n_weeks", "obs", "r2_absorbed")})
print("indep :", {"mean": mean, "std": std, "t_stat": t,
                  "r2_absorbed": float(np.mean(ref_absorbed))})
print("weekly diff:", max(abs(a - b) for a, b in
                          zip(r_oi["weekly"]["resic"].to_list(), ref_resic)))

# ---------- base=[] consistency with average-rank spearman ----------
print("\n=== base=[] degenerates to plain rank IC (direct formula) ===")
r0 = orthogonalized_ic(wk, "x1", [], min_stocks=3)
ref0 = [spear(x1[w], fwd[w]) for w in range(W)]
print("kernel mean:", r0["mean"], " indep:", float(np.mean(ref0)))

# ---------- collinear detection threshold exponent ----------
print("\n=== residual-collinearity policy threshold ===")
# F = 1e-9 * X + eps independent noise: std(e)=1e-9*std(X)-ish; eps=1e-10*max(1,std(F))...
for delta in (1e-12, 1e-9, 1e-7):
    F = 1.0 * x2 + delta * rng.normal(size=(W, N))
    r = orthogonalized_ic(wide({"x": F}, fwd, weeks), "x", [], min_stocks=3)
    print(f"  perturbation delta={delta:g}: n_weeks={r['n_weeks']} mean={r['mean']:.3g} "
          f"r2_absorbed={r['r2_absorbed']:.6f}")

# ---------- joint mode: group computed on base not target ----------
import tempfile, pathlib
print("\n=== joint_diagnostics: group R2 excludes target in target mode ===")
with tempfile.TemporaryDirectory() as td:
    td = pathlib.Path(td)
    for name, mat in (("a", x1), ("b", x2), ("tgt", x3)):
        p = td / name
        p.mkdir()
        rows = [{"date": weeks[w], "code": f"{s:06d}", "signal": float(mat[w, s]),
                 "forward_return_5d": float(fwd[w, s])}
                for w in range(W) for s in range(N)]
        pl.DataFrame(rows).write_parquet(p / "panel.parquet")
    jd = joint_diagnostics(["a", "b"], td, target="tgt", min_stocks=30)
    wide_all = wide({"a": x1, "b": x2, "tgt": x3}, fwd, weeks)
    ref_group = cs_r2(wide_all, ["a", "b"])
    ref_target = orthogonalized_ic(wide_all, "tgt", ["a", "b"])
    print("group mean match:", abs(jd["group"]["mean"] - ref_group["mean"]) < 1e-12,
          " target factor mean match:",
          abs(jd["factors"][0]["mean"] - ref_target["mean"]) < 1e-12)
    print("mode:", jd["mode"], " factors:", [(f["name"], f["base"]) for f in jd["factors"]])

# ---------- empty-panel structural behavior ----------
print("\n=== empty / degenerate ===")
empty = pl.DataFrame(schema={"date": pl.Date, "code": pl.String,
                             "a": pl.Float64, "forward_return_5d": pl.Float64})
r_e = cs_r2(empty, ["a"])
r_o = orthogonalized_ic(empty, "a", [])
print("cs_r2 empty:", {k: r_e[k] for k in ("mean", "n_weeks")},
      "weekly rows:", r_e["weekly"].height)
print("oi empty:", {k: r_o[k] for k in ("mean", "std", "t_stat", "n_weeks")},
      "weekly rows:", r_o["weekly"].height)
