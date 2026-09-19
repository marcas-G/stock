import datetime
import json
import re
from pathlib import Path

import typer
from rich.console import Console

from factorlab import __version__
from factorlab.adapters.catalog import catalog_json, render_catalog_markdown
# Plan DQ-M1 终审修复 N2：读取门 opt-in 通道（入口参数校验 + 友好报错）
from factorlab.adapters.read.health import (DatasetQualityError,
                                            parse_accept_quality,
                                            resolve_accept_quality)
from factorlab.config import settings
from factorlab.research.cli import research_app
from factorlab.core.factor.errors import FactorDSLError
from factorlab.core.factor.ast_gate import validate_formula
from factorlab.core.eval.layered import degenerate_decile_groups
from factorlab.core.engine.compute import substitute_params
from factorlab.adapters import plugins
from factorlab.core.ops import registry
from factorlab.core.spec import load_spec


app = typer.Typer(no_args_is_help=True)
console = Console()
op_app = typer.Typer(no_args_is_help=True)
app.add_typer(op_app, name="op")
# R31：研究员统一门面（flab 短入口的落点；命令注册单点在 factorlab.research.registry）
app.add_typer(research_app, name="research")


@app.callback()
def main() -> None:
    """factorlab 因子 DSL 计算平台"""
    # 装配单点（2026-09-14）：CLI 组回调处装齐注册面（算子族 + process 处理器），
    # 新增子命令自动覆盖——否则 `op list` 会打印空表（注册靠 import 副作用，
    # CLI 进程此前从不 import 实现模块），且 catalog dump 的关闸指引正是 `op list`。
    from factorlab.app.bootstrap import ensure_assembly
    ensure_assembly()


@app.command()
def version() -> None:
    typer.echo(__version__)


def _lint_one(spec_path: Path, *, force_strategy: bool = False) -> str:
    """校验单个 spec；返回 name。

    分派（R07-STRAT-I6）：YAML 顶层含 `signal`/`portfolio` 形态 → 策略文档，
    走 `load_strategy_doc` 严格校验（未知键/类型/NEXT_WINDOW 无窗口/rules V1）；
    其余仍走因子 spec 的完整静态管线（行为不变）。`force_strategy=True`
    （CLI `--strategy`）对给定路径强制按策略文档校验（错误可读，exit 1）。

    因子校验范围：formula / factors[].formula / universe.formula（池公式）/
    operators 宏体。R22（Task 8）：改用 `prepare_static`——参数替换 → 宏展开 →
    def 内联 → 薄封装展开 → stable_rank/vendor alias 改写 → 分类表归一化
    （开放面全量放行，未知算子给 op_meta 指引）→ 未来门（全形态 行:列）→
    输出名检查。引擎侧 compute_formula 执行同一序列（仅多一层 universe
    masking），lint 只是把失败提前到写因子的第一条命令。
    """
    from factorlab.core.strategy.spec_io import looks_like_strategy_doc

    if force_strategy or looks_like_strategy_doc(spec_path):
        from factorlab.core.strategy import load_strategy_doc
        return load_strategy_doc(spec_path).strategy.name

    from factorlab.core.engine.compute import prepare_static

    spec = load_spec(spec_path)
    sources: list[str] = []
    if spec.formula is not None:
        sources.append(spec.formula)
    else:
        sources.extend(item.formula for item in spec.factors or [])
    if spec.universe.formula is not None:
        sources.append(spec.universe.formula)
    sources.extend(op.formula for op in (spec.operators or {}).values())
    for source in sources:
        validate_formula(substitute_params(source, spec.params))
    # 完整静态管线（引擎同序；逐 formula/逐 factor，池公式随 pipeline 展开）
    variants = ([spec] if spec.formula is not None
                else [spec.model_copy(update={"formula": item.formula,
                                              "factors": None})
                      for item in spec.factors or []])
    for variant in variants:
        prepare_static(variant)
    return spec.name


def _factor_spec_paths() -> list[Path]:
    """`--all` 的 spec 集合：`settings.research_root/factor/`，rglob *.yaml 跳过 `_` 前缀。

    路径单点（R37）：研究产物区根 = `QUANTRESEARCH_ROOT`（platform 侧经
    `settings.research_root`）——与 `research/tools/factor_lib/build_index.py`
    同布局（元数据 `_*.yaml` 不是 spec）；glob 而非硬编码清单——挖矿在途新增因子
    自动纳入。根不存在（如干净 CI checkout）→ 空列表，调用方给指引。
    """
    factor_root = Path(settings.research_root) / "factor"
    if not factor_root.is_dir():
        return []
    out = []
    for p in sorted(factor_root.rglob("*.yaml")):
        rel = p.relative_to(factor_root)
        if any(part.startswith("_") for part in rel.parts):
            continue  # `_families.yaml` / `_pools/` 等元数据，不是 spec
        out.append(p)
    return out


