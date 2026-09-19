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

`admit` = lint →（缺产物则 run，经闸）→ 对参考库 corr+resic → verdict：
corr_max≥0.95 → 重复；r2_lib≥0.8 且 resic 不显著 → 冗余；否则 → 可加入
（指标计算走 D10 `incremental_diagnostics` 单点）。

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
                             hint="flab factor lint research/factor/<族>/<短名>.yaml")
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


def factor_resic(args: Any) -> envelope.Envelope:
    """增量信息（--against 参考库）或组内互评（resIC/R²/verdict）。"""
    from factorlab.app.analysis.correlation import resolve_against
    from factorlab.app.analysis.cross_section import (incremental_diagnostics,
                                                      joint_diagnostics)
    names = list(getattr(args, "names", None) or [])
    target = getattr(args, "target", None)
    against = getattr(args, "against", None)
    min_stocks = getattr(args, "min_stocks", None) or 30
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
                                        min_stocks=min_stocks)
            data = {"mode": "incremental", "base": r["base"],
                    "candidates": [_strip_weekly(c) for c in r["candidates"]]}
        else:
            r = joint_diagnostics(names, settings.results_dir, target=target,
                                  min_stocks=min_stocks)
            data = {"mode": r["mode"], "group": _strip_weekly(r["group"]),
                    "factors": [_strip_weekly(f) for f in r["factors"]]}
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
                    hint="用 --all 扫全库，或修 `research/factor/_reference.yaml`")
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
# admit（lint →（缺产物则 run，经闸）→ 参考库 corr+resic → verdict）
# ================================================================

_VERDICT_JOIN = "可加入"
_VERDICT_REDUNDANT = "冗余"
_VERDICT_DUPLICATE = "重复"


def _admit_verdict(corr_max: float, r2_lib: float, resic_t: float) -> str:
    """plan Task 4 判决：corr_max≥0.95 重复；r2_lib≥0.8 且 resic 不显著 冗余；
    否则可加入（resic 显著 = |t|≥2，与 D10 `_incremental_verdict` 同门槛）。"""
    if _finite(corr_max) and corr_max >= 0.95:
        return _VERDICT_DUPLICATE
    if _finite(r2_lib) and r2_lib >= 0.8 and not (
            _finite(resic_t) and abs(resic_t) >= 2.0):
        return _VERDICT_REDUNDANT
    return _VERDICT_JOIN


_ADVICE = {
    _VERDICT_JOIN: "残差信息显著/独立——可加入参考库；补风格档案后 `flab factor ref add`",
    _VERDICT_REDUNDANT: "与参考库近亲（r2_lib 高且残差无显著增量）——不建议加入；考虑合并或替换库内近亲",
    _VERDICT_DUPLICATE: "与参考库成员高度相关（corr_max≥0.95）——重复，不加入",
}


