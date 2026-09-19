"""`factorlab research` 命令组（R31）：typer 薄壳挂进现有 CLI。

命令元数据与 handler 均在 registry 单点注册（`describe` 由此自动生成）；
本模块只做参数面与委托，stdout 一律经 `envelope.emit` 输出单个 JSON。
"""

from __future__ import annotations

import argparse

import typer

from factorlab import __version__
from factorlab.research import envelope, registry

research_app = typer.Typer(
    no_args_is_help=False,
    help="研究员统一门面（flab）：数据/因子/策略/报告/study，统一 JSON 契约",
)


def _describe_handler(args: argparse.Namespace) -> envelope.Envelope:
    command = getattr(args, "command", None)
    if command:
        spec = registry.COMMANDS.get(command)
        if spec is None:
            return envelope.fail(
                "describe", "NOT_FOUND", f"未知命令: {command}",
                hint="factorlab research describe --json 查看全部命令")
        return envelope.ok("describe", spec.to_doc())
    return envelope.ok("describe", {
        "commands": {name: spec.to_doc()
                     for name, spec in registry.COMMANDS.items()},
        "exit_codes": dict(envelope.EXIT_CODES),
    })


def _version_handler(args: argparse.Namespace) -> envelope.Envelope:
    return envelope.ok("version", {"version": __version__})


registry.register(
    registry.CommandSpec(
        name="describe",
        params=(
            registry.ParamSpec("command", kind="str",
                               help="只描述一个命令（缺省=全部命令目录）"),
            registry.ParamSpec("json", kind="bool",
                               help="输出单个 JSON 信封（当前即默认口径）"),
            registry.ParamSpec("pretty", kind="bool", help="缩进 JSON（人读）"),
        ),
        defaults={"command": None, "json": True, "pretty": False},
        description="自描述：命令目录/单命令 schema/错误码表（registry 单点）",
        examples=(
            "factorlab research describe --json",
            "factorlab research describe --command describe --pretty",
        ),
        output_schema={
            "type": "object",
            "required": ["commands", "exit_codes"],
            "properties": {
                "commands": {"type": "object",
                             "description": "命令名 → {params,defaults,examples,output_schema}"},
                "exit_codes": {"type": "object",
                               "description": "错误码 → 进程退出码"},
            },
        },
    ),
    _describe_handler,
)

registry.register(
    registry.CommandSpec(
        name="version",
        params=(
            registry.ParamSpec("json", kind="bool", help="输出单个 JSON 信封（默认）"),
            registry.ParamSpec("pretty", kind="bool", help="缩进 JSON（人读）"),
        ),
        defaults={"json": True, "pretty": False},
        description="版本",
        examples=("factorlab research version", "factorlab research version --pretty"),
        output_schema={
            "type": "object",
            "required": ["version"],
            "properties": {"version": {"type": "string"}},
        },
    ),
    _version_handler,
)


def _dispatch_typer(name: str, args: argparse.Namespace, pretty: bool) -> None:
    env = registry.get_handler(name)(args)
    raise typer.Exit(code=envelope.emit(env, pretty=pretty))


def _maybe_group_help(ctx: typer.Context, argv: list[str]) -> None:
    """组级裸 `--help`（组命令 add_help_option=False；叶子命令参数帮助走
    `registry.dispatch` 的 `--help` 透传）。"""
    if argv in (["--help"], ["-h"]):
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)


def _maybe_subgroup_help(argv: list[str]) -> None:
    """`flab factor ref|op --help`：列出该二级组命令（叶子参数 help 透传）。"""
    if (len(argv) == 2 and argv[0] in _FACTOR_SUBGROUPS
            and argv[1] in ("-h", "--help")):
        prefix = f"factor.{argv[0]}."
        names = sorted(n[len(prefix):] for n in registry.COMMANDS
                       if n.startswith(prefix))
        typer.echo(f"factor {argv[0]} 子命令：{', '.join(names)}")
        typer.echo(f"用法：flab factor {argv[0]} <子命令> [参数]"
                   f"（`flab factor {argv[0]} <子命令> --help` 看参数）")
        raise typer.Exit(code=0)


@research_app.command("describe")
def describe_cmd(
    command: str | None = typer.Option(None, "--command",
                                       help="只描述一个命令（缺省=全部命令目录）"),
    json_out: bool = typer.Option(True, "--json/--no-json",
                                  help="输出单个 JSON 信封（当前即默认口径）"),
    pretty: bool = typer.Option(False, "--pretty", help="缩进 JSON（人读）"),
) -> None:
    """自描述：命令目录/单命令 schema/错误码表（registry 单点）。"""
    _dispatch_typer(
        "describe",
        argparse.Namespace(command=command, json=json_out, pretty=pretty),
        pretty)


