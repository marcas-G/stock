"""D9 逐日评估口径（R30 Task 13）：`evaluation_frequency` daily 默认 / weekly 可选零变更。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2 D9（逐日：每日截面/每日调仓/每日 forward）+ D11（因子评估 forward 固定 1 日）+
interface 评估节（frequency/target/252 年化）。

合成面板**手算**：
- `_ic_panel`：6 交易日 × 5 股，signal=[0..4]；每日 forward_return_1d 为已知排列，
  Spearman ρ 按 `1−Σd²/20` 手算得 [1.0, 0.9, 0.0, −1.0, 0.9, −0.5]（mean=0.2167）。
- `_turnover_panel`：4 日 × 4 股，最佳档成员隔日全换 → D1 换手 [0,1,0,1]、
  组收益 [0.035,0.055,0.055,0.035] → 年化 = 0.045×252。

禁止行为断言：daily 路径不得调用 `align_weekly`（spy + weekly 正向对照证明 spy 非死）。
"""
from __future__ import annotations

import datetime as dt
import math
import statistics

import polars as pl
import pytest

from factorlab.adapters.rust_ic import evaluate_factor_weekly
from factorlab.app.context import RunContext
from factorlab.app.evaluate import evaluate_run
from factorlab.core.engine.compute import FactorResult
from factorlab.core.engine.forward import DEFAULT_FORWARD_HORIZONS, compute_forward_returns
from factorlab.core.eval.layered import layered_backtest
from factorlab.core.spec import FactorSpec, UniverseSpec

# 每日 forward_return_1d 的排列 → 与该日 signal 的已知 Spearman ρ（1−Σd²/20）
_DAILY_IC_RHOS = [1.0, 0.9, 0.0, -1.0, 0.9, -0.5]
_DAILY_PERMS = [
    [0, 1, 2, 3, 4],
    [1, 0, 2, 3, 4],
    [1, 2, 3, 4, 0],
    [4, 3, 2, 1, 0],
    [0, 1, 2, 4, 3],
    [2, 3, 4, 0, 1],
]


def _spec(name="daily_eval", *, evaluation_frequency="daily", target="forward_return_5d",
          outputs=None, cost_rate=0.0):
    return FactorSpec(name=name, category="custom", direction=1,
                      universe=UniverseSpec(codes=["000001.SZ"]),
                      formula="signal = close", outputs=outputs,
                      evaluation_frequency=evaluation_frequency,
                      target=target, cost_rate=cost_rate)


def _result(panel, spec):
    return FactorResult(spec=spec, signal_artifact=None, label_artifact=None, panel=panel)


def _ic_panel(days=6, stocks=5):
    rows = []
    for d_i, perm in enumerate(_DAILY_PERMS[:days]):
        date = dt.date(2024, 1, 2) + dt.timedelta(days=d_i)
        for s in range(stocks):
            rows.append({"date": date, "code": f"{s:06d}", "signal": float(s),
                         "forward_return_1d": float(perm[s]),
                         "forward_return_5d": float(perm[s]) * 0.01})
    return pl.DataFrame(rows)


def _turnover_panel():
    """4 日 × 4 股：signals 隔日反转 → 最佳档（D1，direction=1）成员隔日全换。"""
    signals = [[0, 1, 2, 3], [3, 2, 1, 0], [3, 2, 1, 0], [0, 1, 2, 3]]
    fwds = [[0.01, 0.02, 0.03, 0.04], [0.05, 0.06, 0.07, 0.08],
            [0.05, 0.06, 0.07, 0.08], [0.01, 0.02, 0.03, 0.04]]
    rows = []
    for d_i in range(4):
        date = dt.date(2024, 1, 2) + dt.timedelta(days=d_i)
        for s in range(4):
            rows.append({"date": date, "code": f"{s:06d}",
                         "signal": float(signals[d_i][s]),
                         "forward_return_1d": fwds[d_i][s]})
    return pl.DataFrame(rows)


