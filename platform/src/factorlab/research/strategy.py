"""R31 Task 5：strategy 组门面（策略校验/回测/浏览/导出/容量/成本）。

只装配不实现：全部业务经既有实现复用——
- lint：`surfaces.cli.main._lint_one(force_strategy=True)`（与 CLI `--strategy` 同一管线）；
- run：`core.strategy.load_strategy_doc` → `app.strategy.run_strategy`（信号→M7→M8
  →持久化）+ `_guard.guard_heavy`（过闸/env 注入/释放）+ `app.bootstrap.open_read`；
- list/show/export：`adapters.strategy_artifacts` / `adapters.execution_store` 真读回；
- capacity/cost：`app.strategy.target_one_side_turnover`（组合层单边换手单点）+
  `capacity_proxy`（E4）/ `cost_net_report`（E3）真实调用；capacity 的 avg_amount
  经 `adapters.read.source.load_daily` 真读（不硬编码）。

错误码（spec §4）：USAGE/LINT/STRATEGY_FAILED/DATA/NOT_FOUND/BUSY/MEMORY_GUARD。
默认目录：results 根 = `settings.results_dir`，策略产物 = `<root>/strategies/<name>`。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import statistics
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import polars as pl

from factorlab.adapters.execution_store import load_backtest_result
# Plan DQ-M1 终审修复 N2：读取门 opt-in（入口参数校验 + DATA 信封映射）
from factorlab.adapters.read.health import (DatasetQualityError,
                                            parse_accept_quality,
                                            resolve_accept_quality)
from factorlab.adapters.read.source import load_daily
from factorlab.adapters.strategy_artifacts import load_strategy_artifacts
from factorlab.app import bootstrap
from factorlab.app.strategy import (capacity_proxy, cost_net_report, run_strategy,
                                    target_one_side_turnover)
from factorlab.config import settings
from factorlab.research import envelope, registry
from factorlab.research._guard import GuardError, guard_heavy, release_slots
from factorlab.research.data_meta import result_frame
from factorlab.research.factor import _jsonify

_PRETTY = registry.ParamSpec("pretty", kind="bool", help="缩进 JSON（人读）")
_JSON = registry.ParamSpec("json", kind="bool",
                           help="输出单个 JSON 信封（默认口径，恒开）")
_ART_HINT = ("先 `flab strategy run <doc.yaml>` 产策略产物；"
             "命令目录 `flab describe --json`")
_FACTOR_HINT = ("信号产物缺失先 `flab factor run <signal 的 spec.yaml>`；"
                "命令目录 `flab describe --json`")
# Plan CX-C4 T3（T1 遗留）：composite 产物命令为 `factorlab compose`（C1 CLI 单点；
# flab 门面无 composite 组）——预检 hint 按 signal_kind 分派，不误指 factor。
_COMPOSITE_HINT = ("composite 信号产物缺失先 `factorlab compose <composite 的 spec.yaml>`；"
                   "命令目录 `flab describe --json`")
_EXPORT_FORMATS = {"parquet": ".parquet", "csv": ".csv", "json": ".json"}
_EXPORT_KINDS = {
    "nav": "nav/nav_series.parquet",
    "target": "target_portfolio.parquet",
    "schedule": "rebalance_schedule.parquet",
}
_PERIODS_PER_YEAR = {"daily": 252, "weekly": 52, "monthly": 12}


# ================================================================
# 公共：注册助手 / 名称安全 / 读句柄 / 闸 env
# ================================================================

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


def _safe_name(name: str) -> bool:
    """策略名 = <results_dir>/strategies/<name> 单层目录（拒绝路径穿越）。"""
    return bool(name) and name not in (".", "..") and not any(
        c in name for c in "/\\:")


def _strategy_dir(name: str) -> Path:
    return Path(settings.results_dir) / "strategies" / name


@contextmanager
def _read_handle() -> Iterator[Any]:
    rd = bootstrap.open_read()
    try:
        yield rd
    finally:
        rd.close()


@contextmanager
def _guarded_env(argv: list[str], *, wait: bool) -> Iterator[tuple[dict, Path]]:
    """过 heavy 闸 → 注入 env/settings（执行期生效）→ 退出释放槽 + 复原。"""
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


def _load_for(command: str, name: str):
    """读取策略三对象 + M8 结果：→ ((bundle, backtest, dir), None) | (None, fail)。"""
    if not _safe_name(name):
        return None, envelope.fail(
            command, "NOT_FOUND", f"非法策略名: {name!r}",
            hint="策略名对应 <results_dir>/strategies/<name> 单层目录")
    d = _strategy_dir(name)
    if not (d / "strategy_manifest.json").is_file() \
            or not (d / "manifest.json").is_file():
        return None, envelope.fail(
            command, "NOT_FOUND", f"策略 {name} 无完整产物（{d}）",
            hint=_ART_HINT)
    try:
        bundle = load_strategy_artifacts(d)
        backtest = load_backtest_result(d)
    except ValueError as exc:
        return None, envelope.fail(
            command, "NOT_FOUND", f"策略产物不可加载: {exc}", hint=_ART_HINT)
    except OSError as exc:
        return None, envelope.fail(
            command, "DATA", f"策略产物读取失败: {exc}", hint=_ART_HINT)
    return (bundle, backtest, d), None


def _nav_summary(nav_frame: pl.DataFrame) -> dict[str, Any]:
    navs = nav_frame["nav"].to_list()
    dates = nav_frame["execution_date"].to_list()
    first, last = (navs[0], navs[-1]) if navs else (None, None)
    total = (last / first - 1.0) if navs and first else None
    return {
        "events": len(navs), "first": first, "last": last,
        "total_return": total,
        "date_start": dates[0].isoformat() if dates else None,
        "date_end": dates[-1].isoformat() if dates else None,
    }


def _nav_returns(nav_frame: pl.DataFrame) -> list[float]:
    """逐执行事件收益（首期 = 0——首事件 NAV 已含建仓；换手序列首期含建仓）。"""
    navs = nav_frame["nav"].to_list()
    out = [0.0]
    for prev, cur in zip(navs, navs[1:]):
        out.append(cur / prev - 1.0)
    return out


def _turnover_block(bundle: Any) -> dict[str, Any]:
    series = target_one_side_turnover(bundle.target)
    return {"mean": statistics.fmean(series) if series else 0.0,
            "series": series}


def _periods_per_year(bundle: Any, args: Any) -> int:
    explicit = getattr(args, "periods_per_year", None)
    if explicit:
        return int(explicit)
    return _PERIODS_PER_YEAR[bundle.spec.rebalance_frequency]


def _cost_summary(report: dict) -> dict:
    """成本报告摘要（去掉逐期序列——明细走 result_frame/成本命令）。"""
    return {k: report[k] for k in
            ("layer", "cost_rate", "periods_per_year", "periods",
             "annual_turnover", "total_cost", "gross", "net")}


# ================================================================
# lint
# ================================================================

def strategy_lint(args: Any) -> envelope.Envelope:
    """策略 YAML 静态校验（不连库；坏文档 → LINT，缺文件 → NOT_FOUND）。"""
    from factorlab.surfaces.cli.main import _lint_one
    paths = [Path(p) for p in (getattr(args, "doc_paths", None) or [])]
    if not paths:
        return envelope.fail(
            "strategy.lint", "USAGE", "请给出至少一个策略 YAML 路径",
            hint="flab strategy lint $QUANTRESEARCH_ROOT/strategy/<name>.yaml")
    results: list[dict] = []
    failures: list[tuple[Path, str, str]] = []
    for path in paths:
        try:
            name = _lint_one(path, force_strategy=True)
            results.append({"path": str(path), "name": name, "ok": True})
        except FileNotFoundError as exc:
            results.append({"path": str(path), "ok": False, "error": str(exc)})
            failures.append((path, "NOT_FOUND", f"策略 YAML 不存在: {exc}"))
        except Exception as exc:  # noqa: BLE001 —— 批跑失败隔离，逐个上报
            results.append({"path": str(path), "ok": False, "error": str(exc)})
            failures.append((path, "LINT", str(exc)))
    if failures:
        code = failures[0][1]
        detail = "; ".join(f"{path}: {msg}" for path, _c, msg in failures)
        return envelope.fail(
            "strategy.lint", code, f"{len(failures)}/{len(paths)} 个策略校验失败——{detail}",
            hint="修正六层声明（portfolio/execution/date）后重跑 `flab strategy lint`")
    return envelope.ok("strategy.lint", {"n_pass": len(results), "results": results})


# ================================================================
# run（过闸 + 信号存在性预检 + 真链）
# ================================================================

def _doc_view(doc: Any) -> dict:
    return doc.model_dump(mode="json")


def strategy_run(args: Any) -> envelope.Envelope:
    """策略回测：load_strategy_doc →（dry-run 只回解析）→ open_read → run_strategy。"""
    from factorlab.app.memory import apply_hard_memory_limit_from_settings
    from factorlab.core.domain.execution import ExecutionDataQualityError
    from factorlab.core.lockbox import LockboxError
    from factorlab.core.strategy import load_strategy_doc
    from factorlab.research._guard import GuardError as _GuardError  # noqa: F401

    # N2：入口参数校验先于重链（缺 reason / FAIL / 未知状态 → USAGE，不占闸）
    override_reason = getattr(args, "override_reason", None)
    try:
        quality = resolve_accept_quality(
            parse_accept_quality(getattr(args, "accept_quality", None)),
            override_reason)
    except ValueError as exc:
        return envelope.fail(
            "strategy.run", "USAGE", str(exc),
            hint="例：flab strategy run <doc> --accept-quality PASS,DEGRADED "
                 "--override-reason '探索性研究'")

    doc_path = Path(args.doc_path)
    try:
        doc = load_strategy_doc(doc_path)
    except FileNotFoundError as exc:
        return envelope.fail("strategy.run", "NOT_FOUND", f"策略 YAML 不存在: {exc}",
                             hint="检查路径；命令目录 `flab describe --json`")
    except NotImplementedError as exc:
        return envelope.fail("strategy.run", "LINT", f"策略校验失败: {exc}",
                             hint="先 `flab strategy lint <doc.yaml>`")
    except ValueError as exc:
        return envelope.fail("strategy.run", "LINT", f"策略校验失败: {exc}",
                             hint="先 `flab strategy lint <doc.yaml>`")

    signal = getattr(args, "signal", None)
    if signal:
        try:
            spec = type(doc.strategy).model_validate(
                {**doc.strategy.model_dump(), "signal_name": signal})
        except ValueError as exc:
            return envelope.fail("strategy.run", "LINT",
                                 f"--signal 非法: {exc}")
        doc = doc.model_copy(update={"strategy": spec})

    if doc.rules.max_hold is not None:
        return envelope.fail(
            "strategy.run", "STRATEGY_FAILED",
            f"max_hold={doc.rules.max_hold} 的 V1 近似仅在研究侧入口实现",
            hint="用 research/tools/strategies/run_strategy.py 跑 max_hold 规则；"
                 "平台门面不静默忽略规则")

    if getattr(args, "dry_run", False):
        return envelope.ok(
            "strategy.run",
            {"name": doc.strategy.name, "dry_run": True, "doc": _jsonify(_doc_view(doc))},
            warnings=("dry-run：未过闸、未开读句柄、未落盘",))

    # 信号产物预检（先于占闸；缺失 → NOT_FOUND + 按 signal_kind 的生成命令提示）
    # factor：<results>/<name>/summary.json；composite：<results>/composites/<name>/
    # artifact.json（C1 writer 的完成标记；run_composite 的 summary 是其后的派生件，
    # 预检必须与 loader 真实前置一致——否则可加载产物被误报 NOT_FOUND）。
    from factorlab.adapters import results_fs
    from factorlab.app.composite.resolver import (COMPOSITE_ARTIFACT_NAME,
                                                  COMPOSITES_DIRNAME)
    results_dir = Path(settings.results_dir)
    signal_name = doc.strategy.signal_name
    if doc.signal_kind == "composite":
        signal_dir = results_dir / COMPOSITES_DIRNAME / signal_name
        marker = signal_dir / COMPOSITE_ARTIFACT_NAME
        hint = _COMPOSITE_HINT
    else:
        signal_dir = results_dir / signal_name
        marker = results_fs.summary_path(results_dir, signal_name)
        hint = _FACTOR_HINT
    if not marker.is_file():
        return envelope.fail(
            "strategy.run", "NOT_FOUND",
            f"信号产物缺失: {signal_name}（{signal_dir}）",
            hint=hint)

    out_dir = (Path(args.out_dir) if getattr(args, "out_dir", None)
               else results_dir / "strategies" / doc.strategy.name)
    argv = ["strategy", "run", str(doc_path)]
    try:
        with _guarded_env(argv, wait=bool(getattr(args, "wait", False))):
            apply_hard_memory_limit_from_settings()
            with _read_handle() as rd:
                res = run_strategy(doc, rd, results_dir=results_dir,
                                   out_dir=out_dir,
                                   accept_quality=quality,
                                   override_reason=override_reason,
                                   lockbox_intent=getattr(args, "lockbox", None),
                                   lockbox_reason=getattr(args, "lockbox_reason", None),
                                   doc_path=doc_path)
    except GuardError as exc:
        return envelope.fail("strategy.run", exc.code, exc.message,
                             hint=exc.hint, log=exc.log)
    except LockboxError as exc:
        return envelope.fail("strategy.run", exc.code, exc.message,
                             hint="`factorlab lockbox status` 看窗口与配额")
    except ExecutionDataQualityError as exc:
        return envelope.fail(
            "strategy.run", "STRATEGY_FAILED", f"执行数据质量闸拦截: {exc}",
            hint="核对除权/停牌/涨跌停数据面；产物未完成（strategy 目录可能已写）")
    except DatasetQualityError as exc:
        return envelope.fail(
            "strategy.run", "DATA", f"读取门拒绝: {exc}",
            hint="如需探索性读取非 PASS 分区：--accept-quality PASS,DEGRADED "
                 "--override-reason <原因>（FAIL 不可 opt-in；DEGRADED/LEGACY "
                 "opt-in 自动写 Experiment Manifest）")
    except FileNotFoundError as exc:
        return envelope.fail("strategy.run", "NOT_FOUND", f"读面文件缺失: {exc}",
                             hint=_FACTOR_HINT)
    except ValueError as exc:
        return envelope.fail("strategy.run", "STRATEGY_FAILED",
                             f"{type(exc).__name__}: {exc}",
                             hint="核对策略窗口/信号日期域/universe_override")
    except RuntimeError as exc:
        return envelope.fail("strategy.run", "DATA", f"{type(exc).__name__}: {exc}",
                             hint="后端不可达/缺表时先 `flab data tables`")
    except (OSError, NotImplementedError) as exc:
        return envelope.fail("strategy.run", "STRATEGY_FAILED",
                             f"{type(exc).__name__}: {exc}")

    log_path = Path(res.out_dir) / "run.log"
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    from factorlab.adapters.atomicio import atomic_write_text
    atomic_write_text(log_path, "\n".join([
        f"[{stamp}] flab strategy run doc={doc_path} out_dir={res.out_dir}",
        f"[{stamp}] signal={res.signal_name} decisions={res.decision_count} "
        f"events={len(res.backtest.artifacts)}",
    ]) + "\n")

    nav_summary = _nav_summary(res.nav_series.frame)
    fills = sum(a.fills.frame.height for a in res.backtest.artifacts)
    return envelope.ok(
        "strategy.run",
        {"name": doc.strategy.name, "signal": res.signal_name,
         "decisions": res.decision_count,
         "execution_events": len(res.backtest.artifacts),
         "fills": fills, "nav": nav_summary},
        artifacts={
            "strategy_dir": str(res.out_dir),
            "strategy_manifest": str(Path(res.out_dir) / "strategy_manifest.json"),
            "backtest_manifest": str(Path(res.out_dir) / "manifest.json"),
            "nav": str(Path(res.out_dir) / "nav" / "nav_series.parquet"),
            "log": str(log_path),
        })


# ================================================================
# list / show / export
# ================================================================

def strategy_list(args: Any) -> envelope.Envelope:
    """已保存策略产物列表（strategies/ 下双 manifest 完整者；新产物在前）。"""
    root = Path(settings.results_dir) / "strategies"
    rows: list[dict[str, Any]] = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            manifest_p = d / "manifest.json"
            sm_path = d / "strategy_manifest.json"
            if not manifest_p.is_file() or not sm_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
                sm = json.loads(sm_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            nav_p = d / "nav" / "nav_series.parquet"
            nav_first = nav_last = total = None
            try:
                if nav_p.is_file():
                    navs = pl.read_parquet(nav_p)["nav"].to_list()
                    if navs:
                        nav_first, nav_last = navs[0], navs[-1]
                        total = nav_last / nav_first - 1.0
            except (OSError, pl.exceptions.PolarsError):
                pass
            rows.append({
                "name": d.name,
                "signal": (sm.get("source_signal") or {}).get("name"),
                "created_at": manifest.get("created_at"),
                "n_events": manifest.get("artifact_count"),
                "date_start": manifest.get("execution_date_start"),
                "date_end": manifest.get("execution_date_end"),
                "nav_first": nav_first, "nav_last": nav_last,
                "total_return": total,
                "path": str(d),
            })
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return envelope.ok("strategy.list", _jsonify({"n": len(rows), "strategies": rows}))


def strategy_show(args: Any) -> envelope.Envelope:
    """单策略摘要：spec + 净值 + 换手 + 成本（E3 零成本对照）。"""
    loaded, err = _load_for("strategy.show", args.name)
    if err is not None:
        return err
    bundle, backtest, d = loaded
    turnover = _turnover_block(bundle)
    report = cost_net_report(_nav_returns(backtest.nav_series.frame),
                             turnover["series"], cost_rate=0.0,
                             periods_per_year=_periods_per_year(bundle, args))
    return envelope.ok(
        "strategy.show",
        _jsonify({
            "name": args.name,
            "signal": bundle.spec.signal_name,
            "spec": bundle.spec.model_dump(mode="json"),
            "nav": _nav_summary(backtest.nav_series.frame),
            "turnover": turnover,
            "cost": _cost_summary(report),
        }),
        artifacts={"strategy_dir": str(d),
                   "strategy_manifest": str(d / "strategy_manifest.json"),
                   "backtest_manifest": str(d / "manifest.json")})


def strategy_export(args: Any) -> envelope.Envelope:
    """策略产物导出：nav | target | schedule → parquet|csv|json。"""
    kind = str(getattr(args, "kind", None) or "nav").lower()
    if kind not in _EXPORT_KINDS:
        return envelope.fail("strategy.export", "USAGE",
                             f"未知 kind {kind}（支持 {'|'.join(_EXPORT_KINDS)}）",
                             hint="flab strategy export <name> --kind nav|target|schedule")
    fmt = str(getattr(args, "format", None) or "parquet").lower()
    if fmt not in _EXPORT_FORMATS:
        return envelope.fail("strategy.export", "USAGE",
                             f"未知格式 {fmt}（支持 {'|'.join(_EXPORT_FORMATS)}）")
    loaded, err = _load_for("strategy.export", args.name)
    if err is not None:
        return err
    _bundle, _backtest, d = loaded
    src = d / _EXPORT_KINDS[kind]
    if not src.is_file():
        return envelope.fail("strategy.export", "NOT_FOUND",
                             f"策略 {args.name} 缺 {_EXPORT_KINDS[kind]} 产物",
                             hint=_ART_HINT)
    try:
        df = pl.read_parquet(src)
    except (OSError, pl.exceptions.PolarsError) as exc:
        return envelope.fail("strategy.export", "DATA", f"产物读取失败: {exc}")
    out = getattr(args, "out", None)
    if out is None:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = (Path(settings.results_dir).parent / "research" / "strategy" / "export"
               / stamp / f"{args.name}_{kind}{_EXPORT_FORMATS[fmt]}")
    return result_frame(df, command="strategy.export", out=Path(out))


# ================================================================
# capacity / cost（E4/E3；真读面 + 真 metric 单点）
# ================================================================

def strategy_capacity(args: Any) -> envelope.Envelope:
    """E4 容量代理：daily amount 真读 × 参与率 ÷ 目标组合单边换手。"""
    loaded, err = _load_for("strategy.capacity", args.name)
    if err is not None:
        return err
    bundle, _backtest, d = loaded
    participation = float(getattr(args, "participation_rate", None) or 0.1)
    turnover = _turnover_block(bundle)
    if not turnover["series"] or turnover["mean"] <= 0:
        return envelope.fail(
            "strategy.capacity", "STRATEGY_FAILED",
            "目标组合换手为 0——容量无上界，不伪造成大数",
            hint="检查策略是否真实调仓（decision_dates/权重变化）")
    codes = sorted(bundle.target.frame["code"].unique().to_list())
    dates = bundle.target.decision_dates
    start, end = min(dates).isoformat(), max(dates).isoformat()
    try:
        with _read_handle() as rd:
            daily = load_daily(rd, codes, start, end, cols=["amount"],
                               float32=False).collect()
    except Exception as exc:  # noqa: BLE001 —— 读面统一 DATA
        return envelope.fail("strategy.capacity", "DATA",
                             f"{type(exc).__name__}: {exc}",
                             hint="daily.amount 不可读时先 `flab data daily --codes ...`")
    per_mean: dict[str, float] = {}
    if daily.height and "amount" in daily.columns:
        grouped = daily.group_by("code").agg(
            pl.col("amount").mean().alias("__avg_amount"))
        per_mean = {row["code"]: row["__avg_amount"]
                    for row in grouped.to_dicts()}
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    for code in codes:
        amount = per_mean.get(code.split(".")[0])
        if amount is None or amount != amount or amount <= 0:
            warnings.append(f"{code} 窗口内无有效 amount——该标的不计容量（不伪造）")
            continue
        try:
            proxy = capacity_proxy(float(amount), turnover["mean"],
                                   participation_rate=participation)
        except ValueError as exc:
            return envelope.fail("strategy.capacity", "STRATEGY_FAILED", str(exc))
        rows.append({"code": code, **proxy})
    if not rows:
        return envelope.fail(
            "strategy.capacity", "STRATEGY_FAILED",
            f"{len(codes)} 个持仓标的在 {start}~{end} 均无有效 amount",
            hint="核对 daily 数据覆盖与策略窗口")
    capacities = [r["capacity"] for r in rows]
    return envelope.ok(
        "strategy.capacity",
        _jsonify({
            "name": args.name,
            "window": {"start": start, "end": end},
            "codes": codes,
            "one_side_turnover": turnover["mean"],
            "turnover_series": turnover["series"],
            "participation_rate": participation,
            "per_code": rows,
            "aggregate": {"n": len(rows),
                          "min": min(capacities),
                          "mean": statistics.fmean(capacities),
                          "median": statistics.median(capacities)},
            "formula": rows[0]["formula"], "unit": rows[0]["unit"],
            "layer": rows[0]["layer"],
        }),
        artifacts={"strategy_dir": str(d)},
        warnings=tuple(warnings))


def strategy_cost(args: Any) -> envelope.Envelope:
    """E3 成本后净值：M8 逐事件收益 × 目标组合换手 → cost_net_report。"""
    loaded, err = _load_for("strategy.cost", args.name)
    if err is not None:
        return err
    bundle, backtest, d = loaded
    rate = float(getattr(args, "cost_rate", None) or 0.0)
    if not (0.0 <= rate < 1.0):
        return envelope.fail("strategy.cost", "USAGE",
                             f"cost_rate 必须在 [0, 1)（收到 {rate}）")
    turnover = _turnover_block(bundle)
    ppy = _periods_per_year(bundle, args)
    try:
        report = cost_net_report(_nav_returns(backtest.nav_series.frame),
                                 turnover["series"], cost_rate=rate,
                                 periods_per_year=ppy)
    except ValueError as exc:
        return envelope.fail("strategy.cost", "STRATEGY_FAILED", str(exc))
    return envelope.ok(
        "strategy.cost",
        _jsonify({
            "name": args.name,
            "signal": bundle.spec.signal_name,
            "returns_basis": "m8_nav_per_event（首期=0；换手序列首期含建仓）",
            "turnover": turnover,
            "report": report,
        }),
        artifacts={"strategy_dir": str(d)})


# ================================================================
# 注册（registry 单点；describe 自动可见）
# ================================================================

def _reg_all() -> None:
    _register(
        "strategy.lint", handler=strategy_lint,
        params=(registry.ParamSpec("doc_paths", kind="list[str]", positional=True,
                                   help="一个或多个策略 YAML 路径"),
                _JSON, _PRETTY),
        defaults={"doc_paths": []},
        description="策略 YAML 静态校验（六层声明；不连库；错误码 LINT）",
        examples=("flab strategy lint $QUANTRESEARCH_ROOT/strategy/low_lottery_top30_weekly.yaml",),
        output_schema={"type": "object", "properties": {
            "n_pass": {"type": "integer"}, "results": {"type": "array"}}},
    )
    _register(
        "strategy.run", handler=strategy_run,
        params=(registry.ParamSpec("doc_path", kind="path", positional=True,
                                   required=True, help="策略 YAML 路径（六层声明）"),
                registry.ParamSpec("signal", kind="str",
                                   help="覆盖 doc.strategy.signal_name（指定信号产物名）"),
                registry.ParamSpec("dry_run", kind="bool",
                                   help="只解析六层声明，不过闸/不读库/不落盘"),
                registry.ParamSpec("out_dir", kind="path",
                                   help="策略产物目录（缺省 runs/platform/strategies/<name>）"),
                registry.ParamSpec("wait", kind="bool",
                                   help="heavy 闸满时阻塞等槽（缺省立即 BUSY）"),
                registry.ParamSpec("accept_quality", kind="str",
                                   help="读取门 opt-in：逗号分隔 health_status"
                                        "（默认空=仅 PASS；非空必须同时给 "
                                        "--override-reason；FAIL 不可 opt-in）"),
                registry.ParamSpec("override_reason", kind="str",
                                   help="非 PASS 读取门 opt-in 原因（写入 "
                                        "Experiment Manifest）"),
                registry.ParamSpec("lockbox", kind="str",
                                   help="R40 锁箱意图 exploration|final"
                                        "（doc.date 与锁箱相交时必需）"),
                registry.ParamSpec("lockbox_reason", kind="str",
                                   help="R40 锁箱访问理由（与 --lockbox 配对，"
                                        "必填非空）"),
                _JSON, _PRETTY),
        defaults={"signal": None, "dry_run": False, "out_dir": None, "wait": False,
                  "accept_quality": None, "override_reason": None,
                  "lockbox": None, "lockbox_reason": None},
        description="策略回测：信号→M7 组合→M8 回测→持久化（过 heavy 闸；"
                    "R40 锁箱硬门）",
        examples=("flab strategy run $QUANTRESEARCH_ROOT/strategy/low_lottery_top30_weekly.yaml",
                  "flab strategy run <doc> --dry-run",
                  "flab strategy run <doc> --signal max_effect_20d_high --wait"),
        output_schema={"type": "object", "properties": {
            "name": {"type": "string"}, "signal": {"type": "string"},
            "decisions": {"type": "integer"}, "execution_events": {"type": "integer"},
            "fills": {"type": "integer"},
            "nav": {"type": "object", "properties": {
                "events": {"type": "integer"}, "first": {"type": "number"},
                "last": {"type": "number"}, "total_return": {"type": "number"}}}}},
    )
    _register(
        "strategy.list", handler=strategy_list,
        params=(_JSON, _PRETTY),
        description="已保存策略产物列表（净值/事件数；新产物在前）",
        examples=("flab strategy list --json",),
        output_schema={"type": "object", "properties": {
            "n": {"type": "integer"}, "strategies": {"type": "array"}}},
    )
    _register(
        "strategy.show", handler=strategy_show,
        params=(registry.ParamSpec("name", kind="str", positional=True,
                                   required=True, help="策略名（runs/platform/strategies/<name>）"),
                _JSON, _PRETTY),
        defaults={"name": None},
        description="单策略摘要：spec + 净值 + 换手 + 成本（零成本对照）",
        examples=("flab strategy show low_lottery_top30_weekly --json",),
        output_schema={"type": "object", "properties": {
            "name": {"type": "string"}, "spec": {"type": "object"},
            "nav": {"type": "object"}, "turnover": {"type": "object"},
            "cost": {"type": "object"}}},
    )
    _register(
        "strategy.export", handler=strategy_export,
        params=(registry.ParamSpec("name", kind="str", positional=True,
                                   required=True, help="策略名"),
                registry.ParamSpec("kind", kind="str",
                                   help="nav|target|schedule（缺省 nav）"),
                registry.ParamSpec("format", kind="str",
                                   help="parquet|csv|json（缺省 parquet）"),
                registry.ParamSpec("out", kind="path", help="输出路径覆盖"),
                _JSON, _PRETTY),
        defaults={"kind": "nav", "format": "parquet", "out": None},
        description="策略产物导出：nav|target|schedule → parquet|csv|json",
        examples=("flab strategy export low_lottery_top30_weekly --kind nav --format csv",),
        output_schema={"type": "object", "properties": {
            "path": {"type": "string"}, "n_rows": {"type": "integer"},
            "schema": {"type": "object"}, "head": {"type": "array"}}},
    )
    _register(
        "strategy.capacity", handler=strategy_capacity,
        params=(registry.ParamSpec("name", kind="str", positional=True,
                                   required=True, help="策略名"),
                registry.ParamSpec("participation_rate", kind="float",
                                   help="ADV 参与率上限（缺省 0.1）"),
                _JSON, _PRETTY),
        defaults={"participation_rate": 0.1},
        description="E4 容量代理：ADV×参与率÷单边换手（daily amount 真读）",
        examples=("flab strategy capacity low_lottery_top30_weekly --json",),
        output_schema={"type": "object", "properties": {
            "one_side_turnover": {"type": "number"},
            "per_code": {"type": "array"},
            "aggregate": {"type": "object", "properties": {
                "min": {"type": "number"}, "mean": {"type": "number"},
                "median": {"type": "number"}}},
            "formula": {"type": "string"}, "layer": {"type": "string"}}},
    )
    _register(
        "strategy.cost", handler=strategy_cost,
        params=(registry.ParamSpec("name", kind="str", positional=True,
                                   required=True, help="策略名"),
                registry.ParamSpec("cost_rate", kind="float",
                                   help="每单位单边换手的买卖总成本（缺省 0.0）"),
                registry.ParamSpec("periods_per_year", kind="int",
                                   help="年化期数（缺省按 rebalance_frequency：252/52/12）"),
                _JSON, _PRETTY),
        defaults={"cost_rate": 0.0, "periods_per_year": None},
        description="E3 成本后净值：net=gross−cost_rate×换手 + 年化/Sharpe/回撤",
        examples=("flab strategy cost low_lottery_top30_weekly --cost-rate 0.0007",),
        output_schema={"type": "object", "properties": {
            "turnover": {"type": "object"},
            "report": {"type": "object", "properties": {
                "cost_rate": {"type": "number"}, "periods": {"type": "integer"},
                "gross": {"type": "object"}, "net": {"type": "object"}}}}},
    )


_reg_all()
