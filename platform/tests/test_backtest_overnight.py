"""M8-04E：overnight T+1 inventory release——POST_EXECUTION(D) →
PRE_EXECUTION(next trade_cal open day)。

release 数量 = 当天 FillBatch 中 same-day BUY 的 filled_quantity
（provenance-aware——严禁 sellable=quantity 全量释放，因为 PortfolioState
不记录不可卖库存的原因）。calendar authority = trading_calendar（唯一）。

库测试（trade_cal 单表）双腿参数化（env：duckdb|ch，见 tests/conftest.py）；
source audit 测试不触库，保持单腿。
"""

import datetime
import inspect
import re

import polars as pl
import pytest

from factorlab.core.domain import (FillBatch, PortfolioState, PortfolioStatePhase)
from factorlab.core.domain.timing import ExecutionTiming
from factorlab.app.backtest import advance_to_next_trading_day

# 2024-01-05 Friday open；01-06/07 closed；01-08 Monday open
FRI = datetime.date(2024, 1, 5)
MON = datetime.date(2024, 1, 8)
TUE = datetime.date(2024, 1, 9)

_CAL_COLS = [("cal_date", "date"), ("is_open", "i64")]


def _seed_cal(env, opens):
    """opens: list of (date, is_open)。"""
    env.seed({"trade_cal": (_CAL_COLS,
                            [(d.strftime("%Y%m%d"), int(o)) for d, o in opens])})


def _default_cal(env):
    # 额外 trailing open（TUE）保证 FRI 之后有 next
    _seed_cal(env, [(FRI, 1), (datetime.date(2024, 1, 6), 0),
                    (datetime.date(2024, 1, 7), 0), (MON, 1), (TUE, 1)])


def _state(cash, positions, as_of=FRI, phase=PortfolioStatePhase.POST_EXECUTION):
    frame = pl.DataFrame(positions, schema=["code", "quantity",
                                            "sellable_quantity"], orient="row")
    frame = frame.with_columns(pl.col("code").cast(pl.String),
                               pl.col("quantity").cast(pl.Int64),
                               pl.col("sellable_quantity").cast(pl.Int64))
    if frame.height:
        frame = frame.sort("code")
    return PortfolioState(as_of_date=as_of, phase=phase, cash=float(cash),
                          positions=frame)


def _fills(rows, exec_date=FRI, timing=ExecutionTiming.NEXT_OPEN):
    frame = pl.DataFrame(rows, schema=["code", "side", "order_quantity",
                                       "filled_quantity", "reference_price",
                                       "execution_price", "gross_notional",
                                       "commission", "stamp_tax",
                                       "transfer_fee", "total_fees",
                                       "effective_cash_delta"], orient="row")
    frame = frame.with_columns(pl.col("code").cast(pl.String),
                               pl.col("side").cast(pl.String),
                               pl.col("order_quantity").cast(pl.Int64),
                               pl.col("filled_quantity").cast(pl.Int64),
                               pl.col("reference_price").cast(pl.Float64),
                               pl.col("execution_price").cast(pl.Float64),
                               pl.col("gross_notional").cast(pl.Float64),
                               pl.col("commission").cast(pl.Float64),
                               pl.col("stamp_tax").cast(pl.Float64),
                               pl.col("transfer_fee").cast(pl.Float64),
                               pl.col("total_fees").cast(pl.Float64),
                               pl.col("effective_cash_delta").cast(pl.Float64))
    if frame.height:
        frame = frame.sort("code")
    return FillBatch(decision_date=datetime.date(2024, 1, 4),
                     execution_date=exec_date, execution_timing=timing,
                     frame=frame)


def _buy(code, filled, ordered=None):
    ordered = ordered if ordered is not None else filled
    gross = 10.0 * filled
    return (code, "buy", ordered, filled, 10.0, 10.0, gross, 0.0, 0.0, 0.0,
            0.0, -gross)


def _sell(code, filled):
    gross = 10.0 * filled
    return (code, "sell", filled, filled, 10.0, 10.0, gross, 0.0, 0.0, 0.0,
            0.0, gross)


