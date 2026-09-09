import datetime
import json
import re
from pathlib import Path

import typer
from rich.console import Console

from factorlab import __version__
from factorlab.catalog import catalog_json, render_catalog_markdown
from factorlab.config import settings
from factorlab.data.fetcher import TeaJoinClient
from factorlab.data.platform_db import PlatformDB
from factorlab.data.rebuild import RebuildScope, build_final_db, rebuild_all
from factorlab.data.refresh import refresh, refresh_indexes
from factorlab.data.verify import verify_all
from factorlab.factor.errors import FactorDSLError
from factorlab.factor.ast_gate import validate_formula
from factorlab.ops import plugins, registry
from factorlab.spec import load_spec


app = typer.Typer(no_args_is_help=True)
console = Console()
op_app = typer.Typer(no_args_is_help=True)
app.add_typer(op_app, name="op")


@app.callback()
def main() -> None:
    """factorlab 因子 DSL 计算平台"""


@app.command()
def version() -> None:
    typer.echo(__version__)


@app.command()
def lint(spec_path: Path) -> None:
    """校验 YAML Spec 与 factor formula AST。"""
    try:
        spec = load_spec(spec_path)
        formulas = [spec.formula] if spec.formula is not None else [item.formula for item in spec.factors or []]
        for formula in formulas:
            validate_formula(formula)
    except (ValueError, FactorDSLError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(f"OK {spec.name}")


@op_app.command("list")
def op_list() -> None:
    plugins.discover_plugins(settings.plugin_dir)
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
    op = registry.get_op(name)
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


data_app = typer.Typer(no_args_is_help=True)
app.add_typer(data_app, name="data")
catalog_app = typer.Typer(no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")


def _staging_db() -> PlatformDB:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return PlatformDB(settings.data_dir / "rebuild_staging.duckdb")


def _final_db() -> PlatformDB:
    return PlatformDB(settings.data_dir / "factorlab.duckdb")


def _client() -> TeaJoinClient:
    return TeaJoinClient(token=settings.teajoin_token, base_url=settings.teajoin_base_url)


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
                                          help="日期分块（交易日/块；缺省=单块整段跑）"),
    warmup_days: int | None = typer.Option(None, "--warmup-days", min=0,
                                           help="TS 窗口预热天数（缺省=按公式自动提取窗口+20）"),
) -> None:
    """计算因子并评估（平台库）。--backtest 默认产出分层回测；--no-backtest 关闭（快速评估）。
    --groups 分层档数（>=2）。--set k=v 覆盖 spec.params 生成变体（results 独立目录）。
    --universe 默认 FACTORLAB_DEFAULT_UNIVERSE。"""
    from factorlab.engine.compute import RunContext, run_factor
    from factorlab.engine.minute import run_factor_minute
    from factorlab.eval.alignment import align_weekly
    from factorlab.eval.layered import layered_backtest
    from factorlab.eval.rust_ic import evaluate_factor_weekly

    overrides = {}
    for kv in set_params or []:
        key, _, value = kv.partition("=")
        if not key or not value:
            raise typer.BadParameter(f"--set 格式应为 k=v: {kv}")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise typer.BadParameter(f"--set 值含非法字符（仅字母数字_.-）: {value}")
        overrides[key] = _parse_param_value(value)
    try:
        spec = load_spec(spec_path)
    except FileNotFoundError as exc:
        console.print(f"错误: {exc}")
        raise typer.Exit(code=1) from exc
    variant = spec.name
    if overrides:
        spec.params = {**spec.params, **overrides}
        variant = spec.name + "_" + "_".join(f"{k}{v}" for k, v in overrides.items())
    ctx = RunContext(
        db_path=settings.platform_db,
        output_dir=output_dir or (settings.results_dir / variant),
        universe_override=universe or settings.default_universe,
        float32=float32,
        chunk_days=chunk_days,
        warmup_days=warmup_days,
    )
    # load_daily 在调用时读取 settings.default_max_memory——临时覆盖并在结束后恢复
    original_memory = settings.default_max_memory
    settings.default_max_memory = max_memory
    # W5 分派：分钟面 spec（interface: bars_1m）走分钟链 run_factor_minute（折日
    # 面板与日频同列契约，下方评估/分层回测零改动复用）；日频 spec 走原 run_factor。
    run_impl = run_factor_minute if spec.interface == "bars_1m" else run_factor
    try:
        result = run_impl(spec, ctx)
        # 周频对齐面板：评估与分层回测的实际输入（对齐一次，复用给评估——
        # 千万行面板重复对齐在低内存机器上 segfault）
        weekly = align_weekly(result.panel)
        # 多输出（outputs != ["signal"]）：逐输出独立评估（dsl-shape §3.2 收口）——
        # outputs == ["signal"]（legacy）保持顶层结构逐键不变；多输出 → 顶层仅
        # {"outputs": {o: 评估 dict}}；outputs 含字面 "signal" 是一等输出（无隐式主信号）
        outputs = list(spec.outputs) if spec.outputs is not None else ["signal"]
        if outputs == ["signal"]:
            evaluation = evaluate_factor_weekly(result.panel, spec.name, spec.direction,
                                                target=spec.target, weekly=weekly)
            if backtest:
                bt = layered_backtest(weekly, spec.direction, n_groups=groups,
                                      forward_col=spec.target)
                evaluation["layered_backtest"] = bt
                if bt.get("empty_groups"):
                    console.print(f"提示: 档位 {bt['empty_groups']} 全期无股票——universe 过小或 --groups 过大")
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
                                          forward_col=spec.target)
                    ev_o["layered_backtest"] = bt
                    if bt.get("empty_groups"):
                        console.print(f"提示: 输出 {o} 档位 {bt['empty_groups']} "
                                      "全期无股票——universe 过小或 --groups 过大")
                evaluation["outputs"][o] = ev_o
    except (ValueError, FileNotFoundError, FactorDSLError) as exc:
        console.print(f"错误: {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        settings.default_max_memory = original_memory
    # run_factor 已落盘 panel.parquet/summary.json（无 evaluation）；CLI 追加评估结果并重写
    result.summary["evaluation"] = evaluation
    weekly.write_parquet(ctx.output_dir / "weekly.parquet")  # 周频对齐面板（替代原日频冗余）
    (ctx.output_dir / "summary.json").write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if outputs == ["signal"]:
        ic = evaluation.get("ic", {})
        console.print(f"{variant}: n_weeks={evaluation.get('n_weeks')} "
                      f"ic_mean={ic.get('mean')} spread={evaluation.get('decile_returns', {}).get('spread', {}).get('ret')}")
    else:
        for o, ev_o in evaluation["outputs"].items():  # 逐输出一行（legacy 行同型）
            ic = ev_o.get("ic", {})
            console.print(f"{variant}__{o}: n_weeks={ev_o.get('n_weeks')} "
                          f"ic_mean={ic.get('mean')} spread={ev_o.get('decile_returns', {}).get('spread', {}).get('ret')}")


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


@app.command("list")
def list_factors() -> None:
    """列出已保存因子与最近运行摘要（results_dir/*/summary.json）。"""
    results_dir = settings.results_dir
    if not results_dir.is_dir():
        console.print("暂无因子结果（先运行 factorlab run）")
        return
    rows = []
    for summary_path in sorted(results_dir.glob("*/summary.json")):
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
            "run_at": run_at,
            "_sort": sort_key,
        })
    if not rows:
        console.print("暂无因子结果（先运行 factorlab run）")
        return
    for row in sorted(rows, key=lambda r: r["_sort"], reverse=True):
        console.print(f"{row['name']} | {row['category']} | dir={row['direction']} "
                      f"| ic={row['ic_mean']} | spread={row['spread']} | {row['run_at']}")


