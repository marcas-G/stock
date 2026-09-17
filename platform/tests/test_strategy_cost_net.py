"""E3 成本后净值（策略层）：`factorlab.app.strategy.cost_net.cost_net_report`。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2b/§3（E3 属**策略层**交付，不写入因子评估 summary）+ 计划 Task 8。

口径（手算锚）：`net_t = gross_t − cost_rate × turnover_t`，成本后净值 =
`(1+net_t)` 连乘；`annual_return = mean(period_ret) × periods_per_year`，
因此 `net 年化 == gross 年化 − cost_rate × 年换手`（其中年换手 =
`mean(turnover) × periods_per_year`）。`cost_rate=0.0`（缺省）= 与 gross
**逐值一致**（零行为变化）。

禁止行为断言：
- **不进因子评估**：`evaluate_run` evaluation 不得出现成本后净值字段；
- 硬编码存根必败：换手/收益率逐值手算对上（含 net_nav 连乘）；
- 非法输入 fail loud。
"""
from __future__ import annotations

import datetime as dt
import math
import statistics

import polars as pl
import pytest

from factorlab.app.context import RunContext
from factorlab.app.evaluate import evaluate_run
from factorlab.app.strategy.cost_net import cost_net_report
from factorlab.core.engine.compute import FactorResult
from factorlab.core.spec import FactorSpec, UniverseSpec

_RET = [0.01, 0.02, 0.03, 0.04]
_TURN = [1.0, 0.0, 1.0, 0.0]
_COST = 0.0007


# ── 手算：net = gross − cost_rate×turnover；年化差 = cost_rate×年换手 ────────
def test_cost_net_hand_computed_returns_nav_and_annualization():
    r = cost_net_report(_RET, _TURN, cost_rate=_COST, periods_per_year=252)

    assert r["periods"] == 4
    assert r["cost_rate"] == pytest.approx(_COST)
    assert r["total_cost"] == pytest.approx(_COST * 2.0)
    assert r["annual_turnover"] == pytest.approx(0.5 * 252)
    # net_t 逐值手算
    assert r["net_returns"] == pytest.approx([0.0093, 0.02, 0.0293, 0.04], rel=1e-12)
    # net_nav = cumprod(1+net)
    nv = 1.0
    expected_nav = []
    for x in (0.0093, 0.02, 0.0293, 0.04):
        nv *= 1.0 + x
        expected_nav.append(nv)
    assert r["net_nav"] == pytest.approx(expected_nav, rel=1e-12)
    # 年化恒等式：net 年化 = gross 年化 − cost_rate × 年换手
    gross_annual = statistics.fmean(_RET) * 252
    assert r["gross"]["annual_return"] == pytest.approx(gross_annual, rel=1e-12)
    assert r["net"]["annual_return"] == pytest.approx(
        gross_annual - _COST * r["annual_turnover"], rel=1e-12)
    # 波动/夏普按期收益样本标准差（ddof=1）× √ppy
    net = [0.0093, 0.02, 0.0293, 0.04]
    assert r["net"]["annual_vol"] == pytest.approx(
        statistics.stdev(net) * math.sqrt(252), rel=1e-12)
    assert r["net"]["sharpe"] == pytest.approx(
        r["net"]["annual_return"] / r["net"]["annual_vol"], rel=1e-12)
    assert r["net"]["win_rate"] == pytest.approx(1.0)


def test_cost_rate_zero_is_identical_to_gross():
    z = cost_net_report(_RET, _TURN)                      # 缺省 0.0
    assert z["cost_rate"] == 0.0
    assert z["total_cost"] == 0.0
    assert z["net_returns"] == pytest.approx(_RET, rel=1e-15)
    assert z["net_nav"] == pytest.approx(z["gross_nav"], rel=1e-15)
    assert z["net"] == z["gross"]                         # 逐值一致（含全部摘要键）


def test_periods_per_year_is_explicit_and_scales():
    daily = cost_net_report(_RET, _TURN, cost_rate=_COST, periods_per_year=252)
    weekly = cost_net_report(_RET, _TURN, cost_rate=_COST, periods_per_year=52)
    assert weekly["periods_per_year"] == 52
    assert weekly["annual_turnover"] == pytest.approx(0.5 * 52)
    assert weekly["net"]["annual_return"] == pytest.approx(
        weekly["gross"]["annual_return"] - _COST * weekly["annual_turnover"],
        rel=1e-12)
    assert weekly["net"]["annual_return"] != pytest.approx(
        daily["net"]["annual_return"], rel=1e-3)


def test_empty_series_returns_empty_structure():
    r = cost_net_report([], [], cost_rate=_COST)
    assert r["periods"] == 0
    assert r["net_returns"] == [] and r["net_nav"] == []
    assert r["annual_turnover"] == 0.0 and r["total_cost"] == 0.0
    assert r["gross"] == {} and r["net"] == {}


@pytest.mark.parametrize("returns,turnover,cost_rate", [
    ([0.1], [0.5, 0.5], 0.0007),           # 长度不一致
    ([0.1, float("nan")], [0.5, 0.5], 0.0007),
    ([0.1, float("inf")], [0.5, 0.5], 0.0007),
    ([0.1, 0.2], [0.5, float("nan")], 0.0007),
    ([0.1, 0.2], [0.5, -0.1], 0.0007),     # 负换手
    ([0.1, 0.2], [0.5, 0.5], -0.0001),     # 负费率
    ([0.1, 0.2], [0.5, 0.5], 1.5),         # 费率 ≥ 100% 无意义
])
def test_cost_net_invalid_inputs_fail_loud(returns, turnover, cost_rate):
    with pytest.raises(ValueError):
        cost_net_report(returns, turnover, cost_rate=cost_rate)


# ── 禁止行为：E3 属策略层，不得写进因子评估 summary（D11/§2b）──────────────
def test_cost_net_not_in_factor_evaluation_summary():
    rows = []
    for d_i in range(6):
        date = dt.date(2024, 1, 2) + dt.timedelta(days=d_i)
        for s in range(5):
            rows.append({"date": date, "code": f"{s:06d}", "signal": float(s),
                         "forward_return_1d": float(s) * 0.01})
    panel = pl.DataFrame(rows)
    spec = FactorSpec(name="cost_purity", category="custom", direction=1,
                      universe=UniverseSpec(codes=["000001.SZ"]),
                      formula="signal = close")
    result = FactorResult(spec=spec, signal_artifact=None,
                          label_artifact=None, panel=panel)
    ev = evaluate_run(result, spec, RunContext()).evaluation
    keys = set(ev)
    if "outputs" in ev:
        for o in ev["outputs"].values():
            keys |= set(o)
    assert "cost_net" not in keys and "net_nav" not in keys, \
        f"因子评估 summary 出现成本后净值字段（E3 属策略层）：{sorted(keys)}"


def test_doc_contract_e3():
    """interface 必须写 E3 入口与成本口径（防漂移）。"""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "knowledge" / "contracts"
            / "interface.md").read_text(encoding="utf-8")
    assert "cost_net_report" in text, "interface 缺 E3 cost_net_report 入口"
    assert "cost_rate × turnover" in text or "cost_rate×turnover" in text, \
        "interface 缺 E3 成本口径 cost_rate×turnover"
    assert "不进因子评估" in text, "interface 缺「不进因子评估 summary」边界声明"
