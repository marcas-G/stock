"""R03 评估链路 findings 最小复现（before/after 对照，同脚本两次运行）。

命令（仓库根）:
    platform/.venv/bin/python docs/verification/R22/R03/eval/probe_r03_eval.py

覆盖:
- R03-I2: 对齐后未过滤面板的 coverage（null 行计入 total、不计入 valid）
  ——修复前桥接层在过滤后调 kernel，pct_valid 恒 1.0（与 signal_null_ratio 矛盾）。
- R03-I3: tie-heavy 小整数信号 → 平均秩对称分位跳档 → decile groups 全期空档 NaN；
  分层 empty_groups=[]、notes=[] 无告警（CLI 只打印 spread=nan）。
- R03-M1: 误 import 平台宏（from polars_ta.prefix.wq import returns）→ expr_codegen
  exec 阶段深层裸堆栈；修复后应为 FactorDSLError("平台宏 ... 请裸用")。
"""
from __future__ import annotations

import datetime as dt
import traceback

import polars as pl

from factorlab.adapters.rust_ic import evaluate_factor_weekly
from factorlab.app.context import RunContext
from factorlab.app.evaluate import evaluate_run
from factorlab.core.engine.compute import FactorResult, compute_formula
from factorlab.core.spec import FactorSpec, UniverseSpec


def _panel_with_nulls(weeks=12, stocks=10):
    rows = []
    for w in range(weeks):
        d = dt.date(2024, 1, 5) + dt.timedelta(weeks=w)
        for s in range(stocks):
            rows.append({"date": d, "code": f"{s:06d}", "signal": float(s) + w * 0.1,
                         "forward_return_5d": 0.01 * s + 0.001 * w})
    rows[5]["signal"] = None                 # 周内一只停牌股
    rows[-1]["forward_return_5d"] = None     # 最后一周无未来收益
    return pl.DataFrame(rows)


def _tie_heavy_panel(weeks=4, n=100):
    """小整数计数信号：60% 股票 signal=0（平均秩同档 → 分位跳档）。"""
    rows = []
    for w in range(weeks):
        d = dt.date(2024, 1, 5) + dt.timedelta(weeks=w)
        for s in range(n):
            sig = 0.0 if s < 60 else (1.0 if s < 80 else (2.0 if s < 95 else 3.0))
            rows.append({"date": d, "code": f"{s:06d}", "signal": sig,
                         "forward_return_5d": 0.001 * sig + 1e-6 * s})
    return pl.DataFrame(rows)


def _spec(name="probe_r03"):
    return FactorSpec(
        name=name, category="custom", direction=1,
        universe=UniverseSpec(codes=["000001.SZ"]),
        formula="signal = close",
    )


def repro_i2():
    print("=== R03-I2: coverage vs signal_null_ratio ===")
    panel = _panel_with_nulls()
    null_ratio = panel["signal"].null_count() / panel.height
    weekly = panel.height  # 日频=周频（每周一条/股）
    res = evaluate_factor_weekly(panel, "probe", 1)
    print(f"panel rows={panel.height} signal_null_ratio={null_ratio:.4f}")
    print(f"coverage={res['coverage']}  (期望 total={weekly}, valid≈{panel.height - 2},"
          f" pct_valid≈{(panel.height - 2) / panel.height:.4f})")


def repro_i3():
    print("=== R03-I3: tie-heavy 信号分层静默 NaN ===")
    panel = _tie_heavy_panel()
    res = evaluate_factor_weekly(panel, "probe", 1)
    dr = res["decile_returns"]
    print("decile groups:")
    for g in dr["groups"]:
        print(f"  group={g['group']} mean_ret={g['mean_ret']}")
    print(f"spread={dr['spread']['ret']} monotonic={dr['monotonic']}")
    result = FactorResult(spec=_spec(), signal_artifact=None, label_artifact=None,
                          panel=panel)
    outcome = evaluate_run(result, _spec(), RunContext(db_path=None, output_dir=None))
    ev = outcome.evaluation
    print(f"layered empty_groups={ev['layered_backtest'].get('empty_groups')}")
    print(f"notes={outcome.notes}")
    print(f"degenerate_groups={ev['decile_returns'].get('degenerate_groups')}")


def repro_m1():
    print("=== R03-M1: 误 import 平台宏 ===")
    df = pl.DataFrame({"date": ["2020-01-01", "2020-01-02"], "code": ["A", "A"],
                       "close": [10.0, 12.0]})
    try:
        compute_formula(df, "from polars_ta.prefix.wq import returns\nsignal = returns(close)")
        print("compute_formula: 意外通过")
    except BaseException as exc:  # noqa: BLE001 - 复现要求打印原始异常形态
        print(f"compute_formula: {type(exc).__name__}: {exc}")
        tail = traceback.format_exc().strip().splitlines()[-3:]
        print("traceback tail:", *tail, sep="\n  ")


if __name__ == "__main__":
    repro_i2()
    print()
    repro_i3()
    print()
    repro_m1()
