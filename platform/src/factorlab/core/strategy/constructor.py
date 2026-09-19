"""M7-02：construct_target_portfolio——SignalArtifact → Top-K TargetPortfolio。

Strategy Runtime 边界：**只接受 SignalArtifact**（LabelArtifact/DataFrame/
FactorResult/panel 全部拒绝——无 DataFrame shortcut）。

流程（M7-02 daily v1；C4 §19.2 扩 score_weighted；C4b 扩 top_k_buffered /
market_cap_weighted）：

    SignalArtifact(date, code, signal) +（可选）market_cap(date, code, total_mv)
        │
        ├── Rebalance Scheduler（daily/weekly/monthly——日期选择唯一权威）
        ├── validate signal_name / frequency / canonical code / non-finite
        ├── per-date null drop（仅 scheduled dates）
        ├── direction sort（±1）
        ├── code_asc exact tie break
        ├── top_k / top_k_buffered / insufficient（use_available / all_cash）
        └── equal_weight（gross_exposure / M）
            | score_weighted（w = gross × s' / Σs'，s' = max(signal×direction, 0)；
              Σs'==0 → 当日显式 all-cash；long-only，无 single-name cap）
            | market_cap_weighted（w = gross × mv_i / Σmv；mv 来自注入面板，
              缺/非法 mv → 显式 ValueError）
        ▼
    TargetPortfolio

decision_dates 来自 build_rebalance_schedule（M7-03）——daily = 全部 signal
dates；weekly/monthly = ISO week / calendar month 最后 available date。
非 decision date 无决策（≠ all-cash）。

C4b top_k_buffered（顺序式，持有状态仅存在于同一 run 的 decision date 序列内）：

- 每日候选 = 当日非 null signal；排名按 (direction, code_asc) 确定性排序；
- 当前持仓（前一 target 的实际建仓行）在当日名次 <= retain_k 内 → 保留；
  掉出（含当日候选缺席/null）→ 释放；
- 空位按名次从 enter_k 内候选补入（不追涨：持仓未掉出时即使排名更高也不换）；
- 目标仓位数 = enter_k（on_insufficient=use_available 时受当日可用数约束）；
- 显式 all-cash 日（0 行输出，含 on_insufficient=all_cash 与 score_weighted
  Σs'==0）→ 清空持有状态（次日重新建仓，不携带幽灵持仓）。

market_cap_weighted 的缺值语义（**已锁定**）：选中的 Top-K 股票在 (date, code)
上无 mv 行、或 total_mv 为 null/NaN/±Inf/<= 0 → 构造 fail fast（显式 ValueError
点名 date/code）。**不静默剔除、不回退等权**——缺市值不得伪装成零权重。
未被选中的 code 缺 mv 不影响当日结果（join 只要求覆盖选中集）。
面板列契约：恰为 {date, code, total_mv}，(date, code) 必须唯一。
"""

from __future__ import annotations

import datetime
import math

import polars as pl

from factorlab.core.domain.codes import is_canonical_stock_code
from factorlab.core.domain.frames import SignalArtifact
from factorlab.core.domain.portfolio import TargetPortfolio, TargetPortfolioMeta
from factorlab.core.strategy.schedule import build_rebalance_schedule
from factorlab.core.strategy.spec import StrategySpec

_EMPTY_SCHEMA = {"decision_date": pl.Date, "code": pl.String,
                 "target_weight": pl.Float64}

_MARKET_CAP_COLUMNS = {"date", "code", "total_mv"}


def _require_signal_artifact(signal) -> SignalArtifact:
    """Strategy Runtime type guard：只接受 SignalArtifact（fail fast 于正式
    type guard，不是 AttributeError 偶然崩）。"""
    if not isinstance(signal, SignalArtifact):
        raise TypeError(
            f"Strategy Runtime 只接受 SignalArtifact（收到 "
            f"{type(signal).__name__}）——LabelArtifact/DataFrame/FactorResult/"
            f"panel 均拒绝，请显式取 factor_result.signal_artifact")
    return signal


def _require_strategy_spec(spec) -> StrategySpec:
    if not isinstance(spec, StrategySpec):
        raise TypeError(
            f"spec 必须为 StrategySpec（收到 {type(spec).__name__}）——"
            f"dict/FactorSpec 不自动转换，配置解析属于更外层入口")
    return spec


