"""统一语义推断 pass（设计 §6.5；开放算子底座 G2/G3/G4）。

自底向上为公式每个 AST 节点推断：
    { level: el|ts|cs|gp, keys, order, lookback, forward }

- **组合继承**：元素级函数/运算继承最外层非 el 子节点的 level/keys/order；
- **分类表驱动**：库函数（polars_ta）与 polars 方法按 `Catalog` 元数据分区；
- **窗口可加性**：`lookback(f∘g) = window(f) + lookback(g)`（求和口径——与旧
  `_ts_window_days` 一致，预热只多不少，见零迁移证据）；
- **未来检查**：负窗/负位移/负下标 → `forward += |w|`；forward > 0 由
  `partitions.check_causality` 拒绝。

窗口求值：`"arg:N"` 位置参数常量（含 keyword 形式 `d=`/`n=` 等窗口名）；
非恒定窗口**保守放行**（lookback 0，不报错）——与平台既有"未知变量静态放行"
方向一致（`ts_delay(close, n)` / `close[n]` 不得误杀，见 tests/test_partitions.py）。

本模块只吃**展开后**公式（宏/def 内联/参数替换已完成），不重复语法制品的门。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from typing import Literal

from factorlab.core.factor.errors import FactorDSLError
from factorlab.core.ops.classification import Catalog, OpMeta, window_spec

Level = Literal["el", "ts", "cs", "gp", "im", "day"]

# 元素级纯函数（Python/Polars 语义，无窗口、无分组；与 partitions/ast_gate 同源）
_ELEMENTWISE = {
    "abs", "log", "log1p", "sqrt", "exp", "sign", "floor", "if_else",
}

# 窗口参数的 keyword 名（`ts_delay(close, d=-1)` / `.shift(n=-1)` 形态；
# 分类表只存 arg:N，无法反查签名——用命名约定映射）
_WINDOW_KW_NAMES = {"d", "n", "window", "window_size", "period", "length", "timeperiod"}


class SemanticError(FactorDSLError):
    """语义推断失败（未知算子/方法、歧义方法等），携带 行:列。"""


@dataclass(frozen=True)
class NodeInfo:
    level: Level
    keys: tuple[str, ...] = ()
    order: str | None = None
    lookback: int = 0
    forward: int = 0
    unbounded: bool = False


_EL = NodeInfo("el")


def _alias_map(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
    return aliases


def _fold_consts(node: ast.expr, consts: dict[str, int | float]):
    """递归常量折叠（字面量 + 顶层常量 Name + 一元 ± + 四则/整除/取模/幂，绝不 eval）。

    与 engine/partitions 的折叠同语义（未来门收编后以本模块为单源）。
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
    """顶层赋值常量表（Name 目标 = 可折叠值，源序 last-wins，绝不 eval）。"""
    consts: dict[str, int | float] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            value = _fold_consts(node.value, consts)
            if value is not None:
                consts[node.targets[0].id] = value
    return consts


def _window_value(meta: OpMeta, call: ast.Call, params: dict | None,
                  consts: dict[str, int | float]):
    """求值算子窗口：位置参数 arg:N（常量折叠 `-1`/`-_n`）→ keyword 窗口名 → None。

    位置参数非常量 → None（保守放行：`ts_delay(close, n)` 不误杀）。
    """
    w = window_spec(meta, call.args, params)
    if w is not None or meta.window is None:
        return w
    if isinstance(meta.window, str) and meta.window.startswith("arg:"):
        pos = int(meta.window.split(":")[1])
        if pos < len(call.args):
            folded = _fold_consts(call.args[pos], consts)
            if isinstance(folded, (int, float)) and not isinstance(folded, bool):
                return folded
        for kw in call.keywords:
            if kw.arg in _WINDOW_KW_NAMES:
                folded = _fold_consts(kw.value, consts)
                if isinstance(folded, (int, float)) and not isinstance(folded, bool):
                    return folded
    return None


