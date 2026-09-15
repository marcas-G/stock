"""唯一评估装配（WS5）：周频对齐 → 逐输出评估 → 分层回测 → 发布落盘。

从 `cli/main.py` 自建装配上移（历史：CLI 是唯一装配点，Python API 调用方
复刻不出同一评估口径）。语义逐字保留：
- legacy 单输出（outputs == ["signal"]）：顶层结构 `{...评估键...}`；
- 多输出：顶层 `{"outputs": {o: 评估 dict}}`（无隐式主信号）；
- 对齐一次复用给评估与分层回测（千万行面板重复对齐在低内存机器上 segfault）；
- 发布 = weekly.parquet + summary.json（evaluation 追加后重写）；summary 由
  run 链先落盘（无 evaluation），本层负责追加并重写。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from factorlab.core.domain.frames import SignalArtifact  # noqa: F401  (类型语义文档)
from factorlab.app.context import RunContext
from factorlab.core.engine.compute import FactorResult
from factorlab.core.eval.alignment import align_weekly
from factorlab.core.eval.layered import layered_backtest
from factorlab.adapters.rust_ic import evaluate_factor_weekly
from factorlab.core.spec import FactorSpec


@dataclass
class EvaluationOutcome:
    evaluation: dict
    weekly: pl.DataFrame
    outputs: list[str]
    notes: list[str] = field(default_factory=list)   # 展示层提示（CLI 打印）


def evaluate_run(result: FactorResult, spec: FactorSpec, ctx: RunContext, *,
                 groups: int = 10, backtest: bool = True) -> EvaluationOutcome:
    """评估装配：align_weekly 一次 → 单输出顶层 / 多输出逐输出 → 可选分层回测。"""
    weekly = align_weekly(result.panel)
    outputs = list(spec.outputs) if spec.outputs is not None else ["signal"]
    notes: list[str] = []
    if outputs == ["signal"]:
        evaluation = evaluate_factor_weekly(result.panel, spec.name, spec.direction,
                                            target=spec.target, weekly=weekly)
        if backtest:
            bt = layered_backtest(weekly, spec.direction, n_groups=groups,
                                  forward_col=spec.target,
                                  cost_rate=spec.cost_rate)
            evaluation["layered_backtest"] = bt
            if bt.get("empty_groups"):
                notes.append(f"档位 {bt['empty_groups']} 全期无股票——universe 过小或 --groups 过大")
    else:
        # per-output 面板：周频对齐结果里取该输出列（date/code/o/target）→ 归一 signal
        evaluation = {"outputs": {}}
        for o in outputs:
            p = weekly.select(["date", "code", o, spec.target])
            if o != "signal":  # 字面 signal 输出：列名已就绪，rename 会自撞
                p = p.rename({o: "signal"})
            ev_o = evaluate_factor_weekly(p, spec.name, spec.direction,
                                          target=spec.target, weekly=p)
            if backtest:
                bt = layered_backtest(p, spec.direction, n_groups=groups,
                                      forward_col=spec.target,
                                      cost_rate=spec.cost_rate)
                ev_o["layered_backtest"] = bt
                if bt.get("empty_groups"):
                    notes.append(f"输出 {o} 档位 {bt['empty_groups']} 全期无股票"
                                 "——universe 过小或 --groups 过大")
            evaluation["outputs"][o] = ev_o
    return EvaluationOutcome(evaluation=evaluation, weekly=weekly,
                             outputs=outputs, notes=notes)


def publish_run(result: FactorResult, outcome: EvaluationOutcome,
                ctx: RunContext) -> dict:
    """发布单点：summary 追加 evaluation → weekly.parquet + summary.json 落盘。"""
    result.summary["evaluation"] = outcome.evaluation
    out_dir: Path = Path(ctx.output_dir)
    outcome.weekly.write_parquet(out_dir / "weekly.parquet")
    (out_dir / "summary.json").write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    return result.summary
