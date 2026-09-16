"""Probe 6: Web detail page IC curve target — does it match the run's spec.target?"""
import datetime
import json
import re
from pathlib import Path

import polars as pl
from fastapi.testclient import TestClient

from factorlab.core.eval.ic_series import weekly_ic
from factorlab.surfaces.web.app import create_app

tmp = Path("/tmp/opencode/reviewer-eval/web_results")
import shutil
if tmp.exists():
    shutil.rmtree(tmp)
name = "f20"
d = tmp / name
d.mkdir(parents=True)

D0 = datetime.date(2024, 1, 5)
rows = []
import random
rng = random.Random(7)
for w in range(10):
    dt = D0 + datetime.timedelta(weeks=w)
    for s in range(30):
        sig = float(s)
        f5 = 0.1 * sig + rng.uniform(-0.2, 0.2)
        f20 = -0.1 * sig + rng.uniform(-0.2, 0.2)   # 20d: opposite sign!
        rows.append({"date": dt, "code": f"{s:06d}", "signal": sig,
                     "forward_return_5d": f5, "forward_return_20d": f20})
panel = pl.DataFrame(rows)
panel.write_parquet(d / "weekly.parquet")

ic5 = float(weekly_ic(panel)["ic"].mean())
ic20 = float(weekly_ic(panel, target="forward_return_20d")["ic"].mean())

summary = {
    "name": name, "category": "test", "direction": 1,
        "universe_count": 30, "date_start": "2024-01-05", "date_end": "2024-03-08",
        "panel_rows": 300, "signal_null_ratio": 0.0,
    "evaluation": {
        "factor": "_factor", "factor_name": name, "target": "forward_return_20d",
        "n_weeks": 10,
        "ic": {"mean": ic20, "std": 0.01, "t_stat": 20.0, "ir": 1.0, "n_weeks": 10,
               "recent_26w_mean": ic20, "recent_26w_t": 20.0, "sign_consistent": 0.0},
        "pearson_ic": {"mean": ic20, "t_stat": 20.0},
        "decile_returns": {"weighting": "equal_weight", "monotonic": False,
                           "spread": {"ret": 0.01},
                           "groups": [{"group": g, "mean_ret": 0.0} for g in range(10)]},
        "turnover": {"monthly": 0.1, "quarterly": 0.2},
        "coverage": {"pct_valid": 1.0, "total_rows": 300, "valid_rows": 300},
    },
}
(d / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

client = TestClient(create_app(tmp))
resp = client.get(f"/factor/{name}")
print("status:", resp.status_code)
# extract the ic chart figure JSON from the page
m = re.search(r'Plotly\.newPlot\("ic-chart",\s*(\{.*?\}\));', resp.text, re.S)
if not m:
    # fall back: find any plotly json containing "RankIC"
    idx = resp.text.find("RankIC")
    print("page contains 'RankIC':", idx >= 0)
    m = None
if m:
    fig, _end = json.JSONDecoder().raw_decode(resp.text[m.start(1):])
    y = fig["data"][0]["y"]
    print("chart y values:", y)
    print("weekly_ic 5d  mean :", ic5)
    print("weekly_ic 20d mean :", ic20)
    print("summary evaluation.ic.mean (20d):", summary["evaluation"]["ic"]["mean"])
    print("chart matches 5d :", abs(sum(y) / len(y) - ic5) < 1e-9)
    print("chart matches 20d:", abs(sum(y) / len(y) - ic20) < 1e-9)
else:
    # find embedded figure JSON alternatives
    for pat in ("Plotly.newPlot", "icData", "charts.ic", '"RankIC"'):
        print(pat, "present:", pat in resp.text)
    snippet = resp.text[:1500]
    print(snippet)