@app.command()
def lint(
    spec_paths: list[Path] = typer.Argument(
        None, help="一个或多个因子 spec 或策略文档 YAML 路径"),
    all_specs: bool = typer.Option(False, "--all",
                                   help="扫描 $QUANTRESEARCH_ROOT/factor/**/*.yaml 全库单进程批跑"),
    strategy: bool = typer.Option(
        False, "--strategy",
        help="强制按策略文档校验给定 YAML（策略文档也可被自动识别；与 --all 互斥）"),
) -> None:
    """校验 YAML Spec（因子 formula / 策略文档，与引擎同序静态管线，不打开 DB）。

    分派：含 `signal`/`portfolio` 的 YAML → 策略文档（load_strategy_doc 严格校验）；
    其余 → 因子 spec（完整静态管线）。单路径：输出 `OK <name>`；失败打印原因并
    exit 1（行为与退出码保持兼容）。多路径 / `--all`：单进程批跑（消灭 N 个进程
    N 次 import 的启动开销），逐个失败隔离上报，任一失败 exit 1，末行汇总
    `factor lint: N 通过 / M 失败`。
    """
    paths: list[Path] = list(spec_paths or [])
    if strategy and all_specs:
        console.print("--strategy 与 --all 互斥（--all 只扫因子 spec）")
        raise typer.Exit(code=2)
    if strategy and not paths:
        console.print("--strategy 需要给出策略 YAML 路径")
        raise typer.Exit(code=2)
    if all_specs:
        discovered = _factor_spec_paths()
        if not discovered:
            console.print(
                f"未找到 {settings.research_root}/factor/**/*.yaml"
                "——核对 QUANTRESEARCH_ROOT，或直接给出 spec 路径")
            raise typer.Exit(code=1)
        paths = sorted(set(paths) | set(discovered))
    if not paths:
        console.print("请给出至少一个 spec 路径，或用 --all 全库批跑")
        raise typer.Exit(code=2)
    if len(paths) == 1 and not all_specs:
        try:
            name = _lint_one(paths[0], force_strategy=strategy)
        except (ValueError, FactorDSLError, NotImplementedError) as exc:
            console.print(str(exc))
            raise typer.Exit(code=1) from exc
        console.print(f"OK {name}")
        return
    failures: list[tuple[Path, str]] = []
    for path in paths:
        try:
            _lint_one(path, force_strategy=strategy)
        except Exception as exc:  # noqa: BLE001 —— 批跑失败隔离：逐个上报，不中断
            failures.append((path, str(exc)))
    for path, msg in failures:
        console.print(f"  lint 失败: {path}: {msg}")
    console.print(f"factor lint: {len(paths) - len(failures)} 通过 / {len(failures)} 失败")
    if failures:
        raise typer.Exit(code=1)


@op_app.command("list")
def op_list(
    catalog: bool = typer.Option(
        False, "--catalog",
        help="列分类表全集（含未注册库函数）：name/partition/window/source/returns"),
) -> None:
    plugins.discover_plugins(settings.plugin_dir)
    if catalog:
        # R05-M1：注册面（~55）≠ 分类面（生成表全量，含未注册库函数）——R05 实测
        # 用户/AI 无法从 CLI 发现"现在能写什么"。这里给最小可用检索入口（完整
        # 同源 = 算子档案/catalog.md 合并归 Plan 2）。默认行为（无 --catalog）不变。
        from factorlab.core.ops.registration import effective_catalog
        metas = sorted(effective_catalog().all(), key=lambda m: m.name)
        typer.echo(f"# 分类表全集: {len(metas)} 条（含未注册库函数；"
                   f"注册面见不带 --catalog 的 op list）")
        for meta in metas:
            window = "-" if meta.window is None else meta.window
            typer.echo(f"{meta.name}  partition={meta.partition}  window={window}"
                       f"  source={meta.source}  returns={meta.returns}")
        return
    rows = [
        {
            "name": op.name,
            "kind": op.kind,
            "version": op.version,
        }
        for op in registry.list_ops()
    ]
    console.print(rows)


@op_app.command("doc")
def op_doc(name: str) -> None:
    plugins.discover_plugins(settings.plugin_dir)
    try:
        op = registry.get_op(name)
    except KeyError:
        # R05-M1：未注册但在分类表（开放算子底座生成面）→ 回退打印分类元数据
        # （含返回形态——Struct/multi 不得直接作输出/进 process，见 lint 静态门）。
        from factorlab.core.ops.registration import effective_catalog
        meta = effective_catalog().get(name)
        if meta is None:
            console.print(
                f"未知算子: {name}（注册面与分类表均无——"
                f"`factorlab op list --catalog` 查看全集；自定义算子见 `factorlab op add`）")
            raise typer.Exit(code=1)
        window = "-" if meta.window is None else meta.window
        console.print(f"{meta.name} ({meta.partition}, source={meta.source}, "
                      f"returns={meta.returns})")
        console.print(f"window={window}  mask_args={meta.mask_args}")
        console.print("未注册（无 docstring）——分类表条目可直接在公式调用；"
                      "算子档案/字段访问归 Plan 2")
        return
    console.print(f"{op.name} ({op.kind}, {op.version})")
    console.print(op.doc or "no doc")


@op_app.command("add")
def op_add(path: Path, force: bool = False) -> None:
    names = plugins.add_plugin(path, plugin_dir=settings.plugin_dir, force=force)
    console.print(f"registered: {', '.join(names)}")


@op_app.command("remove")
def op_remove(name: str) -> None:
    plugins.remove_plugin(name, plugin_dir=settings.plugin_dir)
    console.print(f"disabled: {name}")


