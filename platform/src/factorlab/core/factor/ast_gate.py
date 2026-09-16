from __future__ import annotations

import ast

from factorlab.core.factor.errors import FactorDSLError


ALLOWED_NODES = {
    ast.Module,
    ast.Import,
    ast.ImportFrom,
    ast.alias,
    ast.FunctionDef,
    ast.ClassDef,
    ast.Assign,
    ast.AnnAssign,
    ast.Expr,
    ast.Return,
    ast.arguments,
    ast.arg,
    ast.Name,
    ast.Constant,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Call,
    ast.Attribute,
    ast.IfExp,
    ast.Subscript,
    ast.Tuple,
    ast.List,
    ast.Load,
    ast.Store,
    ast.keyword,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
}

ALLOWED_IMPORT_PREFIXES = (
    "polars",
    "polars_ta.prefix.",
    "factorlab.core.ops.",
)

FORBIDDEN_CALLS = {"eval", "exec", "open", "compile", "__import__"}

# polars Expr 纯元素级方法白名单（与元素级函数名单同语义——无窗口、无分组、无副作用）。
# 属性调用仅放行「白名单方法 + 基表达式非裸 Name」：ts_delta(x, 1).abs() 合法
# （free-form 设计 §2.1）；np.abs / pl.read_csv / x.rolling_mean 仍被拒。
ALLOWED_EXPR_METHODS = {"abs", "log", "log1p", "sqrt", "exp", "sign", "floor"}


def _is_allowed_import(module: str | None) -> bool:
    return module is not None and module.startswith(ALLOWED_IMPORT_PREFIXES)


def _platform_macro_names() -> frozenset[str]:
    """平台宏名单单点在 `core.ops.platform_ops`（与注册面同源）——延迟导入避免
    ast_gate ↔ platform_ops 模块级成环（后者模块级 import 本模块的
    ALLOWED_EXPR_METHODS）。"""
    from factorlab.core.ops.platform_ops import PLATFORM_MACRO_NAMES
    return PLATFORM_MACRO_NAMES


def _reject_platform_macro_import(alias: ast.alias, node: ast.AST) -> None:
    """平台宏（returns/vwap/adv20/gp_rank/gp_mean）必须裸用——import 即报错。

    R03-M1：`from polars_ta.prefix.wq import returns`（宏不在 vendor 面）会在
    expr_codegen exec 阶段以 ImportError 裸堆栈收场，用户看不到"应裸用"的指引；
    本门在 codegen 前 fail fast。任何模块（含宏的定义模块）一律拒绝——统一入口，
    避免别名/定义模块 import 绕过并造成后续静默分区语义问题。
    """
    if alias.name in _platform_macro_names():
        raise FactorDSLError(
            f"平台宏 {alias.name} 请裸用（不要 import）——平台在编译期自动展开；"
            "从模块 import 会得到未定义符号或深层报错",
            getattr(node, "lineno", None),
            getattr(node, "col_offset", None),
        )


def validate_formula(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise FactorDSLError(f"语法错误: {exc.msg}", exc.lineno, exc.offset) from exc

    for node in ast.walk(tree):
        # R03-I7：and/or/not 对列表达式无法求值（expr_codegen 经 sympy 执行期
        # TypeError: cannot determine truth value of Relational）——此前 BoolOp/
        # ast.Not 在 ALLOWED_NODES，lint 通过但运行崩；现前置拒绝并给可用写法。
        boolean_connector = isinstance(node, ast.BoolOp) or (
            isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not))
        if boolean_connector:
            raise FactorDSLError(
                "不支持布尔连接 and/or/not（动态列表达式无法真值判断，codegen "
                "执行期必崩）；布尔与/用嵌套 if_else 表达——"
                "if_else(条件1, if_else(条件2, x, None), None)；分钟零成交/"
                "陈旧尾部 bar 守卫可用注入列 has_trade："
                "if_else(has_trade, x, None)（见 docs/interface.md 分钟面）",
                getattr(node, "lineno", None),
                getattr(node, "col_offset", None),
            )

        if type(node) not in ALLOWED_NODES:
            raise FactorDSLError(
                f"不支持的语法节点: {type(node).__name__}",
                getattr(node, "lineno", None),
                getattr(node, "col_offset", None),
            )

        if isinstance(node, ast.Import):
            for alias in node.names:
                _reject_platform_macro_import(alias, node)
                if not _is_allowed_import(alias.name):
                    raise FactorDSLError(
                        f"禁止导入模块: {alias.name}",
                        node.lineno,
                        node.col_offset,
                    )

        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                _reject_platform_macro_import(alias, node)
            if not _is_allowed_import(node.module):
                raise FactorDSLError(
                    f"禁止导入模块: {node.module}",
                    node.lineno,
                    node.col_offset,
                )

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                raise FactorDSLError(
                    f"禁止调用函数: {node.func.id}",
                    node.lineno,
                    node.col_offset,
                )

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            # 仅放行纯元素级方法链在表达式结果上（基表达式非裸 Name——模块/对象属性如
            # np.abs、pl.read_csv 仍属禁止的属性调用）
            if isinstance(node.func.value, ast.Name) or node.func.attr not in ALLOWED_EXPR_METHODS:
                raise FactorDSLError(
                    "禁止属性调用；请使用已导入的算子函数"
                    f"（元素级方法链仅限 {sorted(ALLOWED_EXPR_METHODS)}）",
                    node.lineno,
                    node.col_offset,
                )
