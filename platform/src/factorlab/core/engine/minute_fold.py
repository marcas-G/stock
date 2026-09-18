"""R09-PERF-I1：bars_1m 分钟链折日物化共享（评审 r09-2026-09-17-minute-perf §2.1）。

问题（评审 §1①）：旧路径每个 day_* 调用 = 独立 `.over(["code","date"])` 分组
物化；折日公式多算子串联时 polars 不共享分组，整组序列被逐算子反复物化
（实测 vol_price_corr 折日 64.7s/58 日）。

本模块在 `compute_formula(scope="bars_1m")` 的 codegen 前尝试融合：

1. **聚合检测/调度**：AST 上收集全部 day_* 调用（含嵌在算术与变量链中的），
   按依赖划分 pass（pass = 1 + max(依赖聚合 pass)）。
2. **每 pass**：用 codegen 把该 pass 各聚合参数表达式物化为临时列（与旧路径
   同一展开链/同一 sympy 化简——数值一致的前提）→ 组内 over 广播
   （sum/mean/max/min 单 over；day_first/day_last 与旧实现同形双 over）→
   结果列供后续 pass 与最终输出复用；最终公式死赋值剪枝。
3. **im_* 滚动族**保留 over 路径，但：bars 预排序一次（已物理有序零代价）、
   over 去 order_by（`seq_*` 物理序变体）、重复子表达式 CSE 临时列只算一次。
4. **完整回退**：build_plan 返回 None 的不支持形态（跨层/未知调用、非简单
   赋值、保留前缀名字冲突、缺网格列、依赖环）交由现有 codegen 旧路径处理
   ——行为与数值不变。

数值锁定：新路径 vs 旧路径在合成网格与真数据 4 因子同窗逐 cell 对拍——纯
逐行参数形态 bit-exact；旧路径把 im_delay/day_mean 内联进 day_* 聚合的嵌套
over 形态仅末位 ulp 级差异（polars 归约计划对嵌套敏感，null 掩码一致），
证据与复现见 `governance/evidence/verification/R31/minute-perf/`。

临时列命名不带前导下划线——expr_codegen 模板 `main()` 末尾会执行
`df.select(~cs.starts_with("_"))` 丢弃下划线中间列；前缀 factorlab_fold_/
factorlab_cse_ 被用户名字或输入列占用即回退（不覆盖用户数据）。
"""
from __future__ import annotations

import ast
import copy
from dataclasses import dataclass, field
from typing import Iterable

import polars as pl
from expr_codegen import codegen_exec

from factorlab.core.engine.minute_gate import _UNARY_CONST, _import_aliases
from factorlab.core.ops.minute_ops import (
    DAY_OPS_NAMES,
    IM_OPS_NAMES,
    SEQ_EXTRA_CODES,
    SEQ_FUNCS,
)

FOLD_PREFIX = "factorlab_fold_"
CSE_PREFIX = "factorlab_cse_"
_ORDER = "minute_index"
_DAY = frozenset(DAY_OPS_NAMES)
_IM = frozenset(IM_OPS_NAMES)
_ALLOWED_CALLS = _IM | _UNARY_CONST | {"if_else"}
_AGG_METHOD = {"day_sum": "sum", "day_mean": "mean",
               "day_max": "max", "day_min": "min"}
_UNSUPPORTED_NODES = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                      ast.GeneratorExp, ast.NamedExpr, ast.Await, ast.Yield,
                      ast.YieldFrom)


class _Fallback(Exception):
    """分析期发现不支持形态——build_plan 捕获后返回 None（完整回退旧路径）。"""


@dataclass(eq=False)
class FoldNode:
    """一个 day_* 折日聚合项（一个 AST 调用节点 ↔ 一个结果临时列）。"""

    op: str
    arg: ast.expr
    pass_no: int = 0
    arg_temp: str = ""
    result_temp: str = ""


@dataclass(eq=False)
class CseNode:
    """重复 im_* 子表达式 → 全局 CSE 临时列（只算一次）。"""

    name: str
    raw_expr: ast.expr
    pass_no: int = 0


