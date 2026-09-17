"""D1=B：spread 口径 v2（正=好）+ `evaluation.version=2`（R30 Task 5）。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2 D1/D6 与 interface spread 节。手算合成面板：20 只 signal=0..19、fwd=signal×0.001；
average-rank 对称分位（n=20）→ group0={signal 0,1}、group9={signal 18,19}。

禁止行为断言：v2 spread 不是"翻转 direction 的旧值"，也不是常量——由手算值 + 旧公式
取负对照共同锁定；`version` 必须随结果走（空面板也不能少）。
"""
from __future__ import annotations

import datetime as dt
import json

import polars as pl
import pytest

import quant_core
from factorlab.adapters.rust_ic import evaluate_factor_weekly
from factorlab.app.context import RunContext
from factorlab.app.evaluate import evaluate_run, publish_run
from factorlab.core.engine.compute import FactorResult
from factorlab.core.spec import FactorSpec, UniverseSpec


def _monotone_panel(weeks=2, stocks=20):
    """signal 0..19 严格递增；fwd=signal×0.001 同周内单调正相关（无并列）。"""
    rows = []
    for w in range(weeks):
        d = dt.date(2024, 1, 5) + dt.timedelta(weeks=w)
        for s in range(stocks):
            rows.append({"date": d, "code": f"{s:06d}",
                         "signal": float(s), "forward_return_5d": float(s) * 0.001,
                         "forward_return_1d": float(s) * 0.001})
    return pl.DataFrame(rows)


def _kernel_args(df: pl.DataFrame):
    return (df["date"].dt.strftime("%Y-%m-%d").to_list(),
            df["code"].to_list(),
            df["signal"].to_list(),
            df["forward_return_5d"].to_list())


def _spec(name, outputs=None):
    return FactorSpec(name=name, category="custom", direction=1,
                      universe=UniverseSpec(codes=["000001.SZ"]),
                      formula="signal = close", outputs=outputs)


def test_spread_v2_positive_when_direction_consistent():
    """direction=+1 + 正相关 → v2 spread=(g9−g0)×dir > 0；旧 v1 公式值=新值取负。"""
    r = quant_core.evaluate_factor(*_kernel_args(_monotone_panel()), "_factor", 1)
    groups = [g["mean_ret"] for g in r["decile_returns"]["groups"]]
    g0, g9 = groups[0], groups[9]
    assert g0 == pytest.approx(0.0005)   # 手算：signal {0,1}
    assert g9 == pytest.approx(0.0185)   # 手算：signal {18,19}
    spread = r["decile_returns"]["spread"]["ret"]
    assert spread == pytest.approx(0.018)             # 手算 (g9−g0)×1
    assert spread > 0                                 # 正=好（v2 口径）
    assert spread == pytest.approx(-((g0 - g9) * 1))  # 旧 v1 值取负（翻转对照）


def test_spread_v2_direction_flip_still_negates():
    """direction 翻转仍翻转 spread（声明反向 → 负）；ic 统计不受方向影响。"""
    up = quant_core.evaluate_factor(*_kernel_args(_monotone_panel()), "_factor", 1)
    down = quant_core.evaluate_factor(*_kernel_args(_monotone_panel()), "_factor", -1)
    up_spread = up["decile_returns"]["spread"]["ret"]
    down_spread = down["decile_returns"]["spread"]["ret"]
    assert up_spread == pytest.approx(0.018)
    assert down_spread == pytest.approx(-0.018)
    assert up_spread == pytest.approx(-down_spread)
    assert up["ic"]["mean"] == pytest.approx(down["ic"]["mean"])


def test_kernel_version_is_2_even_for_empty_panel():
    """口径版本随结果恒在：有数据 v2；空面板（全 nan 结构）同样 v2。"""
    r = quant_core.evaluate_factor(*_kernel_args(_monotone_panel()), "_factor", 1)
    assert r["version"] == 2
    empty = quant_core.evaluate_factor([], [], [], [], "_factor", 1)
    assert empty["version"] == 2
    assert empty["n_weeks"] == 0


def test_evaluation_version_lands_in_summary_single_and_multi_output(tmp_path):
    """summary 的 evaluation 带 version=2（单输出顶层/多输出逐输出），经 publish 落盘。"""
    panel = _monotone_panel()
    spec = _spec("spread_v2")
    result = FactorResult(spec=spec, signal_artifact=None, label_artifact=None,
                          panel=panel)
    ctx = RunContext(output_dir=tmp_path / "single")
    outcome = evaluate_run(result, spec, ctx)
    assert outcome.evaluation["version"] == 2
    publish_run(result, outcome, ctx)
    summary = json.loads((tmp_path / "single" / "summary.json").read_text(encoding="utf-8"))
    assert summary["evaluation"]["version"] == 2

    panel_m = panel.with_columns(pl.col("signal").alias("sig_b"),
                                 (pl.col("signal") * -1).alias("sig_c"))
    spec_m = _spec("spread_v2_m", outputs=["sig_b", "sig_c"])
    result_m = FactorResult(spec=spec_m, signal_artifact=None, label_artifact=None,
                            panel=panel_m)
    ctx_m = RunContext(output_dir=tmp_path / "multi")
    outcome_m = evaluate_run(result_m, spec_m, ctx_m)
    assert outcome_m.evaluation["outputs"]["sig_b"]["version"] == 2
    assert outcome_m.evaluation["outputs"]["sig_c"]["version"] == 2
    publish_run(result_m, outcome_m, ctx_m)
    summary_m = json.loads((tmp_path / "multi" / "summary.json").read_text(encoding="utf-8"))
    for o in ("sig_b", "sig_c"):
        assert summary_m["evaluation"]["outputs"][o]["version"] == 2


def test_bridge_passthrough_keeps_version():
    """桥接层不得吞/改 version（quant_core → evaluate_factor_weekly → evaluation）。"""
    panel = _monotone_panel()
    ev = evaluate_factor_weekly(panel, "demo", 1)
    assert ev["version"] == 2
