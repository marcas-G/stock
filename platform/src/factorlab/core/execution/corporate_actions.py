"""R07-DATA-I8：Corporate-action share/cash transition primitive。

```
PRE_EXECUTION PortfolioState + 除权明细事件行（code×trade_date 窗口）
        ↓
调整后 PRE_EXECUTION PortfolioState（execution date 开盘前应用）
```

语义（设计处置：除权日股数 × 因子 + 分红现金入账；LEAN/zipline 共识）：
- **现金分红** div_cash（元/10股）：cash += 资格日股数 × div_cash/10；
  股数不变。资格股数 = 同事件调整前股数（除权日前收市持仓 = PRE 持仓；
  跨 exec 间隔无成交，PRE 持仓即登记日持仓）。
- **送股/转增** div_bonus/div_transfer（股/10股）：quantity 与
  sellable_quantity ×= 1 + (b+t)/10；**不足 1 股向下取整（floor 舍去，
  V1 近似——不足 1 股的零星股按登记结算惯例可能顺延分配，本层不发明
  分配顺序）**。Decimal 精确缩放（float 朴素乘法在 100×1.01 等可精确
  结果的组合上下取整会少 1 股）。
- **配股** rights_num ≠ 0（股/10股）：V1 策略 = **不参与**——shares/cash
  不变 + CorporateActionWarning 记录（除权价格落差自然计入 NAV = 未参与
  的真实成本；不视为 share-unit 断链）。不支持按 rights_* 参与（需
  现金扣减/申购数量/中签近似，v1 不做）。
- 多事件按 (code, trade_date) 顺序应用（送转后再分红用调整后股数）。
- 不修改输入；输出经 PortfolioState constructor 验证。

fail-closed（ExecutionDataQualityError）——不得静默按无事件放行：
- 明细全 0/NULL（adj_event 命中与 adj_detail 不一致）
- 非有限值（NaN/Inf）
- div_cash < 0（负分红无定义）
- div_bonus + div_transfer < 0（缩股 share-unit 缩减语义未支持）
- rights_num < 0（负配股/缩股未支持）

结构错误（ValueError）：phase 非 PRE_EXECUTION / 列契约不符 / 事件 code 不在
PRE 持仓（gate scoping bug——静默忽略会藏 bug）。

只依赖 stdlib + polars + core.domain.execution（无 market/DB/cost 依赖）。
"""

from __future__ import annotations

import math
import warnings
from decimal import ROUND_FLOOR, Decimal

import polars as pl

from factorlab.core.domain.execution import (ExecutionDataQualityError,
                                        PortfolioState,
                                        PortfolioStatePhase)

EVENT_COLUMNS = ["code", "trade_date", "div_cash", "div_bonus",
                 "div_transfer", "rights_num", "rights_price"]


class CorporateActionWarning(UserWarning):
    """V1 策略的显式记录（配股不参与等文档化近似）——不是数据质量错误。"""


def _finite(value, *, code, trade_date, column) -> float:
    """NULL → 0（非事件列）；非有限值 → fail-closed（loader 已归一 NaN，
    这里防直接 DataFrame 输入绕过）。"""
    if value is None:
        return 0.0
    f = float(value)
    if not math.isfinite(f):
        raise ExecutionDataQualityError(
            f"CA 明细 {code}@{trade_date} 的 {column} 非 finite（{value!r}）"
            f"——fail-closed，不按 0 处理")
    return f


def _scaled_shares(shares: int, div_bonus: float, div_transfer: float) -> int:
    """floor(shares × (10 + b + t) / 10)——Decimal 精确缩放后向下取整。"""
    tenths = (Decimal(10) + Decimal(repr(div_bonus))
              + Decimal(repr(div_transfer)))
    return int((Decimal(shares) * tenths / Decimal(10))
               .to_integral_value(rounding=ROUND_FLOOR))


