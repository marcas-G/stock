"""R03-I2 after-fix 实证：真实归档weekly 面板（max_effect_20d_extcnt）。

命令（仓库根）:
    platform/.venv/bin/python docs/verification/R22/R03/eval/probe_i2_real_extcnt.py \
      2>&1 | tee docs/verification/R22/R03/eval/probe_i2_real_extcnt_after.txt

对照：修复前 run 落盘的 summary.evaluation.coverage（pct_valid=1.0，total=过滤后 896750）
vs 修复后桥接口径（total=对齐后未过滤 934236，pct_valid 与 signal_null_ratio 可对账）。
"""
from __future__ import annotations

import json

import polars as pl

from factorlab.adapters.rust_ic import evaluate_factor_weekly

w = pl.read_parquet("platform/results/max_effect_20d_extcnt/weekly.parquet")
assert w.schema["date"] == pl.Date, w.schema
panel = w.select(["date", "code", "signal", "forward_return_5d"])
r = evaluate_factor_weekly(panel, "max_effect_20d_extcnt", -1, weekly=panel)
summary = json.load(open("platform/results/max_effect_20d_extcnt/summary.json"))

print("weekly rows        =", panel.height)
print("weekly signal_null_ratio =", round(panel["signal"].null_count() / panel.height, 4))
print("coverage (after)   =", r["coverage"])
print("coverage (summary, before-fix run) =", summary["evaluation"]["coverage"])
print("daily signal_null_ratio (summary)  =", summary["signal_null_ratio"])