def _row(st, code):
    f = st.positions.filter(pl.col("code") == code)
    if f.height == 0:
        return None
    return f.row(0)


# ================================================================
# AC-01..18：API / calendar
# ================================================================

def test_api_exists():
    assert callable(advance_to_next_trading_day)


def test_type_guards(env):
    _default_cal(env)
    with pytest.raises(TypeError, match="state"):
        advance_to_next_trading_day({"x": 1}, _fills([]), env.rd)
    with pytest.raises(TypeError, match="fills"):
        advance_to_next_trading_day(_state(0.0, []), {"f": 1}, env.rd)
    with pytest.raises(TypeError, match="rd|读句柄"):
        advance_to_next_trading_day(_state(0.0, []), _fills([]), str(env.rd))


def test_pre_input_fails(env):
    st = _state(0.0, [], phase=PortfolioStatePhase.PRE_EXECUTION)
    _default_cal(env)
    with pytest.raises(ValueError, match="POST_EXECUTION"):
        advance_to_next_trading_day(st, _fills([]), env.rd)


def test_wrong_fill_date_fails(env):
    st = _state(0.0, [])
    f = _fills([], exec_date=MON)
    _default_cal(env)
    with pytest.raises(ValueError, match="as_of_date|execution_date"):
        advance_to_next_trading_day(st, f, env.rd)


def test_next_close_not_implemented(env):
    st = _state(0.0, [])
    f = _fills([], timing=ExecutionTiming.NEXT_CLOSE)
    _default_cal(env)
    with pytest.raises(NotImplementedError, match="next_close|NEXT_CLOSE"):
        advance_to_next_trading_day(st, f, env.rd)


def test_weekend_skip(env):
    _default_cal(env)
    nxt = advance_to_next_trading_day(_state(0.0, []), _fills([]), env.rd)
    assert nxt.as_of_date == MON           # Friday → Monday（跳过周六日）


def test_holiday_skip(env):
    """D open、D+1/2 closed、D+3 open → D+3。"""
    D = datetime.date(2024, 1, 9)          # Tuesday
    D3 = datetime.date(2024, 1, 12)        # Friday（周三四 closed）
    _seed_cal(env, [(D, 1), (datetime.date(2024, 1, 10), 0),
                    (datetime.date(2024, 1, 11), 0), (D3, 1)])
    st = _state(0.0, [], as_of=D)
    nxt = advance_to_next_trading_day(st, _fills([], exec_date=D), env.rd)
    assert nxt.as_of_date == D3


def test_current_date_must_be_open(env):
    _seed_cal(env, [(FRI, 0), (MON, 1)])
    st = _state(0.0, [], as_of=FRI)
    with pytest.raises(ValueError, match="开放|trading"):
        advance_to_next_trading_day(st, _fills([]), env.rd)


def test_trailing_unresolved_fails(env):
    _seed_cal(env, [(FRI, 1)])
    st = _state(0.0, [], as_of=FRI)
    with pytest.raises(ValueError, match="next|后续|开放"):
        advance_to_next_trading_day(st, _fills([]), env.rd)


def test_output_metadata(env):
    _default_cal(env)
    nxt = advance_to_next_trading_day(_state(100.0, []), _fills([]), env.rd)
    assert nxt.as_of_date == MON
    assert nxt.phase is PortfolioStatePhase.PRE_EXECUTION


def test_cash_unchanged(env):
    _default_cal(env)
    nxt = advance_to_next_trading_day(_state(14_488.0, []), _fills([]), env.rd)
    assert nxt.cash == 14_488.0


def test_quantity_unchanged(env):
    _default_cal(env)
    st = _state(0.0, [("000001.SZ", 1000, 600), ("600000.SH", 500, 0)])
    nxt = advance_to_next_trading_day(st, _fills([]), env.rd)
    r1 = _row(nxt, "000001.SZ")
    r2 = _row(nxt, "600000.SH")
    assert (r1[1], r1[2]) == (1000, 600)
    assert (r2[1], r2[2]) == (500, 0)


# ================================================================
# AC-19..42：T+1 provenance release
# ================================================================