catalog_app = typer.Typer(no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")


def _parse_param_value(value: str) -> int | float | bool | str:
    """--set 值类型解析：int → float → bool（true/false）→ str 原样。"""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def execute_run(
    spec_path: Path,
    *,
    universe: str | None = None,
    max_memory: str = "4GB",
    output_dir: Path | None = None,
    float32: bool = True,
    backtest: bool = True,
    groups: int = 10,
    set_params: list[str] | None = None,
    chunk_days: int | None = None,
    warmup_days: int | None = None,
    eval_frequency: str | None = None,
    profile: bool = False,
    chunk_workers: int = 1,
    read_cache: bool | None = None,
    dataset: str | None = "ashare_daily",
    accept_quality: tuple[str, ...] = ("PASS",),
    override_reason: str | None = None,
) -> dict:
    """`factorlab run` 的计算主体（CLI 与 research.factor 门面共用，不打印）。

    R31 Task 4：从 `run_factor_cli` 抽出——消除"门面复刻 run 装配"的业务漂移风险
    （研究门面 `factor run` 经 import 本函数复用同一条链，含内存护栏与评估装配）。

    R09-M3：`profile=True`（或 env `FACTORLAB_PROFILE=1`）时启用分段计时
    （app/profile.py）——summary.runtime.profile 落盘 + 各段墙钟/RSS；缺省关闭
    （零行为变化）。返回 `{"spec", "variant", "ctx", "result", "outcome"}`；
    错误原样抛出（ValueError/FileNotFoundError/FactorDSLError，调用方各自映射
    展示/错误码）。

    R09-PERF-P4：`chunk_workers`（缺省 1=顺序）分钟链 chunk 并行度——仅
    interface=bars_1m 生效（日频链忽略）；并发前按 chunk_days 校准的单 chunk
    估值（3.6GB × max(chunk_days, 10)/10，R09 复评 F1）对内存预算做门。

    Plan DQ-M1 F3：`dataset`（缺省 "ashare_daily"）透传给 `evaluate_run` 的
    读取门（fail-closed，as_of=面板最新日）；合成/测试调用可显式 None 关闭。

    Plan DQ-M1 终审修复 N2：`accept_quality`/`override_reason` 透传读取门
    （DEGRADED/LEGACY opt-in；CLI `--accept-quality`/`--override-reason`）。

    R31：`read_cache`（None=env `FACTORLAB_READ_CACHE` 默认开；False=CLI
    `--no-read-cache`）透传 RunContext——分钟链 bars_1m chunk 级磁盘缓存开关。
    """
    from factorlab.app.run import run_factor, run_factor_minute
    from factorlab.app.context import RunContext
    from factorlab.app.evaluate import evaluate_run, publish_run
    from factorlab.app.profile import Profiler, profile_enabled

    overrides = {}
    for kv in set_params or []:
        key, _, value = kv.partition("=")
        if not key or not value:
            raise ValueError(f"--set 格式应为 k=v: {kv}")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError(f"--set 值含非法字符（仅字母数字_.-）: {value}")
        overrides[key] = _parse_param_value(value)
    if eval_frequency is not None and eval_frequency not in ("daily", "weekly"):
        raise ValueError(f"--eval-frequency 应为 daily|weekly: {eval_frequency}")
    spec = load_spec(spec_path)
    variant = spec.name
    if overrides:
        spec.params = {**spec.params, **overrides}
        variant = spec.name + "_" + "_".join(f"{k}{v}" for k, v in overrides.items())
    profiler = Profiler() if (profile or profile_enabled()) else None
    if profiler is not None:
        profiler.start()
    ctx = RunContext(
        db_path=settings.platform_db,
        output_dir=output_dir or (settings.results_dir / variant),
        universe_override=universe or settings.default_universe,
        float32=float32,
        chunk_days=chunk_days,
        warmup_days=warmup_days,
        max_memory=max_memory,
        profiler=profiler,
        chunk_workers=chunk_workers,
        read_cache=read_cache,
    )
    # R05-C1：显式 FACTORLAB_MAX_MEMORY 时先落进程级 RLIMIT_AS 硬上限
    # （软看门狗在 run_* 内自动启用；未设 = 不动进程资源）。
    # R30：硬上限只跟"显式设置"绑定；CLI 默认化（env 未设 → 6GB 预检 +
    # min(16GB,12%) RSS）在下方上下文内生效，退出即恢复（API 直调语义不变）。
    from factorlab.app.memory import (apply_hard_memory_limit_from_settings,
                                      cli_memory_guardrails)
    apply_hard_memory_limit_from_settings()
    # W5 分派：分钟面 spec（interface: bars_1m）走分钟链 run_factor_minute（折日
    # 面板与日频同列契约，下方评估/分层回测零改动复用）；日频 spec 走原 run_factor。
    run_impl = run_factor_minute if spec.interface == "bars_1m" else run_factor
    try:
        with cli_memory_guardrails():
            result = run_impl(spec, ctx)
            # 评估装配单点（WS5）：app.evaluate.evaluate_run + publish_run
            outcome = evaluate_run(result, spec, ctx, groups=groups, backtest=backtest,
                                   frequency=eval_frequency, dataset=dataset,
                                   accept_quality=accept_quality,
                                   override_reason=override_reason)
            publish_run(result, outcome, ctx)
    finally:
        if profiler is not None:
            profiler.stop()
    return {"spec": spec, "variant": variant, "ctx": ctx,
            "result": result, "outcome": outcome}


def _print_quality_reject(exc: DatasetQualityError) -> None:
    """读取门拒绝的友好报错（N2）：结构化上下文 + 所需旗标指引（不裸 traceback）。"""
    console.print(f"错误: {exc}", soft_wrap=True)
    ctx = f"dataset={exc.dataset} partition={exc.partition} status={exc.status}"
    if exc.freshness is not None:
        ctx += f" freshness={exc.freshness}"
    console.print(f"  读取门上下文: {ctx}", soft_wrap=True)
    console.print("  提示: 如需探索性读取非 PASS 分区，显式传 "
                  "`--accept-quality PASS,DEGRADED --override-reason <原因>`"
                  "（FAIL 不可 opt-in；DEGRADED/LEGACY opt-in 自动写 "
                  "Experiment Manifest）", soft_wrap=True)


@app.command("run")
def run_factor_cli(
    spec_path: Path,
    universe: str | None = None,
    max_memory: str = "4GB",
    output_dir: Path | None = None,
    float32: bool = True,
    backtest: bool = True,
    groups: int = typer.Option(10, min=2),
    set_params: list[str] = typer.Option(None, "--set", help="覆盖 spec.params（k=v，可多次，生成 name_kv 变体）"),
    chunk_days: int | None = typer.Option(None, "--chunk-days", min=1,
                                          help="日期分块（交易日/块；缺省：分钟链 20 交易日/块自动分块，日频单块整段跑；显式超大块按内存估算告警/拒绝，见 interface.md §1）"),
    warmup_days: int | None = typer.Option(None, "--warmup-days", min=0,
                                           help="TS 窗口预热天数（缺省=按公式自动提取窗口+20）"),
    eval_frequency: str | None = typer.Option(
        None, "--eval-frequency",
        help="评估频率覆盖：daily（逐日默认）| weekly（周频对照）；缺省取 spec.evaluation_frequency"),
    profile: bool = typer.Option(
        False, "--profile",
        help="R09-M3 分段计时：输出各段（读数据/折日/label/评估/分层回测/落盘）"
             "墙钟+峰值 RSS 到 stderr，并写 summary.runtime.profile（默认关闭；"
             "env FACTORLAB_PROFILE=1 等效）"),
    chunk_workers: int = typer.Option(
        1, "--chunk-workers", min=1,
        help="R09-PERF-P4 分钟链 chunk 并行 worker 数（默认 1=顺序现行为；"
             "N>=2 按 chunk 并行「读+折日」后有序合并，数值与 N=1 逐值一致；"
             "并发前按 chunk_days 校准的单 chunk 估值（3.6GB×max(chunk_days,"
             "10)/10；默认 20 日块 7.2GB/chunk）对 FACTORLAB_MAX_MEMORY 做"
             "预算门，超限拒绝；仅 interface: bars_1m 生效）"),
    no_read_cache: bool = typer.Option(
        False, "--no-read-cache",
        help="R31 关闭分钟链 bars_1m chunk 级磁盘缓存（默认开；env "
             "FACTORLAB_READ_CACHE=0 等效；仅 interface: bars_1m 生效）"),
    accept_quality: str | None = typer.Option(
        None, "--accept-quality",
        help="读取门 opt-in：逗号分隔 health_status（默认空=仅 PASS；非空必须同时给 "
             "--override-reason；FAIL 不可 opt-in；DEGRADED/LEGACY opt-in 自动写 "
             "Experiment Manifest）"),
    override_reason: str | None = typer.Option(
        None, "--override-reason",
        help="非 PASS 读取门 opt-in 的原因（写入 Experiment Manifest；与 "
             "--accept-quality 配对）"),
) -> None:
    """计算因子并评估（平台库）。--backtest 默认产出分层回测；--no-backtest 关闭（快速评估）。
    --groups 分层档数（>=2）。--set k=v 覆盖 spec.params 生成变体（results 独立目录）。
    --universe 默认 FACTORLAB_DEFAULT_UNIVERSE。--eval-frequency 覆盖 spec 评估频率
    （daily 默认逐日口径；weekly 为旧口径可选对照）。--profile 输出分段计时。
    --chunk-workers 分钟链 chunk 并行度（默认 1，见 --help）。
    --accept-quality/--override-reason 读取门 opt-in（N2：DEGRADED/LEGACY 显式接受）。"""
    try:
        quality = resolve_accept_quality(parse_accept_quality(accept_quality),
                                         override_reason)
    except ValueError as exc:
        console.print(f"错误: {exc}", soft_wrap=True)
        raise typer.Exit(code=2) from exc
    try:
        out = execute_run(spec_path, universe=universe, max_memory=max_memory,
                          output_dir=output_dir, float32=float32,
                          backtest=backtest, groups=groups, set_params=set_params,
                          chunk_days=chunk_days, warmup_days=warmup_days,
                          eval_frequency=eval_frequency, profile=profile,
                          chunk_workers=chunk_workers,
                          read_cache=False if no_read_cache else None,
                          dataset="ashare_daily",
                          accept_quality=quality,
                          override_reason=override_reason)
    except DatasetQualityError as exc:
        _print_quality_reject(exc)
        raise typer.Exit(code=1) from exc
    except (ValueError, FileNotFoundError, FactorDSLError) as exc:
        # ValueError 含 pydantic 的 ValidationError（spec 字段非法，如 cost_rate 越界）——
        # 用户写错 YAML 不该看到裸 traceback（`lint` 子命令同款处理）。
        console.print(f"错误: {exc}")
        raise typer.Exit(code=1) from exc
    variant = out["variant"]
    gate = getattr(out["outcome"], "dataset_gate", None)
    if gate is not None and gate.health_status != "PASS":
        console.print(f"提示: 读取门 opt-in（status={gate.health_status}）——"
                      f"Experiment Manifest: {gate.manifest_path}")
    evaluation = out["outcome"].evaluation
    outputs = out["outcome"].outputs
    # R09-M3：人读分段摘要走 stderr（stdout 仍是既有评估行，零污染）
    report = (out["result"].summary.get("runtime") or {}).get("profile")
    if report is not None:
        from factorlab.app.profile import render_profile_summary
        typer.echo(render_profile_summary(report), err=True)
    for note in out["outcome"].notes:
        console.print(f"提示: {note}")
    if outputs == ["signal"]:
        ic = evaluation.get("ic", {})
        console.print(f"{variant}: n_weeks={evaluation.get('n_weeks')} "
                      f"ic_mean={ic.get('mean')} spread={evaluation.get('decile_returns', {}).get('spread', {}).get('ret')} "
                      f"freq={evaluation.get('frequency')}")
    else:
        for o, ev_o in evaluation["outputs"].items():  # 逐输出一行（legacy 行同型）
            ic = ev_o.get("ic", {})
            console.print(f"{variant}__{o}: n_weeks={ev_o.get('n_weeks')} "
                          f"ic_mean={ic.get('mean')} spread={ev_o.get('decile_returns', {}).get('spread', {}).get('ret')} "
                          f"freq={ev_o.get('frequency')}")


@app.command("compose")
def compose(
    spec_path: Path,
    results_dir: Path | None = typer.Option(
        None, "--results-dir",
        help="结果根目录（缺省 settings.results_dir，如 runs/platform）"),
    out_dir: Path | None = typer.Option(
        None, "--out-dir",
        help="产物目录（缺省 <results-dir>/composites/<name>）"),
) -> None:
    """运行 Composite spec：成员 artifact×K → X → Python compute → 评估 → 落盘。

    薄壳（Plan CX-C1 §14）：全链在 app.composite.runner；本命令只做参数透传与
    ValueError/FileNotFoundError → 友好文案 + exit 1（缺成员/交集为空/NaN 输出等）。
    """
    from factorlab.app.composite.runner import run_composite

    try:
        result = run_composite(spec_path, results_dir=results_dir, out_dir=out_dir)
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"错误: {exc}")
        raise typer.Exit(code=1) from exc
    evaluation = result.summary.get("evaluation")
    ic = evaluation.get("ic") if isinstance(evaluation, dict) else None
    incremental = (evaluation.get("incremental_vs_best_member")
                   if isinstance(evaluation, dict) else None)
    console.print(
        f"{result.name}: rows={result.frame.height} cached={result.cached} "
        f"ic_mean={ic.get('mean') if isinstance(ic, dict) else None} "
        f"delta_vs_best={incremental.get('delta') if isinstance(incremental, dict) else None} "
        f"out={result.out_dir}")


