"""R22 Task 4：窗口成交（realize_window_fills）——明细/未成交/成本/现金约束。

断言来源：design.md §2「真实约束」（参与率/T+1/现金 >= 0/成本复用）与
plan.md Task 4 Interfaces（detail 分钟粒度、SELL 先于 BUY、每笔成本）。
合成分钟数据手算（不使用实现输出推导期望）。
"""

import datetime

import polars as pl
import pytest

from factorlab.app.backtest.fills import (WindowRealizedResult,
                                          realize_window_fills)
from factorlab.app.backtest.rules import SecurityQuantityRules
from factorlab.core.domain.execution import (ExecutionDataQualityError,
                                             ExecutionTiming, MarketOpenSnapshot,
                                             OrderBatch, PortfolioState,
                                             PortfolioStatePhase)
from factorlab.core.execution.spec import (ExecutionCostSpec, MinuteWindowSpec,
                                           TriggerSpec)

D1 = datetime.date(2024, 1, 2)
D2 = datetime.date(2024, 1, 3)

_SNAP_COLS = ["code", "open", "pre_close", "up_limit", "down_limit",
              "has_daily", "has_limit", "has_suspend_record",
              "is_suspended_at_open"]
_MIN_COLS = ["code", "minute_index", "open", "high", "low", "close", "volume",
             "amount", "session_type"]


def _snapshot(rows):
    """rows: (code, open, pre_close, up, dn, has_daily, has_limit,
    susp_record, susp_open)."""
    frame = pl.DataFrame(rows, schema=_SNAP_COLS, orient="row")
    frame = frame.with_columns(
        pl.col("code").cast(pl.String), pl.col("open").cast(pl.Float64),
        pl.col("pre_close").cast(pl.Float64), pl.col("up_limit").cast(pl.Float64),
        pl.col("down_limit").cast(pl.Float64),
        pl.col("has_daily").cast(pl.Boolean),
        pl.col("has_limit").cast(pl.Boolean),
        pl.col("has_suspend_record").cast(pl.Boolean),
        pl.col("is_suspended_at_open").cast(pl.Boolean))
    return MarketOpenSnapshot(execution_date=D2, frame=frame.sort("code"))


def _default_snapshot(**over):
    rows = []
    for code, (o, pc, up, dn, hd, hl, sr, so) in {
            "000001.SZ": (10.0, 10.0, 11.0, 9.0, True, True, False, False),
            "600000.SH": (8.0, 8.0, 8.8, 7.2, True, True, False, False),
    }.items():
        vals = over.get(code, (o, pc, up, dn, hd, hl, sr, so))
        rows.append((code, *vals))
    return _snapshot(rows)


def _minute_frame(per_code):
    """per_code: {code: [ (code, i, o, h, l, c, v, a, sess), ... ]}"""
    rows = []
    for code, bars in per_code.items():
        for b in bars:
            assert b[0] == code, (code, b)
            rows.append(tuple(b))
    if not rows:
        return pl.DataFrame({c: [] for c in _MIN_COLS})
    frame = pl.DataFrame(rows, schema=_MIN_COLS, orient="row")
    return frame.with_columns(
        pl.col("code").cast(pl.String), pl.col("minute_index").cast(pl.Int64),
        pl.col("open").cast(pl.Float64), pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64), pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64), pl.col("amount").cast(pl.Float64),
        pl.col("session_type").cast(pl.Int64))


def _orders(rows, timing=ExecutionTiming.NEXT_OPEN):
    frame = pl.DataFrame(rows, schema=["code", "side", "quantity"], orient="row")
    frame = frame.with_columns(pl.col("code").cast(pl.String),
                               pl.col("side").cast(pl.String),
                               pl.col("quantity").cast(pl.Int64))
    return OrderBatch(decision_date=D1, execution_date=D2,
                      execution_timing=timing, orders=frame.sort("code"))


def _state(cash, positions=()):
    if positions:
        pos = pl.DataFrame(positions,
                           schema=["code", "quantity", "sellable_quantity"],
                           orient="row")
        pos = pos.with_columns(pl.col("code").cast(pl.String),
                               pl.col("quantity").cast(pl.Int64),
                               pl.col("sellable_quantity").cast(pl.Int64))
    else:
        pos = pl.DataFrame({"code": pl.Series([], dtype=pl.String),
                            "quantity": pl.Series([], dtype=pl.Int64),
                            "sellable_quantity": pl.Series([], dtype=pl.Int64)})
    return PortfolioState(as_of_date=D2, phase=PortfolioStatePhase.PRE_EXECUTION,
                          cash=cash, positions=pos)