def _build_market_cap_index(market_cap, spec: StrategySpec) -> dict | None:
    """market_cap 面板校验 + (date, code) → total_mv 索引。
    （列契约/唯一性/方法匹配全在构造入口 fail fast。）"""
    mv_weighted = spec.weighting.method == "market_cap_weighted"
    if not mv_weighted:
        if market_cap is not None:
            raise ValueError(
                f"market_cap 面板仅在 weighting.method=market_cap_weighted 时接受"
                f"（当前 {spec.weighting.method!r}）——防串线静默忽略")
        return None
    if market_cap is None:
        raise ValueError(
            "weighting.method=market_cap_weighted 需要 market_cap 面板"
            "（date/code/total_mv）——app 层从读句柄取 PIT total_mv 后传入"
            "（DQ 读取门在其上层，构造函数只做 join）")
    if set(market_cap.columns) != _MARKET_CAP_COLUMNS:
        raise ValueError(
            f"market_cap 面板列必须为 {sorted(_MARKET_CAP_COLUMNS)}，"
            f"实际 {list(market_cap.columns)}")
    market_cap = market_cap.select(["date", "code", "total_mv"])
    if market_cap["date"].dtype != pl.Date or market_cap["code"].dtype != pl.String:
        raise ValueError(
            f"market_cap 面板 dtype 契约：date=Date / code=String，实际 "
            f"{market_cap['date'].dtype}/{market_cap['code'].dtype}")
    dup = (market_cap.group_by(["date", "code"]).len()
           .filter(pl.col("len") > 1).sort(["date", "code"]).head(3))
    if dup.height:
        raise ValueError(
            f"market_cap 面板 (date, code) 重复（歧义市值不可静默取一）："
            f"{dup.to_dicts()}")
    index: dict[tuple[datetime.date, str], float | None] = {}
    for d, c, v in market_cap.iter_rows():
        index[(d, c)] = None if v is None else float(v)
    return index


def _require_market_cap_for_selected(index, d: datetime.date, code: str) -> float:
    """选中股 mv 取值：缺行/null/non-finite/<=0 → 显式 ValueError（点名 date+code）。"""
    value = index.get((d, code))
    if value is None or not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"market_cap_weighted: {d} 选中股 {code} 无有效 PIT total_mv"
            f"（缺行/null/non-finite/<=0；实际 {value!r}）——不静默剔除、"
            f"不回退等权；请检查 daily_basic 市值数据面")
    return value