class _Inferrer:
    def __init__(self, catalog: Catalog, params: dict | None, consts: dict,
                 aliases: dict[str, str], defined: set[str],
                 strict_unknown: bool = True, assigns: dict[str, ast.expr] | None = None) -> None:
        self.catalog = catalog
        self.params = params
        self.consts = consts
        self.aliases = aliases
        self.defined = defined
        self.strict_unknown = strict_unknown
        self.assigns = assigns or {}
        self._resolving: set[str] = set()
        self.infos: dict[int, NodeInfo] = {}

    # ---- 通用 ----
    def visit(self, node: ast.AST) -> NodeInfo:
        cached = self.infos.get(id(node))
        if cached is not None:
            return cached
        info = self._visit(node)
        self.infos[id(node)] = info
        return info

    def _visit(self, node: ast.AST) -> NodeInfo:
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Subscript):
            return self._subscript(node)
        if isinstance(node, ast.Name):
            # 变量引用链：顶层赋值表达式继承（循环引用由 _resolving 防呆）
            target = self.assigns.get(node.id)
            if target is not None and node.id not in self._resolving:
                self._resolving.add(node.id)
                try:
                    return self.visit(target)
                finally:
                    self._resolving.discard(node.id)
            return _EL
        if isinstance(node, (ast.Constant, ast.Attribute)):
            return _EL
        children = [self.visit(c) for c in ast.iter_child_nodes(node)]
        return self._combine(children)

    def _combine(self, children: list[NodeInfo]) -> NodeInfo:
        base = next((c for c in children if c.level != "el"), _EL)
        return NodeInfo(
            level=base.level,
            keys=base.keys,
            order=base.order,
            lookback=max((c.lookback for c in children), default=0),
            forward=max((c.forward for c in children), default=0),
            unbounded=any(c.unbounded for c in children),
        )

    # ---- 调用 ----
    def _call(self, node: ast.Call) -> NodeInfo:
        children = [self.visit(a) for a in node.args]
        children += [self.visit(kw.value) for kw in node.keywords]

        if isinstance(node.func, ast.Name):
            name = self.aliases.get(node.func.id, node.func.id)
            if name in self.defined:
                return self._combine(children)
            meta = self.catalog.get(name)
            if meta is not None:
                return self._apply_meta(meta, node, children)
            if name in _ELEMENTWISE:
                return self._combine(children)
            if not self.strict_unknown:
                return self._combine(children)   # 旧入口：未知算子不因语义门报错
            raise SemanticError(
                f"未知算子 {name}；若为自定义/外部函数请补 op_meta"
                f"（例：op_meta:\n  {name}: {{partition: ts, window: ${{win}}}}）",
                node.lineno, node.col_offset)

        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            meta = self.catalog.get(f".{attr}")
            if meta is not None:
                return self._apply_meta(meta, node, children)
            if not self.strict_unknown:
                return self._combine(children)
            from factorlab.core.ops.classification import method_denied_guidance
            guidance = method_denied_guidance(attr)
            if guidance is not None:
                raise SemanticError(
                    f"方法 .{attr}() 不开放：{guidance}", node.lineno, node.col_offset)
            raise SemanticError(
                f"未知方法 .{attr}()；请改用函数形式（{attr}(...)）或补 op_meta",
                node.lineno, node.col_offset)

        return self._combine(children)

    def _apply_meta(self, meta: OpMeta, node: ast.Call,
                    children: list[NodeInfo]) -> NodeInfo:
        child_lb = max((c.lookback for c in children), default=0)
        child_fw = max((c.forward for c in children), default=0)
        child_ub = any(c.unbounded for c in children)

        level: Level = meta.partition
        if level == "el":
            return self._combine(children)

        w = _window_value(meta, node, self.params, self.consts)
        extra_lb = 0
        extra_fw = 0
        unbounded = child_ub
        if meta.window == "unbounded" or w == "unbounded":
            unbounded = True
        elif isinstance(w, (int, float)) and not isinstance(w, bool):
            if w < 0:
                extra_fw = int(-w)
            elif isinstance(w, int):
                extra_lb = w          # 非整 float 窗口不计 lookback（旧 _ts_window_days 口径）

        keys: tuple[str, ...]
        order: str | None
        if level == "ts":
            keys, order = ("code",), "date"
        elif level == "cs":
            keys, order = ("date",), None
        elif level == "gp":
            key = ast.unparse(node.args[0]) if node.args else ""
            keys, order = ("date", key), None
        else:  # im/day（分钟面；日频门在 compute 层拦截）
            keys, order = ("code",), "date"

        return NodeInfo(
            level=level, keys=keys, order=order,
            lookback=child_lb + extra_lb,
            forward=child_fw + extra_fw,
            unbounded=unbounded,
        )

    # ---- 下标语法糖 X[k] = ts_delay(X, k) ----
    def _subscript(self, node: ast.Subscript) -> NodeInfo:
        base = self.visit(node.value)
        k = _fold_consts(node.slice, self.consts)
        if not isinstance(k, int) or isinstance(k, bool):
            return base        # 未知/非常量 → 保守放行（同旧门）
        if k >= 0:
            return replace(base, lookback=base.lookback + k)
        return replace(base, forward=base.forward + (-k))


def infer(source: str | ast.AST, catalog: Catalog, params: dict | None = None,
          *, strict_unknown: bool = True) -> dict[int, NodeInfo]:
    """自底向上推断；返回 {id(ast.Node): NodeInfo}。

    `source` 可以是源码字符串（内部 ast.parse）或已解析的 AST（调用方持树时
    用同一棵树的 id() 查询，避免重复 parse 的 id 失配）。
    `strict_unknown=False`：未知算子/方法按 el 继承（旧 reject_future_shifts
    入口的既有分工——未知归 validate 管，未来门不误报）。
    """
    tree = ast.parse(source) if isinstance(source, str) else source
    assigns: dict[str, ast.expr] = {}
    for stmt in getattr(tree, "body", []):
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)):
            assigns[stmt.targets[0].id] = stmt.value     # 源序 last-wins
    inferrer = _Inferrer(
        catalog=catalog,
        params=params,
        consts=_top_level_consts(tree),
        aliases=_alias_map(tree),
        defined={n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)},
        strict_unknown=strict_unknown,
        assigns=assigns,
    )
    inferrer.visit(tree)
    return inferrer.infos