def _rules(codes):
    frame = pl.DataFrame([(c, "主板", "round_lot_100") for c in codes],
                         schema=["code", "market", "rule"], orient="row")
    frame = frame.with_columns(pl.col("code").cast(pl.String),
                               pl.col("market").cast(pl.String),
                               pl.col("rule").cast(pl.String))
    if frame.height == 0:
        frame = pl.DataFrame({"code": pl.Series([], dtype=pl.String),
                              "market": pl.Series([], dtype=pl.String),
                              "rule": pl.Series([], dtype=pl.String)})
    return SecurityQuantityRules(frame=frame)


def _bar_minute(code, i, price, volume, *, amount=None, fallback_close=None):
    """以 price 为 o/h/l/c（除非给了 fallback_close 制造 close 差异）。"""
    c = fallback_close if fallback_close is not None else price
    a = amount if amount is not None else price * volume
    return (code, i, price, price, price, c, volume, a, 1)


def _run(orders, state, minute_frame, spec, *, snapshot=None, cost=None,
         rules=None):
    codes = sorted(orders.orders["code"].to_list())
    return realize_window_fills(
        orders, state, minute_frame=minute_frame,
        snapshot=snapshot if snapshot is not None else _default_snapshot(),
        spec=spec,
        quantity_rules=rules if rules is not None else _rules(codes),
        cost_spec=cost if cost is not None else ExecutionCostSpec())


def test_api_and_type_guards():
    assert callable(realize_window_fills)
    orders = _orders([("000001.SZ", "buy", 100)])
    spec = MinuteWindowSpec(start=0, end=0)
    mf = _minute_frame({"000001.SZ": [_bar_minute("000001.SZ", 0, 10.0, 1000)]})
    r = _run(orders, _state(1_000_000), mf, spec)
    assert isinstance(r, WindowRealizedResult)
    assert r.detail.columns == ["code", "side", "minute_index", "quantity",
                                "price", "fell_back"]
    with pytest.raises(TypeError, match="orders"):
        realize_window_fills({"x": 1}, _state(1.0), minute_frame=mf,
                             snapshot=_default_snapshot(), spec=spec,
                             quantity_rules=_rules(["000001.SZ"]),
                             cost_spec=ExecutionCostSpec())
    with pytest.raises(TypeError, match="state"):
        realize_window_fills(orders, {"x": 1}, minute_frame=mf,
                             snapshot=_default_snapshot(), spec=spec,
                             quantity_rules=_rules(["000001.SZ"]),
                             cost_spec=ExecutionCostSpec())


def test_vwap_aggregate_detail_and_unfilled():
    mf = _minute_frame({"000001.SZ": [
        _bar_minute("000001.SZ", 0, 10.0, 1000.0, amount=10000.0),
        _bar_minute("000001.SZ", 1, 10.1, 2000.0, amount=20200.0),
        _bar_minute("000001.SZ", 2, 10.2, 500.0, amount=5100.0),
    ]})
    spec = MinuteWindowSpec(start=0, end=2, participation=0.5)
    r = _run(_orders([("000001.SZ", "buy", 1500)]), _state(1_000_000), mf, spec)
    fb = r.fill_batch.frame
    # 手算：cap m0=500@10.0、m1=1000@10.1；ref=(5000+10100)/1500
    assert fb.height == 1
    assert fb["filled_quantity"].to_list() == [1500]
    assert fb["reference_price"][0] == pytest.approx(15100.0 / 1500.0)
    assert r.unfilled == {}
    assert r.detail["minute_index"].to_list() == [0, 1]
    assert r.detail["quantity"].to_list() == [500, 1000]
    assert r.detail["price"].to_list() == pytest.approx([10.0, 10.1])
    assert r.detail["fell_back"].to_list() == [False, False]


def test_partial_unfilled_recorded():
    mf = _minute_frame({"000001.SZ": [
        _bar_minute("000001.SZ", 0, 10.0, 1000.0, amount=10000.0)]})
    spec = MinuteWindowSpec(start=0, end=0, participation=0.1)
    r = _run(_orders([("000001.SZ", "buy", 500)]), _state(1_000_000), mf, spec)
    assert r.fill_batch.frame["filled_quantity"].to_list() == [100]
    assert r.unfilled == {"000001.SZ": 400}