def construct_target_portfolio(
    signal: SignalArtifact,
    spec: StrategySpec,
    *,
    market_cap: pl.DataFrame | None = None,
) -> TargetPortfolio:
    """把已完成的 SignalArtifact 转化为每日 Top-K TargetPortfolio。

    - 输入只读 date/code/signal 三列（safe extra columns 不影响结果）
    - top_k 每日期独立选择（无跨日期 carry/排名）；top_k_buffered 在同一 run 内
      按 decision date 顺序维护前一 target 持仓（enter_k/retain_k 缓冲，见模块
      docstring）
    - null → drop；NaN/±Inf → fail fast（non-finite 无策略语义）
    - exact tie（signal 数值相同）→ code ASC（输入行序不影响）
    - equal_weight = gross_exposure / selected_count（无 residual correction）
    - score_weighted（C4 §19.2，long-only）：Top-K 后 s' = max(signal×direction, 0)，
      w_i = gross_exposure × s'_i / Σs'；s'==0 的名字不建行（sparse 语义）；
      Σs'==0 → 该日显式 all-cash（不 fallback 等权）。single-name cap 本期不做。
    - market_cap_weighted（C4b）：w_i = gross_exposure × mv_i / Σmv（Top-K 内，
      PIT (date,code) 对齐；缺/非法 mv → 显式 ValueError，不静默剔除/回退等权）
    - 输出 sparse positions（0 weight 不创建 row；all-cash 日 0 rows 但
      decision_date 存在）；按 (decision_date, code) 稳定排序
    - 不改动输入 SignalArtifact（pure）
    """
    _require_signal_artifact(signal)
    _require_strategy_spec(spec)
    if signal.meta.name != spec.signal_name:
        raise ValueError(
            f"signal_name 不匹配：SignalArtifact.meta.name={signal.meta.name!r} "
            f"vs StrategySpec.signal_name={spec.signal_name!r}")
    if signal.meta.frequency != "1d":
        raise ValueError(
            f"Strategy Runtime frequency contract：SignalMeta.frequency 必须为 '1d'"
            f"（收到 {signal.meta.frequency!r}）——rebalance_frequency=daily 兼容")
    mv_index = _build_market_cap_index(market_cap, spec)
    df = signal.frame
    if df.height:
        # ---- 输入边界全量校验（非法 code / non-finite 即使不入选也 fail）----
        bad_code = df.filter(~pl.col("code").map_elements(
            is_canonical_stock_code, return_dtype=pl.Boolean).fill_null(False))
        if bad_code.height:
            raise ValueError(
                f"SignalArtifact 含非 canonical code: "
                f"{bad_code['code'].unique().to_list()}——输入边界全量检查，"
                f"即使永不入选也 fail")
        nonfinite = df.filter(pl.col("signal").is_not_null()
                              & ~pl.col("signal").is_finite())
        if nonfinite.height:
            raise ValueError(
                f"SignalArtifact 含 non-finite signal（NaN/±Inf 无策略语义，"
                f"非 null_policy 范畴）: {nonfinite.height} 行")

    # M7-03：日期选择唯一权威 = Rebalance Scheduler（不再自己决定 schedule）
    schedule = build_rebalance_schedule(signal, spec)
    decision_dates = schedule.decision_dates
    meta = TargetPortfolioMeta(
        strategy_name=spec.name,
        source_signal_name=signal.meta.name,
        source_timing=signal.meta.timing,
        gross_exposure=spec.gross_exposure,
        frequency=signal.meta.frequency,
        rebalance_frequency=spec.rebalance_frequency,
    )
    if df.height == 0:
        return TargetPortfolio(
            frame=pl.DataFrame(schema=_EMPTY_SCHEMA),
            decision_dates=decision_dates,
            meta=meta,
        )

    selection = spec.selection
    buffered = selection.method == "top_k_buffered"
    k = selection.k if not buffered else selection.enter_k
    retain_k = selection.retain_k
    gross = spec.gross_exposure
    use_available = selection.on_insufficient == "use_available"
    desc = spec.direction == 1
    score_weighted = spec.weighting.method == "score_weighted"
    mv_weighted = spec.weighting.method == "market_cap_weighted"

    work = (df.select(["date", "code", "signal"])
              .filter(pl.col("signal").is_not_null()))
    parts: list[pl.DataFrame] = []
    holdings: tuple[str, ...] = ()   # C4b buffered：上一决策日实际建仓的 code
    for d in decision_dates:
        day = work.filter(pl.col("date") == d)
        n = day.height
        if n == 0 or (n < k and not use_available):
            holdings = ()   # 显式 all-cash：清空状态（次日重建，不携带幽灵持仓）
            continue
        ranked = day.sort(by=["signal", "code"], descending=[desc, False])
        ranked_codes = ranked["code"].to_list()
        if buffered:
            rank = {c: i + 1 for i, c in enumerate(ranked_codes)}
            kept = [c for c in holdings if c in rank and rank[c] <= retain_k]
            target_count = min(k, n)
            slots = max(0, target_count - len(kept))
            selected = kept + [c for c in ranked_codes[:k]
                               if c not in kept][:slots]
        else:
            selected = ranked_codes[:min(n, k)]
        if score_weighted:
            # C4 §19.2：Top-K 后取有符号正部；0 权重不建行（sparse）
            signal_by_code = dict(zip(ranked_codes, ranked["signal"].to_list()))
            positive = []
            for c in selected:
                s = signal_by_code[c] * spec.direction
                positive.append(s if s > 0 else 0.0)
            total = sum(positive)
            if total == 0.0:
                holdings = ()
                continue   # Σs'==0 → 该日显式 all-cash（不 fallback 等权）
            codes = [c for c, s in zip(selected, positive) if s > 0]
            weights = [gross * s / total for s in positive if s > 0]
        elif mv_weighted:
            mv_by_code = {c: _require_market_cap_for_selected(mv_index, d, c)
                          for c in selected}
            total_mv = sum(mv_by_code.values())
            codes = selected
            weights = [gross * mv_by_code[c] / total_mv for c in codes]
        else:
            codes = selected
            weights = [gross / len(selected)] * len(selected)
        holdings = tuple(codes)
        parts.append(pl.DataFrame({
            "decision_date": pl.Series([d] * len(codes), dtype=pl.Date),
            "code": codes,
            "target_weight": pl.Series(weights, dtype=pl.Float64),
        }))
    frame = (pl.concat(parts).sort(["decision_date", "code"])
             if parts else pl.DataFrame(schema=_EMPTY_SCHEMA))
    return TargetPortfolio(frame=frame, decision_dates=decision_dates, meta=meta)