def test_new_buy_position_release(env):
    """POST A 500/0 + BUY 500 → NEXT A 500/500。"""
    _default_cal(env)
    st = _state(0.0, [("000001.SZ", 500, 0)])
    f = _fills([_buy("000001.SZ", 500)])
    nxt = advance_to_next_trading_day(st, f, env.rd)
    r = _row(nxt, "000001.SZ")
    assert (r[1], r[2]) == (500, 500)


def test_existing_buy_release(env):
    """POST A 1500/1000 + BUY 500 → NEXT 1500/1500。"""
    _default_cal(env)
    st = _state(0.0, [("000001.SZ", 1500, 1000)])
    f = _fills([_buy("000001.SZ", 500)])
    nxt = advance_to_next_trading_day(st, f, env.rd)
    r = _row(nxt, "000001.SZ")
    assert (r[1], r[2]) == (1500, 1500)


def test_partial_buy_release_uses_filled(env):
    """order=1000 filled=600 → 只释放 600。"""
    _default_cal(env)
    st = _state(0.0, [("000001.SZ", 600, 0)])
    f = _fills([_buy("000001.SZ", 600, ordered=1000)])
    nxt = advance_to_next_trading_day(st, f, env.rd)
    r = _row(nxt, "000001.SZ")
    assert (r[1], r[2]) == (600, 600)


def test_preserve_other_unavailable_inventory(env):
    """POST A 1500/600 + BUY 200 → NEXT 1500/800（剩余 700 不释放）。"""
    _default_cal(env)
    st = _state(0.0, [("000001.SZ", 1500, 600)])
    f = _fills([_buy("000001.SZ", 200)])
    nxt = advance_to_next_trading_day(st, f, env.rd)
    r = _row(nxt, "000001.SZ")
    assert (r[1], r[2]) == (1500, 800)


def test_no_buy_holding_unchanged(env):
    """fills 只有别的 code → 本 holding 严格保持。"""
    _default_cal(env)
    st = _state(0.0, [("000001.SZ", 1000, 600), ("600000.SH", 300, 0)])
    f = _fills([_buy("600000.SH", 300)])
    nxt = advance_to_next_trading_day(st, f, env.rd)
    r = _row(nxt, "000001.SZ")
    assert (r[1], r[2]) == (1000, 600)


def test_sell_only_no_release(env):
    """SELL-only 的 holding 不因过夜自动释放。"""
    _default_cal(env)
    st = _state(0.0, [("000001.SZ", 400, 100)])
    f = _fills([_sell("000001.SZ", 300)])
    nxt = advance_to_next_trading_day(st, f, env.rd)
    r = _row(nxt, "000001.SZ")
    assert (r[1], r[2]) == (400, 100)


def test_empty_fills_preserves_all(env):
    _default_cal(env)
    st = _state(14_488.0, [("000001.SZ", 1000, 600)])
    nxt = advance_to_next_trading_day(st, _fills([]), env.rd)
    assert nxt.cash == 14_488.0
    r = _row(nxt, "000001.SZ")
    assert (r[1], r[2]) == (1000, 600)
    assert nxt.as_of_date == MON


def test_cash_only_state(env):
    _default_cal(env)
    st = _state(100_000.0, [])
    nxt = advance_to_next_trading_day(st, _fills([]), env.rd)
    assert nxt.cash == 100_000.0
    assert nxt.positions.height == 0
    assert nxt.positions.schema["code"] == pl.String
    assert nxt.positions.schema["quantity"] == pl.Int64
    assert nxt.positions.schema["sellable_quantity"] == pl.Int64


def test_no_new_or_deleted_codes(env):
    _default_cal(env)
    st = _state(0.0, [("000001.SZ", 1000, 600)])
    nxt = advance_to_next_trading_day(st, _fills([_buy("000001.SZ", 100)]),
                                      env.rd)
    assert nxt.positions["code"].to_list() == ["000001.SZ"]


def test_output_sorted_asc(env):
    _default_cal(env)
    st = _state(0.0, [("600000.SH", 300, 0), ("000001.SZ", 200, 0)])
    nxt = advance_to_next_trading_day(st, _fills([]), env.rd)
    assert nxt.positions["code"].to_list() == ["000001.SZ", "600000.SH"]


