"""Probe 8: sign conventions (kernel spread vs layered long_short), small-n deciles."""
import datetime
import random

import polars as pl

import quant_core
from factorlab.core.eval.alignment import align_weekly
from factorlab.core.eval.layered import layered_backtest

D0 = datetime.date(2024, 1, 5)
rng = random.Random(3)
rows = []
for w in range(4):
    d = D0 + datetime.timedelta(weeks=w)
    for s in range(10):
        sig = float(s)
        rows.append({"date": d, "code": f"{s:06d}", "signal": sig,
                     "forward_return_5d": 0.001 * s + rng.uniform(-0.005, 0.005)})
panel = pl.DataFrame(rows)
w = align_weekly(panel)
k = quant_core.evaluate_factor(w["date"].dt.strftime("%Y-%m-%d").to_list(),
                               w["code"].to_list(), w["signal"].to_list(),
                               w["forward_return_5d"].to_list(), "_factor", 1)
bt = layered_backtest(panel, direction=1, n_groups=10)
print("positive-IC panel, direction=1:")
print("  kernel spread.ret      :", k["decile_returns"]["spread"]["ret"])
print("  kernel groups g0/g9    :", k["decile_returns"]["groups"][0]["mean_ret"],
      k["decile_returns"]["groups"][9]["mean_ret"])
print("  layered D1/D10 last nv :", bt["net_values"]["D1"][-1], bt["net_values"]["D10"][-1])
print("  layered long_short last:", bt["net_values"]["long_short"][-1],
      "(opposite sign vs kernel spread)")
print("  kernel ic.mean         :", k["ic"]["mean"])

print("\nsmall cross-sections: empty decile groups -> spread NaN?")
for n in (5, 9, 10, 11):
    rows = []
    for w_i in range(3):
        d = D0 + datetime.timedelta(weeks=w_i)
        for s in range(n):
            rows.append({"date": d, "code": f"{s:06d}", "signal": float(s),
                         "forward_return_5d": 0.01 * s})
    p = pl.DataFrame(rows)
    kk = quant_core.evaluate_factor(
        p["date"].dt.strftime("%Y-%m-%d").to_list(), p["code"].to_list(),
        p["signal"].to_list(), p["forward_return_5d"].to_list(), "_factor", 1)
    groups = [g["mean_ret"] for g in kk["decile_returns"]["groups"]]
    nonempty = [i for i, v in enumerate(groups) if v == v]
    print(f"  n={n}: nonempty decile groups={nonempty} spread={kk['decile_returns']['spread']['ret']}")
