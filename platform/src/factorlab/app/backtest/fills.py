"""M8-04C：cost-aware realized funding + FillBatch——OrderBatch/assessment/state
→ actual modeled fills。

```
1. cross-object validation（metadata / identity / state / snapshot / rules）
2. quantity-rule / inventory revalidation（所有订单含 blocked——市场 blocked
   不掩盖 state/order corruption）
3. realize FILLABLE SELLs（full fill——无 market partial）
4. actual net sell proceeds（ExecutionCostBreakdown.effective_cash_delta）
5. available buy cash = state.cash + Σ actual net SELL proceeds
6. FILLABLE BUY candidates
7. iterative cost-aware buy funding（global proportional scale → quantity
   projection → re-cost → repeat 直到 cash-feasible；无 greedy redistribution）
8. build FillBatch（sparse：filled>0 才有一行）
9. final cash safety check（cash_after >= 0 严格，无 tolerance/clamp）
```

关键边界：
- market eligibility authority = OpenFillAssessment（只读 disposition，不重判
  suspension/limit queue）；snapshot 仅用于 reference-price consistency +
  execution-price legal-bound（slippage 越过涨跌停价时按对应限价封顶，
  并用封顶价重算成本）
- 成本唯一 authority = compute_execution_cost（不手写第二份费用公式）；
  BUY partial 费用基于 filled_quantity 重算；fill 行 order_quantity = 委托量、
  filled_quantity = 实际成交（R01-M8-I2：部分成交在 fill 行可审计）
- SELL proceeds 先于 BUY funding 入账（deterministic accounting convention，
  不是交易所微观顺序声明）；blocked SELL 提供 0 现金
- 不修改任何输入；不创建 POST state；无 NAV/PnL；NEXT_OPEN only
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl

from factorlab.core.domain.execution import (ExecutionDataQualityError,
                                        ExecutionTiming, FillBatch,
                                        MarketOpenSnapshot, OpenFillAssessment,
                                        OpenOrderDisposition, OrderBatch,
                                        OrderSide, PortfolioState,
                                        PortfolioStatePhase, QuantityRuleKind)
from factorlab.app.backtest.rules import SecurityQuantityRules
from factorlab.core.execution.costs import compute_execution_cost
from factorlab.app.backtest.rules import (is_valid_buy_quantity,
                                       is_valid_sell_quantity,
                                       project_buy_quantity)
from factorlab.core.execution.minute_window import (MinuteBar, MinuteFill,
                                                simulate_window)
from factorlab.core.execution.spec import ExecutionCostSpec, MinuteWindowSpec

_EMPTY_FILLS = pl.DataFrame(
    {"code": pl.Series([], dtype=pl.String),
     "side": pl.Series([], dtype=pl.String),
     "order_quantity": pl.Series([], dtype=pl.Int64),
     "filled_quantity": pl.Series([], dtype=pl.Int64),
     "reference_price": pl.Series([], dtype=pl.Float64),
     "execution_price": pl.Series([], dtype=pl.Float64),
     "gross_notional": pl.Series([], dtype=pl.Float64),
     "commission": pl.Series([], dtype=pl.Float64),
     "stamp_tax": pl.Series([], dtype=pl.Float64),
     "transfer_fee": pl.Series([], dtype=pl.Float64),
     "total_fees": pl.Series([], dtype=pl.Float64),
     "effective_cash_delta": pl.Series([], dtype=pl.Float64)})


def _compute_bounded_execution_cost(
    *,
    side: OrderSide,
    reference_price: float,
    quantity: int,
    spec: ExecutionCostSpec,
    up_limit: float,
    down_limit: float,
):
    """在合法价带内确定成交价，并由唯一成本 authority 计算全套金额。

    仅当 slippage 价越过涨跌停时封顶；价带内（含恰好等于边界）保留原价。
    封顶后以零滑点 spec 在限价上重新调用 compute_execution_cost，确保
    gross、fees 和 cash delta 全部与最终成交价一致。
    """
    breakdown = compute_execution_cost(
        side=side, reference_price=reference_price, quantity=quantity,
        spec=spec)
    bounded_price = min(up_limit, max(down_limit, breakdown.execution_price))
    if bounded_price == breakdown.execution_price:
        return breakdown

    bounded_spec = spec.model_copy(update={"slippage_bps": 0.0})
    return compute_execution_cost(
        side=side, reference_price=bounded_price, quantity=quantity,
        spec=bounded_spec)


def _check_window_price_bounds(breakdown, code: str, up_limit: float,
                               down_limit: float) -> None:
    """NEXT_WINDOW keeps its existing daily-limit consistency gate.

    Window fill prices are produced by the minute simulator and this issue's
    bounded NEXT_OPEN pricing rule does not change that execution path.
    """
    p = breakdown.execution_price
    if p > up_limit or p < down_limit:
        raise ValueError(
            f"{code} window cost-model slippage crosses legal market limit："
            f"execution_price={p} 不在 [down={down_limit}, up={up_limit}]")


def realize_open_fills(
    orders: OrderBatch,
    assessment: OpenFillAssessment,
    state: PortfolioState,
    snapshot: MarketOpenSnapshot,
    quantity_rules: SecurityQuantityRules,
    cost_spec: ExecutionCostSpec,
) -> FillBatch:
    """realize NEXT_OPEN actual modeled fills（见模块 docstring）。

    Raises:
        TypeError: 任一参数类型不匹配
        ValueError: cross-object / inventory / quantity-rule / cost-model 违规
        NotImplementedError: NEXT_CLOSE（v1 仅 NEXT_OPEN）
        RuntimeError: 迭代 progress 破坏 / 最终现金为负（安全网）
    """
    if not isinstance(orders, OrderBatch):
        raise TypeError(f"orders 必须为 OrderBatch（收到 {type(orders).__name__}）")
    if not isinstance(assessment, OpenFillAssessment):
        raise TypeError(
            f"assessment 必须为 OpenFillAssessment（收到 {type(assessment).__name__}）")
    if not isinstance(state, PortfolioState):
        raise TypeError(
            f"state 必须为 PortfolioState（收到 {type(state).__name__}）")
    if not isinstance(snapshot, MarketOpenSnapshot):
        raise TypeError(
            f"snapshot 必须为 MarketOpenSnapshot（收到 {type(snapshot).__name__}）")
    if not isinstance(quantity_rules, SecurityQuantityRules):
        raise TypeError(
            f"quantity_rules 必须为 SecurityQuantityRules（收到 "
            f"{type(quantity_rules).__name__}）")
    if not isinstance(cost_spec, ExecutionCostSpec):
        raise TypeError(
            f"cost_spec 必须为 ExecutionCostSpec（收到 {type(cost_spec).__name__}）")

    if orders.execution_timing is not ExecutionTiming.NEXT_OPEN:
        raise NotImplementedError(
            f"{orders.execution_timing.value} realized funding 尚未实现——"
            f"M8-04C v1 仅支持 NEXT_OPEN")

    # ---- 1. cross-object validation ----
    if (orders.decision_date != assessment.decision_date
            or orders.execution_date != assessment.execution_date
            or orders.execution_timing is not assessment.execution_timing):
        raise ValueError(
            f"OrderBatch 与 OpenFillAssessment metadata 必须一致（decision/"
            f"execution/timing）")
    if not assessment.frame.select(["code", "side", "quantity"]).equals(
            orders.orders):
        raise ValueError(
            f"assessment.frame(code,side,quantity) 必须与 orders.orders 逐行"
            f"完全一致（多/少/错行均 fail——不 sort/join 后比较）")
    if state.as_of_date != orders.execution_date:
        raise ValueError(
            f"state.as_of_date {state.as_of_date} != orders.execution_date "
            f"{orders.execution_date}")
    if state.phase is not PortfolioStatePhase.PRE_EXECUTION:
        raise ValueError(
            f"state.phase 必须为 PRE_EXECUTION（收到 {state.phase.value}）")
    if snapshot.execution_date != orders.execution_date:
        raise ValueError(
            f"snapshot.execution_date {snapshot.execution_date} != "
            f"orders.execution_date {orders.execution_date}")

    order_codes = set(orders.orders["code"].to_list())
    snap_codes = set(snapshot.frame["code"].to_list())
    rule_codes = set(quantity_rules.frame["code"].to_list())
    if not order_codes <= snap_codes:
        raise ValueError(
            f"order codes 不在 snapshot 中：{sorted(order_codes - snap_codes)}"
            f"（cross-object coverage bug）")
    if not order_codes <= rule_codes:
        raise ValueError(
            f"order codes 不在 quantity_rules 中："
            f"{sorted(order_codes - rule_codes)}")

    # maps
    snap_map: dict[str, tuple[float, float, float]] = {}
    for code, open_, _pc, up, dn, *_rest in snapshot.frame.iter_rows():
        snap_map[code] = (open_, up, dn)
    rule_map: dict[str, QuantityRuleKind] = {}
    for code, _mkt, rule_str in quantity_rules.frame.iter_rows():
        rule_map[code] = QuantityRuleKind(rule_str)
    pos_map: dict[str, tuple[int, int]] = {}
    for code, qty, sellable in state.positions.iter_rows():
        pos_map[code] = (qty, sellable)

    # ---- 2. quantity-rule / inventory revalidation（含 blocked）----
    # 逐行以 orders 顺序验证，然后按 code 索引 assessment disposition
    ass_map = {}
    for code, side, qty, disp, price in assessment.frame.iter_rows():
        ass_map[code] = (side, qty, disp, price)
    for code, side, qty in orders.orders.iter_rows():
        a_side, a_qty, disp, _p = ass_map[code]
        if a_side != side or a_qty != qty:
            raise ValueError(f"assessment row mismatch for {code}")
        rule = rule_map[code]
        if side == "buy":
            if not is_valid_buy_quantity(rule, qty):
                raise ValueError(
                    f"BUY {code} {qty} 未通过 is_valid_buy_quantity"
                    f"（{rule}）——order quantity 必须重新验证")
        else:
            if code not in pos_map:
                raise ValueError(
                    f"SELL {code} 在 PRE state 无 position——inventory check "
                    f"fail（blocked 订单也必须通过）")
            hold, sellable = pos_map[code]
            if qty > sellable:
                raise ValueError(
                    f"SELL {code} {qty} 超出 sellable_quantity {sellable}")
            if qty > hold:
                raise ValueError(f"SELL {code} {qty} 超出 holding {hold}")
            if not is_valid_sell_quantity(rule, holding_quantity=hold,
                                          sell_quantity=qty):
                raise ValueError(
                    f"SELL {code} {qty}（holding {hold}）未通过 "
                    f"is_valid_sell_quantity（{rule}）")

    # ---- 3/4. realize FILLABLE SELLs + actual net proceeds ----
    rows: list[tuple] = []
    sell_net = 0.0
    for code, side, qty in orders.orders.iter_rows():
        a_side, a_qty, disp, fillable_price = ass_map[code]
        if side != "sell" or disp != OpenOrderDisposition.FILLABLE.value:
            continue
        open_, up, dn = snap_map[code]
        if fillable_price != open_:
            raise ValueError(
                f"{code} assessment.fillable_price {fillable_price} != "
                f"snapshot.open {open_}（reference-price consistency）")
        breakdown = _compute_bounded_execution_cost(
            side=OrderSide.SELL, reference_price=fillable_price,
            quantity=qty, spec=cost_spec, up_limit=up, down_limit=dn)
        rows.append((code, "sell", qty, qty, fillable_price,
                     breakdown.execution_price, breakdown.gross_notional,
                     breakdown.commission, breakdown.stamp_tax,
                     breakdown.transfer_fee, breakdown.total_fees,
                     breakdown.effective_cash_delta))
        sell_net += breakdown.effective_cash_delta

    # ---- 5/6/7. FILLABLE BUY funding（迭代比例缩量）----
    available = state.cash + sell_net
    buy_candidates: list[tuple[str, int, float, str, float, float]] = []
    for code, side, qty in orders.orders.iter_rows():
        a_side, a_qty, disp, fillable_price = ass_map[code]
        if side != "buy" or disp != OpenOrderDisposition.FILLABLE.value:
            continue
        open_, up, dn = snap_map[code]
        if fillable_price != open_:
            raise ValueError(
                f"{code} assessment.fillable_price {fillable_price} != "
                f"snapshot.open {open_}")
        # slippage 与 price bounds 与 qty 无关——先按最终合法价格验证一次
        _compute_bounded_execution_cost(
            side=OrderSide.BUY, reference_price=fillable_price,
            quantity=1, spec=cost_spec, up_limit=up, down_limit=dn)
        buy_candidates.append((code, qty, fillable_price, rule_map[code],
                               up, dn))

    def _required(q: int, price: float, up: float, dn: float) -> float:
        b = _compute_bounded_execution_cost(
            side=OrderSide.BUY, reference_price=price, quantity=q,
            spec=cost_spec, up_limit=up, down_limit=dn)
        return -b.effective_cash_delta

    current: list[tuple[str, int, float, QuantityRuleKind, float, float]] = \
        list(buy_candidates)
    while True:
        total = sum(_required(q, price, up, dn)
                    for _code, q, price, _rule, up, dn in current)
        if total <= available:
            break
        scale = available / total
        nxt: list[tuple[str, int, float, object, float, float]] = []
        for code, q, price, rule, up, dn in current:
            cap = math.floor(q * scale)
            nq = project_buy_quantity(rule, cap)
            if nq > 0:
                nxt.append((code, nq, price, rule, up, dn))
        if [c for c, *_ in nxt] == [c for c, *_ in current] \
                and [q for _, q, *_ in nxt] == [q for _, q, *_ in current]:
            raise RuntimeError(
                f"BUY funding 迭代无 progress（scale={scale}）——数量未下降，"
                f"禁止无限循环")
        current = nxt
        if not current:
            break

    for code, q, price, _rule, up, dn in current:
        breakdown = _compute_bounded_execution_cost(
            side=OrderSide.BUY, reference_price=price, quantity=q,
            spec=cost_spec, up_limit=up, down_limit=dn)
        # R01-M8-I2：order_quantity = 委托数量（assessment/orders 行），
        # filled_quantity = funding 缩量后的实际成交——部分成交在 fill 行可审计
        order_qty = ass_map[code][1]
        rows.append((code, "buy", order_qty, q, price, breakdown.execution_price,
                     breakdown.gross_notional, breakdown.commission,
                     breakdown.stamp_tax, breakdown.transfer_fee,
                     breakdown.total_fees, breakdown.effective_cash_delta))

    # ---- 8. build FillBatch（code ASC）----
    if rows:
        frame = pl.DataFrame(rows, schema=["code", "side", "order_quantity",
                                           "filled_quantity", "reference_price",
                                           "execution_price", "gross_notional",
                                           "commission", "stamp_tax",
                                           "transfer_fee", "total_fees",
                                           "effective_cash_delta"], orient="row")
        frame = frame.with_columns(
            pl.col("code").cast(pl.String), pl.col("side").cast(pl.String),
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
        frame = frame.sort("code")
    else:
        frame = _EMPTY_FILLS

    # ---- 9. final cash safety（严格 >= 0，无 tolerance/clamp）----
    cash_after = state.cash + frame["effective_cash_delta"].sum()
    if not math.isfinite(cash_after) or cash_after < 0:
        raise RuntimeError(
            f"cash_after {cash_after} 非法（必须 finite >= 0）——funding "
            f"不变量破坏，不允许负现金 tolerance/clamp")

    return FillBatch(decision_date=orders.decision_date,
                     execution_date=orders.execution_date,
                     execution_timing=orders.execution_timing,
                     frame=frame)


# ================================================================
# R22：NEXT_WINDOW 窗口成交（realize_window_fills）
# ================================================================

_EMPTY_WINDOW_DETAIL = pl.DataFrame(
    {"code": pl.Series([], dtype=pl.String),
     "side": pl.Series([], dtype=pl.String),
     "minute_index": pl.Series([], dtype=pl.Int64),
     "quantity": pl.Series([], dtype=pl.Int64),
     "price": pl.Series([], dtype=pl.Float64),
     "fell_back": pl.Series([], dtype=pl.Boolean)})

_WINDOW_MINUTE_COLS = ("code", "minute_index", "open", "high", "low", "close",
                       "volume", "amount")


@dataclass(frozen=True)
class WindowRealizedResult:
    """窗口成交结果：既有 FillBatch 契约 + 未成交明细 + 分钟级成交明细。

    - fill_batch：每 code 一行（aggregate：filled = Σ 分钟成交，reference_price
      = 分钟成交价加权平均，成本经 compute_execution_cost 一次聚合——与
      NEXT_OPEN 的「每订单一行」一致）
    - unfilled：{code: 未成交数量}（只含 >0 的 code；市场/现金/封板原因合并在
      数量中，原因 authority 在 assessment/minutes）
    - detail：分钟级成交（code/side/minute_index/quantity/price/fell_back；
      price 为引擎口径价（滑点前），FillBatch.reference_price 为其加权平均）
    """

    fill_batch: FillBatch
    unfilled: dict
    detail: pl.DataFrame


def _minute_bars(minute_frame: pl.DataFrame) -> dict[str, dict[int, MinuteBar]]:
    """9 列分钟 frame → {code: {minute_index: MinuteBar}}（契约校验 fail fast）。"""
    missing = [c for c in _WINDOW_MINUTE_COLS if c not in minute_frame.columns]
    if missing:
        raise ValueError(
            f"minute_frame 缺列 {missing}（期望 {list(_WINDOW_MINUTE_COLS)}）"
            f"——契约违约")
    out: dict[str, dict[int, MinuteBar]] = {}
    for r in minute_frame.iter_rows(named=True):
        out.setdefault(r["code"], {})[int(r["minute_index"])] = MinuteBar(
            minute_index=int(r["minute_index"]), open=r["open"], high=r["high"],
            low=r["low"], close=r["close"], volume=float(r["volume"]),
            amount=float(r["amount"]))
    return out


def _scale_minute_fills(fills: list[MinuteFill], scale: float) -> list[MinuteFill]:
    """按比例缩减分钟成交量（floor；0 量分钟丢弃——现金约束）。"""
    out: list[MinuteFill] = []
    for f in fills:
        q = math.floor(f.quantity * scale)
        if q > 0:
            out.append(MinuteFill(minute_index=f.minute_index, quantity=q,
                                  price=f.price, fell_back=f.fell_back))
    return out


def realize_window_fills(
    orders: OrderBatch,
    state: PortfolioState,
    *,
    minute_frame: pl.DataFrame,
    snapshot: MarketOpenSnapshot,
    spec: MinuteWindowSpec,
    quantity_rules: SecurityQuantityRules,
    cost_spec: ExecutionCostSpec,
) -> WindowRealizedResult:
    """realize NEXT_WINDOW 分钟窗口成交（见 design.md §2 真实约束）。

    顺序：SELL 先于 BUY（sell proceeds 先入账），逐 code：
    simulate_window（参与率/触发/封板/兜底）→ 成本（compute_execution_cost，
    每 code 聚合一次）→ BUY 现金约束（不足按比例缩减分钟量，严格现金 >= 0）。

    Raises:
        TypeError: 参数类型不匹配
        ValueError: cross-object/quantity-rule/inventory/slippage-bound/
          分钟 frame 契约违规
        ExecutionDataQualityError: has_daily=False（缺可执行价证据，fail fast）
        RuntimeError: 现金约束迭代破坏 / 期末现金为负（安全网）
    """
    if not isinstance(orders, OrderBatch):
        raise TypeError(f"orders 必须为 OrderBatch（收到 {type(orders).__name__}）")
    if not isinstance(state, PortfolioState):
        raise TypeError(
            f"state 必须为 PortfolioState（收到 {type(state).__name__}）")
    if not isinstance(snapshot, MarketOpenSnapshot):
        raise TypeError(
            f"snapshot 必须为 MarketOpenSnapshot（收到 {type(snapshot).__name__}）")
    if not isinstance(spec, MinuteWindowSpec):
        raise TypeError(
            f"spec 必须为 MinuteWindowSpec（收到 {type(spec).__name__}）")
    if not isinstance(quantity_rules, SecurityQuantityRules):
        raise TypeError(
            f"quantity_rules 必须为 SecurityQuantityRules（收到 "
            f"{type(quantity_rules).__name__}）")
    if not isinstance(cost_spec, ExecutionCostSpec):
        raise TypeError(
            f"cost_spec 必须为 ExecutionCostSpec（收到 {type(cost_spec).__name__}）")
    if state.as_of_date != orders.execution_date:
        raise ValueError(
            f"state.as_of_date {state.as_of_date} != orders.execution_date "
            f"{orders.execution_date}")
    if state.phase is not PortfolioStatePhase.PRE_EXECUTION:
        raise ValueError(
            f"state.phase 必须为 PRE_EXECUTION（收到 {state.phase.value}）")
    if snapshot.execution_date != orders.execution_date:
        raise ValueError(
            f"snapshot.execution_date {snapshot.execution_date} != "
            f"orders.execution_date {orders.execution_date}")

    # ---- snapshot / rules / positions maps ----
    snap_map: dict[str, tuple] = {}
    for code, open_, pc, up, dn, has_daily, has_limit, _rec, susp \
            in snapshot.frame.iter_rows():
        snap_map[code] = (open_, pc, up, dn, has_daily, has_limit, susp)
    rule_map: dict[str, QuantityRuleKind] = {}
    for code, _mkt, rule_str in quantity_rules.frame.iter_rows():
        rule_map[code] = QuantityRuleKind(rule_str)
    pos_map: dict[str, tuple[int, int]] = {}
    for code, qty, sellable in state.positions.iter_rows():
        pos_map[code] = (qty, sellable)

    bars_map = _minute_bars(minute_frame)

    def _gate(code: str):
        """日级证据闸门 + 分钟 bars；返回 (bars, up, dn, has_limit) 或 None
        （suspended 跳过）。"""
        if code not in snap_map:
            raise ValueError(
                f"order code {code} 不在 snapshot 中——cross-object coverage bug")
        _o, _pc, up, dn, has_daily, has_limit, susp = snap_map[code]
        if not has_daily:
            raise ExecutionDataQualityError(
                f"{code} missing executable price evidence (has_daily=False)"
                f"——DATA UNKNOWN ≠ TRADE REJECTED，不模拟 no-fill")
        if susp:
            return None
        return (bars_map.get(code, {}),
                up if has_limit else None, dn if has_limit else None, has_limit)

    # ---- 1. revalidation（quantity-rule / inventory，含全部订单）----
    for code, side, qty in orders.orders.iter_rows():
        if code not in rule_map:
            raise ValueError(f"order code {code} 不在 quantity_rules 中")
        rule = rule_map[code]
        if side == "buy":
            if not is_valid_buy_quantity(rule, qty):
                raise ValueError(
                    f"BUY {code} {qty} 未通过 is_valid_buy_quantity（{rule}）")
        else:
            if code not in pos_map:
                raise ValueError(
                    f"SELL {code} 在 PRE state 无 position——inventory check fail")
            hold, sellable = pos_map[code]
            if qty > sellable:
                raise ValueError(f"SELL {code} {qty} 超出 sellable_quantity {sellable}")
            if qty > hold:
                raise ValueError(f"SELL {code} {qty} 超出 holding {hold}")
            if not is_valid_sell_quantity(rule, holding_quantity=hold,
                                          sell_quantity=qty):
                raise ValueError(
                    f"SELL {code} {qty}（holding {hold}）未通过 "
                    f"is_valid_sell_quantity（{rule}）")

    rows: list[tuple] = []
    detail_rows: list[tuple] = []
    unfilled: dict[str, int] = {}

    # ---- 2. SELL 先（无现金约束；proceeds 供 BUY）----
    sell_net = 0.0
    for code, side, qty in orders.orders.iter_rows():
        if side != "sell":
            continue
        gate = _gate(code)
        if gate is None:
            unfilled[code] = qty
            continue
        bars, up, dn, has_limit = gate
        result = simulate_window(bars, side="sell", target_qty=qty, spec=spec,
                                 ref_price=snap_map[code][1], limit_up=up,
                                 limit_down=dn)
        if result.filled_qty == 0:
            unfilled[code] = qty
            continue
        breakdown = compute_execution_cost(
            side=OrderSide.SELL, reference_price=result.avg_price,
            quantity=result.filled_qty, spec=cost_spec)
        if has_limit:
            _check_window_price_bounds(breakdown, code, up, dn)
        rows.append((code, "sell", qty, result.filled_qty, result.avg_price,
                     breakdown.execution_price, breakdown.gross_notional,
                     breakdown.commission, breakdown.stamp_tax,
                     breakdown.transfer_fee, breakdown.total_fees,
                     breakdown.effective_cash_delta))
        sell_net += breakdown.effective_cash_delta
        for f in result.fills:
            detail_rows.append((code, "sell", f.minute_index, f.quantity,
                                f.price, f.fell_back))

    # ---- 3. BUY：现金约束（available = state.cash + Σ sell net）----
    available = state.cash + sell_net
    for code, side, qty in orders.orders.iter_rows():
        if side != "buy":
            continue
        gate = _gate(code)
        if gate is None:
            unfilled[code] = qty
            continue
        bars, up, dn, has_limit = gate
        result = simulate_window(bars, side="buy", target_qty=qty, spec=spec,
                                 ref_price=snap_map[code][1], limit_up=up,
                                 limit_down=dn)
        minute_fills = list(result.fills)
        q = sum(f.quantity for f in minute_fills)
        if q == 0:
            unfilled[code] = qty
            continue
        required = 0.0
        while q > 0:
            ref = sum(f.price * f.quantity for f in minute_fills) / q
            breakdown = compute_execution_cost(
                side=OrderSide.BUY, reference_price=ref, quantity=q,
                spec=cost_spec)
            if has_limit:
                _check_window_price_bounds(breakdown, code, up, dn)
            required = -breakdown.effective_cash_delta
            if required <= available:
                break
            scale = available / required
            nxt = _scale_minute_fills(minute_fills, scale)
            nq = sum(f.quantity for f in nxt)
            if nq == q:
                raise RuntimeError(
                    f"BUY {code} 现金缩减迭代无 progress（scale={scale}）")
            minute_fills, q = nxt, nq
        if q == 0:
            unfilled[code] = qty
            continue
        ref_final = sum(f.price * f.quantity for f in minute_fills) / q
        rows.append((code, "buy", qty, q, ref_final,
                     breakdown.execution_price, breakdown.gross_notional,
                     breakdown.commission, breakdown.stamp_tax,
                     breakdown.transfer_fee, breakdown.total_fees,
                     breakdown.effective_cash_delta))
        for f in minute_fills:
            detail_rows.append((code, "buy", f.minute_index, f.quantity,
                                f.price, f.fell_back))
        remaining = qty - q
        if remaining > 0:
            unfilled[code] = remaining
        available -= required

    # ---- 4. build FillBatch（code ASC）/ detail ----
    if rows:
        frame = pl.DataFrame(rows, schema=["code", "side", "order_quantity",
                                           "filled_quantity", "reference_price",
                                           "execution_price", "gross_notional",
                                           "commission", "stamp_tax",
                                           "transfer_fee", "total_fees",
                                           "effective_cash_delta"], orient="row")
        frame = frame.with_columns(
            pl.col("code").cast(pl.String), pl.col("side").cast(pl.String),
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
        frame = frame.sort("code")
    else:
        frame = _EMPTY_FILLS
    if detail_rows:
        detail = pl.DataFrame(detail_rows, schema=["code", "side",
                                                   "minute_index", "quantity",
                                                   "price", "fell_back"],
                              orient="row")
        detail = detail.with_columns(
            pl.col("code").cast(pl.String), pl.col("side").cast(pl.String),
            pl.col("minute_index").cast(pl.Int64),
            pl.col("quantity").cast(pl.Int64),
            pl.col("price").cast(pl.Float64),
            pl.col("fell_back").cast(pl.Boolean))
    else:
        detail = _EMPTY_WINDOW_DETAIL

    cash_after = state.cash + frame["effective_cash_delta"].sum()
    if not math.isfinite(cash_after) or cash_after < 0:
        raise RuntimeError(
            f"cash_after {cash_after} 非法（必须 finite >= 0）——窗口 funding "
            f"不变量破坏，不允许负现金 tolerance/clamp")

    return WindowRealizedResult(
        fill_batch=FillBatch(decision_date=orders.decision_date,
                             execution_date=orders.execution_date,
                             execution_timing=orders.execution_timing,
                             frame=frame),
        unfilled=unfilled, detail=detail)
