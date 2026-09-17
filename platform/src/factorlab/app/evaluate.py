"""唯一评估装配（WS5）：频率分支（daily 默认 / weekly 对照）→ 逐输出评估 → 分层回测 → 发布落盘。

从 `cli/main.py` 自建装配上移（历史：CLI 是唯一装配点，Python API 调用方
复刻不出同一评估口径）。语义：
- D9（R30 Task 13）：`evaluation_frequency="daily"`（默认）= 逐日口径——评估面板
  **不做周频对齐**（每日截面/每日调仓/1 日 forward；D11）；`weekly` = 旧口径可选对照
  （ISO 周对齐 + spec.target），逐值零变更；
- legacy 单输出（outputs == ["signal"]）：顶层结构 `{...评估键...}`；
- 多输出：顶层 `{"outputs": {o: 评估 dict}, "frequency": ...}`（无隐式主信号）；
- 对齐一次复用给评估与分层回测（千万行面板重复对齐在低内存机器上 segfault）；
- 发布 = weekly.parquet（评估输入面板：daily=日频 / weekly=周频对齐） +
  summary.json（evaluation 追加后重写）；summary 由 run 链先落盘（无 evaluation），
  本层负责追加并重写。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from factorlab.core.domain.frames import SignalArtifact  # noqa: F401  (类型语义文档)
from factorlab.app.context import RunContext
from factorlab.core.engine.compute import FactorResult
from factorlab.core.engine.forward import FORWARD_COLUMNS
from factorlab.core.eval.alignment import align_weekly
from factorlab.core.eval.ic_decay import ic_decay
from factorlab.core.eval.layered import (WEEKS_PER_YEAR, degenerate_decile_groups,
                                         layered_backtest)
from factorlab.core.eval.metrics import DeadSignalError, dead_signal_report
from factorlab.adapters.ic_kernel import evaluate_factor_daily, evaluate_factor_weekly
from factorlab.core.spec import FactorSpec

# D9：daily 评估固定 1 日 forward（D11）；日频年化系数（250+ 交易日惯例 252）
DAILY_TARGET = "forward_return_1d"
DAILY_PERIODS_PER_YEAR = 252
_FREQUENCIES = ("daily", "weekly")


@dataclass
class EvaluationOutcome:
    evaluation: dict
    eval_panel: pl.DataFrame          # 评估输入面板（daily 日频 / weekly 周频对齐）
    outputs: list[str]
    frequency: str = "daily"
    notes: list[str] = field(default_factory=list)   # 展示层提示（CLI 打印）
    dead_signal: dict | None = None   # D5：死信号明细（非 None → publish 落盘后非零失败）


def _mark_degenerate_deciles(evaluation: dict, notes: list[str], prefix: str = "") -> None:
    """R03-I3：decile 空档（mean_ret 非有限）→ evaluation 落标记 + notes 显著提示。

    高并列/离散信号经 average-rank 分位映射会跳档（组全期无成员）——kernel 静默
    回填 NaN，CLI 只打印 spread=nan；这里把"结论不可用"显式化并给出降组数指引。
    """
    degenerate = degenerate_decile_groups(evaluation.get("decile_returns") or {})
    if not degenerate:
        return
    evaluation.setdefault("decile_returns", {})["degenerate_groups"] = degenerate
    notes.append(
        f"{prefix}十分位组 {degenerate}（0=最小 signal）全期无有效收益——信号重并列/"
        "离散时分位跳档，spread/单调性不可用；建议降低分组数或改用其他评估口径")


def _mark_dead_signal(ev: dict, panel: pl.DataFrame, col: str,
                      notes: list[str], prefix: str = "") -> dict | None:
    """D5（R30 Task 2 / R07-D6）：signal 空值占比 ≥ 阈值 → 显式字段 + 响亮提示。

    口径：null 行占比（与 summary `signal_null_ratio` 同源；分母 = 样本全量面板，
    而非对齐/过滤后的评估面板）。正常因子零变化——不新增任何键、不生成 note。
    返回明细 dict（供 `publish_run` 在落盘后非零失败）；非死信号 → None。
    """
    report = dead_signal_report(panel, signal_col=col)
    if not report["dead_signal"]:
        return None
    ev["dead_signal"] = True
    notes.append(
        f"{prefix}死信号（D5 fail-loud）：signal_null_ratio="
        f"{report['signal_null_ratio']} ≥ 阈值 {report['threshold']}"
        f"（{report['null_rows']}/{report['total_rows']} 行为空）——signal 列无有效"
        "数据，n_weeks=0 不再静默等价于“无效因子”；评估摘要已落盘（dead_signal=true）"
        "并以非零退出，先修取数列/数据面（governance/workspace/data-map.md）")
    return report


def _weighting_args(spec: FactorSpec) -> tuple[str, str]:
    """E1（R30 fix 波）：spec.weighting → kernel `(mode, mv_col)`。

    `equal_weight` 缺省零回归（kernel 不附顶层 `weighting` 披露键）；
    `market_cap` 用 `spec.weighting_mv_col`（= total_mv，E1a 单点）。
    """
    return spec.weighting, spec.weighting_mv_col or "total_mv"


def _evaluate_frame(frame: pl.DataFrame, spec: FactorSpec, frequency: str,
                    weekly: pl.DataFrame | None = None) -> dict:
    """频率分支评估调用（daily 不触 align_weekly；weekly 复用已对齐面板）。

    E1：`spec.weighting` 全频率透传 kernel（市值加权 decile）。
    """
    weighting, mv_col = _weighting_args(spec)
    if frequency == "daily":
        return evaluate_factor_daily(frame, spec.name, spec.direction,
                                     weighting=weighting, mv_col=mv_col)
    return evaluate_factor_weekly(frame, spec.name, spec.direction,
                                  target=spec.target,
                                  weekly=weekly if weekly is not None else frame,
                                  weighting=weighting, mv_col=mv_col)


def _backtest_frame(frame: pl.DataFrame, spec: FactorSpec, frequency: str, *,
                    groups: int) -> dict:
    if frequency == "daily":
        return layered_backtest(frame, spec.direction, n_groups=groups,
                                forward_col=DAILY_TARGET, cost_rate=spec.cost_rate,
                                periods_per_year=DAILY_PERIODS_PER_YEAR)
    return layered_backtest(frame, spec.direction, n_groups=groups,
                            forward_col=spec.target, cost_rate=spec.cost_rate,
                            periods_per_year=WEEKS_PER_YEAR)


def evaluate_run(result: FactorResult, spec: FactorSpec, ctx: RunContext, *,
                 groups: int = 10, backtest: bool = True,
                 frequency: str | None = None) -> EvaluationOutcome:
    """评估装配：频率分支 → 单输出顶层 / 多输出逐输出 → 可选分层回测。

    - `frequency` 显式给出时覆盖 spec.evaluation_frequency（CLI --eval-frequency）；
      缺省取 spec 字段（daily 默认）。
    - daily：评估面板 = result.panel（**禁止 align_weekly**）；weekly：先对齐一次。
    """
    freq = frequency if frequency is not None else getattr(
        spec, "evaluation_frequency", "daily")
    if freq not in _FREQUENCIES:
        raise ValueError(f"未知评估频率 {freq!r}（应为 {'|'.join(_FREQUENCIES)}）")

    if freq == "daily":
        eval_panel = result.panel
    else:
        eval_panel = align_weekly(result.panel)
    outputs = list(spec.outputs) if spec.outputs is not None else ["signal"]
    notes: list[str] = []
    dead: dict | None = None
    if outputs == ["signal"]:
        evaluation = _evaluate_frame(eval_panel, spec, freq)
        evaluation["frequency"] = freq
        # E2（R30 Task 6）：IC 衰减 append（不改变主指标；缺标签 horizon → null）
        evaluation["ic_decay"] = ic_decay(eval_panel)
        _mark_degenerate_deciles(evaluation, notes)
        dead = _mark_dead_signal(evaluation, result.panel, "signal", notes)
        if backtest:
            bt = _backtest_frame(eval_panel, spec, freq, groups=groups)
            evaluation["layered_backtest"] = bt
            if bt.get("empty_groups"):
                notes.append(f"档位 {bt['empty_groups']} 全期无股票——universe 过小或 --groups 过大")
    else:
        # per-output 面板：评估面板里取该输出列（date/code/o/target）→ 归一 signal
        evaluation = {"outputs": {}, "frequency": freq}
        target_col = DAILY_TARGET if freq == "daily" else spec.target
        fwd_cols = [c for c in FORWARD_COLUMNS if c in eval_panel.columns]
        mv_cols = [spec.weighting_mv_col] if spec.weighting_mv_col else []
        for o in outputs:
            p = eval_panel.select(["date", "code", o, *fwd_cols, *mv_cols])
            if o != "signal":  # 字面 signal 输出：列名已就绪，rename 会自撞
                p = p.rename({o: "signal"})
            ev_o = _evaluate_frame(p, spec, freq, weekly=p)
            ev_o["ic_decay"] = ic_decay(p)   # E2：逐输出 append
            _mark_degenerate_deciles(ev_o, notes, prefix=f"输出 {o} ")
            dead_o = _mark_dead_signal(ev_o, result.panel, o, notes, prefix=f"输出 {o} ")
            if dead is None and dead_o is not None:
                dead = dead_o
            if backtest:
                bt = _backtest_frame(p, spec, freq, groups=groups)
                ev_o["layered_backtest"] = bt
                if bt.get("empty_groups"):
                    notes.append(f"输出 {o} 档位 {bt['empty_groups']} 全期无股票"
                                 "——universe 过小或 --groups 过大")
            evaluation["outputs"][o] = ev_o
    return EvaluationOutcome(evaluation=evaluation, eval_panel=eval_panel,
                             outputs=outputs, frequency=freq, notes=notes,
                             dead_signal=dead)


def publish_run(result: FactorResult, outcome: EvaluationOutcome,
                ctx: RunContext) -> dict:
    """发布单点：summary 追加 evaluation → weekly.parquet + summary.json 落盘。

    weekly.parquet 保留历史文件名（布局单点），内容 = 评估输入面板：
    daily 模式为日频全量面板、weekly 模式为周频对齐面板（interface 已注明）。

    D5（R30 Task 2）：死信号（`evaluation.dead_signal=true`）**先落盘再失败**——
    summary 保留审计字段后抛 `DeadSignalError`（CLI `run` 统一非零退出），
    不再以 `n_weeks=0` 静默成功；正常因子零变化。
    """
    from factorlab.adapters import results_fs
    result.summary["evaluation"] = outcome.evaluation
    # R12：走 results 单点（布局 + **原子**写）——原先直写，崩在中途会留半截 summary.json
    results_fs.write_run_outputs(Path(ctx.output_dir), weekly=outcome.eval_panel,
                                 summary=result.summary)
    if outcome.dead_signal is not None:
        rep = outcome.dead_signal
        raise DeadSignalError(
            f"死信号（D5 fail-loud）：signal_null_ratio={rep['signal_null_ratio']} ≥ "
            f"阈值 {rep['threshold']}（{rep['null_rows']}/{rep['total_rows']} 行为空）"
            "——评估摘要已落盘（summary.evaluation.dead_signal=true）供审计；"
            "本次运行非零退出，请先修复信号取数（governance/workspace/data-map.md）")
    return result.summary