def _run_at(summary: dict, summary_path: Path) -> tuple[str, float]:
    """summary 运行时间的 (展示值, 排序键)：timestamp 字段优先；
    缺失/不可解析时回退 summary.json 文件 mtime（M4a run 落盘无 timestamp）。"""
    ts = summary.get("timestamp")
    if ts:
        try:
            return str(ts), datetime.datetime.fromisoformat(str(ts)).timestamp()
        except ValueError:
            pass
    mtime = summary_path.stat().st_mtime
    return datetime.datetime.fromtimestamp(mtime).isoformat(timespec="seconds"), mtime


def collect_result_rows(results_dir: Path | None = None) -> list[dict]:
    """已保存因子的列表行（`list` 命令与 research.factor 门面共用；新运行在前）。

    R31 Task 4：从 `list_factors` 抽出（门面 `factor list` 不重写摘要汇总）。
    返回行含 `_sort`（排序键）；展示与 JSON 消费方各自决定是否剔除。
    """
    results_dir = Path(results_dir) if results_dir is not None else settings.results_dir
    if not results_dir.is_dir():
        return []
    rows = []
    # R12：布局经 results 单点（不再 glob 布局字面量）
    from factorlab.adapters import results_fs
    for _name in results_fs.list_result_dirs(results_dir):
        summary_path = results_fs.summary_path(results_dir, _name)
        if not summary_path.is_file():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # 损坏/不可读的 summary 跳过
        ev = summary.get("evaluation", {})
        run_at, sort_key = _run_at(summary, summary_path)
        # 目录名优先可区分变体（变体目录 summary.name 是基础名）
        base_name = (summary_path.parent.name if summary_path.parent.name != summary.get("name")
                     else summary.get("name"))
        per_outputs = ev.get("outputs") if isinstance(ev, dict) and isinstance(ev.get("outputs"), dict) else None
        if per_outputs:
            # 多输出：逐输出一行（因子名__输出名；evaluation.outputs 声明序）
            for o, ev_o in per_outputs.items():
                rows.append({
                    "name": f"{base_name}__{o}",
                    "category": summary.get("category", ""),
                    "direction": summary.get("direction", ""),
                    "ic_mean": ev_o.get("ic", {}).get("mean"),
                    "spread": ev_o.get("decile_returns", {}).get("spread", {}).get("ret"),
                    "dir_consistent": ev_o.get("ic", {}).get(
                        "direction_consistent_share"),
                    "version": ev_o.get("version"),
                    "frequency": ev_o.get("frequency"),
                    "run_at": run_at,
                    "_sort": sort_key,
                })
            continue
        rows.append({
            "name": base_name,
            "category": summary.get("category", ""),
            "direction": summary.get("direction", ""),
            "ic_mean": ev.get("ic", {}).get("mean"),
            "spread": ev.get("decile_returns", {}).get("spread", {}).get("ret"),
            "dir_consistent": ev.get("ic", {}).get("direction_consistent_share"),
            "version": ev.get("version"),
            "frequency": ev.get("frequency"),
            "run_at": run_at,
            "_sort": sort_key,
        })
    return sorted(rows, key=lambda r: r["_sort"], reverse=True)


