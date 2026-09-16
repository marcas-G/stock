#!/usr/bin/env python
"""L5 路径依赖规则层（V1：研究侧近似，Plan S Task 6）。

设计边界（`docs/reviews/2026-09-16-strategy-decomposition/design.md` §4-G3）：

- `max_hold`：**调仓日粒度近似**——某 code 自进入目标组合起，连续持有的交易日数
  （交易日历按 `(entry, decision]` 计数）严格超过上限 → 该调仓日强制换出。
  **不是成交明细级**：不逐日盯市、不模拟盘中止损/部分成交/T+1；被换出日期是
  调仓日（非触发日）。
- 换出语义（V1）：被换出 code 从该日目标中删除；剩余持仓按 `gross_exposure`
  **再归一**（`TargetPortfolio` gross invariant 不允许留现金缺口）；不引入替补
  候选（规则层不消费 signal，避免与 L3/L4 重复决策）。某日全部被换出 = 显式
  all-cash（0 rows，decision_date 保留）。
- `stop_loss` / `take_profit`：V1 未实现，显式 `NotImplementedError`。平台化
  触发条件（Plan S Task 7）：V1 近似的偏差在复盘中证实有实质影响后另立里程碑
  （需要连续/日内触发语义 + 成交明细级回放）。

注：本模块属研究侧 V1；`docs/reviews/**` 只读，边界登记以本 docstring +
策略索引/档案为准。
"""

from __future__ import annotations

import bisect
import datetime
from collections.abc import Iterable, Sequence

import polars as pl

from factorlab.core.domain.portfolio import TargetPortfolio

_SCHEMA = {"decision_date": pl.Date, "code": pl.String,
           "target_weight": pl.Float64}


def _age(cal: Sequence[datetime.date], entry: datetime.date,
         decision: datetime.date) -> int:
    """连续持有交易日数：日历中落在 `(entry, decision]` 的交易日个数。"""
    return (bisect.bisect_right(cal, decision)
            - bisect.bisect_right(cal, entry))


def _validate_trading_dates(trading_dates: Iterable[datetime.date]) -> list[datetime.date]:
    cal = list(trading_dates)
    for d in cal:
        if not isinstance(d, datetime.date) or isinstance(d, datetime.datetime):
            raise ValueError(f"trading_dates 元素必须为 datetime.date（收到 {d!r}）")
    return sorted(set(cal))


def apply_max_hold(target: TargetPortfolio, max_hold: int,
                   trading_dates: Iterable[datetime.date]) -> TargetPortfolio:
    """按连续持有交易日上限强制换出（调仓日粒度近似）。

    - 输入 target 不被修改（pure）；输出保持 decision_dates/meta（含 all-cash 日）；
    - 无换出发生的日期逐值原样（含浮点），有换出的日期剩余权重再归一；
    - 复杂规则（止损/止盈）请走 `apply_l5_rules`（显式 NotImplementedError）。
    """
    if not isinstance(target, TargetPortfolio):
        raise TypeError(
            f"target 必须为 TargetPortfolio（收到 {type(target).__name__}）")
    if isinstance(max_hold, bool) or not isinstance(max_hold, int) or max_hold < 1:
        raise ValueError(f"max_hold 必须为 >=1 的 strict int（收到 {max_hold!r}）")
    cal = _validate_trading_dates(trading_dates)

    by_date: dict[datetime.date, list[tuple[str, float]]] = {}
    for d, code, w in zip(target.frame["decision_date"].to_list(),
                          target.frame["code"].to_list(),
                          target.frame["target_weight"].to_list()):
        by_date.setdefault(d, []).append((code, w))

    streak: dict[str, datetime.date] = {}
    rows: list[tuple[datetime.date, str, float]] = []
    gross = target.meta.gross_exposure
    for d in target.decision_dates:
        day = by_date.get(d, [])
        if not day:
            streak.clear()              # 显式 all-cash 日：全部持有中断
            continue
        kept: list[tuple[str, float]] = []
        for code, w in day:
            entry = streak.get(code)
            if entry is not None and _age(cal, entry, d) > max_hold:
                del streak[code]        # 强制换出：连续持有结束
                continue
            kept.append((code, w))
            if entry is None:
                streak[code] = d        # 新进/重新进入：从本决策日起算
        day_codes = {code for code, _ in day}
        for code in [c for c in streak if c not in day_codes]:
            del streak[code]            # 未入选 → 连续持有中断
        if not kept:
            continue                    # 全换出：all-cash 日（0 rows）
        if len(kept) == len(day):
            rows.extend((d, code, w) for code, w in kept)
        else:
            total = sum(w for _, w in kept)
            scale = gross / total
            rows.extend((d, code, w * scale) for code, w in kept)

    frame = pl.DataFrame(rows, schema=_SCHEMA, orient="row")
    return TargetPortfolio(frame=frame, decision_dates=target.decision_dates,
                           meta=target.meta)


def apply_l5_rules(target: TargetPortfolio, rules, trading_dates) -> TargetPortfolio:
    """L5 规则分发：V1 仅 `max_hold`；其余复杂规则显式 NotImplementedError。"""
    for name in ("stop_loss", "take_profit"):
        value = getattr(rules, name, None)
        if value is not None:
            raise NotImplementedError(
                f"L5 规则 {name}={value} 在 V1 研究侧未实现——止损/止盈需要连续"
                f"盯市或日内触发语义（成交明细级回放），属平台化另立项范围"
                f"（Plan S Task 7 触发条件：V1 近似偏差在复盘中证实有实质影响）；"
                f"当前仅支持 max_hold")
    max_hold = getattr(rules, "max_hold", None)
    if max_hold is None:
        return target
    return apply_max_hold(target, max_hold, trading_dates)
