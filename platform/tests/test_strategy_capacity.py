"""E4 容量代理（策略层）：`factorlab.app.strategy.capacity.capacity_proxy`。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2b/§3（E3/E4 属**策略层**交付，不写入因子评估 summary）+ 计划 Task 9。

口径（手算锚）：`capacity = avg_amount × participation_rate / one_side_turnover`
——ADV（日均成交额，元）× 参与率上限 ÷ 单边换手 = 该换手水平下不超参与率的
可承载资金。`avg_amount` 由调用方从 daily 读面（`amount` 列均值）注入；
参与率/公式/单位随结果字段披露（可审计，不黑箱）。

禁止行为断言：
- **不进因子评估**：`evaluate_run` 的 evaluation 不得出现容量字段；
- 硬编码存根必败：不同 ADV/换手/参与率必须得到不同容量（手算值逐条对上）；
- 非法输入 fail loud（换手 ≤0 / 参与率越界 / 非有限值）。
"""
from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from factorlab.app.context import RunContext
from factorlab.app.evaluate import evaluate_run
from factorlab.app.strategy.capacity import capacity_proxy
from factorlab.core.engine.compute import FactorResult
from factorlab.core.spec import FactorSpec, UniverseSpec


# ── 手算：capacity = ADV × 参与率 / 单边换手 ────────────────────────────────
def test_capacity_hand_computed_and_disclosed():
    # ADV=1e8 元、单边换手 25%、参与率 10% → 1e8×0.1/0.25 = 4e7 元
    r = capacity_proxy(1e8, 0.25, participation_rate=0.1)
    assert r["capacity"] == pytest.approx(4e7, rel=1e-12)
    assert r["avg_amount"] == pytest.approx(1e8)
    assert r["one_side_turnover"] == pytest.approx(0.25)
    assert r["participation_rate"] == pytest.approx(0.1)
    assert r["unit"] == "元"
    assert "avg_amount" in r["formula"] and "participation_rate" in r["formula"] \
        and "one_side_turnover" in r["formula"]        # 公式/系数随字段披露（可审计）


def test_capacity_varies_with_each_input_not_stub():
    base = capacity_proxy(1e8, 0.25, participation_rate=0.1)["capacity"]
    # 换手翻倍 → 容量减半（1e8×0.1/0.5 = 2e7）
    assert capacity_proxy(1e8, 0.5, participation_rate=0.1)["capacity"] == \
        pytest.approx(2e7, rel=1e-12)
    # 参与率翻倍 → 容量翻倍（8e7）
    assert capacity_proxy(1e8, 0.25, participation_rate=0.2)["capacity"] == \
        pytest.approx(8e7, rel=1e-12)
    # ADV 变化 → 容量等比变化（2e8×0.1/0.25 = 8e7）
    assert capacity_proxy(2e8, 0.25, participation_rate=0.1)["capacity"] == \
        pytest.approx(8e7, rel=1e-12)
    assert base == pytest.approx(4e7, rel=1e-12)


def test_capacity_default_participation_rate_is_disclosed():
    r = capacity_proxy(1e8, 0.25)
    assert r["participation_rate"] > 0.0
    assert r["capacity"] == pytest.approx(
        r["avg_amount"] * r["participation_rate"] / r["one_side_turnover"], rel=1e-12)


@pytest.mark.parametrize("avg_amount,turnover,participation", [
    (0.0, 0.25, 0.1),          # 无成交 → 无容量依据（显式 0 而非伪造）
    (-1.0, 0.25, 0.1),         # 负 ADV
    (float("nan"), 0.25, 0.1),
    (1e8, 0.0, 0.1),           # 单边换手 0 → 公式除零，fail loud
    (1e8, -0.1, 0.1),
    (1e8, float("inf"), 0.1),
    (1e8, 0.25, 0.0),          # 参与率为 0 → 容量恒 0，非有效上限
    (1e8, 0.25, 1.5),          # 参与率 > 100%
])
def test_capacity_invalid_inputs_fail_loud(avg_amount, turnover, participation):
    with pytest.raises(ValueError):
        capacity_proxy(avg_amount, turnover, participation_rate=participation)


# ── 禁止行为：E4 属策略层，不得写进因子评估 summary（D11/§2b）──────────────
def test_capacity_not_in_factor_evaluation_summary():
    rows = []
    for d_i in range(6):
        date = dt.date(2024, 1, 2) + dt.timedelta(days=d_i)
        for s in range(5):
            rows.append({"date": date, "code": f"{s:06d}", "signal": float(s),
                         "forward_return_1d": float(s) * 0.01})
    panel = pl.DataFrame(rows)
    spec = FactorSpec(name="cap_purity", category="custom", direction=1,
                      universe=UniverseSpec(codes=["000001.SZ"]),
                      formula="signal = close")
    result = FactorResult(spec=spec, signal_artifact=None,
                          label_artifact=None, panel=panel)
    ev = evaluate_run(result, spec, RunContext()).evaluation
    flat_keys = set(ev)
    if "outputs" in ev:
        for o in ev["outputs"].values():
            flat_keys |= set(o)
    assert not any("capacity" in k for k in flat_keys), \
        f"因子评估 summary 出现容量字段（E4 属策略层）：{sorted(flat_keys)}"