def test_sell_before_buy_and_proceeds_fund_buys():
    mf = _minute_frame({
        "000001.SZ": [_bar_minute("000001.SZ", 0, 9.5, 2000.0)],
        "600000.SH": [_bar_minute("600000.SH", 0, 8.0, 2000.0)],
    })
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    orders = _orders([("000001.SZ", "sell", 1000), ("600000.SH", "buy", 1000)])
    state = _state(0.0, positions=[("000001.SZ", 1000, 1000)])
    r = _run(orders, state, mf, spec)
    fb = r.fill_batch.frame
    # SELL 先执行：proceeds=9.5×1000=9500；BUY 8×1000=8000 → 期末现金 1500
    assert fb["code"].to_list() == ["000001.SZ", "600000.SH"]
    assert fb["side"].to_list() == ["sell", "buy"]
    assert fb["filled_quantity"].to_list() == [1000, 1000]
    delta = fb["effective_cash_delta"].sum()
    assert state.cash + delta == pytest.approx(1500.0)
    assert r.detail["side"].to_list() == ["sell", "buy"]


def test_buy_blocked_by_zero_cash_without_sell():
    mf = _minute_frame({
        "600000.SH": [_bar_minute("600000.SH", 0, 8.0, 2000.0)]})
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    orders = _orders([("600000.SH", "buy", 1000)])
    state = _state(0.0)
    r = _run(orders, state, mf, spec)
    assert r.fill_batch.frame.height == 0
    assert r.unfilled == {"600000.SH": 1000}


def test_cash_constraint_scales_minute_fills():
    mf = _minute_frame({"000001.SZ": [
        _bar_minute("000001.SZ", 0, 1.0, 1000.0, amount=1000.0),
        _bar_minute("000001.SZ", 1, 1.0, 1000.0, amount=1000.0),
    ]})
    spec = MinuteWindowSpec(start=0, end=1, participation=1.0)
    # 现金 1500：初始 1000@1 + 1000@1 = 2000 > 1500 → scale 0.75 →
    # 750 + 750 = 1500（零成本，恰好打满；现金严格 >= 0）
    snap = _snapshot([("000001.SZ", 1.0, 1.0, 1.1, 0.9, True, True, False,
                       False)])
    r = _run(_orders([("000001.SZ", "buy", 2000)]), _state(1500.0), mf, spec,
             snapshot=snap)
    assert r.fill_batch.frame["filled_quantity"].to_list() == [1500]
    assert r.detail["quantity"].to_list() == [750, 750]
    assert r.unfilled == {"000001.SZ": 500}
    assert _state(1500.0).cash + r.fill_batch.frame["effective_cash_delta"].sum() \
        == pytest.approx(0.0)


def test_cost_model_applied_per_fill():
    mf = _minute_frame({"000001.SZ": [
        _bar_minute("000001.SZ", 0, 10.0, 2000.0, amount=20000.0)]})
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    cost = ExecutionCostSpec(commission_rate=0.001, minimum_commission=0.0,
                             transfer_fee_rate=0.00001, slippage_bps=10.0)
    r = _run(_orders([("000001.SZ", "buy", 1000)]), _state(1_000_000), mf, spec,
             cost=cost)
    b = r.fill_batch.frame.row(0)
    # exec=10×1.001=10.01；gross=10010；comm=10.01；transfer=0.1001
    assert b[5] == pytest.approx(10.01)              # execution_price
    assert b[6] == pytest.approx(10010.0)            # gross_notional
    assert b[7] == pytest.approx(10.01)              # commission
    assert b[9] == pytest.approx(0.1001)             # transfer_fee
    assert b[11] == pytest.approx(-(10010.0 + 10.1101))


def test_fallback_close_detail_flag():
    mf = _minute_frame({"000001.SZ": [
        _bar_minute("000001.SZ", 0, 10.0, 1000.0, amount=10000.0),
        _bar_minute("000001.SZ", 1, 10.0, 1000.0, amount=10000.0,
                    fallback_close=10.5),
    ]})
    spec = MinuteWindowSpec(start=0, end=1, participation=0.1, fallback="close")
    r = _run(_orders([("000001.SZ", "buy", 400)]), _state(1_000_000), mf, spec)
    assert r.fill_batch.frame["filled_quantity"].to_list() == [400]
    assert r.detail["quantity"].to_list() == [100, 100, 200]
    assert r.detail["price"].to_list() == pytest.approx([10.0, 10.0, 10.5])
    assert r.detail["fell_back"].to_list() == [False, False, True]
    assert r.detail["minute_index"].to_list() == [0, 1, 1]
    assert r.unfilled == {}