@app.command("list")
def list_factors() -> None:
    """列出已保存因子与最近运行摘要（results_dir/*/summary.json）。"""
    rows = collect_result_rows()
    if not rows:
        console.print("暂无因子结果（先运行 factorlab run）")
        return
    for row in rows:
        dcs = row["dir_consistent"]
        console.print(f"{row['name']} | {row['category']} | dir={row['direction']} "
                      f"| dir_consistent={dcs if dcs is not None else '—'} "
                      f"| ic={row['ic_mean']} | spread={row['spread']} | {row['run_at']} "
                      f"| freq={row['frequency'] or '—'}")
    # R30 D1=B：spread v2 正值口径；历史 v1 产物（无 version 键）单独注记，不重算
    console.print("提示: spread=(g9−g0)×dir（g9=signal 最高档；"
                  "正值=与声明方向一致，正=好；判有效性看 ic）")
    # R30 D4：方向一致率口径（按声明方向；raw 正 IC 占比仍在 ic.sign_consistent）
    console.print("提示: dir_consistent=方向一致率（dir=1：IC>0 期占比；"
                  "dir=-1：IC<0 期占比）——按声明方向标注；raw 口径见 ic.sign_consistent")
    legacy = sum(1 for row in rows if row["version"] != 2)
    if legacy:
        console.print(f"注: 含历史 v1 产物 {legacy} 条——spread=(g0−g9)×dir，"
                      "负值=与声明方向一致；未按 v2 重算")