# ── D9：逐日 IC（每个交易日一个截面；评估期数=交易日数）────────────────────
def test_daily_ic_per_trading_day_and_stats():
    panel = _ic_panel()
    spec = _spec()
    outcome = evaluate_run(_result(panel, spec), spec, RunContext())
    ev = outcome.evaluation

    assert ev["frequency"] == "daily"
    assert ev["target"] == "forward_return_1d"
    assert ev["version"] == 2
    assert ev["n_weeks"] == 6                       # 日期数=交易日数（不是周数）
    assert ev["n_stocks_avg"] == pytest.approx(5.0)

    mean = statistics.fmean(_DAILY_IC_RHOS)
    std = statistics.stdev(_DAILY_IC_RHOS)
    assert ev["ic"]["mean"] == pytest.approx(mean, abs=1e-9)
    assert ev["ic"]["std"] == pytest.approx(std, abs=1e-9)
    assert ev["ic"]["t_stat"] == pytest.approx(mean / (std / math.sqrt(6)), abs=1e-9)
    assert ev["ic"]["n_weeks"] == 6


# ── D9：每日调仓 layered——组收益=当日 fwd 均值、净值 cumprod、年化 ×252 ────
def test_daily_layered_returns_nav_turnover_and_annualization_252():
    panel = _turnover_panel()
    spec = _spec(cost_rate=0.001)
    outcome = evaluate_run(_result(panel, spec), spec, RunContext(), groups=2)
    ev = outcome.evaluation
    bt = ev["layered_backtest"]

    assert bt["periods"] == ev["n_weeks"] == 4
    # 日频换手 = 1 − |S_t∩S_{t−1}|/|S_t|（D1 成员隔日全换 → [0,1,0,1]）
    assert bt["turnover"]["D1"] == pytest.approx([0.0, 1.0, 0.0, 1.0])
    # 组收益 = 当日该档 fwd 均值；净值 = 日频 cumprod
    assert bt["net_values"]["D1"] == pytest.approx(
        [1.035, 1.035 * 1.054, 1.035 * 1.054 * 1.055,
         1.035 * 1.054 * 1.055 * 1.034], rel=1e-6)
    # 年化 ×252（成本后：net_t = gross_t − 0.001×turnover_t）
    mean_net = statistics.fmean([0.035, 0.054, 0.055, 0.034])
    assert bt["summary"]["D1"]["annual_return"] == pytest.approx(mean_net * 252, rel=1e-6)
    assert bt["cost_rate"] == pytest.approx(0.001)

    # 反向对照：同一面板走周频年化系数（52）必须得到不同值——证明 252 是 daily 分支传入
    weekly_coef = layered_backtest(panel, 1, n_groups=2,
                                   forward_col="forward_return_1d")["summary"]["D1"]
    assert weekly_coef["annual_return"] == pytest.approx(
        statistics.fmean([0.035, 0.055, 0.055, 0.035]) * 52, rel=1e-6)
    assert weekly_coef["annual_return"] != pytest.approx(
        bt["summary"]["D1"]["annual_return"], rel=1e-3)


# ── 禁止行为断言：daily 路径不得调用 align_weekly ──────────────────────────
def test_daily_path_never_calls_align_weekly(monkeypatch):
    calls = []

    def spy(*args, **kwargs):
        calls.append(1)
        raise AssertionError("daily 路径不得调用 align_weekly")

    monkeypatch.setattr("factorlab.app.evaluate.align_weekly", spy, raising=True)
    monkeypatch.setattr("factorlab.adapters.rust_ic.align_weekly", spy, raising=True)

    panel = _ic_panel()
    spec = _spec()
    outcome = evaluate_run(_result(panel, spec), spec, RunContext())
    assert outcome.evaluation["frequency"] == "daily"
    assert calls == []                              # daily：零调用

    # 正向对照：weekly 路径确实会调用（证明 spy 能抓——不是死断言）
    spec_w = _spec(evaluation_frequency="weekly")
    with pytest.raises(AssertionError, match="不得调用"):
        evaluate_run(_result(panel, spec_w), spec_w, RunContext())
    assert calls                                  # weekly：有调用