@research_app.command("version")
def version_cmd(
    json_out: bool = typer.Option(True, "--json/--no-json",
                                  help="输出单个 JSON 信封（当前即默认口径）"),
    pretty: bool = typer.Option(False, "--pretty", help="缩进 JSON（人读）"),
) -> None:
    """版本。"""
    _dispatch_typer("version", argparse.Namespace(json=json_out, pretty=pretty), pretty)


@research_app.command(
    "data",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def data_cmd(ctx: typer.Context) -> None:
    """data 组：全库读取（子命令经 registry 单点，`describe --json` 查看）。"""
    argv = list(ctx.args)
    _maybe_group_help(ctx, argv)
    if not argv:
        raise typer.Exit(code=envelope.emit(envelope.fail(
            "data", "USAGE", "缺少 data 子命令",
            hint="factorlab research describe --json 查看命令目录")))
    raise typer.Exit(code=registry.dispatch([f"data.{argv[0]}", *argv[1:]]))


# factor 组的二级命令（ref/op）→ registry 名映射
_FACTOR_SUBGROUPS = ("ref", "op")


@research_app.command(
    "factor",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def factor_cmd(ctx: typer.Context) -> None:
    """factor 组：lint/run/list/show/export/corr/resic/svd/ref/op/catalog/admit。"""
    argv = list(ctx.args)
    _maybe_group_help(ctx, argv)
    _maybe_subgroup_help(argv)
    if not argv:
        raise typer.Exit(code=envelope.emit(envelope.fail(
            "factor", "USAGE", "缺少 factor 子命令",
            hint="factorlab research describe --json 查看命令目录")))
    if argv[0] in _FACTOR_SUBGROUPS:
        if len(argv) < 2:
            raise typer.Exit(code=envelope.emit(envelope.fail(
                "factor", "USAGE", f"缺少 factor {argv[0]} 子命令",
                hint="factorlab research describe --json 查看命令目录")))
        name, rest = f"factor.{argv[0]}.{argv[1]}", argv[2:]
    else:
        name, rest = f"factor.{argv[0]}", argv[1:]
    raise typer.Exit(code=registry.dispatch([name, *rest]))


@research_app.command(
    "strategy",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def strategy_cmd(ctx: typer.Context) -> None:
    """strategy 组：lint/run/list/show/export/capacity/cost。"""
    argv = list(ctx.args)
    _maybe_group_help(ctx, argv)
    if not argv:
        raise typer.Exit(code=envelope.emit(envelope.fail(
            "strategy", "USAGE", "缺少 strategy 子命令",
            hint="factorlab research describe --json 查看命令目录")))
    raise typer.Exit(code=registry.dispatch([f"strategy.{argv[0]}", *argv[1:]]))


@research_app.command(
    "report",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def report_cmd(ctx: typer.Context) -> None:
    """report 组：list/show/url/serve。"""
    argv = list(ctx.args)
    _maybe_group_help(ctx, argv)
    if not argv:
        raise typer.Exit(code=envelope.emit(envelope.fail(
            "report", "USAGE", "缺少 report 子命令",
            hint="factorlab research describe --json 查看命令目录")))
    raise typer.Exit(code=registry.dispatch([f"report.{argv[0]}", *argv[1:]]))


@research_app.command(
    "study",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def study_cmd(ctx: typer.Context) -> None:
    """study 组：run（一条链）/list（历史记录）。"""
    argv = list(ctx.args)
    _maybe_group_help(ctx, argv)
    if not argv:
        raise typer.Exit(code=envelope.emit(envelope.fail(
            "study", "USAGE", "缺少 study 子命令",
            hint="factorlab research describe --json 查看命令目录")))
    raise typer.Exit(code=registry.dispatch([f"study.{argv[0]}", *argv[1:]]))


@research_app.command("health")
def health_cmd(
    json_out: bool = typer.Option(True, "--json/--no-json",
                                  help="输出单个 JSON 信封（当前即默认口径）"),
    pretty: bool = typer.Option(False, "--pretty", help="缩进 JSON（人读）"),
) -> None:
    """健康一览：CH 连通/内存/磁盘/heavy 闸槽位/数据新鲜度。"""
    _dispatch_typer("health", argparse.Namespace(json=json_out, pretty=pretty),
                    pretty)


@research_app.callback(invoke_without_command=True)
def _research_main(ctx: typer.Context) -> None:
    """裸调用 `factorlab research` → USAGE 信封（与单 JSON 契约一致）。"""
    if ctx.invoked_subcommand is None:
        env = envelope.fail("", "USAGE", "缺少子命令",
                            hint="factorlab research describe --json")
        raise typer.Exit(code=envelope.emit(env))
