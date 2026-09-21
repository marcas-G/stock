"""R31 Task 6：study 一条链（spec §3 study）。

一条链：因子 run →（admit 冗余检验，可 `--skip-admit`）→ 策略回测（可选
`--strategy`）→ 报告 URL；全程单个 JSON 信封，artifacts 汇总全部产物路径。

只装配不实现：链上每一步直接调用对应组门面（`factor_run`/`factor_admit`/
`strategy_run`/`report_url`），业务零复制；任一步失败即停，传播其稳定错误码，
且 artifacts 保留已完成步骤（study 记录照落，`study list` 可见失败历史）。

策略步强制 `signal=<本次因子名>`——一条链语义：回测刚算出的因子，而不是
YAML 里写的旧信号。`--against reference` 映射到参考库 daily 组（admit 的口径）。
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
from typing import Any

from factorlab.adapters.atomicio import atomic_write_text
from factorlab.config import settings
from factorlab.research import envelope, registry
from factorlab.research.factor import (
    _jsonify,
    _run_args as _factor_run_args,
    factor_admit,
    factor_run,
)
from factorlab.research.report import report_url
from factorlab.research.strategy import strategy_run

_PRETTY = registry.ParamSpec("pretty", kind="bool", help="缩进 JSON（人读）")
_JSON = registry.ParamSpec("json", kind="bool",
                           help="输出单个 JSON 信封（默认口径，恒开）")

# `--against` → admit 参考库分组（spec §3：reference/daily|minute 库）
_AGAINST_SCALES = {"reference": "daily", "daily": "daily", "minute": "minute"}


def _study_root() -> Path:
    """study 记录根：`<results_dir 的兄弟>/research/study`（R37 归位后物理在
    `$QUANTRESEARCH_ROOT/results/research/study`）。"""
    return Path(settings.results_dir).parent / "research" / "study"


def _persist(record: dict[str, Any], artifacts: dict[str, Any]) -> Path:
    """原子写 study 记录（study.json + run.log），返回记录目录。"""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    label = record.get("factor") or "unknown"
    directory = _study_root() / f"{stamp}-{label}"
    atomic_write_text(
        directory / "study.json",
        json.dumps(_jsonify(record), ensure_ascii=False, indent=2) + "\n")
    log_lines = [
        f"flab study run factor={record.get('factor')} ok={record.get('ok')}",
        f"steps={' > '.join(record.get('steps') or []) or '-'}",
        f"verdict={record.get('verdict')} error={record.get('error_code')}",
        f"report_url={record.get('report_url')}",
        f"artifacts={json.dumps(_jsonify(artifacts), ensure_ascii=False)}",
    ]
    atomic_write_text(directory / "run.log", "\n".join(log_lines) + "\n")
    return directory


def _admit_args(spec_path: Path, scales: str, wait: bool) -> argparse.Namespace:
    return argparse.Namespace(spec_path=spec_path, scales=scales, wait=wait,
                              json=True, pretty=False)


def _strategy_args(doc_path: Path, signal: str,
                   wait: bool) -> argparse.Namespace:
    return argparse.Namespace(doc_path=doc_path, signal=signal, dry_run=False,
                              out_dir=None, wait=wait, json=True, pretty=False)


def _report_args(name: str) -> argparse.Namespace:
    return argparse.Namespace(name=name, base=None, json=True, pretty=False)


def study_run(args: argparse.Namespace) -> envelope.Envelope:
    """一条链：run →（admit）→ strategy（可选）→ report url；失败传播错误码。"""
    spec_path = Path(args.factor_yaml)
    strategy_yaml = (Path(args.strategy)
                     if getattr(args, "strategy", None) else None)
    against = getattr(args, "against", None) or "reference"
    scales = _AGAINST_SCALES.get(against)
    if scales is None:
        return envelope.fail(
            "study.run", "USAGE", f"未知 --against: {against}",
            hint="against ∈ reference|daily|minute（reference = 参考库 daily 组）")
    skip_admit = bool(getattr(args, "skip_admit", False))
    wait = bool(getattr(args, "wait", False))

    artifacts: dict[str, Any] = {}
    steps: list[str] = []
    warnings: list[str] = []
    factor_name: str | None = None
    verdict: str | None = None
    strategy_data: dict[str, Any] | None = None
    report_data: dict[str, Any] | None = None
    error_env: envelope.Envelope | None = None

    def finish(step_env: envelope.Envelope | None = None) -> envelope.Envelope:
        record = {
            "timestamp": datetime.datetime.now().isoformat(timespec="microseconds"),
            "ok": error_env is None,
            "factor": factor_name,
            "factor_yaml": str(spec_path),
            "strategy_yaml": str(strategy_yaml) if strategy_yaml else None,
            "against": against,
            "skip_admit": skip_admit,
            "steps": list(steps),
            "verdict": verdict,
            "report_url": (report_data or {}).get("url"),
            "error_code": (error_env.error or {}).get("code") if error_env else None,
            "artifacts": dict(artifacts),
        }
        directory = _persist(record, artifacts)
        artifacts["study"] = str(directory / "study.json")
        artifacts["log"] = str(directory / "run.log")
        if error_env is not None:
            err = error_env.error or {}
            out = envelope.fail(
                "study.run", err.get("code") or "INTERNAL",
                err.get("message") or "上游步骤失败",
                hint=err.get("hint"), log=err.get("log"))
            out.artifacts = dict(artifacts)
            return out
        return envelope.ok(
            "study.run",
            {"factor": factor_name, "verdict": verdict,
             "strategy": strategy_data, "report": report_data,
             "steps": list(steps)},
            artifacts=artifacts, warnings=tuple(warnings))

    # step 1：因子计算+评估（过闸在 factor 组内；失败码原样传播）
    # R31.2：性能开关（profile/no_read_cache）同步透传到 factor run
    run_env = factor_run(_factor_run_args(
        spec_path, wait=wait,
        profile=getattr(args, "profile", None),
        no_read_cache=getattr(args, "no_read_cache", None)))
    if not run_env.ok:
        error_env = run_env
        return finish()
    factor_name = run_env.data["name"]
    artifacts.update(run_env.artifacts)
    steps.append("run")

    # step 2：入库冗余检验（缺产物时 admit 内部会经闸重跑）
    if not skip_admit:
        admit_env = factor_admit(_admit_args(spec_path, scales, wait))
        if not admit_env.ok:
            error_env = admit_env
            return finish()
        verdict = admit_env.data.get("verdict")
        steps.append("admit")
        if verdict != "可加入":
            warnings.append(
                f"admit 判决：{verdict or '未知'}——{admit_env.data.get('建议') or ''}")

    # step 3：策略回测（signal 强制=本次因子名，一条链语义）
    if strategy_yaml is not None:
        strategy_env = strategy_run(
            _strategy_args(strategy_yaml, factor_name, wait))
        if not strategy_env.ok:
            error_env = strategy_env
            return finish()
        strategy_data = strategy_env.data
        steps.append("strategy")
        for key in ("strategy_dir", "strategy_manifest", "backtest_manifest",
                    "nav"):
            if key in strategy_env.artifacts:
                artifacts[key] = strategy_env.artifacts[key]

    # step 4：报告 URL（不启服务）
    report_env = report_url(_report_args(factor_name))
    if not report_env.ok:
        error_env = report_env
        return finish()
    report_data = report_env.data
    artifacts["report_url"] = report_data["url"]
    steps.append("report")

    return finish()


def study_list(args: argparse.Namespace) -> envelope.Envelope:
    """历史 study 记录（study.json 真读；新记录在前）。"""
    root = _study_root()
    rows: list[dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(root.glob("*/study.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            record["path"] = str(path)
            rows.append(record)
    rows.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
    return envelope.ok("study.list", _jsonify({"n": len(rows), "studies": rows}))


def _register(name: str, *, handler, description: str, params: Any = (),
              defaults: dict[str, Any] | None = None, examples: Any = (),
              output_schema: dict[str, Any] | None = None) -> None:
    registry.register(
        registry.CommandSpec(
            name=name,
            params=tuple(params),
            defaults={"json": True, "pretty": False, **(defaults or {})},
            description=description,
            examples=tuple(examples),
            output_schema=output_schema or {"type": "object"},
        ),
        handler,
    )


_register(
    "study.run", handler=study_run,
    params=(registry.ParamSpec("factor_yaml", kind="path", positional=True,
                               required=True, help="因子 spec YAML 路径"),
            registry.ParamSpec("strategy", kind="path",
                               help="策略 YAML（缺省只到 admit+报告；给定时回测该因子）"),
            registry.ParamSpec("against", kind="str",
                               help="参考库对照：reference|daily|minute（缺省 reference）"),
            registry.ParamSpec("skip_admit", kind="bool",
                               help="跳过 admit 冗余检验（只 run→报告）"),
            registry.ParamSpec("profile", kind="bool",
                               help="R09-M3 分段计时透传（见 `flab factor run --help`）"),
            registry.ParamSpec("no_read_cache", kind="bool",
                               help="R31 关闭分钟链读缓存透传（见 `flab factor run --help`）"),
            registry.ParamSpec("wait", kind="bool",
                               help="heavy 闸满时阻塞等槽（缺省立即 BUSY）"),
            _JSON, _PRETTY),
    defaults={"strategy": None, "against": "reference", "skip_admit": False,
              "profile": False, "no_read_cache": False, "wait": False},
    description="一条链：因子 run →（admit）→ 策略回测 → 报告 URL（过 heavy 闸）",
    examples=("flab study run $QUANTRESEARCH_ROOT/factor/volatility/max_effect_20d_high.yaml",
              "flab study run <factor.yaml> --strategy $QUANTRESEARCH_ROOT/strategy/low_lottery_top30_weekly.yaml",
              "flab study run <factor.yaml> --skip-admit"),
    output_schema={"type": "object", "properties": {
        "factor": {"type": "string"}, "verdict": {"type": "string"},
        "strategy": {"type": "object"}, "report": {"type": "object"},
        "steps": {"type": "array", "items": {"type": "string"}}}},
)

_register(
    "study.list", handler=study_list,
    params=(_JSON, _PRETTY),
    description="历史 study 记录（含失败；新记录在前）",
    examples=("flab study list --json",),
    output_schema={"type": "object", "properties": {
        "n": {"type": "integer"}, "studies": {"type": "array"}}},
)

__all__ = ["study_list", "study_run"]