# ── weekly 可选对照：target 用 spec.target、对齐到周、逐值=直接桥接 ────────
def test_weekly_frequency_aligns_and_uses_spec_target():
    rows = []
    for w, monday in enumerate((dt.date(2024, 1, 2), dt.date(2024, 1, 9))):
        for d in (monday, monday + dt.timedelta(days=2)):
            for s in range(20):
                rows.append({"date": d, "code": f"{s:06d}", "signal": float(s),
                             "forward_return_5d": float(s) * 0.01 + w * 0.001})
    panel = pl.DataFrame(rows)
    spec = _spec(evaluation_frequency="weekly")
    outcome = evaluate_run(_result(panel, spec), spec, RunContext())
    ev = outcome.evaluation

    assert ev["frequency"] == "weekly"
    assert ev["target"] == "forward_return_5d"
    assert ev["n_weeks"] == 2                       # 2 个 ISO 周
    direct = evaluate_factor_weekly(panel, spec.name, spec.direction)
    assert ev["ic"]["mean"] == pytest.approx(direct["ic"]["mean"])
    assert ev["decile_returns"]["spread"]["ret"] == pytest.approx(
        direct["decile_returns"]["spread"]["ret"])


# ── forward_return_1d 标签：默认 horizons 含 1、total_return 公式手算 ──────
def test_default_forward_horizons_include_1d_and_total_return_value():
    assert DEFAULT_FORWARD_HORIZONS == (1, 5, 20)
    df = pl.DataFrame({
        "date": [dt.date(2024, 1, 1), dt.date(2024, 1, 2), dt.date(2024, 1, 3)] * 2,
        "code": ["A"] * 3 + ["B"] * 3,
        "close": [10.0, 11.0, 12.0, 20.0, 19.0, 21.0],
        "adj_factor": [1.0, 1.0, 1.0, 1.0, 1.5, 1.5],
    }).sort(["code", "date"])
    out = compute_forward_returns(df)
    assert "forward_return_1d" in out.columns
    a = out.filter(pl.col("code") == "A").sort("date")
    assert a["forward_return_1d"][0] == pytest.approx(11.0 / 10.0 - 1.0)
    assert a["forward_return_1d"][1] == pytest.approx(12.0 / 11.0 - 1.0)
    assert a["forward_return_1d"][2] is None
    b = out.filter(pl.col("code") == "B").sort("date")
    assert b["forward_return_1d"][0] == pytest.approx(
        19.0 * 1.5 / (20.0 * 1.0) - 1.0)            # 含分红再投资（adj 变动）


# ── spec 字段默认 + 缺列 fail loud + 多输出 ────────────────────────────────
def test_spec_evaluation_frequency_defaults_daily():
    assert _spec().evaluation_frequency == "daily"
    assert _spec(evaluation_frequency="weekly").evaluation_frequency == "weekly"


def test_daily_missing_forward_return_1d_fails_loud():
    panel = _ic_panel().drop("forward_return_1d")
    spec = _spec()
    with pytest.raises(ValueError, match="forward_return_1d"):
        evaluate_run(_result(panel, spec), spec, RunContext())


def test_daily_multi_output_per_output_uses_1d_target():
    panel = _ic_panel().with_columns(
        (pl.col("signal") * -1).alias("sig_neg"))
    spec = _spec(name="daily_multi", outputs=["signal", "sig_neg"])
    outcome = evaluate_run(_result(panel, spec), spec, RunContext())
    ev = outcome.evaluation
    assert ev["frequency"] == "daily"
    for o in ("signal", "sig_neg"):
        assert ev["outputs"][o]["target"] == "forward_return_1d"
        assert ev["outputs"][o]["frequency"] == "daily"
        assert ev["outputs"][o]["n_weeks"] == 6
