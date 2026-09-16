"""M8-06B：backtest runtime——编排已关闭 execution primitives → BacktestResult。

run_backtest 只做 orchestration（M8-06A §3 契约）：
    每 decision：schedule → snapshot → **CA 调整（R07-DATA-I8）** → orders →
    assessment → fills → POST → accounting → NAV entry（open-based marks）
    → advance → 下一 PRE（execution 间隔 > 1 个交易日时，除 CA 事件外纯
    re-date——无 fills 期间 cash/quantity/sellable 不变）

约束：
- 不接收 StrategySpec/SignalArtifact；不引入 strategy logic
- execution_spec 必须显式传入（cost model 显式选择）
- MarksPolicy v1 = OPEN_BASED，叠加**停牌冻结**（closeout 决策 1：
  停牌 = 缺行推断）：
  - 持仓 code 当日无 daily open 行 → 停牌冻结：不产生 fills、估值沿用该
    code **最近一次 mark**（run 内 mark_map 携带，无历史表查询）；多日停牌
    逐日沿用；复牌日真实 open 恢复。账本恒等式不受影响（冻结 code 无 fills）。
  - 目标 code 当日无 open 行 → 隔夜停牌 → 该 order 编排层跳过（不进
    pipeline，fillability 的 missing-evidence fail 属数据未知语义，二者不同层）
  - 整轮无"缺 open → fail run"路径（数据层 coverage gate 仍拦全市场无行）
  - 除权事件由 CA Gate 在开盘前**应用/拦截**（R07-DATA-I8，见
    _apply_ca_adjustments）：窗口 (prev_exec, exec] 内 held(PRE) 命中
    adj_event 行 → 读 adj_detail 明细 → 分红现金入账 / 送转股数缩放
    （floor）→ 调整后 PRE 状态进入 orders/NAV（连续 NAV）；配股 V1 不参与
    （warning）；明细缺失/缩股/停牌命中 → fail-closed（armed = 多事件 +
    持仓非空，armed 且事件表缺失 → fail-closed）
- 全链 fail fast（ExecutionDataQualityError/ValueError 直接传播）——例外：
  m8-06a §6.3 最后一个 execution 后无下一开放日 = 合法终止（保留中间
  artifacts/nav，BacktestResult.trailing_unresolved=True；R01-M8-I5）
- zero-cost zero-slippage 每 event 断言 value-neutrality（POST NAV ==
  PRE NAV @ 同 basis marks）；slippage-free 时 NAV drag == total_fees
- memory-only runtime object（无 persistence/DB 写入）

依赖边界：只 import 既有 primitive modules + domain——无 strategy/engine/
duckdb 直连。
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import timedelta
from enum import Enum

import polars as pl

from factorlab.ports.read import ReadPort
from factorlab.adapters.read.market_open import (load_adj_detail_window,
                                             load_adj_event_window)
from factorlab.adapters.read.minute_window import load_execution_window
from factorlab.core.domain.accounting import PortfolioMarkSnapshot
from factorlab.core.domain.backtest import (BacktestResult, ExecutionArtifact,
                                       NavSeries)
from factorlab.core.domain.execution import (ExecutionDataQualityError,
                                        MarketOpenSnapshot,
                                        OpenOrderDisposition, PortfolioState,
                                        PortfolioStatePhase)
from factorlab.core.domain.portfolio import TargetPortfolio
from factorlab.core.domain.timing import ExecutionTiming
from factorlab.core.execution.accounting import summarize_execution_accounting
from factorlab.core.execution.corporate_actions import apply_corporate_actions
from factorlab.app.backtest.calendar import resolve_execution_schedule
from factorlab.core.execution.fillability import assess_open_fillability
from factorlab.app.backtest.fills import (realize_open_fills,
                                      realize_window_fills)
from factorlab.app.backtest.market import load_market_open_snapshot
from factorlab.core.execution.minute_window import WindowBacktestResult
from factorlab.app.backtest.orders import construct_order_batch
from factorlab.app.backtest.overnight import (TrailingUnresolvedError,
                                          advance_to_next_trading_day)
from factorlab.app.backtest.rules import (SecurityQuantityRules,
                                       resolve_security_quantity_rules)
from factorlab.core.execution.spec import ExecutionSpec
from factorlab.core.execution.state import apply_fill_batch
from factorlab.core.execution.valuation import value_portfolio

_EMPTY_POS = pl.DataFrame(
    {"code": pl.Series([], dtype=pl.String),
     "quantity": pl.Series([], dtype=pl.Int64),
     "sellable_quantity": pl.Series([], dtype=pl.Int64)})

_EMPTY_MINUTES = pl.DataFrame(
    {"code": pl.Series([], dtype=pl.String),
     "minute_index": pl.Series([], dtype=pl.Int64),
     "open": pl.Series([], dtype=pl.Float64),
     "high": pl.Series([], dtype=pl.Float64),
     "low": pl.Series([], dtype=pl.Float64),
     "close": pl.Series([], dtype=pl.Float64),
     "volume": pl.Series([], dtype=pl.Float64),
     "amount": pl.Series([], dtype=pl.Float64),
     "session_type": pl.Series([], dtype=pl.Int64)})


class MarksPolicy(Enum):
    """NAV marks 来源策略。

    - OPEN_BASED：execution date raw open（NEXT_OPEN v1）
    - WINDOW_END_BASED：执行日**窗口末分钟 close**（NEXT_WINDOW；该 code 当日
      无分钟行 → 沿用 mark_map 上次 mark——停牌冻结语义同 OPEN_BASED）
    """

    OPEN_BASED = "open_based"
    WINDOW_END_BASED = "window_end_based"


def _canonicalize_window(minute_frame: pl.DataFrame, codes: list[str],
                         exec_date) -> pl.DataFrame:
    """批读 6 位 code → canonical ts_code（读契约输出 6 位；M8 全链 canonical）。"""
    mapping = {c.split(".")[0]: c for c in codes}
    unknown = sorted({c for c in minute_frame["code"].to_list()
                      if "." not in c and c not in mapping})
    if unknown:
        raise ExecutionDataQualityError(
            f"{unknown} 出现在分钟 frame 但不在 planning codes 中——"
            f"读取范围外数据（cross-object coverage bug）")
    return minute_frame.with_columns(
        pl.when(pl.col("code").is_in(list(mapping)))
        .then(pl.col("code").replace_strict(mapping))
        .otherwise(pl.col("code")).alias("code"))


def _window_open_prices(minute_frame: pl.DataFrame, codes: list[str],
                        exec_date) -> dict[str, float]:
    """窗口首分钟 open 规划参考价（缺任一 code → fail fast，不发明价格）。"""
    valid = minute_frame.filter(pl.col("open").is_not_null())
    first = valid.sort(["code", "minute_index"]).unique(
        subset=["code"], keep="first")
    m = dict(zip(first["code"].to_list(), first["open"].to_list()))
    missing = [c for c in codes if c not in m]
    if missing:
        raise ExecutionDataQualityError(
            f"{missing} 在 {exec_date} 缺窗口首分钟 open（分钟数据缺失/停牌）"
            f"——NEXT_WINDOW 规划参考价 fail fast（不发明价格）")
    return {c: float(m[c]) for c in codes}


def _marks_from_window(minute_frame: pl.DataFrame, codes: list[str], date, *,
                       mark_map: dict[str, float]) -> PortfolioMarkSnapshot:
    """窗口末分钟 close → mark（无分钟行 → 沿用 mark_map 上次 mark）。"""
    rows = []
    for code in sorted(codes):
        sub = minute_frame.filter(pl.col("code") == code)
        close = None
        if sub.height:
            valid = sub.filter(pl.col("close").is_not_null())
            if valid.height:
                close = valid.sort("minute_index")["close"][-1]
        if close is not None:
            mark_map[code] = close
            mark = close
        else:
            mark = mark_map.get(code)
            if mark is None:
                raise ExecutionDataQualityError(
                    f"{code} 在 {date} 无窗口分钟 close 且 run mark_map 无先前 "
                    f"mark——无法估值（结构上不应发生，防御性 fail）")
        rows.append((code, mark))
    frame = pl.DataFrame(rows, schema=["code", "mark_price"], orient="row")
    frame = frame.with_columns(pl.col("code").cast(pl.String),
                               pl.col("mark_price").cast(pl.Float64))
    return PortfolioMarkSnapshot(as_of_date=date, frame=frame)


def _marks_from_snapshot(snapshot, codes: list[str], date, *,
                         mark_map: dict[str, float]) -> PortfolioMarkSnapshot:
    """integration 层：snapshot.open → PortfolioMarkSnapshot（valuation.py
    不 import snapshot）。估值按 code 精确查询 marks。

    WS4 停牌冻结（缺行 = 停牌）：code 当日无 open（has_daily=False）→ mark
    沿用 run 内 mark_map 的**最近一次真实 open mark**（多日停牌逐日沿用、
    无历史表查询）；有真实 open（含复牌日）→ 刷新 mark_map。既无 open 亦无
    先前 mark（结构上不可能：能持仓必有买入日真实 open）→ 防御性 fail，
    不发明估值。
    """
    rows = []
    for code in sorted(codes):
        r = snapshot.frame.filter(pl.col("code") == code)
        if r.height != 1:
            raise ValueError(f"snapshot 缺 {code}")
        open_ = r["open"][0]
        if open_ is not None:
            mark_map[code] = open_          # 真实 open（含复牌日）刷新
            mark = open_
        else:
            mark = mark_map.get(code)
            if mark is None:
                raise ExecutionDataQualityError(
                    f"{code} 在 {date} 无 open evidence（停牌）且 run mark_map "
                    f"无先前 open mark——无法估值（结构上不应发生：持仓必有 "
                    f"买入日真实 open，防御性 fail 拒绝无依据 mark")
        rows.append((code, mark))
    frame = pl.DataFrame(rows, schema=["code", "mark_price"], orient="row")
    frame = frame.with_columns(pl.col("code").cast(pl.String),
                               pl.col("mark_price").cast(pl.Float64))
    return PortfolioMarkSnapshot(as_of_date=date, frame=frame)


def _apply_ca_adjustments(rd: ReadPort, *, decision_date, prev_exec_date,
                          exec_date, state: PortfolioState,
                          halted_codes: list[str]) -> PortfolioState:
    """R07-DATA-I8 CA Gate v2（M8-06A §5.5 落地升级；事件源 = adj_event +
    adj_detail）：窗口内 held(PRE) 除权事件 → **execution date 开盘前调整**
    （分红现金入账 / 送转股数缩放 / 配股不参与）或 fail-closed 拒绝。

    懒性触发在调用处（多事件 + 持仓非空）。语义：
    - **fail-closed**（保持 WS5 不加宽）：
      - armed 且 adj_event 表缺失 → 明确报错（数据任务未完成不静默降级）
      - 命中事件但 adj_detail 表缺失 / (code, date) 无明细行 → 明确报错
        （事件只有日期，调整量在明细；缺明细不得静默按无事件放行）
      - 命中停牌持仓（exec_date 无 daily open，冻结 mark 为除权前 basis）→
        拒绝（调整后无法估值=不发明价格；WS4 × CA 交叉）
      - 负分红/缩股/负配股/明细全 0 → primitive fail-closed
    - 窗口 = (prev_exec_date, exec_date] **左开右闭**：除权事件当日零点生效、
      隔夜持仓断链 → 右端闭（B4/B7）；买入日 = 事件日的新买 code 以当日
      post-CA 价成交、无隔夜断链 → 左端开（B6 豁免——持有跨窗口才检测）
    - 已支持事件 → apply_corporate_actions（资格日 = PRE 持仓 = 窗口内
      每日收市持仓；多事件按 (code, date) 复合）
    """
    held_codes = state.positions["code"].to_list()
    if "adj_event" not in rd.tables():
        raise ExecutionDataQualityError(
            f"CA Gate fail-closed：缺 adj_event 表（decision {decision_date} "
            f"→ execution {exec_date}，持仓 {len(held_codes)} code 跨 "
            f"{prev_exec_date}→{exec_date} 窗口）——CA Gate 需除权事件数据"
            f"（real 数据任务未完成 / 合成请 seed 空表）；不确认窗口内无 CA "
            f"事件即不产出连续 NAV")
    events = load_adj_event_window(
        rd, start_date=prev_exec_date + timedelta(days=1),
        end_date=exec_date, codes=held_codes)
    if not events.height:
        return state
    hits = [f"{r[0]}@{r[1]}" for r in events.iter_rows()]
    event_codes = sorted({r[0] for r in events.iter_rows()})
    halted_hits = [c for c in event_codes if c in set(halted_codes)]
    if halted_hits:
        raise ExecutionDataQualityError(
            f"CA Gate fail-closed：除权事件 {hits} 命中停牌持仓 "
            f"{halted_hits}（{exec_date} 无 daily open = 停牌冻结）——冻结 "
            f"mark 为除权前 basis，股数/现金调整后无法估值（不发明价格）；"
            f"请以 decision_range 绕开或等待复牌（WS4 × CA 交叉）")
    if "adj_detail" not in rd.tables():
        raise ExecutionDataQualityError(
            f"CA Gate fail-closed：adj_event 命中 {hits}，但缺 adj_detail "
            f"明细表——事件只有日期，分红/送转/配股调整量在 adj_detail；"
            f"无法执行除权调整即不产出连续 NAV（真实数据任务："
            f"platform/tools/ch_ingest/adj_backfill.py；合成 run：seed "
            f"明细表或清空 adj_event）。窗口 "
            f"({prev_exec_date}, {exec_date}]")
    details = load_adj_detail_window(
        rd, start_date=prev_exec_date + timedelta(days=1),
        end_date=exec_date, codes=held_codes)
    detail_keys = {(r[0], r[1]) for r in details.iter_rows()}
    missing = [f"{r[0]}@{r[1]}" for r in events.iter_rows()
               if (r[0], r[1]) not in detail_keys]
    if missing:
        raise ExecutionDataQualityError(
            f"CA Gate fail-closed：事件明细缺失（adj_detail 无对应行）"
            f"{missing}——fail-closed 不静默按无事件放行")
    # 只消费**事件命中**的明细行：adj_detail 是全量行级表（18M 行，非事件行
    # 明细 NULL）——非事件行不得进入 primitive（全 0 = 不一致 fail-closed
    # 是给"adj_event 命中但明细无效"的，不是给未命中行）。loader 已按
    # held_codes 过滤；join 后排序保证确定性。
    scoped = (events.join(details, on=["code", "trade_date"], how="inner")
              .sort(["code", "trade_date"]))
    return apply_corporate_actions(state, scoped)


def run_backtest(
    target: TargetPortfolio,
    execution_spec: ExecutionSpec,
    rd: ReadPort,
    *,
    marks: MarksPolicy = MarksPolicy.OPEN_BASED,
    decision_range: tuple | None = None,
) -> BacktestResult:
    """按 target.decision_dates 顺序编排完整 execution pipeline。

    decision_range 为 decision 级过滤（target.decision_dates ∩ range）——
    schedule 只解析范围内 decisions（范围外 trailing unresolved 不影响本 run）。

    rd 为读句柄（duckdb|ch，经 data/backend.open_read 打开）。

    Raises:
        TypeError / ValueError / NotImplementedError / ExecutionDataQualityError
          ——全部直接传播（fail fast，不 per-day skip）。例外：最后一个
          execution 的 overnight advance 遇到 trailing unresolved（§6.3）
          → 合法终止返回（trailing_unresolved=True）。
    """
    if not isinstance(target, TargetPortfolio):
        raise TypeError(
            f"target 必须为 TargetPortfolio（收到 {type(target).__name__}）")
    if not isinstance(execution_spec, ExecutionSpec):
        raise TypeError(
            f"execution_spec 必须显式传入 ExecutionSpec（收到 "
            f"{type(execution_spec).__name__}——cost model 显式选择 Gate）")
    if not isinstance(rd, ReadPort):
        raise TypeError(f"rd 必须为读句柄（收到 {type(rd).__name__}）")

    # ---- R22 timing 分派：NEXT_OPEN（默认，零改动）/ NEXT_WINDOW / NEXT_CLOSE 拒绝 ----
    timing = execution_spec.execution_timing
    is_window = timing is ExecutionTiming.NEXT_WINDOW
    if timing is ExecutionTiming.NEXT_CLOSE:
        raise NotImplementedError(
            "NEXT_CLOSE 未实现（设计非目标；既有 7 处显式拒绝保持不动）")
    if marks not in (MarksPolicy.OPEN_BASED, MarksPolicy.WINDOW_END_BASED):
        raise NotImplementedError(
            f"MarksPolicy 仅支持 OPEN_BASED/WINDOW_END_BASED（收到 {marks!r}"
            f"——caller-explicit/stale policy 未实现）")
    if marks is MarksPolicy.WINDOW_END_BASED and not is_window:
        raise ValueError(
            "MarksPolicy.WINDOW_END_BASED 仅适用于 execution_timing="
            "NEXT_WINDOW（NEXT_OPEN 必须用 OPEN_BASED）")
    minute_window = execution_spec.minute_window
    if is_window and minute_window is None:                   # pydantic 已拦
        raise ValueError("NEXT_WINDOW 缺 minute_window")

    window_details: list[pl.DataFrame] = []

    # ---- 决策序列（R01-M8-I4：range 内 target/schedule 一致）----
    all_dates = list(target.decision_dates)
    scoped_target = target
    if decision_range is not None:
        lo, hi = decision_range
        all_dates = [d for d in all_dates if lo <= d <= hi]
        if not all_dates:
            raise ValueError("decision_range 内无任何 decision——empty run 拒绝")
        # 只解析 range 内 decisions 的 schedule——范围外（含尾部未决）决策
        # 不参与本次 run，也不得使其失败
        scoped_target = TargetPortfolio(
            frame=target.frame.filter(
                pl.col("decision_date").is_in(all_dates)),
            decision_dates=tuple(all_dates), meta=target.meta)

    # ---- schedule（scoped target——construct_order_batch 要求全局一致）----
    schedule = resolve_execution_schedule(scoped_target, rd)

    def _exec_date(d):
        r = schedule.frame.filter(pl.col("decision_date") == d)
        return r["execution_date"][0]

    # ---- 初始 PRE state @ 第一 execution date ----
    state = PortfolioState(as_of_date=_exec_date(all_dates[0]),
                           phase=PortfolioStatePhase.PRE_EXECUTION,
                           cash=execution_spec.initial_cash,
                           positions=_EMPTY_POS)

    artifacts = []
    nav_rows = []
    trailing = False
    final_state = None
    # run 内最近一次真实 open mark（停牌冻结沿用；无历史表查询）
    mark_map: dict[str, float] = {}
    for decision_d in all_dates:
        exec_date = _exec_date(decision_d)
        if state.as_of_date != exec_date:
            if state.as_of_date > exec_date:
                raise ValueError(
                    f"state date {state.as_of_date} 超过 event date {exec_date}")
            # 纯 re-date：间隔日无 fills/CA——cash/quantity/sellable 不变
            state = PortfolioState(as_of_date=exec_date,
                                   phase=PortfolioStatePhase.PRE_EXECUTION,
                                   cash=state.cash, positions=state.positions)

        # ---- 市场证据（planning codes = current ∪ target(d)）----
        t_rows = scoped_target.frame.filter(
            pl.col("decision_date") == decision_d)
        codes = sorted(set(state.positions["code"].to_list())
                       | set(t_rows["code"].to_list()))
        snapshot = load_market_open_snapshot(rd, execution_date=exec_date,
                                             codes=codes)
        rules = resolve_security_quantity_rules(rd, codes)
        halted = sorted(
            c for c, has_daily in
            snapshot.frame.select(["code", "has_daily"]).iter_rows()
            if not has_daily)

        # ---- R07-DATA-I8 CA Gate v2（懒性：第二 event 起且持仓非空）----
        # 窗口 (prev_exec, exec] 内 held(PRE) 事件 → 开盘前调整（分红/送转/
        # 配股不参与）或 fail-closed（缺明细/停牌/缩股）。调整后 state 才进入
        # orders/NAV——停牌状态先算好供 CA 判定（冻结 mark 不可作调整后 basis）。
        if artifacts and state.positions.height:
            state = _apply_ca_adjustments(
                rd, decision_date=decision_d,
                prev_exec_date=artifacts[-1].execution_date, exec_date=exec_date,
                state=state, halted_codes=halted)

        # ---- WS4 停牌 mask（缺行 = 停牌：持仓冻结 / 目标跳过）----
        # orders/fillability 对缺 evidence 是 fail-fast 原语（不动它们）——
        # 停牌码不得进入 planning universe：持仓冻结码从 state 过滤副本剔除
        # （无 SELL 生成）、隔夜停牌目标行从 target 事件切片剔除（无 BUY
        # 生成）。目标权重下调到可见部分（gross_exposure 随迁——冻结缺口 =
        # 现金自然持有，不归一化 A5/A6 语义）；目标行全停牌 → 0 rows =
        # all-cash（与显式空目标同构，可见持仓照常清仓意图不变）。估值在
        # 真实 state 上进行，冻结码 mark 沿用 mark_map（_marks_from_snapshot）。
        if not halted:
            plan = (scoped_target, state, snapshot, rules)  # 无停牌：原对象（A7）
        else:
            visible = [c for c in codes if c not in halted]
            plan_state = PortfolioState(as_of_date=state.as_of_date,
                                        phase=state.phase,
                                        cash=state.cash,
                                        positions=state.positions.filter(
                                            pl.col("code").is_in(visible)))
            plan_snapshot = MarketOpenSnapshot(
                execution_date=snapshot.execution_date,
                frame=snapshot.frame.filter(pl.col("code").is_in(visible)))
            plan_rules = SecurityQuantityRules(
                frame=rules.frame.filter(pl.col("code").is_in(visible)))
            halt_t = sorted(set(t_rows["code"].to_list()) & set(halted))
            if not halt_t:
                plan_target = scoped_target             # 停牌码全在持仓侧
            else:
                t_vis = t_rows.filter(~pl.col("code").is_in(halted))
                meta_d = (replace(scoped_target.meta,
                                  gross_exposure=float(
                                      t_vis["target_weight"].sum()))
                          if t_vis.height else scoped_target.meta)
                plan_target = TargetPortfolio(
                    frame=t_vis, decision_dates=scoped_target.decision_dates,
                    meta=meta_d)
            plan = (plan_target, plan_state, plan_snapshot, plan_rules)
        plan_target, plan_state, plan_snapshot, plan_rules = plan

        # ---- 已关闭 pipeline（plan 输入已 mask；成交/记账用真实对象）----
        if is_window:
            window_codes = plan_snapshot.frame["code"].to_list()
            if window_codes:
                minute_frame = _canonicalize_window(
                    load_execution_window(rd, window_codes,
                                          exec_date.isoformat(),
                                          minute_window.start,
                                          minute_window.end),
                    window_codes, exec_date)
                planning_prices = _window_open_prices(minute_frame, window_codes,
                                                      exec_date)
            else:
                minute_frame = _EMPTY_MINUTES
                planning_prices = {}
            orders = construct_order_batch(
                plan_target, schedule, plan_state, plan_snapshot, plan_rules,
                decision_date=decision_d, planning_prices=planning_prices)
        else:
            minute_frame = None
            orders = construct_order_batch(plan_target, schedule, plan_state,
                                           plan_snapshot, plan_rules,
                                           decision_date=decision_d)
        assessment = assess_open_fillability(orders, snapshot)
        if is_window:
            realized = realize_window_fills(
                orders, state, minute_frame=minute_frame, snapshot=snapshot,
                spec=minute_window, quantity_rules=rules,
                cost_spec=execution_spec.cost_model)
            fills = realized.fill_batch
            window_details.append(realized.detail)
        else:
            fills = realize_open_fills(orders, assessment, state, snapshot,
                                       rules, execution_spec.cost_model)
        post = apply_fill_batch(state, fills)
        accounting = summarize_execution_accounting(state, fills, post)

        # ---- marks + valuation + sanity（冻结沿用 mark_map）----
        pre_codes = state.positions["code"].to_list()
        post_codes = post.positions["code"].to_list()
        if is_window:
            pre_marks = _marks_from_window(minute_frame, pre_codes, exec_date,
                                           mark_map=mark_map)
            post_marks = _marks_from_window(minute_frame, post_codes, exec_date,
                                            mark_map=mark_map)
        else:
            pre_marks = _marks_from_snapshot(snapshot, pre_codes, exec_date,
                                             mark_map=mark_map)
            post_marks = _marks_from_snapshot(snapshot, post_codes, exec_date,
                                              mark_map=mark_map)
        pre_nav = value_portfolio(state, pre_marks)
        post_nav = value_portfolio(post, post_marks)
        total_fees = fills.frame["total_fees"].sum() if fills.frame.height \
            else 0.0
        if is_window:
            # 窗口执行零差异锚：NAV 变化 == 成交 mark-to-market + 费用
            # （mark 与成交价口径不同，NEXT_OPEN 的零成本不变式不适用）
            expected_delta = 0.0
            for code, side, filled, exec_p in fills.frame.select(
                    ["code", "side", "filled_quantity",
                     "execution_price"]).iter_rows():
                m = mark_map[code]
                expected_delta += (filled * (m - exec_p) if side == "buy"
                                   else filled * (exec_p - m))
            expected_delta -= total_fees
            if not math.isclose(post_nav.nav, pre_nav.nav + expected_delta,
                                rel_tol=1e-9, abs_tol=1e-6):
                raise RuntimeError(
                    f"{exec_date} NEXT_WINDOW NAV 恒等式失败：POST NAV "
                    f"{post_nav.nav} != PRE NAV {pre_nav.nav} + MTM "
                    f"{expected_delta}（fees {total_fees}）")
        else:
            slippage_free = fills.frame.height == 0 or bool(
                (fills.frame["execution_price"]
                 == fills.frame["reference_price"]).all())
            if slippage_free:
                # 浮点容差（2026-09-08 真实段实测）：合成价（小整数/二分位）恰好
                # 二进制可表示 → exact 相等成立；真实价（任意小数 × 大 qty 累计）
                # 单 ulp 噪声（~1e-10@7e5）即破坏 exact 比较。rel 1e-9/abs 1e-6
                # 只放行 float 舍入，真实缺陷（错价/错 qty/漏记账）偏差 ≥ 分级别。
                if not math.isclose(post_nav.nav, pre_nav.nav - total_fees,
                                    rel_tol=1e-9, abs_tol=1e-6):
                    raise RuntimeError(
                        f"{exec_date} value-neutrality sanity 失败：POST NAV "
                        f"{post_nav.nav} != PRE NAV {pre_nav.nav} - fees "
                        f"{total_fees}")

        # ---- disposition 计数（只读诊断）----
        counts = [0, 0, 0, 0]
        if assessment.frame.height:
            for disp in assessment.frame["disposition"].to_list():
                counts[list(OpenOrderDisposition).index(OpenOrderDisposition(disp))] += 1

        artifact = ExecutionArtifact(
            decision_date=decision_d, execution_date=exec_date,
            pre_state=state, orders=orders, assessment=assessment,
            fills=fills, post_state=post, accounting=accounting,
            nav=post_nav, disposition_counts=tuple(counts))
        artifacts.append(artifact)
        nav_rows.append((exec_date, post.cash, post_nav.market_value,
                         post_nav.nav))

        # ---- overnight → 下一 PRE（最后 event 也 advance——final_state）----
        try:
            state = advance_to_next_trading_day(post, fills, rd)
        except TrailingUnresolvedError:
            # m8-06a §6.3：最后一个 execution 后无下一开放日 = 合法终止——
            # 保留已产出的 artifacts/nav（不 drop）；final_state = 最后 POST
            # （无 next open，不以合成日期冒充 PRE）。非最后决策仍 fail。
            if decision_d != all_dates[-1]:
                raise
            trailing = True
            final_state = post
            break

    nav_frame = pl.DataFrame(nav_rows, schema=["execution_date", "cash",
                                               "market_value", "nav"],
                             orient="row")
    nav_frame = nav_frame.with_columns(
        pl.col("execution_date").cast(pl.Date),
        pl.col("cash").cast(pl.Float64),
        pl.col("market_value").cast(pl.Float64),
        pl.col("nav").cast(pl.Float64))
    if is_window:
        return WindowBacktestResult(
            artifacts=tuple(artifacts), nav_series=NavSeries(frame=nav_frame),
            final_state=final_state if trailing else state,
            trailing_unresolved=trailing,
            window_fills=tuple(window_details), execution_spec=execution_spec)
    return BacktestResult(artifacts=tuple(artifacts),
                          nav_series=NavSeries(frame=nav_frame),
                          final_state=final_state if trailing else state,
                          trailing_unresolved=trailing)