def test_limit_trigger_price_in_fill_batch():
    mf = _minute_frame({"000001.SZ": [
        _bar_minute("000001.SZ", 0, 10.0, 1000.0, amount=10000.0)]})
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0,
                            trigger=TriggerSpec(mode="limit", ref="pre_close",
                                                offset_bps=0))
    # snapshot 000001.SZ pre_close=10.0；limit=10.0 → low=10.0 命中 →
    # 成交价 min(10.0, open=10.0)=10.0
    r = _run(_orders([("000001.SZ", "buy", 1000)]), _state(1_000_000), mf, spec)
    assert r.fill_batch.frame["reference_price"].to_list() == [10.0]
    assert r.detail["price"].to_list() == [10.0]


def test_sealed_limit_up_no_buy():
    snap = _snapshot([("000001.SZ", 11.0, 10.0, 11.0, 9.0, True, True, False,
                       False)])
    mf = _minute_frame({"000001.SZ": [
        ("000001.SZ", 0, 11.0, 11.0, 11.0, 11.0, 1000.0, 11000.0, 0)]})
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    r = _run(_orders([("000001.SZ", "buy", 100)]), _state(1_000_000), mf, spec,
             snapshot=snap)
    assert r.fill_batch.frame.height == 0
    assert r.unfilled == {"000001.SZ": 100}


def test_suspended_code_skipped():
    snap = _snapshot([("000001.SZ", 10.0, 10.0, 11.0, 9.0, True, True, True,
                       True)])
    mf = _minute_frame({"000001.SZ": [
        _bar_minute("000001.SZ", 0, 10.0, 1000.0)]})
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    r = _run(_orders([("000001.SZ", "buy", 100)]), _state(1_000_000), mf, spec,
             snapshot=snap)
    assert r.fill_batch.frame.height == 0
    assert r.unfilled == {"000001.SZ": 100}


def test_missing_daily_evidence_fails():
    snap = _snapshot([("000001.SZ", None, None, None, None, False, False,
                       False, False)])
    mf = _minute_frame({"000001.SZ": [
        _bar_minute("000001.SZ", 0, 10.0, 1000.0)]})
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    with pytest.raises(ExecutionDataQualityError, match="has_daily"):
        _run(_orders([("000001.SZ", "buy", 100)]), _state(1_000_000), mf, spec,
             snapshot=snap)


def test_quantity_rules_and_inventory_revalidated():
    mf = _minute_frame({"000001.SZ": [
        _bar_minute("000001.SZ", 0, 10.0, 100000.0, amount=1_000_000.0)]})
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    with pytest.raises(ValueError, match="is_valid_buy_quantity"):
        _run(_orders([("000001.SZ", "buy", 150)]), _state(1_000_000), mf, spec)
    with pytest.raises(ValueError, match="sellable"):
        _run(_orders([("000001.SZ", "sell", 1200)]),
             _state(0.0, positions=[("000001.SZ", 1000, 1000)]), mf, spec)


def test_order_code_must_be_in_snapshot():
    mf = _minute_frame({"000001.SZ": [
        _bar_minute("000001.SZ", 0, 10.0, 1000.0)]})
    snap = _snapshot([("600000.SH", 8.0, 8.0, 8.8, 7.2, True, True, False,
                       False)])
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    with pytest.raises(ValueError, match="snapshot"):
        _run(_orders([("000001.SZ", "buy", 100)]), _state(1_000_000), mf, spec,
             snapshot=snap)


def test_minute_frame_schema_must_match_contract():
    mf = pl.DataFrame({"code": ["000001.SZ"], "minute_index": [0]})
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    with pytest.raises(ValueError, match="列"):
        _run(_orders([("000001.SZ", "buy", 100)]), _state(1_000_000), mf, spec)


def test_empty_orders_typed_empty():
    mf = _minute_frame({})
    spec = MinuteWindowSpec(start=0, end=0, participation=1.0)
    orders = _orders([])
    r = _run(orders, _state(1_000_000), mf, spec, rules=_rules([]))
    assert r.fill_batch.frame.height == 0
    assert r.fill_batch.frame.columns == [
        "code", "side", "order_quantity", "filled_quantity", "reference_price",
        "execution_price", "gross_notional", "commission", "stamp_tax",
        "transfer_fee", "total_fees", "effective_cash_delta"]
    assert r.detail.height == 0 and r.unfilled == {}