@app.command("show")
def show_factor(name: str) -> None:
    """查看单因子完整摘要（spec 原文/评估/分层回测）。"""
    from factorlab.adapters import results_fs
    summary_path = results_fs.summary_path(settings.results_dir, name)
    if not summary_path.exists():
        console.print(f"错误: 因子 {name} 不存在（{settings.results_dir / name}）")
        raise typer.Exit(code=1)
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        console.print(f"错误: 因子 {name} 的 summary.json 读取失败: {exc}")
        raise typer.Exit(code=1) from exc

    def _warn_degenerate(ev: dict, prefix: str = "") -> None:
        # R03-I3：空档（mean_ret 非有限）→ 显著提示，避免 spread=nan 被读成"无分层效应"
        degenerate = degenerate_decile_groups(ev.get("decile_returns") or {})
        if degenerate:
            console.print(f"警告: {prefix}十分位组 {degenerate}（0=最小 signal）全期无有效"
                          "收益——信号重并列/离散，spread/单调性不可用；建议降低分组数"
                          "或改用其他评估口径")

    def _print_dir_consistent(ev_: dict, prefix: str = "") -> None:
        # R30 D4：方向一致率按声明方向标注；raw 正 IC 占比仍在 ic.sign_consistent
        ic_ = ev_.get("ic") or {}
        dcs = ic_.get("direction_consistent_share")
        direction = summary.get("direction")
        if dcs is None:
            console.print(f"{prefix}方向一致率: —（历史产物无该字段）")
            return
        console.print(f"{prefix}方向一致率: {dcs}（dir={direction}：IC×dir>0 期占比；"
                      f"raw sign_consistent={ic_.get('sign_consistent')}）")

    console.print(f"=== {name} ===")
    console.print(f"spec: {summary.get('spec_yaml', '')}")
    console.print(f"universe: {summary.get('universe_count')} 只 | "
                  f"{summary.get('date_start')} ~ {summary.get('date_end')} | "
                  f"rows={summary.get('panel_rows')} | null_ratio={summary.get('signal_null_ratio')}")
    ev = summary.get('evaluation', {})
    per_outputs = ev.get("outputs") if isinstance(ev, dict) and isinstance(ev.get("outputs"), dict) else None
    # R30 D1=B：按 version 渲染口径行——v2 正值读法；历史（无 version ≡ v1）不重算
    versions = ({ev_o.get("version") for ev_o in per_outputs.values()}
                if per_outputs else {ev.get("version")})
    if versions == {2}:
        console.print("评估口径: v2（spread 正值=与声明方向一致，正=好）")
    else:
        console.print("评估口径: v1（历史产物：spread 负值=与声明方向一致；未按 v2 重算）")
    # D9（R30 Task 13）：按 frequency 渲染评估期语义（历史产物无该键 ≡ weekly 旧口径）
    freqs = ({ev_o.get("frequency") for ev_o in per_outputs.values()}
             if per_outputs else {ev.get("frequency")})
    freqs.discard(None)
    freq = next(iter(freqs)) if len(freqs) == 1 else None
    if freq == "daily":
        console.print("评估频率: daily（逐日截面/每日调仓/1 日 forward）")
    elif freq == "weekly":
        console.print("评估频率: weekly（ISO 周对齐；目标=spec.target）")
    else:
        console.print("评估频率: —（历史产物未记录（≡ weekly 旧口径）或逐输出不一致）")
    if per_outputs:
        # 多输出：逐输出块（缺键 None/无 → 显示语义字段，不崩）
        for o, ev_o in per_outputs.items():
            console.print(f"输出: {o}")
            _warn_degenerate(ev_o, prefix=f"输出 {o} ")
            console.print(f"  IC: {ev_o.get('ic')}")
            _print_dir_consistent(ev_o, prefix="  ")
            console.print(f"  十分位 spread: {ev_o.get('decile_returns', {}).get('spread')}")
            console.print(f"  换手: {ev_o.get('turnover')} | 覆盖: {ev_o.get('coverage')}")
            console.print(f"  分层回测: {ev_o.get('layered_backtest', {}).get('summary', '无')}")
        return
    _warn_degenerate(ev)
    console.print(f"IC: {ev.get('ic')}")
    _print_dir_consistent(ev)
    console.print(f"十分位 spread: {ev.get('decile_returns', {}).get('spread')}")
    console.print(f"换手: {ev.get('turnover')} | 覆盖: {ev.get('coverage')}")
    console.print(f"评估: ic={summary.get('evaluation', {}).get('ic')}")
    console.print(f"分层回测: {summary.get('evaluation', {}).get('layered_backtest', {}).get('summary', '无')}")


