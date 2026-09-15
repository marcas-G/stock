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


def _fold_consts(node: ast.expr, consts: dict[str, int | float]):
    """递归常量折叠（字面量 + 顶层常量 Name + 一元 ± + 四则/整除/取模/幂，绝不 eval）。

    与旧 `_const_fold` 的差别是 Name 查 `consts` 表：`-_n` / `1 - _n` 这类
    **内嵌 Name** 的位移表达式必须同样能静态判定（C2 旁路：旧门只回退裸 Name，
    `_n = 3` 后 `ts_delay(close, -_n)` 漏网 → 实测 shift(-3) 未来函数）。
    非常量成分（未知 Name/调用等）返回 None → 调用方保守放行。
    """
    if isinstance(node, ast.Constant):
        value = node.value
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.UnaryOp):
        value = _fold_consts(node.operand, consts)
        if value is None:
            return None
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return value
        return None
    if isinstance(node, ast.BinOp):
        left, right = _fold_consts(node.left, consts), _fold_consts(node.right, consts)
        if left is None or right is None:
            return None
        op = type(node.op)
        try:
            if op is ast.Add:
                return left + right
            if op is ast.Sub:
                return left - right
            if op is ast.Mult:
                return left * right
            if op is ast.Div:
                return left / right
            if op is ast.FloorDiv:
                return left // right
            if op is ast.Mod:
                return left % right
            if op is ast.Pow:
                return left ** right
        except (ZeroDivisionError, OverflowError):
            return None
    return None


def _top_level_consts(tree: ast.AST) -> dict[str, int | float]:
    """顶层赋值常量表（Name 目标 = 可折叠值，源序 last-wins，绝不 eval）。

    AI 生成因子的常见形态是位移量走命名常量（`_d = -3` 后 `ts_delay(x, _d)`）——
    只查参数表达式本身会漏网负位移（未来函数）。仅跟踪模块顶层简单赋值；
    def 内局部/分支赋值不跟踪（保守方向：未知变量按"无法静态判断"放行，
    inline_defs 会把 def 参数绑定后的常量提到顶层赋值，同样被覆盖）。
    折叠递归解析 Name（源序累积）——`_n = 3; _m = -_n` 的二级常量同样入表。
    """
    consts: dict[str, int | float] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            value = _fold_consts(node.value, consts)
            if value is not None:
                consts[node.targets[0].id] = value
    return consts


def reject_future_shifts(source: str) -> None:
    """拒绝负 lookback：ts_delay/ts_delta 的位移参数与下标语法糖不能为负。

    覆盖形态（AI 生成均可触达）：
    - 字面量/可折叠表达式（`-1`、`1 - 2`、`d=-1.0`）
    - 顶层常量赋值间接（`_d = -3` 后 `ts_delay(x, _d)`——last-wins 静态近似）
    - 常量表内嵌 Name（`_n = 3` 后 `ts_delay(x, -_n)` / `1 - _n`——C2 旁路，
      递归折叠解析 Name）
    - import 别名（`from polars_ta.prefix import ts_delay as td` 后 `td(x, -2)`）
    - 下标语法糖（`X[k]`——expr_codegen `visit_Subscript` 把 X[k] 编译为
      ts_delay(X, k)；k 可折叠为负 = 取未来行，C1 旁路）
    未知变量（如 def 形参）无法静态判断 → 放行（方向同文档既有约定）。
    正向 `X[1]` / `X[0]` 必须继续可用（合法 lookback/自身）。
    """
    tree = ast.parse(source)
    aliases = _alias_map(tree)
    consts = _top_level_consts(tree)

    def _shift_arg(node: ast.Call) -> ast.expr | None:
        if len(node.args) >= 2:
            return node.args[1]
        for kw in node.keywords:
            if kw.arg == "d":
                return kw.value
        return None

    for node in _call_names(tree):
        name = aliases.get(node.func.id, node.func.id)
        if name not in {"ts_delay", "ts_delta"}:
            continue
        arg = _shift_arg(node)
        shift = _fold_consts(arg, consts) if arg is not None else None
        if shift is not None and shift < 0:
            raise FactorDSLError(
                f"{node.func.id} 不允许负位移（lookback 只能取过去）",
                node.lineno,
                node.col_offset,
            )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        shift = _fold_consts(node.slice, consts)
        if shift is not None and shift < 0:
            raise FactorDSLError(
                f"{ast.unparse(node)} 不允许负位移（下标 = ts_delay，lookback 只能取过去）",
                node.lineno,
                node.col_offset,
            )