@dataclass
class FusionPlan:
    imports: list[ast.stmt]
    statements: list[ast.stmt]
    assigns: dict[str, ast.expr]
    nodes: list[FoldNode]
    cse: list[CseNode]
    max_pass: int
    has_im: bool = False
    aliases: dict[str, str] = field(default_factory=dict)
    _cse_by_key: dict[tuple, CseNode] = field(default_factory=dict)
    _defs: dict[tuple, ast.expr] = field(default_factory=dict)

    # ---- 内部工具 ----

    def _target(self, stmt: ast.stmt) -> str:
        if isinstance(stmt, ast.Assign):
            return stmt.targets[0].id
        return stmt.target.id

    def _effective(self, call: ast.Call) -> str | None:
        if not isinstance(call.func, ast.Name):
            return None
        return self.aliases.get(call.func.id, call.func.id)

    def _call_key(self, call: ast.Call):
        eff = self._effective(call)
        args = ast.Tuple(elts=[*call.args, *(k.value for k in call.keywords)],
                         ctx=ast.Load())
        return (eff, tuple(k.arg for k in call.keywords), ast.dump(args))

    def _fold_node(self, call: ast.Call) -> FoldNode | None:
        """按 deepcopy 保留的 `_fold_day` 属性找回原 day 节点（跨拷贝身份稳定）。"""
        node = getattr(call, "_fold_day", None)
        return node if isinstance(node, FoldNode) else None

    def _rewrite(self, expr: ast.expr, *, pass_no: int, cse_upto: int,
                 exclude_cse: CseNode | None = None) -> ast.expr:
        """替换：day(已物化)→结果列；选中的 im_* →CSE 列；其余 im_*→seq_*。

        exclude_cse：CSE 定义式自身不替换为 CSE 列（否则自引用）。"""
        plan = self

        class _Rw(ast.NodeTransformer):
            def visit_Call(self, node: ast.Call) -> ast.expr:
                fold = plan._fold_node(node)
                if fold is not None:
                    if fold.pass_no >= pass_no:
                        raise _Fallback(f"day 节点尚未物化: {fold.op}")
                    return ast.copy_location(
                        ast.Name(id=fold.result_temp, ctx=ast.Load()), node)
                eff = plan._effective(node)
                if eff in _IM:
                    seq = SEQ_FUNCS.get(eff)
                    if seq is None:
                        raise _Fallback(f"无物理序变体: {eff}")
                    cse = plan._cse_by_key.get(plan._call_key(node))
                    if cse is not None and cse is not exclude_cse \
                            and cse.pass_no <= cse_upto:
                        return ast.copy_location(
                            ast.Name(id=cse.name, ctx=ast.Load()), node)
                    new = self.generic_visit(node)
                    new.func = ast.Name(id=seq, ctx=ast.Load())
                    return new
                return self.generic_visit(node)

        return _Rw().visit(expr)

    def _names(self, expr: ast.expr) -> set[str]:
        return {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)}

    def _needed_closure(self, names: set[str]) -> set[str]:
        """经赋值链扩张依赖名集合（含赋值目标自身——语句包含判定用）。"""
        out: set[str] = set()
        stack = list(names)
        while stack:
            name = stack.pop()
            if name in out or name not in self.assigns:
                continue
            out.add(name)
            stack.extend(self._names(self.assigns[name]))
        return out

    def nodes_in_pass(self, p: int) -> list[FoldNode]:
        return [n for n in self.nodes if n.pass_no == p]

    # ---- 公式文本生成 ----

    def pass_source(self, p: int) -> str:
        """第 p pass 的物化公式：用户赋值（按需）+ CSE 定义 + 聚合参数临时列。"""
        arg_items: list[tuple[FoldNode, ast.expr]] = []
        needed: set[str] = set()
        for node in self.nodes_in_pass(p):
            rewritten = self._rewrite(copy.deepcopy(node.arg), pass_no=p,
                                      cse_upto=p)
            arg_items.append((node, rewritten))
            needed |= self._names(rewritten)
        cse_items: list[tuple[CseNode, ast.expr]] = []
        for cse in sorted(self.cse, key=lambda c: (c.pass_no,
                                                   _node_count(c.raw_expr))):
            if cse.pass_no != p:
                continue
            rewritten = self._rewrite(copy.deepcopy(cse.raw_expr), pass_no=p,
                                      cse_upto=p, exclude_cse=cse)
            cse_items.append((cse, rewritten))
            needed |= self._names(rewritten)
        needed = self._needed_closure(needed)

        lines = [ast.unparse(stmt) for stmt in self.imports]
        for stmt in self.statements:
            if self._target(stmt) not in needed:
                continue
            if any(dep.pass_no >= p
                   for dep in _expr_deps(stmt.value, self.assigns)):
                continue
            rewritten = self._rewrite(copy.deepcopy(stmt), pass_no=p,
                                      cse_upto=p - 1)
            lines.append(ast.unparse(rewritten))
        lines += [f"{cse.name} = {ast.unparse(expr)}"
                  for cse, expr in cse_items]
        lines += [f"{node.arg_temp} = {ast.unparse(expr)}"
                  for node, expr in arg_items]
        return "\n".join(lines)

    def final_source(self, outputs: Iterable[str]) -> str:
        """最终公式：全部聚合/CSE 已物化 → 结果列引用；死赋值剪枝（只留 outputs
        及其传递依赖——中间量已由聚合结果替代，不再重复计算）。"""
        big = self.max_pass + 1
        rewritten = [self._rewrite(copy.deepcopy(stmt), pass_no=big,
                                   cse_upto=self.max_pass)
                     for stmt in self.statements]
        by_name = {self._target(stmt): stmt for stmt in rewritten}
        needed = set(outputs)
        stack = list(outputs)
        while stack:
            name = stack.pop()
            stmt = by_name.get(name)
            if stmt is None:
                continue
            for sub in ast.walk(stmt.value):
                if isinstance(sub, ast.Name) and sub.id in by_name \
                        and sub.id not in needed:
                    needed.add(sub.id)
                    stack.append(sub.id)
        lines = [ast.unparse(stmt) for stmt in self.imports]
        lines += [ast.unparse(stmt) for stmt in rewritten
                  if self._target(stmt) in needed]
        return "\n".join(lines)


