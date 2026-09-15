"""R22：纯分钟窗口成交引擎——窗口切片 / 价格口径 / 累计 VWAP / 触发 /
参与率顺延 / 封板与缺行判定（全部纯函数，无 IO）。

数据契约（`adapters/intraday.py` docstring 为准，禁止自造）：
`minute_index` 0=09:25 开盘集合竞价、239=15:00 收盘集合竞价；每
(code, 交易日) 240 行网格；OHLC raw、`amount=元`、`volume=股`（vwap =
amount/volume）；缺行/无价分钟跳过（消费侧自守卫）。

语义（design.md §2 固定）：
- 无 `slices` = 单切片 [start,end] 权重 1；每切片目标量 = floor(weight×总量)，
  尾差归最后一片；
- `trigger=None`：逐分钟按 `price_basis` 成交（cap = floor(participation×
  bar.volume)，不足顺延下一分钟）；
- `trigger.mode="limit"`：limit 由 `ref`（pre_close/window_open/window_vwap）
  与 offset_bps 推出（买 ×(1+offset/1e4)、卖 ×(1-offset/1e4)）；买
  `low<=limit` 命中、卖 `high>=limit` 命中，成交价 `min/max(limit, open)`
  （限价或更好）；触发条件**逐分钟重判**（限价单语义——价格回到 limit 才
  继续成交，绝不追价）；
- `trigger.mode="vwap_offset"`：ref 忽略，limit = **截至上一分钟**的窗口
  累计 VWAP × (1±offset)；第一分钟无累计 → 不触发；
- 封板：`open==high==low==limit_up` 的分钟 BUY 跳过、`limit_down` 同理
  SELL；部分封板（high≠low）不拦；
- `fallback="close"`：窗口结束仍有剩余 → 最后窗口 close 一次性补足
  （不参与率约束，`fell_back=True`；封板 close 不补）；`none` 如实返回；
- 全程不读未来：累计 VWAP / 触发只用当前及更早分钟；
- `price_basis` 定义（本模块唯一权威）：vwap=amount/volume、open=bar.open、
  close=bar.close、twap=(open+high+low+close)/4、mid=(high+low)/2。

结果载体：`WindowBacktestResult`（NEXT_WINDOW run 的 BacktestResult 扩展，
携带逐 event 窗口成交明细 `window_fills` 与 `execution_spec`——持久化层
（execution_store）据此写/读 `window_fills.parquet` 与 manifest.execution_spec；
不触碰 domain/backtest.py 的既有契约）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import polars as pl

from factorlab.core.domain.backtest import BacktestResult
from factorlab.core.execution.spec import ExecutionSpec, MinuteWindowSpec, SliceSpec


@dataclass(frozen=True)
class MinuteBar:
    """单分钟 bar（raw 价；None = 该分钟无该价字段）。

    - minute_index 0..239（= 09:25..15:00 契约网格）
    - volume/amount finite >= 0（股 / 元）；价格 None 或 finite > 0
    """

    minute_index: int
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float
    amount: float

    def __post_init__(self) -> None:
        if isinstance(self.minute_index, bool) \
                or not isinstance(self.minute_index, int):
            raise ValueError(
                f"minute_index 必须为 int（收到 {self.minute_index!r}）")
        if not 0 <= self.minute_index <= 239:
            raise ValueError(
                f"minute_index 必须 0..239（收到 {self.minute_index!r}）")
        for name in ("volume", "amount"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"{name} 必须为数值（收到 {v!r}）")
            if not math.isfinite(v) or v < 0:
                raise ValueError(f"{name} 必须 finite >= 0（收到 {v!r}）")
        for name in ("open", "high", "low", "close"):
            v = getattr(self, name)
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"{name} 必须为数值或 None（收到 {v!r}）")
            if not math.isfinite(v) or v <= 0:
                raise ValueError(
                    f"{name} 必须 finite > 0 或 None（收到 {v!r}）")


@dataclass(frozen=True)
class MinuteFill:
    """单分钟成交（price 为该分钟口径价；fell_back = 窗口结束 close 兜底）。"""

    minute_index: int
    quantity: int
    price: float
    fell_back: bool = False


@dataclass(frozen=True)
class WindowFillResult:
    """窗口仿真结果（filled + unfilled == target 恒等式由引擎保证）。"""

    filled_qty: int
    avg_price: float | None
    fills: tuple[MinuteFill, ...]
    unfilled_qty: int


def _basis_price(bar: MinuteBar,
                 basis: Literal["vwap", "open", "close", "twap", "mid"],
                 ) -> float | None:
    """按口径取该分钟成交价；数据不足 → None（跳过该分钟，不发明价格）。"""
    if basis == "vwap":
        if bar.volume <= 0 or bar.amount <= 0:
            return None
        return float(bar.amount) / float(bar.volume)
    if basis == "open":
        return bar.open
    if basis == "close":
        return bar.close
    if basis == "twap":
        if None in (bar.open, bar.high, bar.low, bar.close):
            return None
        return (bar.open + bar.high + bar.low + bar.close) / 4.0
    if basis == "mid":
        if bar.high is None or bar.low is None:
            return None
        return (bar.high + bar.low) / 2.0
    raise ValueError(f"未知 price_basis {basis!r}")


def _limit_price(side: str, base: float, offset_bps: float) -> float:
    """由基准价与 offset_bps 推限价（买 ×(1+offset/1e4)、卖 ×(1-offset/1e4)）。"""
    factor = 1.0 + offset_bps / 10_000.0 if side == "buy" \
        else 1.0 - offset_bps / 10_000.0
    limit = base * factor
    if not math.isfinite(limit) or limit <= 0:
        raise ValueError(
            f"限价非法（base={base} offset_bps={offset_bps} → {limit!r}）"
            f"——不发明触发价")
    return limit


def _is_sealed(bar: MinuteBar, side: str, limit_up: float | None,
               limit_down: float | None) -> bool:
    """一字封板判定（`open==high==low==limit`；部分封板 high≠low 不拦）。"""
    if side == "buy" and limit_up is not None \
            and bar.open == bar.high == bar.low == limit_up:
        return True
    if side == "sell" and limit_down is not None \
            and bar.open == bar.high == bar.low == limit_down:
        return True
    return False


def simulate_window(
    bars: dict[int, MinuteBar],
    *,
    side: Literal["buy", "sell"],
    target_qty: int,
    spec: MinuteWindowSpec,
    ref_price: float | None,
    limit_up: float | None,
    limit_down: float | None,
) -> WindowFillResult:
    """在窗口内仿真单边订单成交（见模块 docstring 语义）。

    Raises:
        TypeError: bars/spec 类型不匹配
        ValueError: side/target_qty/ref_price/limit 非法（fail fast）
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side 仅允许 'buy'/'sell'（收到 {side!r}）")
    if isinstance(target_qty, bool) or not isinstance(target_qty, int):
        raise ValueError(f"target_qty 必须为 int（收到 {target_qty!r}）")
    if target_qty <= 0:
        raise ValueError(f"target_qty 必须 > 0（收到 {target_qty!r}）")
    if not isinstance(spec, MinuteWindowSpec):
        raise TypeError(
            f"spec 必须为 MinuteWindowSpec（收到 {type(spec).__name__}）")
    for name, v in (("limit_up", limit_up), ("limit_down", limit_down)):
        if v is not None and (isinstance(v, bool)
                              or not isinstance(v, (int, float))
                              or not math.isfinite(v) or v <= 0):
            raise ValueError(f"{name} 必须为 finite > 0 或 None（收到 {v!r}）")

    window_bars = sorted(
        ((i, b) for i, b in bars.items() if spec.start <= i <= spec.end),
        key=lambda t: t[0])

    # ---- 切片与目标量（无 slices = 单切片；尾差归最后一片）----
    slices: list[SliceSpec] = list(spec.slices) if spec.slices \
        else [SliceSpec(start=spec.start, end=spec.end, weight=1.0)]
    slice_targets: list[int] = []
    assigned = 0
    for s in slices[:-1]:
        t = math.floor(s.weight * target_qty)
        slice_targets.append(t)
        assigned += t
    slice_targets.append(target_qty - assigned)

    trigger = spec.trigger
    need_ref = trigger is not None and trigger.mode == "limit" \
        and trigger.ref == "pre_close"
    if need_ref:
        if ref_price is None or isinstance(ref_price, bool) \
                or not isinstance(ref_price, (int, float)) \
                or not math.isfinite(ref_price) or ref_price <= 0:
            raise ValueError(
                f"limit 触发（ref=pre_close）要求 ref_price finite > 0"
                f"（收到 {ref_price!r}）——不发明基准价")

    static_limit: float | None = None
    if trigger is not None and trigger.mode == "limit" \
            and trigger.ref != "window_vwap":
        if trigger.ref == "pre_close":
            base = float(ref_price)
        else:                                     # window_open
            base = next((b.open for _i, b in window_bars
                         if b.open is not None), None)
            if base is None:
                raise ValueError(
                    "limit 触发（ref=window_open）但窗口内无任何分钟 open"
                    "——不发明基准价")
        static_limit = _limit_price(side, base, trigger.offset_bps)

    fills: list[MinuteFill] = []
    filled_per_slice = [0] * len(slices)
    si = 0
    cum_amount = 0.0
    cum_volume = 0.0
    for i, b in window_bars:
        prev_vwap = cum_amount / cum_volume if cum_volume > 0 else None
        # 累计窗口 VWAP（所有成交分钟；先取 prev 再累积，不看未来）
        if b.volume > 0 and b.amount > 0:
            cum_amount += float(b.amount)
            cum_volume += float(b.volume)

        while si < len(slices) and i > slices[si].end:
            si += 1
        if si >= len(slices) or i < slices[si].start:
            continue                                   # 切片外分钟不成交
        remaining_slice = slice_targets[si] - filled_per_slice[si]
        if remaining_slice <= 0:
            continue

        # 封板（一字）分钟跳过（部分封板 high≠low 不拦）
        if _is_sealed(b, side, limit_up, limit_down):
            continue

        if trigger is None:
            price = _basis_price(b, spec.price_basis)
            if price is None:
                continue
        else:
            if trigger.mode == "limit":
                limit = static_limit if static_limit is not None else (
                    _limit_price(side, prev_vwap, trigger.offset_bps)
                    if prev_vwap is not None else None)
            else:                                      # vwap_offset
                limit = (_limit_price(side, prev_vwap, trigger.offset_bps)
                         if prev_vwap is not None else None)
            if limit is None:
                continue                               # 无累计 → 不触发
            # 逐分钟重判触发条件（限价单语义：价格回到 limit 才继续成交）
            hit = (b.low is not None and b.low <= limit) if side == "buy" \
                else (b.high is not None and b.high >= limit)
            if not hit:
                continue
            open_ = b.open if b.open is not None else limit
            price = min(limit, open_) if side == "buy" \
                else max(limit, open_)

        cap = math.floor(spec.participation * b.volume)
        if cap < 1:
            continue
        qty = min(remaining_slice, cap)
        fills.append(MinuteFill(minute_index=i, quantity=qty, price=price))
        filled_per_slice[si] += qty

    filled_total = sum(f.quantity for f in fills)
    remaining = target_qty - filled_total

    # ---- fallback close：最后窗口有价 bar 的 close 一次性补齐 ----
    if remaining > 0 and spec.fallback == "close":
        last = next(((i, b) for i, b in reversed(window_bars)
                     if b.close is not None), None)
        if last is not None and not _is_sealed(last[1], side, limit_up,
                                               limit_down):
            fills.append(MinuteFill(minute_index=last[0], quantity=remaining,
                                    price=last[1].close, fell_back=True))
            filled_total += remaining
            remaining = 0

    avg_price = (sum(f.price * f.quantity for f in fills) / filled_total
                 if filled_total > 0 else None)
    return WindowFillResult(filled_qty=filled_total, avg_price=avg_price,
                            fills=tuple(fills), unfilled_qty=remaining)


@dataclass(frozen=True)
class WindowBacktestResult(BacktestResult):
    """NEXT_WINDOW run 的结果载体（BacktestResult 扩展；persistence 契约）。

    - window_fills：与 artifacts 一一对应的逐 event 明细 DataFrame
      （列 code/side/minute_index/quantity/price/fell_back）
    - execution_spec：run 时的 ExecutionSpec（execution_timing=NEXT_WINDOW +
      minute_window——manifest.execution_spec 序列化来源）
    """

    window_fills: tuple = field(default_factory=tuple)
    execution_spec: object = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.execution_spec is None:
            raise ValueError("WindowBacktestResult.execution_spec 不允许 None")
        if not isinstance(self.execution_spec, ExecutionSpec):
            raise ValueError(
                f"execution_spec 必须为 ExecutionSpec（收到 "
                f"{type(self.execution_spec).__name__}）")
        if len(self.window_fills) != len(self.artifacts):
            raise ValueError(
                f"window_fills({len(self.window_fills)}) 必须与 artifacts"
                f"({len(self.artifacts)}) 一一对应")
        for f in self.window_fills:
            if not isinstance(f, pl.DataFrame):
                raise ValueError("window_fills 元素必须为 polars DataFrame")
