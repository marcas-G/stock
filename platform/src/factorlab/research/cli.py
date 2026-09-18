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
        params=(registry.ParamSpec("pretty", kind="bool", help="缩进 JSON（人读）"),),
        defaults={"pretty": False},
        description="版本",
        examples=("factorlab research version",),
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
    pretty: bool = typer.Option(False, "--pretty", help="缩进 JSON（人读）"),
) -> None:
    """版本。"""
    _dispatch_typer("version", argparse.Namespace(pretty=pretty), pretty)


@research_app.callback(invoke_without_command=True)
def _research_main(ctx: typer.Context) -> None:
    """裸调用 `factorlab research` → USAGE 信封（与单 JSON 契约一致）。"""
    if ctx.invoked_subcommand is None:
        env = envelope.fail("", "USAGE", "缺少子命令",
                            hint="factorlab research describe --json")
        raise typer.Exit(code=envelope.emit(env))