def test_new_sellable_never_exceeds_quantity(env):
    """release 后 new_sellable <= quantity 显式检查。"""
    _default_cal(env)
    st = _state(0.0, [("000001.SZ", 500, 400)])
    f = _fills([_buy("000001.SZ", 100)])
    nxt = advance_to_next_trading_day(st, f, env.rd)
    r = _row(nxt, "000001.SZ")
    assert r[1] == 500 and r[2] == 500


# ================================================================
# AC-28..33：cross-object guards
# ================================================================

def test_buy_code_missing_in_post_fails(env):
    _default_cal(env)
    st = _state(0.0, [])
    f = _fills([_buy("000001.SZ", 500)])
    with pytest.raises(ValueError, match="position|持仓|存在"):
        advance_to_next_trading_day(st, f, env.rd)


def test_insufficient_unsellable_capacity_fails(env):
    """POST 1000/900（unsellable=100）+ BUY 200 → 无法解释 200 provenance。"""
    _default_cal(env)
    st = _state(0.0, [("000001.SZ", 1000, 900)])
    f = _fills([_buy("000001.SZ", 200)])
    with pytest.raises(ValueError, match="unsellable|capacity|inventory"):
        advance_to_next_trading_day(st, f, env.rd)


# ================================================================
# AC-56..60：immutability / determinism / reapply
# ================================================================

def test_immutability(env):
    _default_cal(env)
    st = _state(14_488.0, [("000001.SZ", 1500, 1000)])
    f = _fills([_buy("000001.SZ", 500)])
    pos_before = st.positions.clone()
    f_before = f.frame.clone()
    advance_to_next_trading_day(st, f, env.rd)
    assert st.positions.equals(pos_before)
    assert f.frame.equals(f_before)
    assert st.phase is PortfolioStatePhase.POST_EXECUTION


def test_determinism(env):
    _default_cal(env)
    st = _state(14_488.0, [("000001.SZ", 1500, 1000)])
    f = _fills([_buy("000001.SZ", 500)])
    a = advance_to_next_trading_day(st, f, env.rd)
    b = advance_to_next_trading_day(st, f, env.rd)
    assert a.as_of_date == b.as_of_date
    assert a.cash == b.cash
    assert a.positions.equals(b.positions)


def test_cannot_reapply_to_pre_output(env):
    _default_cal(env)
    st = _state(0.0, [("000001.SZ", 500, 0)])
    f = _fills([_buy("000001.SZ", 500)])
    nxt = advance_to_next_trading_day(st, f, env.rd)
    assert nxt.phase is PortfolioStatePhase.PRE_EXECUTION
    with pytest.raises(ValueError, match="POST_EXECUTION"):
        advance_to_next_trading_day(nxt, _fills([]), env.rd)


def test_output_passes_validator(env):
    _default_cal(env)
    nxt = advance_to_next_trading_day(_state(100.0, [("000001.SZ", 100, 50)]),
                                      _fills([]), env.rd)
    assert isinstance(nxt, PortfolioState)


# ================================================================
# AC-85/86：source audits
# ================================================================

def test_no_blanket_release_source_audit():
    """overnight.py 不得出现 sellable=quantity 等价逻辑。"""
    from factorlab.app.backtest import overnight as mod
    src = inspect.getsource(mod)
    assert "alias(\"sellable_quantity\")" not in src
    assert "sellable_quantity = quantity" not in src
    assert "pl.col(\"quantity\").alias" not in src


def test_no_forbidden_dependencies():
    from factorlab.app.backtest import overnight as mod
    src = inspect.getsource(mod)
    for forbidden in ("compute_execution_cost", "ExecutionCostSpec",
                      "MarketOpenSnapshot", "OpenFillAssessment",
                      "SecurityQuantityRules", "OrderBatch",
                      "TargetPortfolio", "StrategySpec",
                      "daily", "stk_limit", "suspend_d"):
        assert not re.search(rf"^\s*(import|from)\s+[^\s]*{forbidden}", src,
                             re.M), f"overnight.py 不得引用 {forbidden}"
