from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable, Literal

import polars as pl


# "im"（日内窗口族）/"day"（折日族）= bars_1m 分钟面算子（2026-09-08）——
# 分区内联在表达式自身（ops/minute_ops.py），不走 expr_codegen 前缀分区通道。
OperatorKind = Literal["el", "ts", "cs", "gp", "ta", "im", "day"]


@dataclass(frozen=True)
class OperatorDef:
    name: str
    kind: OperatorKind
    version: str
    func: Callable[..., pl.Expr]
    doc: str = ""
    # 生成代码作用域的 import 来源模块（第三方插件由加载器登记）。内置算子留空：
    # 它们的名字来自公式自身的 import 或宏展开，不注入额外 import 头（保证
    # 无插件时生成代码逐字节不变）。
    source_module: str = ""


_REGISTRY: dict[str, OperatorDef] = {}
_ALIASES: dict[str, str] = {}


def reset_registry() -> None:
    _REGISTRY.clear()
    _ALIASES.clear()


def factor_op(
    name: str,
    kind: OperatorKind,
    version: str,
    aliases: tuple[str, ...] = (),
) -> Callable:
    def decorator(func: Callable[..., pl.Expr]) -> Callable[..., pl.Expr]:
        op = OperatorDef(
            name=name,
            kind=kind,
            version=version,
            func=func,
            doc=func.__doc__ or "",
        )
        _REGISTRY[name] = op
        for alias in aliases:
            _ALIASES[alias] = name
        return func

    return decorator


def mark_source_module(names: Iterable[str], module: str) -> None:
    """登记这些算子的 import 来源模块（第三方插件加载器调用）。

    为什么需要：expr_codegen 生成的代码以**裸名字**调用算子，名字必须在其 exec
    作用域内绑定——内置算子由公式自身 import 或宏展开提供，插件算子没有这一层，
    故由加载器登记来源模块，引擎据此注入 import 头。
    """
    for name in names:
        op = _REGISTRY.get(name)
        if op is not None:
            _REGISTRY[name] = replace(op, source_module=module)


def source_import_lines() -> list[str]:
    """已登记来源的算子的 import 头（去重排序）；无登记（如纯内置环境）→ 空列表。"""
    return sorted({f"from {op.source_module} import {op.name}"
                   for op in _REGISTRY.values() if op.source_module})


def canonical_name(name: str, kind: OperatorKind) -> str:
    prefix = {"ts": "ts_", "cs": "cs_", "gp": "gp_", "ta": "ta_"}.get(kind, "")
    return name if not prefix or name.startswith(prefix) else f"{prefix}{name}"


# 插件算子的**分区前缀**门（2026-09-14 实测归纳）：expr_codegen 的 printer 据
# **名字前缀**（不是 kind）决定分区语义——实测 ts_ → `.over(asset, order_by=date)`、
# cs_ → `.over(date)`、gp_ → 翻译为 `cs_<后缀>(...).over(date, <key>)`，其余前缀与
# 裸名一律当**元素级函数**（行级、无分区）。故裸名注册的"ts 算子"会静默跨资产泄漏
# ——本门把它变成注册期报错。ta_ 族内置名为 ts_*（ts_MACD/ts_RSI），沿用 ts_ 前缀。
PLUGIN_KIND_PREFIX: dict[str, str] = {"ts": "ts_", "ta": "ts_", "cs": "cs_"}
# kind="gp" 暂不支持插件自定义：printer 会把 `gp_X` 翻译为 `cs_X`，该符号必须可
# 导入（内置只有 platform_ops 的 cs_mean/cs_rank）——放行只会得到 NameError 或
# 静默错值。组算子需求用公式内 gp_rank/gp_mean 组合表达。


def plugin_naming_error(name: str, kind: str) -> str | None:
    """插件算子命名门：违规返回错误说明，合规返回 None。

    为什么在插件层而非 `factor_op` 装饰器：内置算子有逃生条款——returns/vwap/adv20
    是 kind="ts" 的**裸名**薄封装（经 `expand_platform_macros` 展开为 ts_ 表达式），
    装饰器级强制会误伤它们。kind="el" 无前缀要求（裸名本就对应元素级语义）。

    裸名的两种静默失效（实测证据见 `tests/test_plugin_partition_prefix.py`）：
    ① 行序窗口——无 `.over()`，滚动窗口跨 code 块边界（假库实测首窗 19.33 而非 null）；
    ② 预热提取失效——`_ts_window_days` 只认 ts_/ta_ 前缀，裸名窗口对引擎不可见（20 → 0）。
    真实日频主链上二者**可能**恰好逐位相同（满载历史把污染行裁掉），属加载策略巧合
    而非保证；工具路径/短帧/非常规行序下即显形。
    """
    if kind == "el":
        return None
    required = PLUGIN_KIND_PREFIX.get(kind)
    if required is None:
        return (f"暂不支持自定义 kind={kind} 插件算子"
                f"（printer 只为 ts_/cs_/gp_ 前缀施划分区语义，其余会静默退化为"
                f"元素级函数 → 跨资产泄漏）；组算子请在公式内用 gp_rank/gp_mean 组合")
    if not name.startswith(required):
        return (f"算子名缺少分区前缀: kind={kind} 必须命名为 {required}{name}"
                f"（分区与窗口预热都据名字前缀识别；裸名会退化为元素级函数："
                f"窗口跨 code 块 + 预热提取为 0 天 → 跨资产泄漏/首窗错值）")
    return None


def get_op(name: str) -> OperatorDef:
    target = _ALIASES.get(name, name)
    try:
        return _REGISTRY[target]
    except KeyError as exc:
        raise KeyError(f"未知算子: {name}") from exc


def has_op(name: str) -> bool:
    return name in _REGISTRY or name in _ALIASES


def list_ops() -> list[OperatorDef]:
    return sorted(_REGISTRY.values(), key=lambda op: op.name)
