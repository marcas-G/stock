"""Plan S Task 6：L5 规则层 V1（研究侧近似）测试。

规格：`docs/reviews/2026-09-16-strategy-decomposition/plan.md` Task 6——
- `max_hold`：目标组合历史中连续持有超 N 个交易日的 code 在调仓日被强制换出
  （调仓日粒度近似，非成交明细级）；
- `max_hold=None`：输出与输入 target 逐值相同（零行为变化）；
- `stop_loss`/`take_profit` 非 null：明确 NotImplementedError（含平台化另立指引）。

断言逐值（frame 全量相等/逐行 code+weight+日期）+ 边界（恰 N 日不换）+ 数据
依赖（同输入不同 max_hold 产生不同输出——硬编码返回必败）。
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import polars as pl
import pytest

_STRATEGIES = Path(__file__).resolve().parents[1]
if str(_STRATEGIES) not in sys.path:
    sys.path.insert(0, str(_STRATEGIES))

pytest.importorskip("factorlab.core.domain.portfolio", reason="需平台 venv（factorlab）")

from factorlab.core.domain.portfolio import (TargetPortfolio,
                                             TargetPortfolioMeta)  # noqa: E402
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING  # noqa: E402
from factorlab.core.strategy.doc import RulesSpec  # noqa: E402

import l5_rules as L5  # noqa: E402

_A, _B, _C = "000001.SZ", "600519.SH", "600000.SH"


def _weekdays(start: datetime.date, n: int) -> list[datetime.date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


_DAYS = _weekdays(datetime.date(2024, 1, 1), 16)
_D0, _D1, _D2, _D3 = _DAYS[0], _DAYS[4], _DAYS[8], _DAYS[12]


def _target(days_to_rows: dict[datetime.date, list[tuple[str, float]]],
            decision_dates: tuple[datetime.date, ...]) -> TargetPortfolio:
    rows = [(d, c, w) for d, kv in days_to_rows.items() for c, w in kv]
    rows.sort(key=lambda r: (r[0], r[1]))
    frame = pl.DataFrame(
        rows,
        schema={"decision_date": pl.Date, "code": pl.String,
                "target_weight": pl.Float64},
        orient="row")
    meta = TargetPortfolioMeta(
        strategy_name="l5_test", source_signal_name="sig",
        source_timing=DEFAULT_EOD_SIGNAL_TIMING, gross_exposure=1.0,
        frequency="1d", rebalance_frequency="weekly")
    return TargetPortfolio(frame=frame, decision_dates=decision_dates, meta=meta)


def _expected(rows: list[tuple[datetime.date, str, float]],
              decision_dates: tuple[datetime.date, ...]) -> TargetPortfolio:
    frame = pl.DataFrame(
        rows,
        schema={"decision_date": pl.Date, "code": pl.String,
                "target_weight": pl.Float64},
        orient="row")
    meta = TargetPortfolioMeta(
        strategy_name="l5_test", source_signal_name="sig",
        source_timing=DEFAULT_EOD_SIGNAL_TIMING, gross_exposure=1.0,
        frequency="1d", rebalance_frequency="weekly")
    return TargetPortfolio(frame=frame, decision_dates=decision_dates, meta=meta)


# ================================================================
# 1. 强制换出：超限 code 在调仓日被剔除（逐值）
# ================================================================

def test_max_hold_excludes_stale_code_and_renormalizes():
    """max_hold=5：A 自 d0 连续持有，d2（age=8>5）被换出；C 留存并再归一为 1.0。

    输出全帧逐值断言：d0 {A,B} / d1 {A,C} 原样；d2 只剩 C 且权重 1.0
    （被换出槽位不留现金缺口，剩余按 gross 再归一——TargetPortfolio 不变量）。
    """
    target = _target({
        _D0: [(_A, 0.5), (_B, 0.5)],
        _D1: [(_A, 0.5), (_C, 0.5)],
        _D2: [(_A, 0.5), (_C, 0.5)],
    }, (_D0, _D1, _D2))
    out = L5.apply_max_hold(target, 5, _DAYS)
    expected = _expected([
        (_D0, _A, 0.5), (_D0, _B, 0.5),
        (_D1, _A, 0.5), (_D1, _C, 0.5),
        (_D2, _C, 1.0),                       # A 换出；C 再归一
    ], (_D0, _D1, _D2))
    assert out.frame.equals(expected.frame)
    assert out.decision_dates == (_D0, _D1, _D2)
    assert out.meta == target.meta


def test_max_hold_full_exclusion_is_all_cash_then_reentry():
    """max_hold=5：{A,B} 连续持有到 d2（age=8）全换出（0 rows 显式 all-cash），
    d3 重新进入（连续持有重新计数，age=0）。"""
    target = _target({d: [(_A, 0.5), (_B, 0.5)] for d in (_D0, _D1, _D2, _D3)},
                     (_D0, _D1, _D2, _D3))
    out = L5.apply_max_hold(target, 5, _DAYS)
    expected = _expected([
        (_D0, _A, 0.5), (_D0, _B, 0.5),
        (_D1, _A, 0.5), (_D1, _B, 0.5),
        (_D3, _A, 0.5), (_D3, _B, 0.5),
    ], (_D0, _D1, _D2, _D3))
    assert out.frame.equals(expected.frame)
    assert out.decision_dates == (_D0, _D1, _D2, _D3)
    assert out.frame.filter(pl.col("decision_date") == _D2).height == 0


def test_age_exactly_max_hold_kept_boundary():
    """恰 max_hold 交易日不换出（只有严格超过才换）：max_hold=8 时 d2 保留 A。"""
    target = _target({
        _D0: [(_A, 0.5), (_B, 0.5)],
        _D1: [(_A, 0.5), (_C, 0.5)],
        _D2: [(_A, 0.5), (_C, 0.5)],
    }, (_D0, _D1, _D2))
    out = L5.apply_max_hold(target, 8, _DAYS)
    assert out.frame.equals(target.frame)     # age(A@d2)=8，不换
    assert out.meta == target.meta


def test_output_depends_on_max_hold_value_not_hardcoded():
    """同输入不同 max_hold → 不同输出（证明规则真实计算，非硬编码返回）。"""
    target = _target({
        _D0: [(_A, 0.5), (_B, 0.5)],
        _D1: [(_A, 0.5), (_C, 0.5)],
        _D2: [(_A, 0.5), (_C, 0.5)],
    }, (_D0, _D1, _D2))
    out5 = L5.apply_max_hold(target, 5, _DAYS)
    out100 = L5.apply_max_hold(target, 100, _DAYS)
    assert not out5.frame.equals(out100.frame)
    assert out100.frame.equals(target.frame)  # 不超限 → 零行为变化
    # 换出真实发生：d2 的 A 行消失且不是简单 remove（C 权重被再归一）
    assert out5.frame.filter(
        (pl.col("decision_date") == _D2) & (pl.col("code") == _A)).height == 0
    assert out5.frame.filter(
        (pl.col("decision_date") == _D2) & (pl.col("code") == _C)
    )["target_weight"][0] == pytest.approx(1.0)


# ================================================================
# 2. max_hold=None：恒等（零行为变化）
# ================================================================

def test_max_hold_none_returns_input_identity():
    target = _target({_D0: [(_A, 0.5), (_B, 0.5)]}, (_D0,))
    out = L5.apply_l5_rules(target, RulesSpec(), _DAYS)
    assert out is target
    assert out.frame.equals(target.frame)
    assert out.decision_dates == target.decision_dates


def test_dispatcher_applies_max_hold_from_rules_spec():
    target = _target({
        _D0: [(_A, 0.5), (_B, 0.5)],
        _D1: [(_A, 0.5), (_C, 0.5)],
        _D2: [(_A, 0.5), (_C, 0.5)],
    }, (_D0, _D1, _D2))
    out = L5.apply_l5_rules(target, RulesSpec(max_hold=5), _DAYS)
    assert out.frame.filter(
        (pl.col("decision_date") == _D2) & (pl.col("code") == _A)).height == 0


# ================================================================
# 3. 未实现规则：明确 NotImplementedError（平台化另立指引）
# ================================================================

@pytest.mark.parametrize("rules", [
    RulesSpec(stop_loss=0.1), RulesSpec(take_profit=0.2),
])
def test_stop_loss_take_profit_not_implemented(rules):
    target = _target({_D0: [(_A, 0.5), (_B, 0.5)]}, (_D0,))
    with pytest.raises(NotImplementedError) as ei:
        L5.apply_l5_rules(target, rules, _DAYS)
    msg = str(ei.value)
    assert "平台化" in msg and ("stop_loss" in msg or "take_profit" in msg)
