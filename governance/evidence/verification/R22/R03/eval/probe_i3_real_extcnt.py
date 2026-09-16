"""R03-I3 after-fix 实证：真实归档面板（max_effect_20d_extcnt）走 evaluate_run。

命令（仓库根）:
    platform/.venv/bin/python docs/verification/R22/R03/eval/probe_i3_real_extcnt.py \
      2>&1 | tee docs/verification/R22/R03/eval/probe_i3_real_extcnt_after.txt

对照：修复前该因子 spread=NaN、empty_groups=[]、notes=[]（CLI 只打印 spread=nan）；
修复后 decile_returns.degenerate_groups 落盘 + notes 显著提示（CLI `run` 打印
`提示: ...`）。
"""
from __future__ import annotations

import polars as pl

from factorlab.app.context import RunContext
from factorlab.app.evaluate import evaluate_run
from factorlab.core.engine.compute import FactorResult
from factorlab.core.spec import FactorSpec, UniverseSpec

panel = pl.read_parquet("platform/results/max_effect_20d_extcnt/panel.parquet")
spec = FactorSpec(name="max_effect_20d_extcnt", category="custom", direction=-1,
                  universe=UniverseSpec(codes=["000001.SZ"]), formula="signal = close")
result = FactorResult(spec=spec, signal_artifact=None, label_artifact=None, panel=panel)
outcome = evaluate_run(result, spec, RunContext())
ev = outcome.evaluation

print("panel rows =", panel.height)
print("decile groups (group, mean_ret):")
for g in ev["decile_returns"]["groups"]:
    print(f"  {g['group']}: {g['mean_ret']}")
print("spread =", ev["decile_returns"]["spread"]["ret"])
print("layered empty_groups =", ev["layered_backtest"].get("empty_groups"))
print("degenerate_groups    =", ev["decile_returns"].get("degenerate_groups"))
print("notes:")
for n in outcome.notes:
    print("  提示:", n)
