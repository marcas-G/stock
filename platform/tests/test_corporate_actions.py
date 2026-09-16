"""R07-DATA-I8：CA share/cash transition primitive 单测（core/execution/
corporate_actions.py）。

语义（red 断言源 = 设计处置：除权日股数 × 因子 + 分红现金入账）：
- 现金分红 div_cash（元/10股）：cash += 资格日股数 × div_cash/10；股数不变。
- 送股/转增 div_bonus+div_transfer（股/10股）：quantity/sellable ×=
  1 + (b+t)/10，**不足 1 股向下取整（floor，V1 舍去近似）**；资格股数 =
  同事件调整前股数（现金与送转同事件用同一登记日持仓）。
- 配股 rights_num ≠ 0 → V1 策略 = **不参与**：shares/cash 不变 +
  CorporateActionWarning 记录在案（除权价格落差自然计入 NAV）。
- 多事件按 (code, trade_date) 顺序复合（送转后再分红用调整后股数）。
- 未支持/明细缺失（负分红/缩股/全 0 事件行/非有限值/非持仓 code）→
  ExecutionDataQualityError fail-closed——不得静默按无事件放行。

红态：primitive 不存在（ImportError）→ 全部失败。
"""

import datetime

import polars as pl
import pytest

from factorlab.core.domain.execution import (ExecutionDataQualityError,
                                        PortfolioState,
                                        PortfolioStatePhase)
from factorlab.core.execution.corporate_actions import (
    CorporateActionWarning, apply_corporate_actions)

_A = "000001.SZ"
_B = "600000.SH"
D1 = datetime.date(2024, 1, 2)
D2 = datetime.date(2024, 1, 3)

_DETAIL_COLUMNS = ["code", "trade_date", "div_cash", "div_bonus",
                   "div_transfer", "rights_num", "rights_price"]


def _state(holdings, cash=0.0, *, phase=PortfolioStatePhase.PRE_EXECUTION):
    """holdings: {code: (quantity, sellable)}；sparse + 稳定排序。"""
    rows = [(c, q, s) for c, (q, s) in sorted(holdings.items())]
    frame = pl.DataFrame(rows, schema=["code", "quantity", "sellable_quantity"],
                         orient="row")
    frame = frame.with_columns(pl.col("code").cast(pl.String),
                               pl.col("quantity").cast(pl.Int64),
                               pl.col("sellable_quantity").cast(pl.Int64))
    return PortfolioState(as_of_date=D1, phase=phase, cash=cash,
                          positions=frame)


def _events(rows):
    """rows: (code, date, div_cash, div_bonus, div_transfer, rights_num,
    rights_price)——None = NULL 明细（策略层按 0 处理）。"""
    frame = pl.DataFrame(rows, schema=_DETAIL_COLUMNS, orient="row")
    return frame.with_columns(
        pl.col("code").cast(pl.String), pl.col("trade_date").cast(pl.Date),
        *[pl.col(c).cast(pl.Float64) for c in _DETAIL_COLUMNS[2:]])


def _holds(state, code):
    r = state.positions.filter(pl.col("code") == code)
    return (int(r["quantity"][0]), int(r["sellable_quantity"][0])) \
        if r.height else None


# ================================================================
# 现金分红
# ================================================================

def test_cash_dividend_credits_cash_only():
    """10 派 2.5（元/10股）× 1000 股 → cash +250；股数/可卖数不变。"""
    s = _state({_A: (1000, 400)}, cash=5.0)
    out = apply_corporate_actions(s, _events([(_A, D1, 2.5, 0.0, 0.0, 0.0, 0.0)]))
    assert out.cash == 255.0
    assert _holds(out, _A) == (1000, 400)
    assert out.as_of_date == D1
    assert out.phase is PortfolioStatePhase.PRE_EXECUTION


def test_cash_dividend_uses_record_date_quantity():
    """资格股数 = 调整前股数（同事件先分红后送转不重复放大现金）。"""
    s = _state({_A: (1000, 1000)}, cash=0.0)
    out = apply_corporate_actions(
        s, _events([(_A, D1, 3.0, 2.0, 3.0, 0.0, 0.0)]))
    # 现金 = 1000 × 3/10（不是 1500 × 3/10）；股数 = 1000 × 1.5
    assert out.cash == 300.0
    assert _holds(out, _A) == (1500, 1500)


# ================================================================
# 送股/转增（含 floor 舍入锁）
# ================================================================

def test_bonus_transfer_scales_quantity_and_sellable():
    s = _state({_A: (1000, 400)}, cash=0.0)
    out = apply_corporate_actions(
        s, _events([(_A, D1, 0.0, 3.0, 2.0, 0.0, 0.0)]))
    assert _holds(out, _A) == (1500, 600)
    assert out.cash == 0.0


def test_fractional_shares_floored_not_rounded():
    """送转不足 1 股向下取整（V1 舍去）：
    - 101 × 1.35 = 136.35 → 136（若 round 会得 136——同一值故再加一例）
    - 105 × 1.15 = 120.75 → 120（round 会得 121）
    - 100 × 1.01 = 101.0 精确 → 101（float 朴素 floor 得 100 的精度陷阱锁定）
    """
    out = apply_corporate_actions(
        _state({_A: (105, 105)}), _events([(_A, D1, 0.0, 1.5, 0.0, 0.0, 0.0)]))
    assert _holds(out, _A) == (120, 120)
    out2 = apply_corporate_actions(
        _state({_A: (101, 101)}), _events([(_A, D1, 0.0, 3.5, 0.0, 0.0, 0.0)]))
    assert _holds(out2, _A) == (136, 136)
    out3 = apply_corporate_actions(
        _state({_A: (100, 100)}), _events([(_A, D1, 0.0, 0.1, 0.0, 0.0, 0.0)]))
    assert _holds(out3, _A) == (101, 101)