@app.command("show")
def show_factor(name: str) -> None:
    """查看单因子完整摘要（spec 原文/评估/分层回测）。"""
    summary_path = settings.results_dir / name / "summary.json"
    if not summary_path.exists():
        console.print(f"错误: 因子 {name} 不存在（{settings.results_dir / name}）")
        raise typer.Exit(code=1)
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        console.print(f"错误: 因子 {name} 的 summary.json 读取失败: {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"=== {name} ===")
    console.print(f"spec: {summary.get('spec_yaml', '')}")
    console.print(f"universe: {summary.get('universe_count')} 只 | "
                  f"{summary.get('date_start')} ~ {summary.get('date_end')} | "
                  f"rows={summary.get('panel_rows')} | null_ratio={summary.get('signal_null_ratio')}")
    ev = summary.get('evaluation', {})
    per_outputs = ev.get("outputs") if isinstance(ev, dict) and isinstance(ev.get("outputs"), dict) else None
    if per_outputs:
        # 多输出：逐输出块（缺键 None/无 → 显示语义字段，不崩）
        for o, ev_o in per_outputs.items():
            console.print(f"输出: {o}")
            console.print(f"  IC: {ev_o.get('ic')}")
            console.print(f"  十分位 spread: {ev_o.get('decile_returns', {}).get('spread')}")
            console.print(f"  换手: {ev_o.get('turnover')} | 覆盖: {ev_o.get('coverage')}")
            console.print(f"  分层回测: {ev_o.get('layered_backtest', {}).get('summary', '无')}")
        return
    console.print(f"IC: {ev.get('ic')}")
    console.print(f"十分位 spread: {ev.get('decile_returns', {}).get('spread')}")
    console.print(f"换手: {ev.get('turnover')} | 覆盖: {ev.get('coverage')}")
    console.print(f"评估: ic={summary.get('evaluation', {}).get('ic')}")
    console.print(f"分层回测: {summary.get('evaluation', {}).get('layered_backtest', {}).get('summary', '无')}")


@data_app.command("rebuild")
def data_rebuild(start: str = "20000104", end: str | None = None, resume: bool = True) -> None:
    """teajoin 全量重建平台数据（暂存库 → 稀疏剔除 → 最终库）。"""
    if not settings.teajoin_token:
        console.print("错误: 未配置 FACTORLAB_TEAJOIN_TOKEN（.env）")
        raise typer.Exit(code=1)
    staging = _staging_db()
    report = rebuild_all(staging, _client(), scope=RebuildScope(start=start, end=end), resume=resume)
    console.print(f"rebuild 完成: {report['tables']}")
    final = build_final_db(staging, settings.data_dir / "factorlab.duckdb")
    console.print(f"稀疏剔除: {final['excluded_fields']}")
    console.print(f"最终库表: {final['tables']}")


@data_app.command("refresh")
def data_refresh() -> None:
    """增量拉取到最新交易日。"""
    if not settings.teajoin_token:
        console.print("错误: 未配置 FACTORLAB_TEAJOIN_TOKEN（.env）")
        raise typer.Exit(code=1)
    report = refresh(_final_db(), _client())
    console.print(f"refresh 完成: {report}")


@data_app.command("update")
def data_update() -> None:
    """一键更新：行情增量 + 指数增量 + 自动验证 + 报告（手动触发）。"""
    if not settings.teajoin_token:
        console.print("错误: 未配置 FACTORLAB_TEAJOIN_TOKEN（.env）")
        raise typer.Exit(code=1)
    report = refresh(_final_db(), _client())
    index_report = refresh_indexes(_final_db(), _client())
    verify = verify_all(_final_db())
    failures = [
        (t, info["failed"])
        for t, info in report.get("tables", {}).items()
        if info.get("failed")
    ]
    index_failures = [
        (t, info["failed"])
        for t, info in index_report.items()
        if info.get("failed")
    ]
    console.print(f"行情增量: {report['tables']}")
    console.print(f"指数增量: {index_report}")
    console.print(f"verify: integrity 规则 "
                  f"{sum(1 for r in verify['integrity'].values() for x in r.values() if x.get('passed'))}"
                  f"/{sum(len(r) for r in verify['integrity'].values())} 通过")
    if failures or index_failures:
        console.print(f"⚠ 失败项: {failures + index_failures}（下次 update 自动重试）")
    else:
        console.print("更新完成，无失败")


@data_app.command("verify")
def data_verify(compare: Path | None = None) -> None:
    """完整性自检 + 稀疏摘要 + 可选抽样对拍。"""
    report = verify_all(_final_db(), ref_db=compare)
    console.print(report)


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
    """目录正文 markdown（与 docs/catalog.md 同源生成——活文档防陈旧）。"""
    payload = render_catalog_markdown()
    if out is None:
        typer.echo(payload)
    else:
        out.write_text(payload, encoding="utf-8")
        console.print(f"catalog 正文已写入 {out}")


@app.command("corr")
def corr_factors(names: list[str] = typer.Argument(...)) -> None:
    """因子两两相关性：周度横截面秩相关均值 + 全局 Pearson。

    用法: factorlab corr <name1> <name2> [<name3>...]
    """
    if len(names) < 2:
        console.print("错误: 至少需要 2 个因子", style="red")
        raise typer.Exit(code=1)
    from factorlab.eval.correlation import factor_correlation
    try:
        m = factor_correlation(names, settings.results_dir)
    except FileNotFoundError as e:
        console.print(f"错误: {e}", style="red")
        raise typer.Exit(code=1)
    console.print(m.to_pandas().to_string(index=False))


@app.command("svd")
def svd_factors(names: list[str] = typer.Argument(None),
                weeks: int = typer.Option(15, "--weeks", help="抽样交易周数（内存护栏，默认 15）")) -> None:
    """因子库 SVD 分解：奇异值谱 + 主成分载荷（因子结构/有效维度分析）。

    用法: factorlab svd [name1 name2 ...] [--weeks 15]
    缺省 names = 全部有 panel 的因子（排除验证目录）。
    """
    from factorlab.eval.correlation import factor_svd
    results_dir = settings.results_dir
    if not names:
        skip = {"acceptance", "demo_vol_skew", "m4b_smoke"}
        names = sorted(p.parent.name for p in results_dir.glob("*/panel.parquet")
                       if p.parent.name not in skip)
    if len(names) < 2:
        console.print("错误: 至少需要 2 个因子", style="red")
        raise typer.Exit(code=1)
    try:
        r = factor_svd(names, results_dir, sample_weeks=weeks)
    except FileNotFoundError as e:
        console.print(f"错误: {e}", style="red")
        raise typer.Exit(code=1)
    console.print(f"SVD（{len(names)} 因子，抽样 {weeks} 周）：")
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
    names: list[str] = typer.Argument(...,
        help="因子名（results/<name>/panel.parquet）；互评模式 ≥2，--target 模式 ≥1"),
    target: str | None = typer.Option(None, "--target",
        help="目标因子（对基准组求正交化残差 IC）；缺省=组内轮流互评"),
    min_stocks: int = typer.Option(None, "--min-stocks", min=3,
        help="每周最少股票数（缺省 30；自动与基准数+2 取大）"),
) -> None:
    """横截面联合诊断：整组联合回归 R² + 每因子正交化残差 IC（resIC）。

    用法: factorlab resic <name1> <name2> [<name3>...] [--target <名>]
    组内互评（缺省）：每个因子轮流当候选、其余因子当基准，输出每因子的
    resIC（候选对基准 OLS 残差的周频 rankIC——剔除与基准重叠后的净新增
    预测力）与整组联合回归 R²。--target <名>：只评估该候选相对显式基准组。
    近共线（相关≈0.9999）会放大 resIC 数值噪声——建议先跑 factorlab corr / svd。
    """
    from factorlab.eval.cross_section import joint_diagnostics

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

    from factorlab.web.app import create_app
    uvicorn.run(create_app(settings.results_dir), host=host, port=port)