def factor_admit(args: Any) -> envelope.Envelope:
    """一键入库检验：lint →（缺产物则 run，经闸）→ 参考库 corr+resic → verdict。"""
    from factorlab.adapters import results_fs
    from factorlab.app.analysis.cross_section import incremental_diagnostics
    from factorlab.app.analysis.reference import reference_names

    _ensure()
    spec_path = Path(args.spec_path)
    results, failures = _lint_paths([spec_path])
    if failures:
        return envelope.fail(
            "factor.admit", "LINT",
            f"{spec_path}: {failures[0][1]}",
            hint="先 `flab factor lint <spec>` 修 spec")
    name = results[0]["name"]

    results_dir = Path(settings.results_dir)
    summary_path = results_fs.summary_path(results_dir, name)
    panel_path = results_fs.panel_path(results_dir, name)
    ran = False
    if not (summary_path.is_file() and panel_path.is_file()):
        run_env = factor_run(_run_args(spec_path, wait=bool(getattr(args, "wait", False))))
        if not run_env.ok:
            err = run_env.error or {}
            return envelope.fail("factor.admit", err.get("code", "RUN_FAILED"),
                                 err.get("message", "候选 run 失败"),
                                 hint=err.get("hint"), log=err.get("log"))
        ran = True

    scales = getattr(args, "scales", None) or "daily"
    try:
        base = [b for b in reference_names(scales) if b != name]
    except FileNotFoundError as exc:
        return envelope.fail("factor.admit", "NOT_FOUND", f"参考库不可用（{exc}）",
                             hint="检查 `research/factor/_reference.yaml` 或 FACTORLAB_REFERENCE")
    except ValueError as exc:
        return envelope.fail("factor.admit", "DATA", f"参考库非法: {exc}")
    if not base:
        return envelope.fail("factor.admit", "DATA",
                             f"参考库 {scales} 组为空（或仅含候选自身）——无可对照成员",
                             hint="先 `flab factor ref add` 入库种子因子")
    try:
        r = incremental_diagnostics([name], results_dir, base=base)
    except FileNotFoundError as exc:
        return envelope.fail("factor.admit", "NOT_FOUND", str(exc), hint=_FACTOR_HINT)
    except ValueError as exc:
        return envelope.fail("factor.admit", "DATA", str(exc))
    cand = r["candidates"][0]
    verdict = _admit_verdict(cand["corr_max"], cand["r2_lib"], cand["resic_t"])
    data = {
        "name": name, "scales": scales, "ran": ran, "base": base,
        "corr_max": cand["corr_max"], "r2_lib": cand["r2_lib"],
        "retention": cand["retention"],
        "resic": {"mean": cand["resic_mean"], "t": cand["resic_t"],
                  "n_weeks": cand["n_weeks"]},
        "d10_verdict": cand["verdict"],
        "verdict": verdict,
        "建议": _ADVICE[verdict],
    }
    return envelope.ok("factor.admit", _jsonify(data),
                       artifacts={"summary": str(summary_path)})


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
                             hint="检查 `research/factor/_reference.yaml`")
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
                             hint="先建 `research/factor/_reference.yaml`（scales: daily/minute）")
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
    entry = {
        "name": name, "style": args.style, "reason": args.reason,
        "added": getattr(args, "added", None) or datetime.date.today().isoformat(),
        "entry_corr_max": getattr(args, "entry_corr_max", None),
        "entry_resic_t": getattr(args, "entry_resic_t", None),
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
    return envelope.ok("factor.ref.add",
                       {"name": name, "scales": scales, "path": str(path),
                        "backup": str(backup), "entry": _jsonify(entry)},
                       artifacts={"reference": str(path), "backup": str(backup)})


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
                registry.ParamSpec("all", kind="bool", help="扫描 research/factor/**/*.yaml 全库批跑"),
                registry.ParamSpec("strategy", kind="bool", help="强制按策略文档校验"),
                _JSON, _PRETTY),
        defaults={"spec_paths": [], "all": False, "strategy": False},
        description="静态校验（秒级，不连库；错误码 LINT）",
        examples=("flab factor lint research/factor/momentum_20d/turnrank_top2.yaml",
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
        examples=("flab factor run research/factor/momentum_20d/turnrank_top2.yaml",
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
                _JSON, _PRETTY),
        defaults={"names": [], "target": None, "min_stocks": None, "against": None},
        description="增量信息/正交化残差 IC（resIC/r2_lib/retention/verdict）",
        examples=("flab factor resic cand --against reference",
                  "flab factor resic a b c"),
        output_schema={"type": "object", "properties": {
            "mode": {"type": "string"}, "base": {"type": "array"},
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
        description="参考库成员清单（D10；读 research/factor/_reference.yaml）",
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
                                   help="入库时对库内其余成员 max|ρ|"),
                registry.ParamSpec("entry_resic_t", kind="float",
                                   help="入库时对库内其余成员残差 t（有符号）"),
                _JSON, _PRETTY),
        defaults={"scales": "daily", "added": None, "entry_corr_max": None,
                  "entry_resic_t": None},
        description="参考库入库（备份/校验 scales/原子写；重名 → LINT）",
        examples=("flab factor ref add my_factor --style 量价 --reason '独立增量'",),
        output_schema={"type": "object", "properties": {
            "name": {"type": "string"}, "scales": {"type": "string"},
            "path": {"type": "string"}, "backup": {"type": "string"}}},
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
                registry.ParamSpec("scales", kind="str", help="参考库分组（缺省 daily）"),
                registry.ParamSpec("wait", kind="bool",
                                   help="缺产物需 run 时，闸满阻塞等槽"),
                _JSON, _PRETTY),
        defaults={"scales": "daily", "wait": False},
        description="一键入库检验：lint→（缺产物则 run）→参考库 corr+resic→verdict",
        examples=("flab factor admit research/factor/volatility/max_effect_20d.yaml",),
        output_schema={"type": "object", "properties": {
            "verdict": {"type": "string", "enum": ["可加入", "冗余", "重复"]},
            "corr_max": {"type": "number"}, "r2_lib": {"type": "number"},
            "retention": {"type": "number"}, "resic": {"type": "object"},
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
]
