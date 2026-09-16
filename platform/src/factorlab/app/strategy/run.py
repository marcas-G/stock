"""Plan S Task 2：run_strategy——策略文档一键链（信号→组合→回测→持久化）。

链（全部复用既有入口单点，不发明新语义）：

    load_signal_artifact(results_dir / signal_name)
      → 按 doc.date 过滤 signal frame
      → 按 doc.universe_override 过滤 signal frame（R07-STRAT-I6：canonical 子集）
      → construct_target_portfolio（M7：StrategySpec）
      → build_rebalance_schedule
      → write_strategy_artifacts(out_dir)
      → run_backtest(target, doc.execution, rd)（M8：ExecutionSpec）
      → save_backtest_result(out_dir)

默认目录：results 根 = `settings.results_dir`（可显式覆写）；`out_dir` =
`results_dir / "strategies" / doc.strategy.name`（与因子结果目录隔离）。
错误透传：CA Gate / `ExecutionDataQualityError` / fail-fast ValueError 原样抛
——不吞、不自动重试。空窗口（过滤后无任何 signal 行）显式报错，不静默空跑。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from factorlab.adapters.parquet_artifacts import load_signal_artifact
from factorlab.adapters.strategy_artifacts import write_strategy_artifacts
from factorlab.app.backtest import run_backtest, save_backtest_result
from factorlab.config import settings
from factorlab.core.domain.backtest import BacktestResult, NavSeries
from factorlab.core.domain.frames import SignalArtifact
from factorlab.core.domain.portfolio import TargetPortfolio
from factorlab.core.strategy import (StrategyDoc, build_rebalance_schedule,
                                     construct_target_portfolio)
from factorlab.ports.read import ReadPort


@dataclass(frozen=True)
class StrategyRunResult:
    """一键链结果：落盘目录 + 组合/回测对象 + 摘要字段。"""

    out_dir: Path
    target: TargetPortfolio
    backtest: BacktestResult
    nav_series: NavSeries
    signal_name: str
    decision_count: int


def _filter_to_window(signal: SignalArtifact, doc: StrategyDoc) -> SignalArtifact:
    frame = signal.frame.filter(
        (pl.col("date") >= doc.date.start) & (pl.col("date") <= doc.date.end))
    if frame.height == 0:
        raise ValueError(
            f"策略 {doc.strategy.name}: date 窗口 {doc.date.start}~{doc.date.end} "
            f"在因子 {signal.meta.name!r} 的 SignalArtifact 中无任何信号行"
            f"（检查窗口与因子日期域；不静默空跑）")
    return SignalArtifact(frame=frame, meta=signal.meta)


def _filter_to_universe(signal: SignalArtifact, doc: StrategyDoc) -> SignalArtifact:
    """L1 universe_override：按 canonical ts_code 列表过滤信号帧（R07-STRAT-I6）。

    - `None`（缺省）→ 原样返回（零行为变化）；
    - 口径：override 为 canonical ts_code（如 `000001.SZ`），信号帧 code 已是
      canonical——精确匹配、输出保持 canonical（不做后缀推断/静默改写）；
    - 空交集 → fail fast（列出 override 与窗口内可用 codes，不静默空跑）；
    - 部分命中 = 合法子集（个股停牌/退市由 M7 on_insufficient 语义处理）。
    """
    override = doc.universe_override
    if override is None:
        return signal
    wanted = sorted(set(override))
    frame = signal.frame.filter(pl.col("code").is_in(wanted))
    if frame.height == 0:
        available = sorted(signal.frame["code"].unique().to_list())
        raise ValueError(
            f"策略 {doc.strategy.name}: universe_override {wanted} 与 date 窗口 "
            f"{doc.date.start}~{doc.date.end} 内的 signal codes 无交集"
            f"（窗口内可用 {available}；override 需为 canonical ts_code 形态，"
            f"如 '000001.SZ'）——不静默空跑")
    return SignalArtifact(frame=frame, meta=signal.meta)


def run_strategy(doc: StrategyDoc, rd: ReadPort,
                 results_dir: Path | None = None,
                 out_dir: Path | None = None,
                 target_transform=None) -> StrategyRunResult:
    """执行策略文档：读信号 → 窗口过滤 → M7 组合 → 落盘 → M8 回测 → 落盘。

    - results_dir：results 根（缺省 settings.results_dir）——信号按
      `results_dir/<signal_name>` 读取；
    - out_dir：策略产物目录显式覆盖（缺省 `results_dir/"strategies"/<name>`）；
    - `doc.universe_override` 非 null → 组合前先按 canonical ts_code 过滤信号帧
      （空交集 fail fast；null = 零行为变化，见 `_filter_to_universe`）；
    - target_transform：可选 M7 → M8 之间的目标组合变换钩子（研究侧 L5 规则
      V1 注入点，如 max_hold；须返回 TargetPortfolio 且保持 decision_dates/
      gross_exposure 契约——写盘交叉校验会复验）。
    """
    if not isinstance(doc, StrategyDoc):
        raise TypeError(
            f"doc 必须为 StrategyDoc（收到 {type(doc).__name__}）——"
            f"dict/YAML 路径不自动转换，请先 load_strategy_doc")
    root = Path(results_dir) if results_dir is not None else Path(
        settings.results_dir)
    signal = load_signal_artifact(root / doc.strategy.signal_name)
    filtered = _filter_to_window(signal, doc)
    filtered = _filter_to_universe(filtered, doc)
    target = construct_target_portfolio(filtered, doc.strategy)
    if target_transform is not None:
        target = target_transform(target)
        if not isinstance(target, TargetPortfolio):
            raise TypeError(
                f"target_transform 必须返回 TargetPortfolio（收到 "
                f"{type(target).__name__}）——L5 规则层不得改变目标组合契约")
    schedule = build_rebalance_schedule(filtered, doc.strategy)
    target_dir = (Path(out_dir) if out_dir is not None
                  else root / "strategies" / doc.strategy.name)
    write_strategy_artifacts(target_dir, source_signal=filtered,
                             spec=doc.strategy, schedule=schedule, target=target)
    backtest = run_backtest(target, doc.execution, rd)
    save_backtest_result(backtest, target_dir)
    return StrategyRunResult(
        out_dir=target_dir,
        target=target,
        backtest=backtest,
        nav_series=backtest.nav_series,
        signal_name=signal.meta.name,
        decision_count=len(target.decision_dates),
    )