def apply_corporate_actions(
    state: PortfolioState,
    events: pl.DataFrame,
) -> PortfolioState:
    """把除权明细事件应用到 PRE_EXECUTION state → 新 PRE_EXECUTION state。

    events 列契约（loader `load_adj_detail_window` 输出）：code String /
    trade_date Date / div_cash, div_bonus, div_transfer, rights_num,
    rights_price Float64（NULL 合法）；rows 全部命中 state.positions。

    Raises:
        TypeError: state/events 类型不匹配
        ValueError: phase/列契约/事件 code 不在持仓（结构错误）
        ExecutionDataQualityError: 未支持明细 / 不一致明细 fail-closed
    """
    if not isinstance(state, PortfolioState):
        raise TypeError(
            f"state 必须为 PortfolioState（收到 {type(state).__name__}）")
    if not isinstance(events, pl.DataFrame):
        raise TypeError(
            f"events 必须为 polars.DataFrame（收到 {type(events).__name__}）")
    if list(events.columns) != EVENT_COLUMNS:
        raise ValueError(
            f"events 必须严格为 {EVENT_COLUMNS} 七列（收到 "
            f"{list(events.columns)}）")
    if state.phase is not PortfolioStatePhase.PRE_EXECUTION:
        raise ValueError(
            f"state.phase 必须为 PRE_EXECUTION（收到 {state.phase.value}）"
            f"——CA 调整在 execution date 开盘前应用")
    if not events.height:
        return state

    pos: dict[str, list[int]] = {
        code: [int(qty), int(sell)]
        for code, qty, sell in state.positions.iter_rows()}
    cash = state.cash
    # 确定性顺序：同一 code 按日期复合（跨 code 独立）
    ordered = sorted(events.iter_rows(), key=lambda r: (r[0], r[1]))
    for code, trade_date, c_raw, b_raw, t_raw, r_raw, _px in ordered:
        if code not in pos:
            raise ValueError(
                f"CA 事件 {code}@{trade_date} 不在 PRE 持仓 "
                f"{sorted(pos)}——gate/loader scoping 错误（不静默忽略）")
        div_cash = _finite(c_raw, code=code, trade_date=trade_date,
                           column="div_cash")
        div_bonus = _finite(b_raw, code=code, trade_date=trade_date,
                            column="div_bonus")
        div_transfer = _finite(t_raw, code=code, trade_date=trade_date,
                               column="div_transfer")
        rights_num = _finite(r_raw, code=code, trade_date=trade_date,
                             column="rights_num")
        if (div_cash == 0.0 and div_bonus == 0.0 and div_transfer == 0.0
                and rights_num == 0.0):
            raise ExecutionDataQualityError(
                f"CA 明细 {code}@{trade_date} 全为 0/NULL——adj_event 命中"
                f"而 adj_detail 无有效明细（两源不一致）；fail-closed，"
                f"不静默按无事件放行")
        if div_cash < 0:
            raise ExecutionDataQualityError(
                f"CA 明细 {code}@{trade_date} div_cash={div_cash} < 0——"
                f"负分红未定义，fail-closed（不发明抵扣）")
        if div_bonus + div_transfer < 0:
            raise ExecutionDataQualityError(
                f"CA 明细 {code}@{trade_date} 送股/转增合计 "
                f"{div_bonus + div_transfer} < 0——缩股（share-unit 缩减）"
                f"V1 未支持，fail-closed")
        if rights_num < 0:
            raise ExecutionDataQualityError(
                f"CA 明细 {code}@{trade_date} rights_num={rights_num} < 0"
                f"——负配股/缩股未支持，fail-closed")
        if rights_num != 0:
            warnings.warn(
                f"CA Gate 配股不参与（V1 策略）：{code}@{trade_date} "
                f"rights_num={rights_num}（股/10股）；shares/cash 不变；"
                f"配股除权价格落差计入 NAV（未参与的真实成本），"
                f"不视为 share-unit 断链",
                CorporateActionWarning, stacklevel=2)

        cur_qty, cur_sell = pos[code]
        cash += cur_qty * div_cash / 10.0
        new_qty = _scaled_shares(cur_qty, div_bonus, div_transfer)
        new_sell = min(_scaled_shares(cur_sell, div_bonus, div_transfer),
                       new_qty)
        if new_qty <= 0:
            raise ExecutionDataQualityError(
                f"CA 调整后 {code} 股数 {new_qty} <= 0（qty={cur_qty}, "
                f"送转 {div_bonus}/{div_transfer}）——不支持持仓消失语义，"
                f"fail-closed")
        pos[code] = [new_qty, new_sell]

    rows = [(code, qty, sell) for code, (qty, sell) in sorted(pos.items())]
    frame = pl.DataFrame(rows, schema=["code", "quantity", "sellable_quantity"],
                         orient="row")
    frame = frame.with_columns(pl.col("code").cast(pl.String),
                               pl.col("quantity").cast(pl.Int64),
                               pl.col("sellable_quantity").cast(pl.Int64))
    return PortfolioState(as_of_date=state.as_of_date,
                          phase=PortfolioStatePhase.PRE_EXECUTION,
                          cash=cash, positions=frame)