def _node_count(expr: ast.expr) -> int:
    return sum(1 for _ in ast.walk(expr))


def _expr_deps(expr: ast.expr,
               assigns: dict[str, ast.expr]) -> set[FoldNode]:
    """表达式依赖的 day 聚合项集合（Name 经赋值链展开；环 → _Fallback）。"""
    ids: dict[int, FoldNode] = {}
    stack: set[str] = set()

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.Call):
            fold = getattr(node, "_fold_day", None)
            if isinstance(fold, FoldNode):
                ids[id(fold)] = fold
                return
            for child in ast.iter_child_nodes(node):
                walk(child)
            return
        if isinstance(node, ast.Name):
            if node.id in assigns:
                if node.id in stack:
                    raise _Fallback(f"赋值环: {node.id}")
                stack.add(node.id)
                walk(assigns[node.id])
                stack.discard(node.id)
            return
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(expr)
    return set(ids.values())


def build_plan(formula: str, columns: Iterable[str] = ()) -> FusionPlan | None:
    """分析折日公式并生成融合计划；不支持 → None（完整回退旧路径）。"""
    try:
        return _build_plan(formula, columns)
    except _Fallback:
        return None


def _build_plan(formula: str, columns: Iterable[str]) -> FusionPlan | None:
    try:
        tree = ast.parse(formula)
    except SyntaxError:
        return None
    imports: list[ast.stmt] = []
    statements: list[ast.stmt] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            imports.append(stmt)
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            statements.append(stmt)
        else:
            return None
    assigns: dict[str, ast.expr] = {}
    for stmt in statements:
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0],
                                                        ast.Name):
                return None
            name = stmt.targets[0].id
        else:
            if stmt.value is None or not isinstance(stmt.target, ast.Name):
                return None
            name = stmt.target.id
        assigns[name] = stmt.value

    cols = set(columns)
    if _ORDER not in cols or any(v.startswith((FOLD_PREFIX, CSE_PREFIX))
                                 for v in (*assigns, *cols)):
        return None

    aliases = _import_aliases(tree)
    fold_nodes: list[FoldNode] = []
    for stmt in statements:
        for sub in ast.walk(stmt.value):
            if isinstance(sub, _UNSUPPORTED_NODES):
                return None
            if isinstance(sub, ast.Call):
                if not isinstance(sub.func, ast.Name):
                    return None
                eff = aliases.get(sub.func.id, sub.func.id)
                if eff in _DAY:
                    if len(sub.args) != 1 or sub.keywords \
                            or isinstance(sub.args[0], ast.Starred):
                        return None
                    fold_nodes.append(FoldNode(eff, sub.args[0]))
                elif eff in _IM:
                    if eff not in SEQ_FUNCS:   # 新算子未配物理序变体 → 回退
                        return None
                elif eff not in _ALLOWED_CALLS:
                    return None
    if not fold_nodes:
        return None

    # 原节点标记：`_fold_day` 随 deepcopy 保留（跨拷贝身份稳定）
    attached = 0
    for stmt in statements:
        for sub in ast.walk(stmt.value):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                eff = aliases.get(sub.func.id, sub.func.id)
                if eff in _DAY:
                    match = [n for n in fold_nodes if n.arg is sub.args[0]
                             and n.op == eff]
                    if not match:
                        return None
                    sub._fold_day = match[0]
                    attached += 1
    if attached != len(fold_nodes):
        return None  # 同一 arg 对象被两个调用复用等异常形态——回退

    # pass 划分（依赖 = 参数表达式中的其他 day 结果；环 → 回退）
    state: dict[int, int] = {}

    def pass_of(node: FoldNode) -> int:
        key = id(node)
        if key in state:
            if state[key] == 0:
                raise _Fallback("day 依赖环")
            return state[key]
        state[key] = 0
        p = 1
        for dep in _expr_deps(node.arg, assigns):
            p = max(p, pass_of(dep) + 1)
        state[key] = p
        node.pass_no = p
        return p

    for node in fold_nodes:
        pass_of(node)
    for i, node in enumerate(fold_nodes):
        node.arg_temp = f"{FOLD_PREFIX}arg_{i}"
        node.result_temp = f"{FOLD_PREFIX}{i}"
    max_pass = max(n.pass_no for n in fold_nodes)

    plan = FusionPlan(imports=imports, statements=statements, assigns=assigns,
                      nodes=fold_nodes, cse=[], max_pass=max_pass,
                      aliases=aliases)

    # CSE：重复 im_* 子表达式（结构键 + 首选出现序）
    im_calls: list[tuple[ast.Call, tuple]] = []
    for stmt in statements:
        for sub in ast.walk(stmt.value):
            if not isinstance(sub, ast.Call) or not isinstance(sub.func,
                                                               ast.Name):
                continue
            eff = aliases.get(sub.func.id, sub.func.id)
            if eff in _IM:
                im_calls.append((sub, plan._call_key(sub)))
    plan.has_im = bool(im_calls)
    counts: dict[tuple, int] = {}
    for _call, key in im_calls:
        counts[key] = counts.get(key, 0) + 1
    chosen: list[tuple] = []
    for _call, key in im_calls:
        if counts[key] >= 2 and key not in chosen:
            chosen.append(key)
    if not chosen:
        return plan

    cse_nodes: list[CseNode] = []
    for i, key in enumerate(chosen):
        raw = next(call for call, k in im_calls if k == key)
        cse = CseNode(name=f"{CSE_PREFIX}{i}", raw_expr=None)
        plan._cse_by_key[key] = cse
        plan._defs[key] = copy.deepcopy(raw)
        cse_nodes.append(cse)
    # 定义表达式：内层 CSE 先替换（根保留原 im_*，供后续按 pass 重写）
    for cse in cse_nodes:
        key = next(k for k, v in plan._cse_by_key.items() if v is cse)
        root = plan._defs[key]
        cse.raw_expr = _replace_inner_cse(root, plan)
    # ready pass：依赖的 day pass / 内层 CSE ready + 1
    ready: dict[int, int] = {}

    def cse_ready(cse: CseNode) -> int:
        key = id(cse)
        if key in ready:
            return ready[key]
        base = 0
        for dep in _expr_deps(cse.raw_expr, assigns):
            base = max(base, dep.pass_no)
        for name in {n.id for n in ast.walk(cse.raw_expr)
                     if isinstance(n, ast.Name)}:
            inner = next((c for c in cse_nodes if c.name == name), None)
            if inner is not None and inner is not cse:
                base = max(base, cse_ready(inner))
        ready[key] = base + 1
        cse.pass_no = base + 1
        return ready[key]

    for cse in cse_nodes:
        cse_ready(cse)
    plan.cse = cse_nodes
    return plan


