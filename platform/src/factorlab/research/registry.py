"""命令注册表（R31 契约层，spec §2/§6）：describe/help 的单点数据源。

只存元数据（参数/默认值/输出 schema/示例）+ handler 引用；业务逻辑在门面模块。
`flab <name>` 经 typer 薄壳委托 handler；Python 侧经 `dispatch(argv)` 执行。
"""

from __future__ import annotations

import argparse
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from factorlab.research import envelope

Handler = Callable[[argparse.Namespace], envelope.Envelope]

# kind → argparse 行为（str 为缺省）
_KINDS = ("str", "int", "float", "bool", "path", "list[str]")


@dataclass(frozen=True)
class ParamSpec:
    """单个参数（describe 展示 + dispatch 解析共用）。

    `positional=True`：按声明序生成 argparse 位置参数（spec §3 的
    `flab factor run <spec.yaml>` / `lint <spec...>` 等命令面带法）；
    list[str] 位置参数 = `nargs="*"`。
    """

    name: str
    kind: str = "str"
    required: bool = False
    help: str = ""
    positional: bool = False

    def to_doc(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind,
                "required": self.required, "help": self.help,
                "positional": self.positional}


@dataclass(frozen=True)
class CommandSpec:
    """命令元数据（registry 单点；describe 逐字段导出）。"""

    name: str
    params: Sequence[ParamSpec | Mapping[str, Any]] = ()
    defaults: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""
    examples: Sequence[str] = ()
    output_schema: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = tuple(
            p if isinstance(p, ParamSpec) else ParamSpec(**dict(p))
            for p in self.params
        )
        object.__setattr__(self, "params", normalized)
        object.__setattr__(self, "defaults", dict(self.defaults))
        object.__setattr__(self, "examples", tuple(self.examples))
        object.__setattr__(self, "output_schema", dict(self.output_schema))

    def to_doc(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "params": [p.to_doc() for p in self.params],
            "defaults": dict(self.defaults),
            "examples": list(self.examples),
            "output_schema": dict(self.output_schema),
        }


# 注册表单点：有序（dict 保序，注册序即 describe 展示序）
COMMANDS: dict[str, CommandSpec] = {}
_HANDLERS: dict[str, Handler] = {}


def register(spec: CommandSpec, handler: Handler) -> None:
    COMMANDS[spec.name] = spec
    _HANDLERS[spec.name] = handler


def get_handler(name: str) -> Handler:
    return _HANDLERS[name]


def _kind_default(kind: str) -> Any:
    return False if kind == "bool" else None


def build_parser(spec: CommandSpec) -> argparse.ArgumentParser:
    """由 CommandSpec 生成 argparse（位置参数按声明序；flags 用连字符）。

    Namespace 属性一律用下划线名；位置参数（spec §3 的命令面写法
    `<spec.yaml>`/`<name>`/`<spec...>`）在选项之前生成。
    """
    parser = argparse.ArgumentParser(
        prog=f"factorlab research {spec.name}", add_help=False)
    for param in spec.params:
        if not param.positional:
            continue
        if param.kind not in _KINDS:
            raise ValueError(f"未知参数类型 {param.kind}: {spec.name}.{param.name}")
        kwargs: dict[str, Any] = {"help": param.help}
        if param.kind == "list[str]":
            kwargs["nargs"] = "*"
            kwargs["default"] = spec.defaults.get(param.name, [])
        else:
            if not param.required:  # required 位置参数不给 default → argparse 必填
                kwargs["default"] = spec.defaults.get(param.name, _kind_default(param.kind))
            if param.kind == "int":
                kwargs["type"] = int
            elif param.kind == "float":
                kwargs["type"] = float
            elif param.kind == "path":
                kwargs["type"] = Path
        parser.add_argument(param.name, **kwargs)
    for param in spec.params:
        if param.positional:
            continue
        if param.kind not in _KINDS:
            raise ValueError(f"未知参数类型 {param.kind}: {spec.name}.{param.name}")
        flag = "--" + param.name.replace("_", "-")
        default = spec.defaults.get(param.name, _kind_default(param.kind))
        if param.kind == "bool":
            parser.add_argument(flag, action="store_true",
                                default=bool(default), help=param.help)
            continue
        kwargs: dict[str, Any] = {"default": default, "help": param.help}
        if param.required:
            kwargs["required"] = True
        if param.kind == "int":
            kwargs["type"] = int
        elif param.kind == "float":
            kwargs["type"] = float
        elif param.kind == "path":
            kwargs["type"] = Path
        elif param.kind == "list[str]":
            kwargs["nargs"] = "*"
        parser.add_argument(flag, **kwargs)
    return parser


def dispatch(argv: Sequence[str]) -> int:
    """通用入口：解析 argv → handler → 单 JSON 信封 → 退出码。"""
    argv = list(argv)
    if not argv:
        return envelope.emit(envelope.fail(
            "", "USAGE", "缺少命令名",
            hint="factorlab research describe --json 查看命令目录"))
    name, rest = argv[0], argv[1:]
    spec = COMMANDS.get(name)
    handler = _HANDLERS.get(name)
    if spec is None or handler is None:
        return envelope.emit(envelope.fail(
            name, "USAGE", f"未知命令: {name}",
            hint="factorlab research describe --json 查看命令目录"))
    try:
        args = build_parser(spec).parse_args(rest)
    except SystemExit:
        return envelope.emit(envelope.fail(
            name, "USAGE", f"参数错误: {name} {' '.join(rest)}".strip(),
            hint=f"factorlab research describe --command {name}"))
    pretty = bool(getattr(args, "pretty", False))
    try:
        env = handler(args)
    except Exception as exc:  # noqa: BLE001 —— 统一信封：异常不得裸传
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code in envelope.EXIT_CODES:
            env = envelope.fail(
                name, code,
                getattr(exc, "message", None) or str(exc),
                hint=getattr(exc, "hint", None),
                log=getattr(exc, "log", None))
        else:
            traceback.print_exc(file=sys.stderr)  # traceback 走 stderr
            env = envelope.fail(name, "INTERNAL", f"{type(exc).__name__}: {exc}")
    if not isinstance(env, envelope.Envelope):
        env = envelope.fail(
            name, "INTERNAL",
            f"handler 返回非法类型: {type(env).__name__}（应返回 Envelope）")
    return envelope.emit(env, pretty=pretty)