# ================================================================
# 多事件复合 / 多 code 独立
# ================================================================

def test_events_compound_in_date_order():
    """10 送 10 后再 10 派 1：现金按调整后 2000 股入账（若顺序错会得 100）。"""
    s = _state({_A: (1000, 1000)}, cash=0.0)
    out = apply_corporate_actions(s, _events([
        (_A, D1, 0.0, 10.0, 0.0, 0.0, 0.0),
        (_A, D2, 1.0, 0.0, 0.0, 0.0, 0.0)]))
    assert _holds(out, _A) == (2000, 2000)
    assert out.cash == 200.0


def test_multiple_codes_independent():
    s = _state({_A: (1000, 1000), _B: (500, 500)}, cash=0.0)
    out = apply_corporate_actions(s, _events([
        (_A, D1, 0.0, 3.0, 0.0, 0.0, 0.0),
        (_B, D1, 2.0, 0.0, 0.0, 0.0, 0.0)]))
    assert _holds(out, _A) == (1300, 1300)
    assert _holds(out, _B) == (500, 500)
    assert out.cash == 100.0


def test_empty_events_noop():
    s = _state({_A: (1000, 1000)}, cash=7.0)
    out = apply_corporate_actions(s, _events([]))
    assert out.cash == s.cash
    assert out.positions.equals(s.positions)
    assert out.as_of_date == s.as_of_date and out.phase is s.phase


# ================================================================
# 配股 = 不参与（V1 策略）+ warning 记录
# ================================================================

def test_rights_non_participation_warns_and_keeps_state():
    s = _state({_A: (1000, 1000)}, cash=0.0)
    with pytest.warns(CorporateActionWarning, match="配股"):
        out = apply_corporate_actions(
            s, _events([(_A, D1, 0.0, 0.0, 0.0, 3.0, 10.0)]))
    assert out.cash == s.cash
    assert out.positions.equals(s.positions)


def test_rights_with_supported_parts_still_applies_them():
    """配股与现金/送转同事件：配股不参与 warning，但现金/送转照常入账。"""
    s = _state({_A: (1000, 1000)}, cash=0.0)
    with pytest.warns(CorporateActionWarning, match="配股"):
        out = apply_corporate_actions(
            s, _events([(_A, D1, 1.0, 1.0, 0.0, 2.5, 6.5)]))
    assert _holds(out, _A) == (1100, 1100)
    assert out.cash == 100.0


# ================================================================
# fail-closed：未支持/明细缺失/非法输入
# ================================================================

def test_negative_cash_dividend_fail_closed():
    s = _state({_A: (1000, 1000)})
    with pytest.raises(ExecutionDataQualityError, match="div_cash"):
        apply_corporate_actions(s, _events([(_A, D1, -1.0, 0.0, 0.0, 0.0, 0.0)]))


def test_negative_bonus_fail_closed():
    """负送转 = 缩股（share-unit 缩减语义未支持）→ fail-closed。"""
    s = _state({_A: (1000, 1000)})
    with pytest.raises(ExecutionDataQualityError, match="送股|缩股"):
        apply_corporate_actions(s, _events([(_A, D1, 0.0, -2.0, -1.0, 0.0, 0.0)]))


def test_all_zero_detail_row_fail_closed():
    """adj_event 命中但明细全 0/NULL（两源不一致）→ fail-closed 不静默放行。"""
    s = _state({_A: (1000, 1000)})
    with pytest.raises(ExecutionDataQualityError, match="全.*0|不一致"):
        apply_corporate_actions(
            s, _events([(_A, D1, None, None, None, None, None)]))


def test_non_finite_detail_fail_closed():
    s = _state({_A: (1000, 1000)})
    with pytest.raises(ExecutionDataQualityError, match="finite|有限"):
        apply_corporate_actions(
            s, _events([(_A, D1, float("inf"), 0.0, 0.0, 0.0, 0.0)]))
    with pytest.raises(ExecutionDataQualityError, match="finite|有限"):
        apply_corporate_actions(
            s, _events([(_A, D1, float("nan"), 0.0, 0.0, 0.0, 0.0)]))


def test_event_code_not_held_fail_fast():
    """事件 code 不在 PRE 持仓（gate/loader 已过滤）→ 结构错误 fail fast
    （不静默忽略——静默忽略会把 gate scoping bug 藏起来）。"""
    s = _state({_A: (1000, 1000)})
    with pytest.raises(ValueError, match="持仓|position"):
        apply_corporate_actions(
            s, _events([(_B, D1, 1.0, 0.0, 0.0, 0.0, 0.0)]))


def test_phase_must_be_pre_execution():
    s = _state({_A: (1000, 1000)}, phase=PortfolioStatePhase.POST_EXECUTION)
    with pytest.raises(ValueError, match="PRE_EXECUTION"):
        apply_corporate_actions(s, _events([(_A, D1, 1.0, 0.0, 0.0, 0.0, 0.0)]))