def _replace_inner_cse(root: ast.expr, plan: FusionPlan) -> ast.expr:
    """CSE 定义表达式：嵌套 CSE（更内层重复子表达式）替换为列名，根保持原样。"""
    class _Inner(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.expr:
            if node is root:
                return self.generic_visit(node)
            cse = plan._cse_by_key.get(plan._call_key(node))
            if cse is not None:
                return ast.copy_location(
                    ast.Name(id=cse.name, ctx=ast.Load()), node)
            return self.generic_visit(node)

    return _Inner().visit(root)


def _aggregate_expr(node: FoldNode, partition: list[str]) -> pl.Expr:
    """聚合项 → 组内广播列表达式（逐字节复刻 minute_ops day_* 旧实现语义：
    sum/mean/max/min 单 over；day_first/day_last 双 over 极值定位）。"""
    col = pl.col(node.arg_temp)
    if node.op in _AGG_METHOD:
        return (getattr(col, _AGG_METHOD[node.op])().over(partition)
                .alias(node.result_temp))
    if node.op == "day_last":
        mmax = pl.col(_ORDER).max().over(partition)
        return (pl.when(pl.col(_ORDER) == mmax).then(col).otherwise(None)
                .max().over(partition).alias(node.result_temp))
    if node.op == "day_first":
        mmin = pl.col(_ORDER).min().over(partition)
        return (pl.when(pl.col(_ORDER) == mmin).then(col).otherwise(None)
                .min().over(partition).alias(node.result_temp))
    raise _Fallback(f"未知折日算子: {node.op}")


def _is_sorted(df: pl.DataFrame, keys: list[str]) -> bool:
    """(code, date, minute_index) 物理序检查（seq_* 物理序等价的前提）。"""
    try:
        return bool(df.select(pl.struct(keys).is_sorted()).item())
    except Exception:  # noqa: BLE001 —— 检查本身失败 → 保守排序
        return False


def try_fused(
    df: pl.DataFrame,
    formula: str,
    *,
    date: str = "date",
    asset: str = "code",
    outputs: list[str] | None = None,
    extra_codes: str = "",
) -> pl.DataFrame | None:
    """融合路径入口：成功返回折日面板帧；不支持 → None（回退旧 codegen 路径）。

    `date`/`asset` 与 compute_formula 同参；缺列时回退（旧路径同报列缺失）。
    聚合用组内 over 广播（旧 day_* 同形；无 join/重排），`seq_*` 物理序变体
    要求 (asset, date, minute_index) 升序——已有序则零排序代价。
    """
    if date not in df.columns or asset not in df.columns:
        return None
    plan = build_plan(formula, columns=df.columns)
    if plan is None:
        return None
    keys = [asset, date, _ORDER]
    # 无 im_* 时 day_* 聚合与行序无关（sum/mean 保持输入物理序 = 旧路径口径）
    # → 零排序代价；有 seq_* 物理序变体才需要 (asset, date, minute_index) 升序。
    frame = df if not plan.has_im or _is_sorted(df, keys) else df.sort(keys)
    partition = [asset, date]
    codes = extra_codes + "\n" + SEQ_EXTRA_CODES
    for p in range(1, plan.max_pass + 1):
        src = plan.pass_source(p)
        frame = codegen_exec(
            frame.lazy(), src, over_null="partition_by", style="polars",
            date=date, asset=asset, extra_codes=codes).collect()
        frame = frame.with_columns(
            [_aggregate_expr(n, partition) for n in plan.nodes_in_pass(p)])
    final = plan.final_source(outputs if outputs is not None else ["signal"])
    return codegen_exec(
        frame.lazy(), final, over_null="partition_by", style="polars",
        date=date, asset=asset, extra_codes=codes).collect()