@catalog_app.command("dump")
def catalog_dump(out: Path | None = typer.Option(None, "--out",
                                                 help="输出文件路径（缺省打 stdout）")) -> None:
    """机器可读目录 JSON（schema 元数据同源生成——写因子的 AI 开写前阅读）。"""
    payload = catalog_json()
    if out is None:
        typer.echo(payload)  # 原样输出：rich console 会折行破坏 JSON
    else:
        out.write_text(payload, encoding="utf-8")
        console.print(f"catalog JSON 已写入 {out}")


@catalog_app.command("docs")
def catalog_docs(out: Path | None = typer.Option(None, "--out",
                                                 help="输出文件路径（缺省打 stdout）")) -> None:
    """目录正文 markdown（与 `knowledge/contracts/catalog.md` 同源生成——活文档防陈旧）。"""
    payload = render_catalog_markdown()
    if out is None:
        typer.echo(payload)
    else:
        out.write_text(payload, encoding="utf-8")
        console.print(f"catalog 正文已写入 {out}")


ref_app = typer.Typer(no_args_is_help=True)
app.add_typer(ref_app, name="ref")


@ref_app.command("list")
def ref_list(scales: str | None = typer.Option(
        None, "--scales", help="只看某组（daily|minute）；缺省打印全部组")) -> None:
    """参考库成员清单（D10；读 `$QUANTRESEARCH_ROOT/factor/_reference.yaml`）。

    用法: factorlab ref list [--scales daily]
    """
    from factorlab.app.analysis.reference import load_reference, default_reference_path
    try:
        ref = load_reference()
    except (FileNotFoundError, ValueError) as e:
        console.print(f"错误: {e}", style="red")
        raise typer.Exit(code=1)
    groups = [scales] if scales else list(ref)
    if scales and scales not in ref:
        console.print(f"错误: 未知 scales {scales}（支持 {list(ref)}）", style="red")
        raise typer.Exit(code=1)
    console.print(f"参考库（{default_reference_path()}；库级分析默认 against=reference）:")
    for s in groups:
        entries = ref[s]
        console.print(f"scales: {s}（{len(entries)} 只）")
        for e in entries:
            extra = (f"  corr_max={e.entry_corr_max}  resic_t={e.entry_resic_t}"
                     if e.entry_corr_max is not None or e.entry_resic_t is not None
                     else "")
            console.print(f"  - {e.name:<28}[{e.style}] 加入 {e.added}{extra}")
            console.print(f"      理由: {e.reason}")


@app.command("corr")
def corr_factors(names: list[str] = typer.Argument(None),
                 against: str | None = typer.Option(
                     None, "--against",
                     help="对照集（D10）：reference（参考库 daily 组）|all（扫全库）|逗号分隔名单；"
                          "缺省=仅 names")) -> None:
    """因子两两相关性：周度横截面秩相关均值 + 全局 Pearson。

    用法: factorlab corr <name1> <name2> [<name3>...] [--against reference]
    `--against reference`：names 与参考库（`$QUANTRESEARCH_ROOT/factor/_reference.yaml` daily 组）
    的并集自成矩阵（names 可省略=库内自相关矩阵）——只读库清单，不扫全库、不跨 scales。
    """
    if against is None and len(names or []) < 2:
        console.print("错误: 至少需要 2 个因子（或用 --against reference|all|<名单>）", style="red")
        raise typer.Exit(code=1)
    from factorlab.app.analysis.correlation import factor_correlation
    try:
        m = factor_correlation(names, settings.results_dir, against=against)
    except FileNotFoundError as e:
        console.print(f"错误: {e}", style="red")
        raise typer.Exit(code=1)
    except ValueError as e:
        console.print(f"错误: {e}", style="red")
        raise typer.Exit(code=1)
    console.print(m.to_pandas().to_string(index=False))


@app.command("svd")
def svd_factors(names: list[str] = typer.Argument(None),
                weeks: int = typer.Option(15, "--weeks", help="抽样交易周数（内存护栏，默认 15）"),
                all_: bool = typer.Option(False, "--all",
                                          help="显式扫全库因子（D10 前旧默认；缺省=参考库 daily 组）")) -> None:
    """因子库 SVD 分解：奇异值谱 + 主成分载荷（因子结构/有效维度分析）。

    用法: factorlab svd [name1 name2 ...] [--weeks 15] [--all]
    缺省 names = 参考库 daily 组（D10；只读 `_reference.yaml`）；`--all` = 全部有
    panel 的因子（排除验证目录——D10 前的旧默认，显式 opt-in）。
    """
    from factorlab.app.analysis.correlation import factor_svd
    from factorlab.app.analysis.reference import reference_names
    results_dir = settings.results_dir
    source = "names"
    if not names:
        if all_:
            skip = {"acceptance", "demo_vol_skew", "m4b_smoke"}
            names = sorted(p.parent.name for p in results_dir.glob("*/panel.parquet")
                           if p.parent.name not in skip)
            source = "全库"
        else:
            try:
                names = reference_names("daily")
            except (FileNotFoundError, ValueError) as e:
                console.print(f"错误: 参考库不可用（{e}）；可用 --all 扫全库", style="red")
                raise typer.Exit(code=1)
            source = "参考库 daily"
    if len(names) < 2:
        console.print("错误: 至少需要 2 个因子", style="red")
        raise typer.Exit(code=1)
    try:
        r = factor_svd(names, results_dir, sample_weeks=weeks)
    except FileNotFoundError as e:
        console.print(f"错误: {e}", style="red")
        raise typer.Exit(code=1)
    console.print(f"SVD（{len(names)} 因子，{source}，抽样 {weeks} 周）：")
    console.print("奇异值谱：")
    for k, (sv, cum) in enumerate(zip(r["singular_values"], r["cum_explained"]), 1):
        console.print(f"  PC{k:<2} 奇异值 {sv:8.3f}  累计解释 {cum * 100:5.1f}%")
    console.print("主成分载荷（每 PC 取 |载荷| 最大 5 因子）：")
    loadings = r["loadings"]
    for k in range(len(r["singular_values"])):
        pc = f"PC{k + 1}"
        ranked = sorted(loadings, key=lambda x: abs(x[pc]), reverse=True)[:5]
        parts = ", ".join(f"{x['name']}({x[pc]:+.2f})" for x in ranked)
        console.print(f"  {pc}: {parts}")


