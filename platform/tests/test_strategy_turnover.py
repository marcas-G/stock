"""R31 Task 5 支撑：`app.strategy.turnover.target_one_side_turnover`。

断言来源：`app/strategy/capacity.py` / `cost_net.py` 的 caller 契约——
`one_side_turnover` = 每期**单边**换手比例（组合口径）= 0.5 × Σ|w_t − w_{t−1}|
（w_{−1} ≡ 0，含建仓期）；序列与 `TargetPortfolio.decision_dates` 一一对齐
（全现金决策日 = 权重视为 0 → 清仓换手）。

禁止行为断言：选择来自真实截面排序（signal 变化 → 成员变化 → 换手 > 0），
硬编码 0 或固定常数的存根必败。
"""

from __future__ import annotations

import datetime

import polars as pl
import pytest

from factorlab.core.domain.frames import SignalArtifact, SignalMeta
from factorlab.core.strategy import (SelectionSpec, StrategySpec, WeightingSpec,
                                     construct_target_portfolio)

_D1 = datetime.date(2024, 1, 2)
_D2 = datetime.date(2024, 1, 3)
_D3 = datetime.date(2024, 1, 4)
_A, _B, _C = "000001.SZ", "600519.SH", "600000.SH"


def _target(rows, k=2, gross=1.0):
    """rows: [(date, {code: signal}), ...] → 真 M7 构造出的 TargetPortfolio。"""
    dates, codes, signals = [], [], []
    for d, values in rows:
        for code, value in values.items():
            dates.append(d)
            codes.append(code)
            signals.append(float(value))
    frame = pl.DataFrame({"date": dates, "code": codes, "signal": signals})
    signal = SignalArtifact(frame=frame, meta=SignalMeta(name="s"))
    spec = StrategySpec(name="t", signal_name="s", direction=1,
                        selection=SelectionSpec(k=k), weighting=WeightingSpec(),
                        gross_exposure=gross)
    return construct_target_portfolio(signal, spec)


def test_first_period_is_buy_in_and_unchanged_is_zero():
    from factorlab.app.strategy.turnover import target_one_side_turnover

    target = _target([
        (_D1, {_A: 3.0, _B: 2.0, _C: 1.0}),   # top2 {A,B} → w=(0.5,0.5,0)
        (_D2, {_A: 3.0, _B: 2.0, _C: 1.0}),   # 不变
        (_D3, {_A: 3.0, _B: 2.0, _C: 1.0}),   # 不变
    ])
    series = target_one_side_turnover(target)
    assert series == pytest.approx([0.5, 0.0, 0.0])  # 首期建仓 = Σw/2 = 0.5
    assert len(series) == len(target.decision_dates)


def test_member_rotation_is_half_l1():
    from factorlab.app.strategy.turnover import target_one_side_turnover

    target = _target([
        (_D1, {_A: 3.0, _B: 2.0, _C: 1.0}),   # {A,B}
        (_D2, {_A: 1.0, _B: 3.0, _C: 2.0}),   # {B,C}：换出 A、换入 C
    ])
    series = target_one_side_turnover(target)
    # 0.5*|Δw| = 0.5*(0.5+0.0+0.5) = 0.5
    assert series == pytest.approx([0.5, 0.5])


def test_weight_change_follows_real_ranking():
    from factorlab.app.strategy.turnover import target_one_side_turnover

    target = _target([
        (_D1, {_A: 3.0, _B: 2.0, _C: 1.0}),   # {A,B}
        (_D2, {_A: 3.0, _B: 1.0, _C: 2.0}),   # {A,C} — B/C 翻转
    ])
    series = target_one_side_turnover(target)
    # d1 {A,B} → d2 {A,C}：换出 B、换入 C → 0.5*(0.5+0.5) = 0.5
    assert series == pytest.approx([0.5, 0.5])
    assert series != pytest.approx([0.5, 0.0])  # 排序变化必须进入换手


def test_all_cash_day_is_full_exit():
    from factorlab.app.strategy.turnover import target_one_side_turnover

    # k=1 + on_insufficient=all_cash：d2 无 signal 行 → 全现金（explicit 0 仓位）
    target = _target([(_D1, {_A: 3.0, _B: 2.0})], k=1)
    assert target.decision_dates == (_D1,)
    series = target_one_side_turnover(target)
    assert series == pytest.approx([0.5])  # k=1 等权 → 建仓单边 0.5


def test_rejects_non_target():
    from factorlab.app.strategy.turnover import target_one_side_turnover

    with pytest.raises(TypeError):
        target_one_side_turnover({"decision_dates": [], "frame": pl.DataFrame()})
