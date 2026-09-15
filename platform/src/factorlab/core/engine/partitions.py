from __future__ import annotations

import ast

from factorlab.core.factor.errors import FactorDSLError
from factorlab.core.ops import registry

# 元素级纯函数（Python/Polars 语义，无窗口、无分组），不进入算子注册表。
# 名单与 expr_codegen 生成代码的作用域逐一核对：缺失的名字会以 NameError 泄漏。
_ELEMENTWISE = {
    "abs", "log", "log1p", "sqrt", "exp", "sign", "floor", "if_else",
}


def _call_names(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            yield node


def _alias_map(tree: ast.AST) -> dict[str, str]:
    """收集 import 别名：'from mod import ts_delay as d' → {'d': 'ts_delay'}。"""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
    return aliases


def validate_partition_calls(source: str) -> None:
    """拒绝未知算子调用；公式内 def 函数、已知 ts_/cs_/gp_/ta_ 算子与平台薄封装、元素级函数放行。

    同时拒绝 def 体内的窗口/截面算子：expr_codegen 把用户 def 当黑盒整体放进
    元素级分区执行，def 内的 ts_/cs_ 调用会在全表上跑窗口，跨资产泄漏。放开条件
    是宏展开器能把窗口语义显式写成顶层 ts_ 调用。
    """
    tree = ast.parse(source)
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    aliases = _alias_map(tree)

    for node in _call_names(tree):
        name = aliases.get(node.func.id, node.func.id)
        if name in defined or name in _ELEMENTWISE:
            continue
        if not registry.has_op(name):
            raise FactorDSLError(f"未知算子: {name}", node.lineno, node.col_offset)

    for fn in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
        for node in _call_names(fn):
            name = aliases.get(node.func.id, node.func.id)
            if name in defined or name in _ELEMENTWISE:
                continue
            if registry.has_op(name):
                raise FactorDSLError(
                    f"窗口/截面算子 {node.func.id} 不能在 def 内使用，请直接写在公式顶层",
                    node.lineno,
                    node.col_offset,
                )


def check_causality(source: str, catalog=None, params: dict | None = None, *,
                    strict_unknown: bool = True) -> None:
    """统一未来函数门（R22）：任何 `forward > 0` 的调用/下标拒绝（行:列 定位）。

    推断单源 = engine.semantics.infer（组合继承/常量折叠/别名/kw 窗口/方法窗体）。
    覆盖：负位移字面量与可折叠表达式、负下标语法糖、方法负窗（.shift/.diff/
    .rolling_mean/.pct_change）、嵌套传播。`strict_unknown=False` 是旧入口
    reject_future_shifts 的兼容模式（未知算子按元素级继承，不因语义门误报——
    未知归 validate_partition_calls/catalog 解析管）。
    """
    from factorlab.core.engine.semantics import infer
    from factorlab.core.ops.classification import default_catalog
    if catalog is None:
        catalog = default_catalog()
    tree = source if isinstance(source, ast.AST) else ast.parse(source)
    infos = infer(tree, catalog, params, strict_unknown=strict_unknown)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Call, ast.Subscript)):
            continue
        info = infos.get(id(node))
        if info is None or info.forward <= 0:
            continue
        child_forward = max(
            (infos[id(c)].forward for c in ast.iter_child_nodes(node) if id(c) in infos),
            default=0)
        if info.forward > child_forward:      # 本节点引入未来（嵌套只报最内层）
            raise FactorDSLError(
                f"{ast.unparse(node)} 不允许负位移（lookback 只能取过去）",
                node.lineno,
                node.col_offset,
            )


def reject_future_shifts(source: str) -> None:
    """兼容入口（旧签名）：未知算子保守放行，其余同 check_causality。"""
    check_causality(source, strict_unknown=False)
