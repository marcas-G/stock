"""Plan S Task 2：run_strategy——策略文档一键链（信号→组合→回测→持久化）。

链（全部复用既有入口单点，不发明新语义）：

    _load_signal(doc, root)        # Plan CX-C4 T1：factor → results_dir/<name>；
                                   # composite → results_dir/composites/<name>
      → 按 doc.date 过滤 signal frame
      → 按 doc.universe_override 过滤 signal frame（R07-STRAT-I6：canonical 子集）
      → 按数据集范围过滤 signal frame（R37：非 .BJ 且 date >= 1996-01-01；
         历史产物兼容——不重算信号也不交易范围外证券）
      → _load_market_cap（仅 market_cap_weighted：读句柄取 PIT total_mv）
      → construct_target_portfolio（M7：StrategySpec + market_cap 面板）
      → build_rebalance_schedule
      → write_strategy_artifacts(out_dir)
      → run_backtest(target, doc.execution, rd)（M8：ExecutionSpec）
      → save_backtest_result(out_dir)

默认目录：results 根 = `settings.results_dir`（可显式覆写）；`out_dir` =
`results_dir / "strategies" / doc.strategy.name`（与因子结果目录隔离）。
composite 信号（design §19.1）：读 C1 `composites/<name>/{panel.parquet,
artifact.json}`，包成 `SignalArtifact` 交 M7——Portfolio 不感知来源。
错误透传：CA Gate / `ExecutionDataQualityError` / fail-fast ValueError 原样抛
——不吞、不自动重试。空窗口（过滤后无任何 signal 行）显式报错，不静默空跑。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from factorlab import __version__
from factorlab.adapters.parquet_artifacts import load_signal_artifact
from factorlab.adapters.read.source import load_daily
from factorlab.adapters.strategy_artifacts import write_strategy_artifacts
from factorlab.app.backtest import run_backtest, save_backtest_result
from factorlab.app.composite.artifact import read_composite_artifact
from factorlab.app.composite.resolver import COMPOSITES_DIRNAME
from factorlab.config import settings
from factorlab.core import scope
from factorlab.core.domain.backtest import BacktestResult, NavSeries
from factorlab.core.domain.frames import SignalArtifact, SignalMeta
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
            f"在信号 {signal.meta.name!r}（{doc.signal_kind}）的 SignalArtifact 中"
            f"无任何信号行（检查窗口与信号日期域；不静默空跑）")
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


def _filter_to_scope(signal: SignalArtifact, doc: StrategyDoc) -> SignalArtifact:
    """R37 数据集范围谓词：组合前剔除非 .BJ 且 date >= 1996-01-01 之外的信号行。

    口径唯一事实源 `factorlab.core.scope`（范围外数据保留在盘，只不参与研究）。
    历史产物兼容：加载既有 SignalArtifact 时同样过滤——不重算信号也能保证
    不交易 BJ/1996 前证券。保持行序与 schema；过滤后为空 → fail fast
    （不静默空跑）。
    """
    frame = scope.filter_frame(signal.frame, date_col="date", code_col="code")
    if frame.height == 0:
        raise ValueError(
            f"策略 {doc.strategy.name}: 信号 {signal.meta.name!r}"
            f"（{doc.signal_kind}）在 date 窗口 {doc.date.start}~{doc.date.end} "
            f"内经数据集范围（trade_date >= {scope.MIN_TRADE_DATE.isoformat()} "
            f"且 code 非 .BJ）过滤后无任何行——历史产物含范围外信号；"
            f"不静默空跑（请重算信号或换窗口）")
    return SignalArtifact(frame=frame, meta=signal.meta)


def _load_composite_signal(doc: StrategyDoc, root: Path) -> SignalArtifact:
    """读 C1 composite 产物（`<root>/composites/<name>/`）→ `SignalArtifact`。

    design §19.1：composite artifact 的 meta 不含 `frequency`——C1 产出为日频
    EOD 信号（M7 frequency 契约 "1d"），包装处显式补齐（timing 用 SignalMeta
    默认 EOD，与 C1 语义一致）。产物名与引用名不一致（目录/内容错配，对齐
    resolver 的 name 契约）→ 拒绝消费。
    """
    name = doc.strategy.signal_name
    comp_dir = root / COMPOSITES_DIRNAME / name
    try:
        frame, meta, _prov = read_composite_artifact(comp_dir)
    except (ValueError, OSError) as exc:
        raise ValueError(
            f"策略 {doc.strategy.name}: composite 信号 {name!r} 加载失败"
            f"（解析目录 {comp_dir}）: {exc}——composite 产物由 "
            f"`factorlab compose <spec.yaml>`（或 run_composite）生成于 "
            f"<results_dir>/{COMPOSITES_DIRNAME}/<name>/；请确认 "
            f"signal: composites/<name> 与产物存在") from exc
    artifact_name = meta.get("name")
    if isinstance(artifact_name, str) and artifact_name and artifact_name != name:
        raise ValueError(
            f"策略 {doc.strategy.name}: composite 产物名 {artifact_name!r} 与引用名 "
            f"{name!r} 不一致（解析目录 {comp_dir}）——目录/内容错配，拒绝消费")
    return SignalArtifact(
        frame=frame,
        meta=SignalMeta(name=name,
                        frequency=meta.get("frequency") or "1d",
                        adjustment=meta.get("adjustment")),
    )


def _load_signal(doc: StrategyDoc, root: Path) -> SignalArtifact:
    """按 `doc.signal_kind` 加载信号：factor（扁平目录，零回归）/ composite。

    factor 分支逐字保持既有调用（`load_signal_artifact(root / <name>)`）；
    composite 读 C1 产物并统一包成 `SignalArtifact` 交 M7——Portfolio 不感知
    来源（design §19.1）。
    """
    if doc.signal_kind == "factor":
        return load_signal_artifact(root / doc.strategy.signal_name)
    return _load_composite_signal(doc, root)


def _load_market_cap(signal: SignalArtifact, doc: StrategyDoc,
                     rd: ReadPort) -> pl.DataFrame | None:
    """PIT total_mv 面板（date/code/total_mv）——仅 market_cap_weighted 时取数。

    - 读路径 = `adapters.read.source.load_daily(cols=["total_mv"])`（平台读面单点，
      内部 LEFT JOIN daily_basic；duckdb/ch 双腿同口径）；DQ 读取门由其上层负责，
      这里只做 join；非 mv 加权直接返回 None（不产生任何读）。
    - `load_daily` 输出的 code 为 6 位（去后缀）→ 用窗口内 canonical codes 的
      前缀映射还原；同前缀多 canonical（歧义）→ fail fast（拒绝静默错 join）。
    - `float32=False`：市值量级大，权重保持 Float64 精度。
    - 选中股缺 mv 行 → M7 constructor fail fast（点名 date/code，不静默剔除）。
    """
    if doc.strategy.weighting.method != "market_cap_weighted":
        return None
    frame = signal.frame
    codes = sorted(frame["code"].unique().to_list())
    by_prefix: dict[str, list[str]] = {}
    for c in codes:
        by_prefix.setdefault(c.split(".")[0], []).append(c)
    ambiguous = {p: cs for p, cs in by_prefix.items() if len(cs) > 1}
    if ambiguous:
        raise ValueError(
            f"策略 {doc.strategy.name}: 信号 codes 存在 6 位前缀歧义 {ambiguous}"
            f"——无法把 total_mv 面板还原为 canonical code（拒绝静默错 join）")
    prefix_to_code = {p: cs[0] for p, cs in by_prefix.items()}
    mv = load_daily(rd, codes,
                    date_start=frame["date"].min().isoformat(),
                    date_end=frame["date"].max().isoformat(),
                    cols=["total_mv"], float32=False).collect()
    unknown = sorted(set(mv["code"].unique().to_list()) - set(prefix_to_code))
    if unknown:
        raise ValueError(
            f"策略 {doc.strategy.name}: total_mv 面板含窗口外/未知 code {unknown}"
            f"——前缀映射不完整（拒绝静默丢弃）")
    mv = mv.with_columns(pl.col("code").replace_strict(
        prefix_to_code, return_dtype=pl.String))
    return mv.select(["date", "code", "total_mv"])


def _lockbox_guard_for_strategy(doc: StrategyDoc, doc_path: Path | None, *,
                                final_mode: bool):
    """执行前锁箱硬门：窗口端点取 `doc.date`（信号加载/组合/回测之前的唯一闸）。

    R42：`final_mode`（host 缺省 False；流水线子进程由 `FACTORLAB_PIPELINE=1`
    推导）——探索碰测试段直接拒（`LOCKBOX_TEST_ONLY_FINAL`）；最终测试自动登记
    （每版本一次，理由从策略名合成）。`artifact` 取策略 YAML 路径（指纹用
    spec_doc，不含路径）。
    """
    from factorlab.adapters import lockbox_store as store
    days, data_end = store.run_calendar()
    reason = (f"pipeline final test: strategy {doc.strategy.name}"
              if final_mode else None)
    return store.guard_run(
        panel_start=doc.date.start, panel_end=doc.date.end,
        final_mode=final_mode, reason=reason,
        spec_doc=doc.model_dump(mode="json"),
        artifact=str(doc_path) if doc_path is not None else "",
        command="strategy run", tool=f"factorlab {__version__}",
        db_path=settings.lockbox_db, trading_days=days, data_end=data_end)


def run_strategy(doc: StrategyDoc, rd: ReadPort,
                 results_dir: Path | None = None,
                 out_dir: Path | None = None,
                 target_transform=None,
                 dataset: str | None = "ashare_daily",
                 accept_quality: tuple[str, ...] = ("PASS",),
                 max_staleness: str = "1d",
                 dq_root=None,
                 override_reason: str | None = None,
                 strict: bool = False,
                 final_mode: bool | None = None,
                 doc_path: Path | None = None) -> StrategyRunResult:
    """执行策略文档：读信号 → 窗口过滤 → M7 组合 → 落盘 → M8 回测 → 落盘。

    - results_dir：results 根（缺省 settings.results_dir）——factor 信号按
      `results_dir/<signal_name>` 读取；composite 信号（doc.signal_kind=
      "composite"）按 `results_dir/composites/<signal_name>/` 读取；
    - out_dir：策略产物目录显式覆盖（缺省 `results_dir/"strategies"/<name>`）；
    - `doc.universe_override` 非 null → 组合前先按 canonical ts_code 过滤信号帧
      （空交集 fail fast；null = 零行为变化，见 `_filter_to_universe`）；
    - `weighting.method=market_cap_weighted` → 组合前从读句柄取 PIT total_mv
      （`_load_market_cap`，daily_basic），join 到 (date, code) 交 M7；缺市值由
      M7 fail fast（显式报错，不静默剔除/回退等权）;
    - target_transform：可选 M7 → M8 之间的目标组合变换钩子（研究侧 L5 规则
      V1 注入点，如 max_hold；须返回 TargetPortfolio 且保持 decision_dates/
      gross_exposure 契约——写盘交叉校验会复验）。
    - Plan DQ-M1 F3/F4：`dataset`（默认 "ashare_daily"）传给 run_backtest 的
      读取门（fail-closed）；过门后五字段随 BacktestResult 进持久化 manifest。
      合成/历史调用可显式 `dataset=None` 关闭（库层默认 None 语义）。
    - R42：`final_mode`（None → `FACTORLAB_PIPELINE=1` 推导；host 缺省 False）——
      doc.date 碰测试段时：探索直接拒（`LOCKBOX_TEST_ONLY_FINAL`），最终测试
      （流水线子进程）自动登记；`strategy_manifest` 挂 `sample` 并把产物目录
      回填 `result_ref`。env `FACTORLAB_LOCKBOX` 关闭时直接 IS 放行；直接 API
      调用不传 doc_path 时 artifact 记空串（指纹不含路径）。
    """
    if not isinstance(doc, StrategyDoc):
        raise TypeError(
            f"doc 必须为 StrategyDoc（收到 {type(doc).__name__}）——"
            f"dict/YAML 路径不自动转换，请先 load_strategy_doc")
    if final_mode is None:
        final_mode = os.environ.get("FACTORLAB_PIPELINE", "").strip() == "1"
    guard = _lockbox_guard_for_strategy(doc, doc_path, final_mode=final_mode)
    root = Path(results_dir) if results_dir is not None else Path(
        settings.results_dir)
    signal = _load_signal(doc, root)
    filtered = _filter_to_window(signal, doc)
    filtered = _filter_to_universe(filtered, doc)
    filtered = _filter_to_scope(filtered, doc)
    mv_panel = _load_market_cap(filtered, doc, rd)
    target = construct_target_portfolio(filtered, doc.strategy, market_cap=mv_panel)
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
                             spec=doc.strategy, schedule=schedule, target=target,
                             sample=guard.sample())
    backtest = run_backtest(target, doc.execution, rd, dataset=dataset,
                            accept_quality=accept_quality,
                            max_staleness=max_staleness, dq_root=dq_root,
                            override_reason=override_reason, strict=strict)
    save_backtest_result(backtest, target_dir)
    guard.mark_result(str(target_dir))
    return StrategyRunResult(
        out_dir=target_dir,
        target=target,
        backtest=backtest,
        nav_series=backtest.nav_series,
        signal_name=signal.meta.name,
        decision_count=len(target.decision_dates),
    )