@app.command("resic")
def resic_factors(
    names: list[str] = typer.Argument(None,
        help="因子名（results/<name>/panel.parquet）；互评模式 ≥2，--target 模式 ≥1"),
    target: str | None = typer.Option(None, "--target",
        help="目标因子（对基准组求正交化残差 IC）；缺省=组内轮流互评"),
    min_stocks: int = typer.Option(None, "--min-stocks", min=3,
        help="每周最少股票数（缺省 30；自动与基准数+2 取大）"),
    against: str | None = typer.Option(None, "--against",
        help="D10 增量信息模式：reference（参考库 daily 组）|all|逗号分隔名单；"
             "候选=names（或 --target），输出 corr_max/mean、r2_lib、resIC、retention、verdict"),
) -> None:
    """横截面联合诊断：整组联合回归 R² + 每因子正交化残差 IC（resIC）。

    用法: factorlab resic <name1> <name2> [<name3>...] [--target <名>] [--against reference]
    组内互评（缺省）：每个因子轮流当候选、其余因子当基准，输出每因子的
    resIC（候选对基准 OLS 残差的周频 rankIC——剔除与基准重叠后的净新增
    预测力）与整组联合回归 R²。--target <名>：只评估该候选相对显式基准组。
    --against reference|all|<名单>（D10）：候选对**参考库**做增量信息评估
    （rank 残差回归 + verdict：可加入/观察/冗余）；候选 ∈ 基准报错。
    近共线（相关≈0.9999）会放大 resIC 数值噪声——建议先跑 factorlab corr / svd。
    """
    from factorlab.app.analysis.correlation import resolve_against
    from factorlab.app.analysis.cross_section import (
        incremental_diagnostics, joint_diagnostics)

    if against is not None:
        candidates = [target] if target else list(names or [])
        if not candidates:
            console.print("错误: --against 模式需要候选（位置参数或 --target）", style="red")
            raise typer.Exit(code=1)
        try:
            base = [b for b in resolve_against(against, settings.results_dir)
                    if b not in candidates]
            r = incremental_diagnostics(candidates, settings.results_dir, base=base,
                                        min_stocks=min_stocks or 30)
        except (ValueError, FileNotFoundError) as exc:
            console.print(f"错误: {exc}", style="red")
            raise typer.Exit(code=1)

        def _num(x: float) -> str:
            return "nan" if x != x else f"{x:.4f}"

        console.print(f"库外增量信息（候选对参考库，基准 {len(r['base'])} 只："
                      f"{', '.join(r['base'])}）:")
        console.print(f"  {'因子':<10}{'corr_max':>10}{'corr_mean':>11}{'r2_lib':>9}"
                      f"{'resIC':>10}{'t值':>8}{'retention':>11}{'有效周':>7}{'verdict':>8}")
        for f in r["candidates"]:
            console.print(
                f"  {f['name']:<10}{_num(abs(f['corr_max'])):>10}{_num(f['corr_mean']):>11}"
                f"{_num(f['r2_lib']):>9}{_num(f['resic_mean']):>10}{_num(f['resic_t']):>8}"
                f"{_num(f['retention']):>11}{f['n_weeks']:>7}{f['verdict']:>8}")
        return

    try:
        r = joint_diagnostics(names, settings.results_dir, target=target,
                              min_stocks=min_stocks or 30)
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"错误: {exc}", style="red")
        raise typer.Exit(code=1)

    def _num(x: float) -> str:
        return "nan" if x != x else f"{x:.4f}"

    group_names = names if target is None else names
    head = "组联合回归" if target is None else "基准组回归"
    g = r["group"]
    obs = "nan" if g["obs"] != g["obs"] else f"{g['obs']:.0f}"
    console.print(f"{head}（fwd~{' '.join(group_names)}, {g['n_weeks']} 周）: "
                  f"R² = {_num(g['mean'])} （周均样本 {obs}）")
    console.print("正交化残差 IC（每周 F~基准 OLS 残差 vs fwd）:")
    console.print(f"  {'因子':<10}{'resIC':>10}{'t值':>10}{'有效周':>7}{'被基准解释R²':>13}")
    for f in r["factors"]:
        base = "" if target is None else f"   基准: {', '.join(f['base'])}"
        console.print(
            f"  {f['name']:<10}{_num(f['mean']):>10}{_num(f['t_stat']):>10}"
            f"{f['n_weeks']:>7}{_num(f['r2_absorbed']):>13}{base}")


@app.command("serve")
def serve(port: int = 8000, host: str = "127.0.0.1") -> None:
    """启动 Web 可视化（只读 results_dir）。"""
    import uvicorn

    from factorlab.surfaces.web.app import create_app
    uvicorn.run(create_app(settings.results_dir), host=host, port=port)
