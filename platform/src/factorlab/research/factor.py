"""R31 Task 4：factor 组门面（计算/评估/浏览/导出/冗余检验）。

只装配不实现：全部业务经既有实现复用——
- lint：`surfaces.cli.main._lint_one`（与 CLI 同一静态管线）；
- run：`surfaces.cli.main.execute_run`（CLI `factorlab run` 的计算主体）+
  `_guard.guard_heavy`（过闸 + env 注入 + 释放）；
- list：`surfaces.cli.main.collect_result_rows`；show/export：`adapters.results_fs` /
  `adapters.panel_store`；
- corr/resic/svd：`app.analysis.correlation` / `app.analysis.cross_section`；
- ref：`app.analysis.reference` 加载 + 本模块安全写（备份/校验/原子/注释保留）；
- op/catalog：`adapters.plugins` / `adapters.catalog` / `core.ops.registration`。

`admit` / `ref add` = lint → 最终测试门（R42：无 final 登记则经入库车道执行
测试段最终测试并冻结 `test_diagnostics.json`；IS-only/冻结件缺失即拒）→ verdict
吃冻结件测试段数：corr_max≥0.95 → 重复；corr_max≥0.9、r2_lib≥0.9、
retention<0.2，或 r2_lib≥0.8 且 |resIC t|<2 → 冗余；只有同时满足
|resIC t|≥3、corr_max<0.7、retention≥0.5 才可加入，其余 → 观察。
3.0 是用户选定的操作门槛；99 项试验、B=300 的联合 max-T 估计临界值约 3.45，
因此不声称已控制 FWER。诊断走 `incremental_diagnostics`，并以 `date_start` 切片。

错误码（spec §4）：USAGE/LINT/MEMORY_GUARD/DEAD_SIGNAL/RUN_FAILED/DATA/NOT_FOUND/BUSY。
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import yaml

from factorlab.adapters.atomicio import atomic_write_text
# Plan DQ-M1 终审修复 N2：读取门 opt-in（入口参数校验 + DATA 信封映射）
from factorlab.adapters.read.health import (DatasetQualityError,
                                            parse_accept_quality,
                                            resolve_accept_quality)
from factorlab.config import settings
from factorlab.research import envelope, registry
from factorlab.research._guard import GuardError, guard_heavy, release_slots
from factorlab.research.data_meta import emit_frame

_PRETTY = registry.ParamSpec("pretty", kind="bool", help="缩进 JSON（人读）")
_JSON = registry.ParamSpec("json", kind="bool",
                           help="输出单个 JSON 信封（默认口径，恒开）")
_FRAME_PARAMS = (
    registry.ParamSpec("out", kind="path",
                       help="落盘路径覆盖（缺省 runs/research/<cmd>/<ts>/data.parquet）"),
    registry.ParamSpec("limit", kind="int", help="截断行数（先截断再决定内联/落盘）"),
    registry.ParamSpec("inline", kind="bool", help="强制内联全部行（忽略 >200 行落盘默认）"),
)
_RUN_PARAMS = (
    registry.ParamSpec("spec_path", kind="path", positional=True, required=True,
                       help="因子 spec YAML 路径"),
    registry.ParamSpec("universe", kind="str",
                       help="宇宙覆盖（6 位代码/池名/路径；缺省 spec/默认池）"),
    registry.ParamSpec("max_memory", kind="str",
                       help="DuckDB 连接内存上限（缺省 4GB）"),
    registry.ParamSpec("output_dir", kind="path", help="产物目录（缺省 results_dir/<变体>）"),
    registry.ParamSpec("no_backtest", kind="bool", help="关闭分层回测（快速评估）"),
    registry.ParamSpec("groups", kind="int", help="分层档数（>=2，缺省 10）"),
    registry.ParamSpec("set", kind="list[str]",
                       help="覆盖 spec.params（k=v，可多次，生成 name_kv 变体）"),
    registry.ParamSpec("chunk_days", kind="int", help="日期分块（交易日/块）"),
    registry.ParamSpec("chunk_workers", kind="int",
                       help="分钟链 chunk 并行 worker 数（默认 1；N>=2 预算门）"),
    registry.ParamSpec("profile", kind="bool",
                       help="R09-M3 分段计时（写 stderr + summary.runtime.profile；"
                            "默认关，env FACTORLAB_PROFILE=1 等效）"),
    registry.ParamSpec("no_read_cache", kind="bool",
                       help="R31 关闭分钟链 bars_1m chunk 磁盘缓存（默认开；"
                            "仅 interface: bars_1m 生效）"),
    registry.ParamSpec("warmup_days", kind="int", help="TS 窗口预热天数"),
    registry.ParamSpec("eval_frequency", kind="str", help="评估频率 daily|weekly"),
    registry.ParamSpec("wait", kind="bool", help="heavy 闸满时阻塞等槽（缺省立即 BUSY）"),
    registry.ParamSpec("no_float32", kind="bool", help="关闭 float32 面板"),
    registry.ParamSpec("accept_quality", kind="str",
                       help="读取门 opt-in：逗号分隔 health_status（默认空=仅 PASS；"
                            "非空必须同时给 --override-reason；FAIL 不可 opt-in）"),
    registry.ParamSpec("override_reason", kind="str",
                       help="非 PASS 读取门 opt-in 原因（写入 Experiment Manifest）"),
)
_FACTOR_HINT = ("因子产物缺失先 `flab factor run <spec.yaml>`；"
                "命令目录 `flab describe --json`")
_ERR_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "description": "稳定错误码（见 describe exit_codes）"},
        "message": {"type": "string"},
        "hint": {"type": "string"},
        "log": {"type": "string"},
    },
}


# ================================================================
# 公共：JSON 清洗 / 注册助手 / 错误出口
# ================================================================

def _jsonify(value: Any) -> Any:
    """信封前置清洗：NaN/±inf → null；date/datetime → ISO；numpy 标量 → python。"""
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy 标量（np.float64 等）
        return _jsonify(value.item())
    return value


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and x == x and abs(x) != float("inf")


def _ensure() -> None:
    """装配单点（算子族/process/插件）——Python 面直调不经 CLI 回调。"""
    from factorlab.app.bootstrap import ensure_assembly
    ensure_assembly()


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


# ================================================================
# lint
# ================================================================

def _lint_paths(paths: list[Path]) -> tuple[list[dict], list[tuple[Path, str]]]:
    """逐路径静态校验（失败隔离，CLI 批跑同款）；复用 surfaces 的 `_lint_one`。"""
    from factorlab.surfaces.cli.main import _lint_one
    results: list[dict] = []
    failures: list[tuple[Path, str]] = []
    for path in paths:
        try:
            name = _lint_one(Path(path))
            results.append({"path": str(path), "name": name, "ok": True})
        except Exception as exc:  # noqa: BLE001 —— 批跑失败隔离：逐个上报，不中断
            results.append({"path": str(path), "ok": False, "error": str(exc)})
            failures.append((Path(path), str(exc)))
    return results, failures


def factor_lint(args: Any) -> envelope.Envelope:
    """静态校验（秒级，不连库；与 CLI 同一管线）。坏 spec → LINT。"""
    _ensure()
    paths = [Path(p) for p in (getattr(args, "spec_paths", None) or [])]
    if getattr(args, "all", False):
        from factorlab.surfaces.cli.main import _factor_spec_paths
        paths = sorted(set(paths) | set(_factor_spec_paths()))
    if not paths:
        return envelope.fail("factor.lint", "USAGE", "请给出至少一个 spec 路径，或用 --all 全库批跑",
                             hint="flab factor lint $QUANTRESEARCH_ROOT/factor/<族>/<短名>.yaml")
    results, failures = _lint_paths(paths)
    if failures:
        first_path, first_err = failures[0]
        return envelope.fail(
            "factor.lint", "LINT",
            f"{len(failures)}/{len(paths)} 个 spec 校验失败——{first_path}: {first_err}",
            hint="修正 formula/字段后重跑 `flab factor lint <spec>`")
    return envelope.ok("factor.lint", {"n_pass": len(results), "results": results})


# ================================================================
# run（过闸 + env 注入 + 摘要 + artifacts）
# ================================================================

@contextmanager
def _guard_env(argv: list[str], *, wait: bool) -> Iterator[tuple[dict, Path]]:
    """过 heavy 闸 → 注入 env/settings（run 执行期生效）→ 退出释放槽 + 复原。"""
    env, slot = guard_heavy(argv, wait=wait)
    saved_env = {key: os.environ.get(key) for key in env}
    saved_settings = (settings.max_memory, settings.min_available_memory)
    try:
        os.environ.update(env)
        if env.get("FACTORLAB_MAX_MEMORY"):
            settings.max_memory = env["FACTORLAB_MAX_MEMORY"]
        if env.get("FACTORLAB_MIN_AVAILABLE_MEMORY"):
            settings.min_available_memory = env["FACTORLAB_MIN_AVAILABLE_MEMORY"]
        yield env, slot
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        settings.max_memory, settings.min_available_memory = saved_settings
        release_slots()


def _evaluation_view(ev: dict) -> dict:
    """摘要口径（spec §3 factor.run）：IC/十分位/换手/覆盖/ic_decay/version/frequency。"""

    def metrics(e: dict) -> dict:
        dec = e.get("decile_returns") or {}
        return {
            "version": e.get("version"),
            "frequency": e.get("frequency"),
            "target": e.get("target"),
            "n_weeks": e.get("n_weeks"),
            "ic": e.get("ic"),
            "decile_spread": dec.get("spread"),
            "turnover": e.get("turnover"),
            "coverage": e.get("coverage"),
            "ic_decay": e.get("ic_decay"),
        }

    outs = ev.get("outputs")
    if isinstance(outs, dict):
        return {"frequency": ev.get("frequency"),
                "outputs": {o: metrics(e) for o, e in outs.items()}}
    return metrics(ev)


def factor_run(args: Any) -> envelope.Envelope:
    """计算+评估+分层回测：过 heavy 闸（BUSY/MEMORY_GUARD）→ 新产物 + 摘要。"""
    from factorlab.app.memory import MemoryLimitExceeded
    from factorlab.core.eval.metrics import DeadSignalError
    from factorlab.core.factor.errors import FactorDSLError
    from factorlab.core.lockbox import LockboxError
    from factorlab.surfaces.cli.main import execute_run

    _ensure()
    # N2：入口参数校验先于重链（缺 reason / FAIL / 未知状态 → USAGE，不占闸）
    override_reason = getattr(args, "override_reason", None)
    try:
        quality = resolve_accept_quality(
            parse_accept_quality(getattr(args, "accept_quality", None)),
            override_reason)
    except ValueError as exc:
        return envelope.fail(
            "factor.run", "USAGE", str(exc),
            hint="例：flab factor run <spec> --accept-quality PASS,DEGRADED "
                 "--override-reason '探索性研究'")
    spec_path = Path(args.spec_path)
    argv = ["factor", "run", str(spec_path)]
    try:
        with _guard_env(argv, wait=bool(getattr(args, "wait", False))):
            out = execute_run(
                spec_path,
                universe=getattr(args, "universe", None),
                max_memory=getattr(args, "max_memory", "4GB"),
                output_dir=(Path(args.output_dir)
                            if getattr(args, "output_dir", None) else None),
                float32=not bool(getattr(args, "no_float32", False)),
                backtest=not bool(getattr(args, "no_backtest", False)),
                groups=getattr(args, "groups", 10),
                set_params=getattr(args, "set", None),
                chunk_days=getattr(args, "chunk_days", None),
                warmup_days=getattr(args, "warmup_days", None),
                eval_frequency=getattr(args, "eval_frequency", None),
                profile=getattr(args, "profile", None),          # R31.2 透传
                chunk_workers=getattr(args, "chunk_workers", None) or 1,
                # R31.2：--no-read-cache → False 强制关；缺省/None → env 默认开
                read_cache=False if getattr(args, "no_read_cache", None) else None,
                # Plan DQ-M1 F3：真实入口 fail-closed 读取门（ashare_daily）
                # N2：DEGRADED/LEGACY opt-in 透传（自动写 Experiment Manifest）
                dataset="ashare_daily",
                accept_quality=quality,
                override_reason=override_reason,
            )
    except GuardError as exc:
        return envelope.fail("factor.run", exc.code, exc.message,
                             hint=exc.hint, log=exc.log)
    except LockboxError as exc:
        return envelope.fail("factor.run", exc.code, exc.message,
                             hint="`factorlab lockbox status` 看窗口与训练段端点"
                                  "（is_end）")
    except DeadSignalError as exc:
        return envelope.fail("factor.run", "DEAD_SIGNAL", str(exc),
                             hint="评估摘要已落盘（dead_signal=true）供审计；先修取数列/数据面")
    except MemoryLimitExceeded as exc:
        return envelope.fail("factor.run", "MEMORY_GUARD", str(exc),
                             hint="减小 chunk/宇宙，或调大 FACTORLAB_MAX_MEMORY")
    except FileNotFoundError as exc:
        return envelope.fail("factor.run", "NOT_FOUND", f"spec 不存在: {exc}",
                             hint="检查路径；命令目录见 `flab describe --json`")
    except FactorDSLError as exc:
        return envelope.fail("factor.run", "LINT", f"spec 校验失败: {exc}",
                             hint="先 `flab factor lint <spec>`")
    except DatasetQualityError as exc:
        return envelope.fail(
            "factor.run", "DATA", f"读取门拒绝: {exc}",
            hint="如需探索性读取非 PASS 分区：--accept-quality PASS,DEGRADED "
                 "--override-reason <原因>（FAIL 不可 opt-in；DEGRADED/LEGACY "
                 "opt-in 自动写 Experiment Manifest）")
    except (ValueError, OSError) as exc:
        return envelope.fail("factor.run", "RUN_FAILED", f"{type(exc).__name__}: {exc}",
                             hint="核对 spec/数据面；日志见 artifacts.log")

    outcome = out["outcome"]
    run_dir = Path(out["ctx"].output_dir)
    summary_path = run_dir / _results_fs().SUMMARY_NAME
    log_path = run_dir / "run.log"
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    log_lines = [
        f"flab factor run spec={spec_path} variant={out['variant']}",
        f"run_dir={run_dir} summary={summary_path}",
        f"frequency={outcome.frequency} outputs={outcome.outputs}",
        *[f"note: {note}" for note in outcome.notes],
    ]
    atomic_write_text(log_path, "\n".join([f"[{stamp}] {ln}" for ln in log_lines]) + "\n")
    return envelope.ok(
        "factor.run",
        {"name": out["variant"], "evaluation": _jsonify(_evaluation_view(outcome.evaluation))},
        artifacts={"run_dir": str(run_dir), "summary": str(summary_path),
                   "log": str(log_path)},
        warnings=tuple(outcome.notes),
    )


def _results_fs():
    from factorlab.adapters import results_fs
    return results_fs


# ================================================================
# list / show / export
# ================================================================

def factor_list(args: Any) -> envelope.Envelope:
    """已保存因子列表（results_dir 摘要；新运行在前）。"""
    from factorlab.surfaces.cli.main import collect_result_rows
    rows = collect_result_rows(settings.results_dir)
    out = [{k: v for k, v in row.items() if k != "_sort"} for row in rows]
    return envelope.ok("factor.list", _jsonify({"n": len(out), "factors": out}))


def factor_show(args: Any) -> envelope.Envelope:
    """单因子完整 summary.json（缺失 → NOT_FOUND）。"""
    results_fs = _results_fs()
    name = args.name
    summary_path = results_fs.summary_path(settings.results_dir, name)
    if not summary_path.is_file():
        return envelope.fail("factor.show", "NOT_FOUND",
                             f"因子 {name} 无产物（{summary_path}）", hint=_FACTOR_HINT)
    try:
        summary = results_fs.read_summary(summary_path)
    except (ValueError, OSError) as exc:
        return envelope.fail("factor.show", "DATA", f"summary 读取失败: {exc}",
                             hint="重跑 `flab factor run <spec>`")
    return envelope.ok("factor.show", {"name": name, "summary": _jsonify(summary)},
                       artifacts={"summary": str(summary_path)})


_EXPORT_FORMATS = {"parquet": ".parquet", "csv": ".csv", "json": ".json"}


def factor_export(args: Any) -> envelope.Envelope:
    """产物导出：panel.parquet → parquet|csv|json（缺失 → NOT_FOUND）。"""
    from factorlab.adapters.panel_store import ParquetPanelStore
    from factorlab.research.data_meta import result_frame
    fmt = str(getattr(args, "format", None) or "parquet").lower()
    if fmt not in _EXPORT_FORMATS:
        return envelope.fail("factor.export", "USAGE",
                             f"未知格式 {fmt}（支持 {'|'.join(_EXPORT_FORMATS)}）")
    name = args.name
    results_dir = Path(settings.results_dir)
    store = ParquetPanelStore()
    if not store.has_panel(results_dir, name):
        return envelope.fail("factor.export", "NOT_FOUND",
                             f"因子 {name} 无 panel 产物", hint=_FACTOR_HINT)
    df = store.load_panel(results_dir, name)
    out = getattr(args, "out", None)
    if out is None:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = (results_dir.parent / "research" / "factor" / "export" / stamp
               / f"{name}{_EXPORT_FORMATS[fmt]}")
    return result_frame(df, command="factor.export", out=Path(out))


# ================================================================
# corr / resic / svd
# ================================================================

def factor_corr(args: Any) -> envelope.Envelope:
    """因子两两相关矩阵（--against 并入对照集；结果帧 >200 行落 parquet）。"""
    from factorlab.app.analysis.correlation import factor_correlation
    names = list(getattr(args, "names", None) or [])
    against = getattr(args, "against", None)
    if not names and against is None:
        return envelope.fail("factor.corr", "USAGE",
                             "至少需要 2 个因子（或用 --against reference|all|<名单>）",
                             hint="flab factor corr a b c / --against reference")
    if against is None and len(names) < 2:
        return envelope.fail("factor.corr", "USAGE",
                             f"至少需要 2 个因子（收到 {len(names)} 个）",
                             hint="flab factor corr <name1> <name2> [--against reference]")
    try:
        mtx = factor_correlation(names, settings.results_dir, against=against)
    except FileNotFoundError as exc:
        return envelope.fail("factor.corr", "NOT_FOUND", str(exc), hint=_FACTOR_HINT)
    except ValueError as exc:
        return envelope.fail("factor.corr", "DATA", str(exc),
                             hint="核对面板日期区间/公共日期")
    return emit_frame("factor.corr", mtx, args)


def _strip_weekly(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "weekly"}


def _resic_payload(d: dict, frequency: str) -> dict:
    """Remove internal frames and name the per-period count for daily callers."""
    out = _strip_weekly(d)
    if frequency == "daily" and "n_weeks" in out:
        out["n_periods"] = out["n_weeks"]
    return out


def factor_resic(args: Any) -> envelope.Envelope:
    """增量信息（--against 参考库）或组内互评（resIC/R²/verdict）。"""
    from factorlab.app.analysis.correlation import resolve_against
    from factorlab.app.analysis.cross_section import (incremental_diagnostics,
                                                      joint_diagnostics)
    names = list(getattr(args, "names", None) or [])
    target = getattr(args, "target", None)
    against = getattr(args, "against", None)
    min_stocks = getattr(args, "min_stocks", None) or 30
    frequency = getattr(args, "frequency", None)
    if frequency is None:
        frequency = "weekly"
    if frequency not in ("daily", "weekly"):
        return envelope.fail(
            "factor.resic", "USAGE",
            f"frequency 只能是 daily 或 weekly（收到 {frequency!r}）",
            hint="flab factor resic <candidate> --frequency daily --horizon 1")
    horizon = getattr(args, "horizon", None)
    if horizon is not None and (
            isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1):
        return envelope.fail(
            "factor.resic", "USAGE",
            f"horizon 必须是正整数（收到 {horizon!r}）",
            hint="flab factor resic <candidate> --horizon 1")
    fwd_col = getattr(args, "fwd_col", None)
    if fwd_col is not None:
        if not isinstance(fwd_col, str) or not fwd_col.strip():
            return envelope.fail(
                "factor.resic", "USAGE", "fwd_col 必须是非空列名",
                hint="用 --fwd-col forward_return_1d 选择标签列")
        fwd_col = fwd_col.strip()
    else:
        default_horizon = 1 if frequency == "daily" else 5
        fwd_col = f"forward_return_{horizon if horizon is not None else default_horizon}d"
    if against is None and target is None and len(names) < 2:
        return envelope.fail("factor.resic", "USAGE",
                             "组内互评至少需要 2 个因子（或 --target/--against）",
                             hint="flab factor resic a b c / --against reference")
    try:
        if against is not None:
            candidates = [target] if target else names
            if not candidates:
                return envelope.fail("factor.resic", "USAGE",
                                     "--against 模式需要候选（位置参数或 --target）",
                                     hint="flab factor resic <name> --against reference")
            base = [b for b in resolve_against(against, settings.results_dir)
                    if b not in candidates]
            r = incremental_diagnostics(candidates, settings.results_dir, base=base,
                                        min_stocks=min_stocks, fwd_col=fwd_col,
                                        frequency=frequency)
            data = {
                "mode": "incremental", "frequency": frequency,
                "fwd_col": fwd_col, "base": r["base"],
                "candidates": [_resic_payload(c, frequency)
                               for c in r["candidates"]],
            }
        else:
            r = joint_diagnostics(names, settings.results_dir, target=target,
                                  min_stocks=min_stocks, fwd_col=fwd_col,
                                  frequency=frequency)
            data = {
                "mode": r["mode"], "frequency": frequency, "fwd_col": fwd_col,
                "group": _resic_payload(r["group"], frequency),
                "factors": [_resic_payload(f, frequency) for f in r["factors"]],
            }
    except FileNotFoundError as exc:
        return envelope.fail("factor.resic", "NOT_FOUND", str(exc), hint=_FACTOR_HINT)
    except ValueError as exc:
        return envelope.fail("factor.resic", "DATA", str(exc),
                             hint="核对候选/基准与面板区间（候选不得 ∈ 基准）")
    return envelope.ok("factor.resic", _jsonify(data))


def factor_svd(args: Any) -> envelope.Envelope:
    """SVD 结构分析：默认参考库 daily 组；--all 显式扫全库。"""
    from factorlab.app.analysis.correlation import factor_svd as _svd
    from factorlab.app.analysis.reference import reference_names
    names = list(getattr(args, "names", None) or [])
    weeks = getattr(args, "weeks", None) or 15
    source = "names"
    if not names:
        if getattr(args, "all", False):
            skip = {"acceptance", "demo_vol_skew", "m4b_smoke"}
            names = sorted(p.parent.name
                           for p in Path(settings.results_dir).glob("*/panel.parquet")
                           if p.parent.name not in skip)
            source = "全库"
        else:
            try:
                names = reference_names("daily")
            except (FileNotFoundError, ValueError) as exc:
                return envelope.fail(
                    "factor.svd", "NOT_FOUND", f"参考库不可用（{exc}）",
                    hint="用 --all 扫全库，或修 `$QUANTRESEARCH_ROOT/factor/_reference.yaml`")
            source = "参考库 daily"
    if len(names) < 2:
        return envelope.fail("factor.svd", "USAGE", "至少需要 2 个因子",
                             hint="flab factor svd [name1 name2 ...] [--all]")
    try:
        r = _svd(names, settings.results_dir, sample_weeks=weeks)
    except FileNotFoundError as exc:
        return envelope.fail("factor.svd", "NOT_FOUND", str(exc), hint=_FACTOR_HINT)
    except ValueError as exc:
        return envelope.fail("factor.svd", "DATA", str(exc))
    return envelope.ok("factor.svd", _jsonify({
        "source": source, "n_factors": len(names),
        "singular_values": list(r["singular_values"]),
        "cum_explained": list(r["cum_explained"]),
        "loadings": list(r["loadings"]),
    }))


# ================================================================
# 入库门 + admit（lint → 最终测试门/冻结件 → verdict 吃测试段数）
# ================================================================

_VERDICT_JOIN = "可加入"
_VERDICT_WATCH = "观察"
_VERDICT_REDUNDANT = "冗余"
_VERDICT_DUPLICATE = "重复"
_VERDICT_SEED = "种子"


def _admit_verdict(corr_max: float, r2_lib: float, resic_t: float,
                   retention: float | None) -> str:
    """D10 入库判决：先判重复/冗余，再要求 t、相关性与 retention 均达标。"""
    from factorlab.app.analysis.cross_section import (
        REFERENCE_ADMISSION_MIN_ABS_RESIC_T,
    )

    if _finite(corr_max) and corr_max >= 0.95:
        return _VERDICT_DUPLICATE
    if ((_finite(corr_max) and corr_max >= 0.9)
            or (_finite(r2_lib) and r2_lib >= 0.9)
            or (_finite(retention) and retention < 0.2)
            or (_finite(r2_lib) and r2_lib >= 0.8 and not (
                _finite(resic_t) and abs(resic_t) >= 2.0))):
        return _VERDICT_REDUNDANT
    if (_finite(resic_t)
            and abs(resic_t) >= REFERENCE_ADMISSION_MIN_ABS_RESIC_T
            and _finite(corr_max) and corr_max < 0.7
            and _finite(retention) and retention >= 0.5):
        return _VERDICT_JOIN
    return _VERDICT_WATCH


_ADVICE = {
    _VERDICT_JOIN: "残差信息显著/独立——可加入参考库；补风格档案后 `flab factor ref add`",
    _VERDICT_SEED: "该 scales 组为空，作为首个种子登记；后续候选需经过 D10 增量准入门",
    _VERDICT_WATCH: "D10 增量准入条件未全部满足（|resIC t|≥3、corr_max<0.7、retention≥50%）——继续观察",
    _VERDICT_REDUNDANT: "与参考库近亲（r2_lib 高且残差无显著增量）——不建议加入；考虑合并或替换库内近亲",
    _VERDICT_DUPLICATE: "与参考库成员高度相关（corr_max≥0.95）——重复，不加入",
}


def _lockbox_hint(exc: Any, *, spec_missing: bool = False) -> str:
    """入库门 LockboxError → 稳定指引（错误码特定 + status 单点）。

    `spec_missing=True`：`LOCKBOX_FINAL_REQUIRED` 因候选 spec 缺失——指引先补 spec
    （`make xpipe` 重建冻结件也无源可跑）。
    """
    base = "`factorlab lockbox status` 看窗口与训练段端点（is_end）"
    if exc.code == "LOCKBOX_TEST_ONLY_FINAL":
        return ("入库只看测试段最终测试结果：把 spec 窗口延伸到测试段（window_start "
                "之后）再试，或先经 `make xpipe` 产出 `_5y` 变体；" + base)
    if exc.code == "LOCKBOX_FINAL_REQUIRED":
        if spec_missing:
            return "先补 spec（`factor/**/<name>.yaml`）后再做最终测试；" + base
        return "先经 `make xpipe`（pipeline final）重建最终测试与冻结件；" + base
    return base


class FinalTestError(Exception):
    """入库车道执行最终测试的 run 链失败（→ admit / ref add 稳定错误码）。"""

    def __init__(self, code: str, message: str, *,
                 hint: str | None = None, log: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.log = log


# 入库车道 = 生产车道：挖矿标准 env 口径（与 pipeline `data_prep._member_env` 同口径）。
# `FACTORLAB_DATA_BACKEND` **强制 ch**（显式值也不放行——车道内 duckdb 库不存在，
# 放行会让最终测试在半口径下跑并占版本）；其余两键 setdefault（显式值优先）。
# 执行后复原，不污染宿主。
_LANE_ENV_DEFAULTS = {
    "FACTORLAB_DATA_BACKEND": "ch",
    "FACTORLAB_ST_DEGRADE": "allow",
    "FACTORLAB_MINUTE_UNCOVERED": "drop",
}
_LANE_ENV_FORCED = ("FACTORLAB_DATA_BACKEND",)


def _execute_final_test(spec_path: Path, *, output_dir: Path, wait: bool,
                        reason: str | None = None) -> None:
    """入库车道执行最终测试：显式 `final_mode=True` + 进程内自设车道标记。

    `FACTORLAB_PIPELINE=1` / `FACTORLAB_LOCKBOX_REASON`（审计来源，如
    `admit final test: <name>`）只在执行期生效（退出复原）：guard_run 的
    `LOCKBOX_PIPELINE_REQUIRED` 靠 marker 过；重复登记由 guard_run 权威拒
    （`LOCKBOX_FINAL_DUPLICATE` 原样上抛）。挖矿标准 env（`_LANE_ENV_DEFAULTS`；
    `FACTORLAB_DATA_BACKEND` 强制 `ch`，其余两键 setdefault 不覆盖显式值）
    同样执行期生效、退出复原。
    run 链错误映射为 `FinalTestError`。基座产物预检由调用方（gate）在**开跑前**
    完成（零跑零登记，见 `_preflight_base_products`）。
    """
    from factorlab.adapters.read.health import DatasetQualityError
    from factorlab.app.memory import MemoryLimitExceeded
    from factorlab.core.eval.metrics import DeadSignalError
    from factorlab.core.factor.errors import FactorDSLError
    from factorlab.core.lockbox import LockboxError
    from factorlab.surfaces.cli.main import execute_run

    saved_pipeline = os.environ.get("FACTORLAB_PIPELINE")
    saved_reason = os.environ.get("FACTORLAB_LOCKBOX_REASON")
    saved_lane_env = {key: os.environ.get(key) for key in _LANE_ENV_DEFAULTS}
    os.environ["FACTORLAB_PIPELINE"] = "1"
    if reason:
        os.environ["FACTORLAB_LOCKBOX_REASON"] = reason
    for key, value in _LANE_ENV_DEFAULTS.items():
        if key in _LANE_ENV_FORCED:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)
    try:
        with _guard_env(["factor", "run", str(spec_path)], wait=wait):
            execute_run(spec_path, final_mode=True, output_dir=output_dir)
    except LockboxError:
        raise
    except GuardError as exc:
        raise FinalTestError(exc.code, exc.message, hint=exc.hint, log=exc.log)
    except DeadSignalError as exc:
        raise FinalTestError("DEAD_SIGNAL", str(exc),
                             hint="评估摘要已落盘（dead_signal=true）供审计；先修取数列/数据面")
    except MemoryLimitExceeded as exc:
        raise FinalTestError("MEMORY_GUARD", str(exc),
                             hint="减小 chunk/宇宙，或调大 FACTORLAB_MAX_MEMORY")
    except FileNotFoundError as exc:
        raise FinalTestError("NOT_FOUND", f"run 失败: {exc}", hint=_FACTOR_HINT)
    except FactorDSLError as exc:
        raise FinalTestError("LINT", f"spec 校验失败: {exc}",
                             hint="先 `flab factor lint <spec>`")
    except DatasetQualityError as exc:
        raise FinalTestError("DATA", f"读取门拒绝: {exc}",
                             hint="如需探索性读取非 PASS 分区：--accept-quality "
                                  "PASS,DEGRADED --override-reason <原因>")
    except (ValueError, OSError) as exc:
        raise FinalTestError("RUN_FAILED", f"{type(exc).__name__}: {exc}",
                             hint="核对 spec/数据面；日志见 artifacts.log")
    finally:
        if saved_pipeline is None:
            os.environ.pop("FACTORLAB_PIPELINE", None)
        else:
            os.environ["FACTORLAB_PIPELINE"] = saved_pipeline
        if saved_reason is None:
            os.environ.pop("FACTORLAB_LOCKBOX_REASON", None)
        else:
            os.environ["FACTORLAB_LOCKBOX_REASON"] = saved_reason
        for key, previous in saved_lane_env.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def _preflight_base_products(*, base: list[str], results_dir: Path) -> None:
    """最终测试/重算诊断前的基座预检（零跑零登记，防"烧掉唯一一次最终测试"）。

    - base 空 → `DATA`（无可对照成员，做不了测试段冗余检验）；
    - 任一参考成员缺 `<results_dir>/<b>_5y/panel.parquet` → `DATA`（列缺失成员；
      hint 先 `make xpipe-data` 补齐或修成员 spec，issue #35）。
    """
    if not base:
        raise FinalTestError("DATA", "参考库为空，不能做测试段冗余检验")
    missing = [b for b in base
               if not (results_dir / f"{b}_5y" / "panel.parquet").is_file()]
    if missing:
        raise FinalTestError(
            "DATA", "参考库成员缺 `_5y` 产物：" + "、".join(missing),
            hint="先 `make xpipe-data` 补齐参考库成员 `_5y` 产物，"
                 "或修复该成员 spec（issue #35）")


def _source_spec_path(name: str) -> Path | None:
    """源 spec（`$QR/factor/**/<name>.yaml` 首个排序匹配）；缺失 → None。

    与 `resolve_candidate_spec` 的回退同口径——变体/源漂移检查（M1）用。
    """
    root = Path(settings.research_root)
    return next(iter(sorted((root / "factor").rglob(f"{name}.yaml"))), None)


def _drift_fingerprint(spec_doc: dict[str, Any]) -> str:
    """变体/源一致性比对指纹（M1）：剔除系统性差异键（`name`/`date`——ref-sync
    变体口径）后取 `spec_fingerprint`，只留 formula/params/universe 等实质内容。

    不剔除则变体与源必然"不同"（name/date 系统性改写），警告会变成恒真噪声。
    """
    from factorlab.core.lockbox import spec_fingerprint
    payload = {k: v for k, v in spec_doc.items() if k not in ("name", "date")}
    return spec_fingerprint(payload)


def resolve_candidate_spec(name: str) -> Path | None:
    """候选因子的**规范 spec 解析单点**（与 pipeline ref-sync 同源约定）。

    优先 `$QR/experiments/r37_5y/<name>_5y.yaml`（ref-sync 生成的 5y 变体——
    最终测试身份与登记以它为准），否则 `$QR/factor/**/<name>.yaml`（首个排序
    匹配）；都没有 → None。
    """
    root = Path(settings.research_root)
    variant = root / "experiments" / "r37_5y" / f"{name}_5y.yaml"
    if variant.is_file():
        return variant
    return _source_spec_path(name)


_TEST_DIAGNOSTICS_SCHEMA = 2


def _diagnostic_options(spec_doc: dict[str, Any] | None) -> tuple[str, str]:
    """Resolve the resIC cadence from the candidate spec.

    D11 fixes daily evaluation to the one-day forward label; weekly keeps the
    spec's explicit target.  A missing spec is only possible on the legacy
    lockbox-off ref-add path, where the historical weekly/5d defaults remain
    explicit in the resulting diagnostics.
    """
    if spec_doc is None:
        return "weekly", "forward_return_5d"
    frequency = str(spec_doc.get("evaluation_frequency") or "daily")
    if frequency == "daily":
        return frequency, "forward_return_1d"
    if frequency == "weekly":
        target = str(spec_doc.get("target") or "forward_return_5d")
        return frequency, target
    raise ValueError(
        f"evaluation_frequency 只能是 daily 或 weekly（收到 {frequency!r}）")


def _compute_test_diagnostics(*, name: str, base: list[str], window: Any,
                              fingerprint: str,
                              spec_doc: dict[str, Any] | None = None,
                              frequency: str | None = None,
                              fwd_col: str | None = None
                              ) -> tuple[dict[str, Any], Path]:
    """测试段诊断（`date_start=window_start`）→ 写 `<results>/<name>_5y/test_diagnostics.json`。

    读最终测试 `_5y` 产物（候选 `<name>_5y` 对参考库 `<base>_5y`），只取
    `date >= window_start` 的测试段面板；返回 `(冻结件 doc, 路径)`。
    """
    from factorlab.app.analysis.cross_section import incremental_diagnostics
    if frequency is None or fwd_col is None:
        frequency, fwd_col = _diagnostic_options(spec_doc)
    results_dir = Path(settings.results_dir)
    r = incremental_diagnostics(
        [f"{name}_5y"], results_dir, base=[f"{b}_5y" for b in base],
        date_start=window.start.isoformat(), frequency=frequency,
        fwd_col=fwd_col)
    cand = r["candidates"][0]
    doc: dict[str, Any] = {
        "version_fingerprint": fingerprint,
        "window_id": window.window_id,
        "window_start": window.start.isoformat(),
        "date_start": window.start.isoformat(),
        "date_end": window.end.isoformat(),
        "diagnostics_schema": _TEST_DIAGNOSTICS_SCHEMA,
        "frequency": frequency,
        "fwd_col": fwd_col,
        "corr_max": cand["corr_max"],
        "r2_lib": cand["r2_lib"],
        "retention": cand["retention"],
        "resic_t": cand["resic_t"],
        "resic_mean": cand["resic_mean"],
        "n_weeks": cand["n_weeks"],
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"),
    }
    path = results_dir / f"{name}_5y" / "test_diagnostics.json"
    atomic_write_text(path, json.dumps(_jsonify(doc), ensure_ascii=False,
                                       indent=2) + "\n")
    return doc, path


def _final_test_gate(spec_doc: dict[str, Any] | None,
                     spec_path: Path | None, *,
                     reason: str | None, command: str,
                     scales: str = "daily",
                     base: list[str] | None = None,
                     wait: bool = False) -> dict[str, Any] | None:
    """admit / ref add 共用的最终测试硬门（设计 §3.3/§3.4）：入库只看测试段冻结结果。

    - env `FACTORLAB_LOCKBOX` off → None（纪律未启用；CI/测试走调用方旧径）；
    - IS-only（窗口全在训练段）→ `LOCKBOX_TEST_ONLY_FINAL`（入库必须有测试段结果）；
    - 无 final 登记 → **基座预检**（base 非空且 `<b>_5y/panel.parquet` 齐；缺 →
      `DATA` 零跑零登记）→ **执行最终测试**（`execute_run(final_mode=True)`，进程内
      `FACTORLAB_PIPELINE=1` 车道身份；guard_run 登记）→ 测试段诊断 →
      写冻结件 `<results>/<name>_5y/test_diagnostics.json`；
    - 已有 final → 冻结件存在且版本/窗口一致才放行；缺失但 `_5y` 产物在 →
      仅重算诊断（同一次测试收尾，不重跑因子）；缺失且产物没了/版本不符 →
      `LOCKBOX_FINAL_REQUIRED`（提示先经 `make xpipe` 重建）。
    返回 {"version_fingerprint", "window_id", "window_start", "access_id",
    "executed", "base", "diagnostics", "path", "reason"}。
    """
    if os.environ.get("FACTORLAB_LOCKBOX", "1").strip().lower() in (
            "0", "off", "false"):
        return None
    from factorlab.adapters import lockbox_store as store
    from factorlab.core import lockbox as lb

    if spec_doc is None or spec_path is None:
        raise lb.LockboxError(
            "LOCKBOX_FINAL_REQUIRED",
            f"参考库成员缺 spec（{command}）：无法执行最终测试——补齐 spec 后重试"
            "（入库必须有测试段最终测试结果）")
    name = str(spec_doc.get("name") or Path(spec_path).stem)
    if base is None:
        from factorlab.app.analysis.reference import reference_names
        base = [b for b in reference_names(scales) if b != name]
    base = list(base)
    # T8 日历单点：adapters.run_calendar（缺省 DATA_ROOT/health，空日历回退 today）
    days, data_end = store.run_calendar()
    date_doc = spec_doc.get("date") or {}
    start = (datetime.date.fromisoformat(str(date_doc["start"]))
             if date_doc.get("start")
             else (min(days) if days else datetime.date(1970, 1, 1)))
    end = (datetime.date.fromisoformat(str(date_doc["end"]))
           if date_doc.get("end") else data_end)
    panel_start, panel_end = min(start, end), max(start, end)
    out_dir = Path(settings.results_dir) / f"{name}_5y"
    frozen_path = out_dir / "test_diagnostics.json"
    conn = store.connect(settings.lockbox_db)
    try:
        no_state = False
        try:
            window = store.current_window(conn, as_of=datetime.date.today(),
                                          trading_days=days, data_end=data_end)
        except lb.LockboxError as exc:
            if exc.code != "LOCKBOX_NO_STATE":
                raise
            no_state = True
            window = lb.compute_window(as_of=datetime.date.today(),
                                       trading_days=days, data_end=data_end)
        role = lb.role_for(panel_start, panel_end, window)
        if role == "is":
            raise lb.LockboxError(
                "LOCKBOX_TEST_ONLY_FINAL",
                f"因子 {name} 窗口 [{panel_start}~{panel_end}] 全在训练段：入库只看"
                "测试段最终测试结果——把 spec 窗口延伸到测试段"
                f"（window_start={window.start}）后再试")
        if no_state:
            raise lb.LockboxError(
                "LOCKBOX_NO_STATE",
                f"评估窗口 [{panel_start}~{panel_end}] 与锁箱（{window.window_id}，"
                f"起点 {window.start}）相交但锁箱未初始化：先 `factorlab lockbox roll`")
        fp = store.final_version_fingerprint(spec_doc=spec_doc,
                                             window_id=window.window_id)
        diagnostic_frequency, diagnostic_fwd_col = _diagnostic_options(spec_doc)
        try:
            access_id: str | None = store.require_final(
                conn, window_id=window.window_id, fingerprint=fp)
        except lb.LockboxError as exc:
            if exc.code != "LOCKBOX_FINAL_REQUIRED":
                raise
            access_id = None
        executed = False
        if access_id is None:
            # 预检先于开跑：缺基座产物 → 零登记零跑（防烧掉唯一一次最终测试）
            _preflight_base_products(base=base,
                                     results_dir=Path(settings.results_dir))
            _execute_final_test(spec_path, output_dir=out_dir, wait=wait,
                                reason=reason)
            access_id = store.require_final(conn, window_id=window.window_id,
                                            fingerprint=fp)
            diag, frozen_path = _compute_test_diagnostics(
                name=name, base=base, window=window, fingerprint=fp,
                spec_doc=spec_doc, frequency=diagnostic_frequency,
                fwd_col=diagnostic_fwd_col)
            executed = True
        elif frozen_path.is_file():
            diag = json.loads(frozen_path.read_text(encoding="utf-8"))
            if (diag.get("version_fingerprint") != fp
                    or diag.get("window_id") != window.window_id):
                raise lb.LockboxError(
                    "LOCKBOX_FINAL_REQUIRED",
                    f"冻结件 {frozen_path} 与登记（版本 {fp[:12]}… / 窗口 "
                    f"{window.window_id}）不符：先经 `make xpipe`（pipeline final）"
                    "重建最终测试与冻结件")
            if (diag.get("diagnostics_schema") != _TEST_DIAGNOSTICS_SCHEMA
                    or diag.get("frequency") != diagnostic_frequency
                    or diag.get("fwd_col") != diagnostic_fwd_col
                    or "retention" not in diag):
                # Recompute diagnostics from the existing final panel when the
                # metric schema or cadence changed; never rerun/re-register final.
                _preflight_base_products(
                    base=base, results_dir=Path(settings.results_dir))
                diag, frozen_path = _compute_test_diagnostics(
                    name=name, base=base, window=window, fingerprint=fp,
                    spec_doc=spec_doc, frequency=diagnostic_frequency,
                    fwd_col=diagnostic_fwd_col)
        elif (out_dir / "panel.parquet").is_file():
            # 已有 final、冻结件缺失但 `_5y` 产物在：同一次测试收尾，仅重算诊断
            _preflight_base_products(base=base,
                                     results_dir=Path(settings.results_dir))
            diag, frozen_path = _compute_test_diagnostics(
                name=name, base=base, window=window, fingerprint=fp,
                spec_doc=spec_doc, frequency=diagnostic_frequency,
                fwd_col=diagnostic_fwd_col)
        else:
            raise lb.LockboxError(
                "LOCKBOX_FINAL_REQUIRED",
                f"窗口 {window.window_id} 已有最终测试登记（版本 {fp[:12]}…）但冻结件"
                f"（{frozen_path}）与 `_5y` 产物均缺失：先经 `make xpipe`（pipeline "
                "final）重建最终测试与冻结件")
        return {"version_fingerprint": fp, "window_id": window.window_id,
                "window_start": window.start.isoformat(), "access_id": access_id,
                "executed": executed, "base": base, "reason": reason,
                "diagnostics": diag, "path": str(frozen_path)}
    finally:
        conn.close()


def factor_admit(args: Any) -> envelope.Envelope:
    """一键入库检验：lint → 最终测试门 → 判决吃测试段冻结件数。

    R42：入库只看测试段结果——无 final 登记时门内执行最终测试（测试段车道）
    并写 `test_diagnostics.json`；已有 final 只读冻结件（版本不符/缺失即拒）。
    判决输入为冻结件 `corr_max/r2_lib/resic_t/retention`，不再现算全窗。
    """
    from factorlab.adapters import results_fs
    from factorlab.app.analysis.reference import reference_names
    from factorlab.core.lockbox import LockboxError
    from factorlab.core.spec import load_spec

    _ensure()
    user_spec_path = Path(args.spec_path)
    results, failures = _lint_paths([user_spec_path])
    if failures:
        return envelope.fail(
            "factor.admit", "LINT",
            f"{user_spec_path}: {failures[0][1]}",
            hint="先 `flab factor lint <spec>` 修 spec")
    name = results[0]["name"]
    # 规范候选 spec 解析（R42 修复轮1）：变体优先——指纹/最终测试身份与 ref-sync 同源；
    # 用户显式传入的 spec 仅作 fallback，替换时在输出注明（spec_used/spec_note）。
    spec_path = resolve_candidate_spec(name)
    spec_note: str | None = None
    if spec_path is not None and spec_path.resolve() != user_spec_path.resolve():
        spec_note = f"使用规范 5y 变体 spec：{spec_path}"
    if spec_path is None:
        spec_path = user_spec_path
    try:
        spec = load_spec(spec_path)
        if spec.name != name:  # 规范件与 lint 名不符（手改变体）→ 回退用户 spec
            raise ValueError(f"规范 spec 名 {spec.name!r} != {name!r}")
        # M1：规范解析选中 5y 变体时，检查源 spec 是否已更新（name/date 之外的实质
        # 内容漂移）→ spec_note 追加警告，不阻断（身份/冻结件仍以变体为准）。
        variant_path = (Path(settings.research_root)
                        / "experiments" / "r37_5y" / f"{name}_5y.yaml")
        if spec_path.resolve() == variant_path.resolve():
            source_path = _source_spec_path(name)
            source_doc = None
            if (source_path is not None
                    and source_path.resolve() != spec_path.resolve()):
                try:
                    source_doc = load_spec(source_path).model_dump(mode="json")
                except (OSError, ValueError, yaml.YAMLError):
                    source_doc = None
            if source_doc is not None and (
                    _drift_fingerprint(source_doc)
                    != _drift_fingerprint(spec.model_dump(mode="json"))):
                if spec_note is None:
                    spec_note = f"使用规范 5y 变体 spec：{spec_path}"
                spec_note += "；变体与源 spec 内容不一致（源已更新？）"
    except (OSError, ValueError, yaml.YAMLError):
        spec_path, spec_note = user_spec_path, None
        spec = load_spec(user_spec_path)
    scales = getattr(args, "scales", None)
    if scales is None:
        scales = "minute" if spec.interface == "bars_1m" else "daily"
    try:
        base = [b for b in reference_names(scales) if b != name]
    except FileNotFoundError as exc:
        return envelope.fail("factor.admit", "NOT_FOUND", f"参考库不可用（{exc}）",
                             hint="检查 `$QUANTRESEARCH_ROOT/factor/_reference.yaml` 或 FACTORLAB_REFERENCE")
    except ValueError as exc:
        return envelope.fail("factor.admit", "DATA", f"参考库非法: {exc}")
    if not base:
        return envelope.fail("factor.admit", "DATA",
                             f"参考库 {scales} 组为空（或仅含候选自身）——无可对照成员",
                             hint="先 `flab factor ref add` 入库种子因子")
    try:
        gate = _final_test_gate(
            spec.model_dump(mode="json"), spec_path,
            reason=f"admit final test: {name}", command="factor admit",
            scales=scales, base=base, wait=bool(getattr(args, "wait", False)))
    except LockboxError as exc:
        return envelope.fail("factor.admit", exc.code, exc.message,
                             hint=_lockbox_hint(exc))
    except FinalTestError as exc:
        return envelope.fail("factor.admit", exc.code, exc.message,
                             hint=exc.hint, log=exc.log)
    except FileNotFoundError as exc:
        return envelope.fail("factor.admit", "NOT_FOUND", str(exc), hint=_FACTOR_HINT)
    except ValueError as exc:
        return envelope.fail("factor.admit", "DATA", str(exc))

    results_dir = Path(settings.results_dir)
    artifacts: dict[str, str] = {}
    if gate is None:
        # 纪律未启用（FACTORLAB_LOCKBOX=off，CI/测试兜底）：缺产物则 run，
        # 全窗诊断（无测试段冻结件；判决不可作正式入库依据）。
        from factorlab.app.analysis.cross_section import incremental_diagnostics
        diagnostic_frequency, diagnostic_fwd_col = _diagnostic_options(
            spec.model_dump(mode="json"))
        summary_path = results_fs.summary_path(results_dir, name)
        panel_path = results_fs.panel_path(results_dir, name)
        ran = False
        if not (summary_path.is_file() and panel_path.is_file()):
            run_env = factor_run(_run_args(
                spec_path, wait=bool(getattr(args, "wait", False))))
            if not run_env.ok:
                err = run_env.error or {}
                return envelope.fail("factor.admit", err.get("code", "RUN_FAILED"),
                                     err.get("message", "候选 run 失败"),
                                     hint=err.get("hint"), log=err.get("log"))
            ran = True
        try:
            r = incremental_diagnostics(
                [name], results_dir, base=base,
                frequency=diagnostic_frequency, fwd_col=diagnostic_fwd_col)
        except FileNotFoundError as exc:
            return envelope.fail("factor.admit", "NOT_FOUND", str(exc),
                                 hint=_FACTOR_HINT)
        except ValueError as exc:
            return envelope.fail("factor.admit", "DATA", str(exc))
        cand = r["candidates"][0]
        verdict = _admit_verdict(
            cand["corr_max"], cand["r2_lib"], cand["resic_t"],
            cand["retention"])
        data = {
            "name": name, "scales": scales, "ran": ran, "base": base,
            "corr_max": cand["corr_max"], "r2_lib": cand["r2_lib"],
            "retention": cand["retention"],
            "diagnostic_frequency": diagnostic_frequency,
            "diagnostic_fwd_col": diagnostic_fwd_col,
            "resic": {"mean": cand["resic_mean"], "t": cand["resic_t"],
                      "n_weeks": cand["n_weeks"]},
            "d10_verdict": cand["verdict"],
            "test_diagnostics": None,
            "spec_used": str(spec_path), "spec_note": spec_note,
            "verdict": verdict,
            "建议": _ADVICE[verdict],
        }
        artifacts["summary"] = str(summary_path)
    else:
        d = gate["diagnostics"]
        verdict = _admit_verdict(
            d["corr_max"], d["r2_lib"], d["resic_t"], d.get("retention"))
        data = {
            "name": name, "scales": scales, "ran": gate["executed"], "base": base,
            "corr_max": d["corr_max"], "r2_lib": d["r2_lib"],
            "retention": d.get("retention"),
            "resic": {"mean": d["resic_mean"], "t": d["resic_t"],
                      "n_weeks": d["n_weeks"]},
            "test_diagnostics": gate["path"],
            "spec_used": str(spec_path), "spec_note": spec_note,
            "verdict": verdict,
            "建议": _ADVICE[verdict],
        }
        artifacts["test_diagnostics"] = gate["path"]
        artifacts["run_dir"] = str(Path(gate["path"]).parent)
    return envelope.ok("factor.admit", _jsonify(data), artifacts=artifacts)


def _run_args(spec_path: Path, **over: Any) -> argparse.Namespace:
    base = dict(spec_path=spec_path, universe=None, max_memory="4GB", output_dir=None,
                no_backtest=False, groups=10, set=None, chunk_days=None,
                warmup_days=None, eval_frequency=None, wait=False,
                chunk_workers=None, no_float32=False, pretty=False,
                profile=None, no_read_cache=None,
                accept_quality=None, override_reason=None)
    base.update(over)
    return argparse.Namespace(**base)


# ================================================================
# ref list / add / remove（安全写：备份/校验/原子/注释保留）
# ================================================================

def _reference_path() -> Path:
    from factorlab.app.analysis.reference import default_reference_path
    return default_reference_path()


def factor_ref_list(args: Any) -> envelope.Envelope:
    """参考库成员清单（可 --scales daily|minute）。"""
    from factorlab.app.analysis.reference import load_reference
    try:
        ref = load_reference()
    except FileNotFoundError as exc:
        return envelope.fail("factor.ref.list", "NOT_FOUND", f"参考库不存在: {exc}",
                             hint="检查 `$QUANTRESEARCH_ROOT/factor/_reference.yaml`")
    except ValueError as exc:
        return envelope.fail("factor.ref.list", "DATA", f"参考库非法: {exc}")
    groups = list(ref)
    if getattr(args, "scales", None):
        if args.scales not in ref:
            return envelope.fail("factor.ref.list", "USAGE",
                                 f"未知 scales {args.scales}（支持 {list(ref)}）")
        groups = [args.scales]
    return envelope.ok("factor.ref.list", {
        "path": str(_reference_path()),
        "scales": {s: [dataclasses.asdict(e) for e in ref[s]] for s in groups},
    })


def _quote_scalar(value: Any) -> str:
    """YAML 单引号标量（转义单引号）；数字/None 原样。"""
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _render_reference_entry(entry: dict) -> list[str]:
    lines = [
        f"    - name: {_quote_scalar(entry['name'])}\n",
        f"      style: {_quote_scalar(entry['style'])}\n",
        f"      reason: {_quote_scalar(entry['reason'])}\n",
        f"      added: {_quote_scalar(entry['added'])}\n",
        f"      entry_corr_max: {_quote_scalar(entry.get('entry_corr_max'))}\n",
        f"      entry_resic_t: {_quote_scalar(entry.get('entry_resic_t'))}\n",
    ]
    return lines


def _insert_reference_entry(text: str, scales: str, entry: dict) -> str:
    """在 `  <scales>:` 组尾部插入条目（文本级——保留既有注释/顺序）。"""
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.rstrip("\n") == f"  {scales}:":
            start = i + 1
            break
    if start is None:
        raise ValueError(f"参考库缺 scales 组: {scales}")
    end = len(lines)
    for j in range(start, len(lines)):
        stripped = lines[j]
        if re.match(r"^  \S.*:\s*$", stripped) and not stripped.startswith("    "):
            end = j
            break
        if stripped and not stripped[0].isspace() and not stripped.startswith("#"):
            end = j
            break
    if end > 0 and not lines[end - 1].endswith("\n"):
        lines[end - 1] = lines[end - 1] + "\n"
    lines[end:end] = _render_reference_entry(entry)
    return "".join(lines)


def _remove_reference_entry(text: str, name: str) -> str | None:
    """文本级删除条目（含续行）；找不到 → None。"""
    lines = text.splitlines(keepends=True)
    pattern = re.compile(rf"^    - name:\s*['\"]?{re.escape(name)}['\"]?\s*$")
    start = next((i for i, ln in enumerate(lines)
                  if pattern.match(ln.rstrip("\n"))), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and lines[end].startswith("      "):
        end += 1
    return "".join(lines[:start] + lines[end:])


def _validate_reference_text(text: str, path: Path) -> None:
    """写前校验：渲染结果必须能被 load_reference 读回。"""
    from factorlab.app.analysis.reference import load_reference
    import tempfile
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".yaml",
                                    dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(text, encoding="utf-8")
        load_reference(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def factor_ref_add(args: Any) -> envelope.Envelope:
    """参考库入库：校验 scales/重名 → 备份 → 原子写 → 读回校验。"""
    from factorlab.app.analysis.reference import REFERENCE_SCALES, load_reference
    path = _reference_path()
    if not path.is_file():
        return envelope.fail("factor.ref.add", "NOT_FOUND", f"参考库不存在: {path}",
                             hint="先建 `$QUANTRESEARCH_ROOT/factor/_reference.yaml`（scales: daily/minute）")
    scales = getattr(args, "scales", None) or "daily"
    if scales not in REFERENCE_SCALES:
        return envelope.fail("factor.ref.add", "LINT",
                             f"未知 scales {scales}（只允许 {list(REFERENCE_SCALES)}）")
    try:
        ref = load_reference(path)
    except ValueError as exc:
        return envelope.fail("factor.ref.add", "DATA", f"参考库非法，先修复: {exc}")
    existing = {e.name for entries in ref.values() for e in entries}
    name = args.name
    if name in existing:
        return envelope.fail("factor.ref.add", "LINT",
                             f"参考库已存在: {name}（近亲/重复登记会污染对照集）",
                             hint=f"如需替换先 `flab factor ref remove {name}`")
    from factorlab.app.analysis.reference import reference_names
    from factorlab.core.lockbox import LockboxError
    from factorlab.core.spec import load_spec

    # 规范候选 spec 解析单点（变体优先；与 admit/ref-sync 同身份）
    spec_path = resolve_candidate_spec(name)
    gate_spec: Any = None
    if spec_path is not None:
        try:
            gate_spec = load_spec(spec_path)
        except (OSError, ValueError, yaml.YAMLError):
            gate_spec = None
    try:
        base = [b for b in reference_names(scales) if b != name]
    except FileNotFoundError as exc:
        return envelope.fail("factor.ref.add", "NOT_FOUND", f"参考库不可用（{exc}）",
                             hint=_FACTOR_HINT)
    except ValueError as exc:
        return envelope.fail("factor.ref.add", "DATA", f"参考库非法: {exc}")
    try:
        gate = None if not base else _final_test_gate(
            (gate_spec.model_dump(mode="json") if gate_spec is not None else None),
            (spec_path if gate_spec is not None else None),
            reason=f"ref add final test: {name}", command="factor ref add",
            scales=scales, base=base)
    except LockboxError as exc:
        return envelope.fail("factor.ref.add", exc.code, exc.message,
                             hint=_lockbox_hint(exc, spec_missing=gate_spec is None))
    except FinalTestError as exc:
        return envelope.fail("factor.ref.add", exc.code, exc.message,
                             hint=exc.hint, log=exc.log)
    except FileNotFoundError as exc:
        return envelope.fail("factor.ref.add", "NOT_FOUND", str(exc), hint=_FACTOR_HINT)
    except ValueError as exc:
        return envelope.fail("factor.ref.add", "DATA", str(exc))
    test_diagnostics: str | None = None
    if gate is None:
        # Lockbox-off (primarily tests/CI) still uses measured diagnostics for the
        # same admission floor. Manual --entry-* values cannot bypass this gate.
        from factorlab.app.analysis.cross_section import incremental_diagnostics
        if not base:
            # An empty scale is initialized by a seed. There is no comparison
            # set yet, so the operator-supplied entry fields remain nullable.
            diagnostics = {
                "corr_max": getattr(args, "entry_corr_max", None),
                "r2_lib": None,
                "resic_t": getattr(args, "entry_resic_t", None),
                "retention": None,
            }
            verdict = _VERDICT_SEED
        else:
            diagnostic_frequency, diagnostic_fwd_col = _diagnostic_options(
                gate_spec.model_dump(mode="json") if gate_spec is not None else None)
            try:
                result = incremental_diagnostics(
                    [name], settings.results_dir, base=base,
                    frequency=diagnostic_frequency, fwd_col=diagnostic_fwd_col)
            except FileNotFoundError as exc:
                return envelope.fail("factor.ref.add", "NOT_FOUND", str(exc),
                                     hint=_FACTOR_HINT)
            except ValueError as exc:
                return envelope.fail("factor.ref.add", "DATA", str(exc))
            diagnostics = result["candidates"][0]
            verdict = _admit_verdict(
                diagnostics["corr_max"], diagnostics["r2_lib"],
                diagnostics["resic_t"], diagnostics.get("retention"))
    else:
        # R42：entry 字段语义 = 测试段冻结件（不看训练段/全窗）
        diagnostics = gate["diagnostics"]
        test_diagnostics = gate["path"]
        verdict = _admit_verdict(
            diagnostics["corr_max"], diagnostics["r2_lib"],
            diagnostics["resic_t"], diagnostics.get("retention"))
    if verdict != _VERDICT_JOIN:
        resic_t = diagnostics.get("resic_t")
        shown_t = abs(resic_t) if _finite(resic_t) else "不可用"
        corr_max = diagnostics.get("corr_max")
        retention = diagnostics.get("retention")
        return envelope.fail(
            "factor.ref.add", "DATA",
            f"候选 {name} 未达到参考库准入门：verdict={verdict}，"
            f"|resIC t|={shown_t}（要求 ≥3），corr_max={corr_max}（要求 <0.7），"
            f"retention={retention}（要求 ≥0.5）",
            hint="先运行 `flab factor admit <spec>` 查看完整判决；满足准入门后再添加")
    entry_corr_max = (float(diagnostics["corr_max"])
                      if _finite(diagnostics["corr_max"]) else None)
    entry_resic_t = (float(diagnostics["resic_t"])
                     if _finite(diagnostics["resic_t"]) else None)
    entry = {
        "name": name, "style": args.style, "reason": args.reason,
        "added": getattr(args, "added", None) or datetime.date.today().isoformat(),
        "entry_corr_max": entry_corr_max,
        "entry_resic_t": entry_resic_t,
    }
    try:
        new_text = _insert_reference_entry(path.read_text(encoding="utf-8"), scales, entry)
        _validate_reference_text(new_text, path)
    except (ValueError, yaml.YAMLError) as exc:
        return envelope.fail("factor.ref.add", "LINT", f"写入前校验失败: {exc}",
                             hint="参考库结构异常；先用 `flab factor ref list` 检查")
    backup = path.with_name(path.name + ".bak")
    shutil.copy2(path, backup)
    atomic_write_text(path, new_text)
    artifacts = {"reference": str(path), "backup": str(backup)}
    if test_diagnostics is not None:
        artifacts["test_diagnostics"] = test_diagnostics
    return envelope.ok("factor.ref.add",
                       {"name": name, "scales": scales, "path": str(path),
                        "backup": str(backup), "entry": _jsonify(entry),
                        "spec": str(spec_path) if spec_path else None,
                        "test_diagnostics": test_diagnostics,
                        "verdict": verdict},
                       artifacts=artifacts)


def factor_ref_remove(args: Any) -> envelope.Envelope:
    """参考库移除：文本级删除（保留注释）→ 备份 → 原子写 → 读回校验。"""
    from factorlab.app.analysis.reference import load_reference
    path = _reference_path()
    if not path.is_file():
        return envelope.fail("factor.ref.remove", "NOT_FOUND", f"参考库不存在: {path}")
    try:
        ref = load_reference(path)
    except ValueError as exc:
        return envelope.fail("factor.ref.remove", "DATA", f"参考库非法，先修复: {exc}")
    name = args.name
    if not any(e.name == name for entries in ref.values() for e in entries):
        return envelope.fail("factor.ref.remove", "NOT_FOUND",
                             f"参考库无此成员: {name}",
                             hint="`flab factor ref list` 查看成员")
    try:
        new_text = _remove_reference_entry(path.read_text(encoding="utf-8"), name)
        if new_text is None:
            return envelope.fail("factor.ref.remove", "NOT_FOUND",
                                 f"参考库文本中未找到: {name}")
        _validate_reference_text(new_text, path)
    except (ValueError, yaml.YAMLError) as exc:
        return envelope.fail("factor.ref.remove", "LINT", f"删除后校验失败: {exc}")
    backup = path.with_name(path.name + ".bak")
    shutil.copy2(path, backup)
    atomic_write_text(path, new_text)
    return envelope.ok("factor.ref.remove",
                       {"name": name, "path": str(path), "backup": str(backup)},
                       artifacts={"reference": str(path), "backup": str(backup)})


# ================================================================
# op / catalog
# ================================================================

def _plugins_and_ops():
    from factorlab.adapters import plugins
    from factorlab.core.ops import registry as op_registry
    _ensure()
    plugins.discover_plugins(settings.plugin_dir)
    return plugins, op_registry


def factor_op_list(args: Any) -> envelope.Envelope:
    """算子清单（缺省注册面；--catalog 分类表全集含未注册库函数）。"""
    _plugins, op_registry = _plugins_and_ops()
    if getattr(args, "catalog", False):
        from factorlab.core.ops.registration import effective_catalog
        metas = sorted(effective_catalog().all(), key=lambda m: m.name)
        rows = [{"name": m.name, "partition": m.partition, "window": m.window,
                 "source": m.source, "returns": m.returns} for m in metas]
        return envelope.ok("factor.op.list", _jsonify({"n": len(rows), "ops": rows}))
    rows = [{"name": op.name, "kind": op.kind, "version": op.version}
            for op in op_registry.list_ops()]
    return envelope.ok("factor.op.list", _jsonify({"n": len(rows), "ops": rows}))


def factor_op_doc(args: Any) -> envelope.Envelope:
    """算子文档（注册面 docstring；未注册但分类表有条目 → 元数据回退）。"""
    from factorlab.core.ops.registration import effective_catalog
    _plugins, op_registry = _plugins_and_ops()
    name = args.name
    try:
        op = op_registry.get_op(name)
    except KeyError:
        meta = effective_catalog().get(name)
        if meta is None:
            return envelope.fail("factor.op.doc", "NOT_FOUND", f"未知算子: {name}",
                                 hint="`flab factor op list --catalog` 查看全集")
        return envelope.ok("factor.op.doc", {
            "name": meta.name, "partition": meta.partition, "window": meta.window,
            "source": meta.source, "returns": meta.returns,
            "mask_args": list(meta.mask_args), "registered": False, "doc": None})
    return envelope.ok("factor.op.doc", {
        "name": op.name, "kind": op.kind, "version": op.version,
        "doc": op.doc or "", "registered": True})


def factor_op_add(args: Any) -> envelope.Envelope:
    """注册用户算子插件（.py；冲突/非法 → LINT）。"""
    plugins, _op_registry = _plugins_and_ops()
    try:
        names = plugins.add_plugin(Path(args.path), plugin_dir=settings.plugin_dir,
                                   force=bool(getattr(args, "force", False)))
    except (ValueError, FileNotFoundError) as exc:
        return envelope.fail("factor.op.add", "LINT", f"{type(exc).__name__}: {exc}",
                             hint="检查插件命名（ts_/cs_ 前缀）与冲突；--force 覆盖")
    return envelope.ok("factor.op.add", {"registered": names},
                       artifacts={"plugin_dir": str(settings.plugin_dir)})


def factor_op_remove(args: Any) -> envelope.Envelope:
    """禁用用户算子插件（未找到 → NOT_FOUND）。"""
    plugins, _op_registry = _plugins_and_ops()
    try:
        plugins.remove_plugin(args.name, plugin_dir=settings.plugin_dir)
    except KeyError as exc:
        return envelope.fail("factor.op.remove", "NOT_FOUND", str(exc),
                             hint="`flab factor op list` 查看注册面")
    return envelope.ok("factor.op.remove", {"disabled": args.name})


def factor_catalog(args: Any) -> envelope.Envelope:
    """列/算子活文档（json | markdown；--out 写文件）。"""
    from factorlab.adapters.catalog import catalog_json, render_catalog_markdown
    _ensure()
    fmt = str(getattr(args, "format", None) or "json").lower()
    if fmt == "json":
        data: dict[str, Any] = {"catalog": json.loads(catalog_json())}
    elif fmt == "markdown":
        data = {"markdown": render_catalog_markdown()}
    else:
        return envelope.fail("factor.catalog", "USAGE", f"未知格式 {fmt}（json|markdown）")
    artifacts: dict[str, str] = {}
    out = getattr(args, "out", None)
    if out is not None:
        payload = json.dumps(data["catalog"], ensure_ascii=False, indent=2) \
            if fmt == "json" else data["markdown"]
        atomic_write_text(Path(out), payload)
        data["path"] = str(out)
        artifacts["catalog"] = str(out)
    return envelope.ok("factor.catalog", data, artifacts=artifacts)


# ================================================================
# 注册（registry 单点；describe 自动可见）
# ================================================================

def _reg_all() -> None:
    _register(
        "factor.lint", handler=factor_lint,
        params=(registry.ParamSpec("spec_paths", kind="list[str]", positional=True,
                                   help="一个或多个因子 spec YAML 路径"),
                registry.ParamSpec("all", kind="bool", help="扫描 $QUANTRESEARCH_ROOT/factor/**/*.yaml 全库批跑"),
                registry.ParamSpec("strategy", kind="bool", help="强制按策略文档校验"),
                _JSON, _PRETTY),
        defaults={"spec_paths": [], "all": False, "strategy": False},
        description="静态校验（秒级，不连库；错误码 LINT）",
        examples=("flab factor lint $QUANTRESEARCH_ROOT/factor/momentum_20d/turnrank_top2.yaml",
                  "flab factor lint --all"),
        output_schema={"type": "object", "properties": {
            "n_pass": {"type": "integer"},
            "results": {"type": "array", "items": {"type": "object"}}}},
    )
    _register(
        "factor.run", handler=factor_run, params=(*_RUN_PARAMS, _JSON, _PRETTY),
        defaults={"universe": None, "max_memory": "4GB", "output_dir": None,
                  "no_backtest": False, "groups": 10, "set": None,
                  "chunk_days": None, "warmup_days": None, "eval_frequency": None,
                  "wait": False, "chunk_workers": None, "no_float32": False,
                  "profile": False, "no_read_cache": False,
                  "accept_quality": None, "override_reason": None},
        description="计算+评估+分层回测（过 heavy 闸；返回 IC/十分位/换手/覆盖/ic_decay）",
        examples=("flab factor run $QUANTRESEARCH_ROOT/factor/momentum_20d/turnrank_top2.yaml",
                  "flab factor run <spec> --no-backtest --wait"),
        output_schema={"type": "object", "properties": {
            "name": {"type": "string"},
            "evaluation": {"type": "object", "properties": {
                "version": {"type": "integer"}, "frequency": {"type": "string"},
                "ic": {"type": "object"}, "decile_spread": {"type": "object"},
                "turnover": {"type": "object"}, "coverage": {"type": "object"},
                "ic_decay": {"type": "array"}}}}},
    )
    _register(
        "factor.list", handler=factor_list,
        params=(_JSON, _PRETTY),
        description="已保存因子列表（results_dir 摘要，新运行在前）",
        examples=("flab factor list --json",),
        output_schema={"type": "object", "properties": {
            "n": {"type": "integer"}, "factors": {"type": "array"}}},
    )
    _register(
        "factor.show", handler=factor_show,
        params=(registry.ParamSpec("name", kind="str", positional=True, required=True,
                                   help="因子名（results/<name>）"), _JSON, _PRETTY),
        defaults={"name": None},
        description="单因子完整 summary.json（缺失 → NOT_FOUND）",
        examples=("flab factor show momentum_20d_turnrank_top2 --json",),
        output_schema={"type": "object", "properties": {
            "name": {"type": "string"}, "summary": {"type": "object"}}},
    )
    _register(
        "factor.export", handler=factor_export,
        params=(registry.ParamSpec("name", kind="str", positional=True, required=True,
                                   help="因子名"),
                registry.ParamSpec("format", kind="str", help="parquet|csv|json（缺省 parquet）"),
                registry.ParamSpec("out", kind="path", help="输出路径覆盖"),
                _JSON, _PRETTY),
        defaults={"format": "parquet", "out": None},
        description="产物导出：panel.parquet → parquet|csv|json",
        examples=("flab factor export momentum_20d_turnrank_top2 --format csv",),
        output_schema={"type": "object", "properties": {
            "path": {"type": "string"}, "n_rows": {"type": "integer"},
            "schema": {"type": "object"}, "head": {"type": "array"}}},
    )
    _register(
        "factor.corr", handler=factor_corr,
        params=(registry.ParamSpec("names", kind="list[str]", positional=True,
                                   help="因子名（可空——配合 --against reference）"),
                registry.ParamSpec("against", kind="str",
                                   help="对照集 reference|all|逗号名单"),
                *_FRAME_PARAMS, _JSON, _PRETTY),
        defaults={"names": [], "against": None, "out": None, "limit": None,
                  "inline": False},
        description="因子两两相关性（周度横截面秩相关 + 全局 Pearson）",
        examples=("flab factor corr a b c", "flab factor corr a --against reference"),
        output_schema={"type": "object", "properties": {
            "n_rows": {"type": "integer"}, "rows": {"type": "array"},
            "path": {"type": "string"}}},
    )
    _register(
        "factor.resic", handler=factor_resic,
        params=(registry.ParamSpec("names", kind="list[str]", positional=True,
                                   help="候选/组内因子名"),
                registry.ParamSpec("target", kind="str", help="目标因子（target 模式）"),
                registry.ParamSpec("min_stocks", kind="int", help="每周最少股票数（缺省 30）"),
                registry.ParamSpec("against", kind="str",
                                   help="D10 增量信息模式 reference|all|逗号名单"),
                registry.ParamSpec("frequency", kind="str",
                                   help="daily|weekly；不传新参数沿用旧 weekly/5d，显式 daily 默认 1d"),
                registry.ParamSpec("horizon", kind="int",
                                   help="标签 horizon 正整数，映射 forward_return_<N>d；缺省 5"),
                registry.ParamSpec("fwd_col", kind="str",
                                   help="显式标签列名；提供时优先于 --horizon"),
                _JSON, _PRETTY),
        defaults={"names": [], "target": None, "min_stocks": None, "against": None,
                  "frequency": None, "horizon": None, "fwd_col": None},
        description="增量信息/正交化残差 IC（resIC/r2_lib/retention/verdict）；"
                    "不传新参数兼容旧 weekly/5d，显式 daily 默认 1d；horizon/fwd-col 可选",
        examples=("flab factor resic cand --against reference",
                  "flab factor resic cand --against reference --frequency daily",
                  "flab factor resic cand --against reference --frequency daily --horizon 20",
                  "flab factor resic cand --against reference --fwd-col forward_return_1d",
                  "flab factor resic a b c"),
        output_schema={"type": "object", "properties": {
            "mode": {"type": "string"}, "frequency": {"type": "string"},
            "fwd_col": {"type": "string"}, "base": {"type": "array"},
            "candidates": {"type": "array"}, "factors": {"type": "array"}}},
    )
    _register(
        "factor.svd", handler=factor_svd,
        params=(registry.ParamSpec("names", kind="list[str]", positional=True,
                                   help="因子名（缺省=参考库 daily 组）"),
                registry.ParamSpec("weeks", kind="int", help="抽样交易周数（缺省 15）"),
                registry.ParamSpec("all", kind="bool", help="显式扫全库（缺省参考库）"),
                _JSON, _PRETTY),
        defaults={"names": [], "weeks": 15, "all": False},
        description="SVD 结构分析：奇异值谱 + 主成分载荷",
        examples=("flab factor svd", "flab factor svd --all"),
        output_schema={"type": "object", "properties": {
            "source": {"type": "string"}, "n_factors": {"type": "integer"},
            "singular_values": {"type": "array"}, "cum_explained": {"type": "array"},
            "loadings": {"type": "array"}}},
    )
    _register(
        "factor.ref.list", handler=factor_ref_list,
        params=(registry.ParamSpec("scales", kind="str", help="只看 daily|minute"),
                _JSON, _PRETTY),
        defaults={"scales": None},
        description="参考库成员清单（D10；读 $QUANTRESEARCH_ROOT/factor/_reference.yaml）",
        examples=("flab factor ref list", "flab factor ref list --scales minute"),
        output_schema={"type": "object", "properties": {
            "path": {"type": "string"}, "scales": {"type": "object"}}},
    )
    _register(
        "factor.ref.add", handler=factor_ref_add,
        params=(registry.ParamSpec("name", kind="str", positional=True, required=True,
                                   help="成员因子名"),
                registry.ParamSpec("scales", kind="str", help="daily|minute（缺省 daily）"),
                registry.ParamSpec("style", kind="str", required=True, help="风格标签"),
                registry.ParamSpec("reason", kind="str", required=True, help="入库理由"),
                registry.ParamSpec("added", kind="str", help="加入日期（缺省今天）"),
                registry.ParamSpec("entry_corr_max", kind="float",
                                   help="兼容旧参数；准入及 entry 值始终由实际诊断计算"),
                registry.ParamSpec("entry_resic_t", kind="float",
                                   help="兼容旧参数；不能覆盖实际诊断或绕过准入门"),
                _JSON, _PRETTY),
        defaults={"scales": "daily", "added": None, "entry_corr_max": None,
                  "entry_resic_t": None},
        description=("参考库入库（最终测试门 + D10 增量判决：|resIC t|≥3、"
                     "corr_max<0.7、retention≥0.5；entry 值取权威诊断）"),
        examples=("flab factor ref add my_factor --style 量价 --reason '独立增量'",),
        output_schema={"type": "object", "properties": {
            "name": {"type": "string"}, "scales": {"type": "string"},
            "path": {"type": "string"}, "backup": {"type": "string"},
            "spec": {"type": ["string", "null"]},
            "test_diagnostics": {"type": ["string", "null"]},
            "verdict": {"type": "string", "enum": ["可加入"]}}},
    )
    _register(
        "factor.ref.remove", handler=factor_ref_remove,
        params=(registry.ParamSpec("name", kind="str", positional=True, required=True,
                                   help="成员因子名"), _JSON, _PRETTY),
        defaults={"name": None},
        description="参考库移除（文本级——保留注释；备份/原子写；未找到 → NOT_FOUND）",
        examples=("flab factor ref remove my_factor",),
        output_schema={"type": "object", "properties": {
            "name": {"type": "string"}, "path": {"type": "string"},
            "backup": {"type": "string"}}},
    )
    _register(
        "factor.admit", handler=factor_admit,
        params=(registry.ParamSpec("spec_path", kind="path", positional=True,
                                   required=True, help="候选因子 spec YAML"),
                registry.ParamSpec(
                    "scales", kind="str",
                    help="未传时按 spec interface 自动：bars_1m→minute，其余/缺省→daily；"
                         "显式 --scales 覆盖自动选择"),
                registry.ParamSpec("wait", kind="bool",
                                   help="执行最终测试时，闸满阻塞等槽"),
                _JSON, _PRETTY),
        defaults={"scales": None, "wait": False},
        description=("一键入库检验：lint→最终测试门（无 final 则执行测试段最终测试并"
                     "冻结）→判决吃测试段 corr_max/r2_lib/resic_t；scales 默认按 spec "
                     "interface 自动选择（bars_1m→minute，其余/缺省→daily）"),
        examples=("flab factor admit $QUANTRESEARCH_ROOT/factor/volatility/max_effect_20d.yaml",),
        output_schema={"type": "object", "properties": {
            "verdict": {"type": "string", "enum": ["可加入", "观察", "冗余", "重复"]},
            "corr_max": {"type": "number"}, "r2_lib": {"type": "number"},
            "retention": {"type": ["number", "null"]},
            "resic": {"type": "object"},
            "test_diagnostics": {"type": ["string", "null"]},
            "spec_used": {"type": "string"},
            "spec_note": {"type": ["string", "null"]},
            "建议": {"type": "string"}}},
    )
    _register(
        "factor.op.list", handler=factor_op_list,
        params=(registry.ParamSpec("catalog", kind="bool",
                                   help="列分类表全集（含未注册库函数）"),
                _JSON, _PRETTY),
        defaults={"catalog": False},
        description="算子清单（缺省注册面；--catalog 分类表全集）",
        examples=("flab factor op list", "flab factor op list --catalog"),
        output_schema={"type": "object", "properties": {
            "n": {"type": "integer"}, "ops": {"type": "array"}}},
    )
    _register(
        "factor.op.doc", handler=factor_op_doc,
        params=(registry.ParamSpec("name", kind="str", positional=True, required=True,
                                   help="算子名"), _JSON, _PRETTY),
        defaults={"name": None},
        description="算子文档（注册面 docstring；分类表回退）",
        examples=("flab factor op doc ts_mean",),
        output_schema={"type": "object", "properties": {
            "name": {"type": "string"}, "doc": {"type": "string"}}},
    )
    _register(
        "factor.op.add", handler=factor_op_add,
        params=(registry.ParamSpec("path", kind="path", positional=True, required=True,
                                   help="插件 .py 路径"),
                registry.ParamSpec("force", kind="bool", help="覆盖已注册算子"),
                _JSON, _PRETTY),
        defaults={"force": False},
        description="注册用户算子插件（命名门/冲突检查；失败 → LINT）",
        examples=("flab factor op add /tmp/myops.py",),
        output_schema={"type": "object", "properties": {"registered": {"type": "array"}}},
    )
    _register(
        "factor.op.remove", handler=factor_op_remove,
        params=(registry.ParamSpec("name", kind="str", positional=True, required=True,
                                   help="算子名"), _JSON, _PRETTY),
        defaults={"name": None},
        description="禁用用户算子插件（未找到 → NOT_FOUND）",
        examples=("flab factor op remove ts_tail_ratio",),
        output_schema={"type": "object", "properties": {"disabled": {"type": "string"}}},
    )
    _register(
        "factor.catalog", handler=factor_catalog,
        params=(registry.ParamSpec("format", kind="str", help="json|markdown（缺省 json）"),
                registry.ParamSpec("out", kind="path", help="输出文件路径（缺省内联）"),
                _JSON, _PRETTY),
        defaults={"format": "json", "out": None},
        description="列/算子活文档（与 catalog.md 同源生成）",
        examples=("flab factor catalog", "flab factor catalog --format markdown --out /tmp/c.md"),
        output_schema={"type": "object", "properties": {
            "catalog": {"type": "object"}, "markdown": {"type": "string"},
            "path": {"type": "string"}}},
    )


_reg_all()

__all__ = [
    "factor_admit",
    "factor_catalog",
    "factor_corr",
    "factor_export",
    "factor_lint",
    "factor_list",
    "factor_op_add",
    "factor_op_doc",
    "factor_op_list",
    "factor_op_remove",
    "factor_ref_add",
    "factor_ref_list",
    "factor_ref_remove",
    "factor_resic",
    "factor_run",
    "factor_show",
    "factor_svd",
    "resolve_candidate_spec",
]
