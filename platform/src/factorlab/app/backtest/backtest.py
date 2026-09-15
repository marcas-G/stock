"""M8-06B：backtest runtime——编排已关闭 execution primitives → BacktestResult。

run_backtest 只做 orchestration（M8-06A §3 契约）：
    每 decision：schedule → snapshot → orders → assessment → fills → POST
    → accounting → NAV entry（open-based marks）→ advance → 下一 PRE
    （execution 间隔 > 1 个交易日时纯 re-date——无 fills/CA 期间状态只变日期）

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
  - 除权事件由 CA Gate 拦截（WS5：窗口 (prev_exec, exec] 内 held(PRE) 命中
    adj_event 行 → ExecutionDataQualityError + decision_range 分段指引；
    armed = 多事件 + 持仓非空，armed 且事件表缺失 → fail-closed）
- 全链 fail fast（ExecutionDataQualityError/ValueError 直接传播）
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
from factorlab.adapters.read.market_open import load_adj_event_window
from factorlab.core.domain.accounting import PortfolioMarkSnapshot
from factorlab.core.domain.backtest import (BacktestResult, ExecutionArtifact,
                                       NavSeries)
from factorlab.core.domain.execution import (ExecutionDataQualityError,
                                        MarketOpenSnapshot,
                                        OpenOrderDisposition, PortfolioState,
                                        PortfolioStatePhase)
from factorlab.core.domain.portfolio import TargetPortfolio
from factorlab.core.execution.accounting import summarize_execution_accounting
from factorlab.app.backtest.calendar import resolve_execution_schedule
from factorlab.core.execution.fillability import assess_open_fillability
from factorlab.app.backtest.fills import realize_open_fills
from factorlab.app.backtest.market import load_market_open_snapshot
from factorlab.app.backtest.orders import construct_order_batch
from factorlab.app.backtest.overnight import advance_to_next_trading_day
from factorlab.app.backtest.rules import (SecurityQuantityRules,
                                       resolve_security_quantity_rules)
from factorlab.core.execution.spec import ExecutionSpec
from factorlab.core.execution.state import apply_fill_batch
from factorlab.core.execution.valuation import value_portfolio

_EMPTY_POS = pl.DataFrame(
    {"code": pl.Series([], dtype=pl.String),
     "quantity": pl.Series([], dtype=pl.Int64),
     "sellable_quantity": pl.Series([], dtype=pl.Int64)})


class MarksPolicy(Enum):
    """NAV marks 来源策略（v1 只实现 OPEN_BASED）。"""

    OPEN_BASED = "open_based"


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


def _assert_ca_gate(rd: ReadPort, *, decision_date, prev_exec_date, exec_date,
                    held_codes: list[str]) -> None:
    """WS5 CA Gate（M8-06A §5.5 落地；closeout 决策 2，事件源 = adj_event）。

    懒性触发在调用处（多事件 + 持仓非空）。语义：
    - **fail-closed**：armed 且 adj_event 表缺失 → 明确报错（数据任务未完成
      不静默降级——无事件表即无法证明窗口无 CA 事件）
    - 窗口 = (prev_exec_date, exec_date] **左开右闭**：除权事件当日零点生效、
      隔夜持仓断链 → 右端闭（B4/B7）；买入日 = 事件日的新买 code 以当日
      post-CA 价成交、无隔夜断链 → 左端开（B6 豁免——持有跨窗口才检测）
    - 命中 → ExecutionDataQualityError（附 code/事件 trade_date/decision_range
      分段指引；不携带任何 adj_factor 列值——事件表可只有日期列）
    """
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
    if events.height:
        hits = [f"{r[0]}@{r[1]}" for r in events.iter_rows()]
        raise ExecutionDataQualityError(
            f"CA Gate：持仓 {sorted(held_codes)} 在窗口 "
            f"({prev_exec_date}, {exec_date}] 内出现除权事件 "
            f"{hits}——跨 share-unit basis 的连续 NAV/return 无定义"
            f"（M8-06A §5.5）；请以 decision_range 分段 run（CA handling "
            f"里程碑前禁止跨 CA 连续估值）")


def run_backtest(
    target: TargetPortfolio,
    execution_spec: ExecutionSpec,
    rd: ReadPort,
    *,
    marks: MarksPolicy = MarksPolicy.OPEN_BASED,
    decision_range: tuple | None = None,
) -> BacktestResult:
    """按 target.decision_dates 顺序编排完整 execution pipeline。

    rd 为读句柄（duckdb|ch，经 data/backend.open_read 打开）。

    Raises:
        TypeError / ValueError / NotImplementedError / ExecutionDataQualityError
          ——全部直接传播（fail fast，不 per-day skip）
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
    if marks is not MarksPolicy.OPEN_BASED:
        raise NotImplementedError(
            f"MarksPolicy v1 仅支持 OPEN_BASED（收到 {marks!r}——"
            f"caller-explicit/stale policy 未实现）")

    # ---- 决策序列 ----
    all_dates = list(target.decision_dates)
    if decision_range is not None:
        lo, hi = decision_range
        all_dates = [d for d in all_dates if lo <= d <= hi]
    if not all_dates:
        raise ValueError("decision_range 内无任何 decision——empty run 拒绝")

    # ---- schedule（全 target——construct_order_batch 要求全局一致）----
    schedule = resolve_execution_schedule(target, rd)

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

        # ---- WS5 CA Gate（懒性：第二 event 起且持仓非空；窗口左开右闭）----
        if artifacts and state.positions.height:
            _assert_ca_gate(rd, decision_date=decision_d,
                            prev_exec_date=artifacts[-1].execution_date,
                            exec_date=exec_date,
                            held_codes=state.positions["code"].to_list())

        # ---- 市场证据（planning codes = current ∪ target(d)）----
        t_rows = target.frame.filter(pl.col("decision_date") == decision_d)
        codes = sorted(set(state.positions["code"].to_list())
                       | set(t_rows["code"].to_list()))
        snapshot = load_market_open_snapshot(rd, execution_date=exec_date,
                                             codes=codes)
        rules = resolve_security_quantity_rules(rd, codes)

        # ---- WS4 停牌 mask（缺行 = 停牌：持仓冻结 / 目标跳过）----
        # orders/fillability 对缺 evidence 是 fail-fast 原语（不动它们）——
        # 停牌码不得进入 planning universe：持仓冻结码从 state 过滤副本剔除
        # （无 SELL 生成）、隔夜停牌目标行从 target 事件切片剔除（无 BUY
        # 生成）。目标权重下调到可见部分（gross_exposure 随迁——冻结缺口 =
        # 现金自然持有，不归一化 A5/A6 语义）；目标行全停牌 → 0 rows =
        # all-cash（与显式空目标同构，可见持仓照常清仓意图不变）。估值在
        # 真实 state 上进行，冻结码 mark 沿用 mark_map（_marks_from_snapshot）。
        halted = sorted(
            c for c, has_daily in
            snapshot.frame.select(["code", "has_daily"]).iter_rows()
            if not has_daily)
        if not halted:
            plan = (target, state, snapshot, rules)     # 无停牌：原对象（A7）
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
                plan_target = target                    # 停牌码全在持仓侧
            else:
                t_vis = t_rows.filter(~pl.col("code").is_in(halted))
                meta_d = (replace(target.meta,
                                  gross_exposure=float(
                                      t_vis["target_weight"].sum()))
                          if t_vis.height else target.meta)
                plan_target = TargetPortfolio(
                    frame=t_vis, decision_dates=target.decision_dates,
                    meta=meta_d)
            plan = (plan_target, plan_state, plan_snapshot, plan_rules)
        plan_target, plan_state, plan_snapshot, plan_rules = plan

        # ---- 已关闭 pipeline（plan 输入已 mask；成交/记账用真实对象）----
        orders = construct_order_batch(plan_target, schedule, plan_state,
                                       plan_snapshot, plan_rules,
                                       decision_date=decision_d)
        assessment = assess_open_fillability(orders, snapshot)
        fills = realize_open_fills(orders, assessment, state, snapshot, rules,
                                   execution_spec.cost_model)
        post = apply_fill_batch(state, fills)
        accounting = summarize_execution_accounting(state, fills, post)

        # ---- open-based marks + valuation + sanity（冻结沿用 mark_map）----
        pre_codes = state.positions["code"].to_list()
        post_codes = post.positions["code"].to_list()
        pre_marks = _marks_from_snapshot(snapshot, pre_codes, exec_date,
                                         mark_map=mark_map)
        post_marks = _marks_from_snapshot(snapshot, post_codes, exec_date,
                                          mark_map=mark_map)
        pre_nav = value_portfolio(state, pre_marks)
        post_nav = value_portfolio(post, post_marks)
        total_fees = fills.frame["total_fees"].sum() if fills.frame.height \
            else 0.0
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
        state = advance_to_next_trading_day(post, fills, rd)

    nav_frame = pl.DataFrame(nav_rows, schema=["execution_date", "cash",
                                               "market_value", "nav"],
                             orient="row")
    nav_frame = nav_frame.with_columns(
        pl.col("execution_date").cast(pl.Date),
        pl.col("cash").cast(pl.Float64),
        pl.col("market_value").cast(pl.Float64),
        pl.col("nav").cast(pl.Float64))
    return BacktestResult(artifacts=tuple(artifacts),
                          nav_series=NavSeries(frame=nav_frame),
                          final_state=state)
