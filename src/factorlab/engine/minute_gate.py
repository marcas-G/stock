"""分钟 scope 静态门（bars_1m 公式，2026-09-08 规格 B2.4/B3.2/B3.3/B3.4 + 实现期
修订）。compute_formula(scope="bars_1m") 变换链完成后调用（宏展开/def 内联后文本）。

- B3.2 禁 ts_/ta_/cs_/gp_ 前缀调用（returns/vwap/adv20 宏展开为 ts_ 后同样命中）。
- 输出折日门（B2.4/B3.3 合一 + 修订）：每个 declared output 的顶层赋值 RHS 必须
  折日常数表达式——day_* 调用/注入列/常量/常量变量/算术比较组合为常数；im_*
  调用、裸 bars 列、分钟序列组合为序列。day_* 可在公式任意位置（中间赋值共享
  合法）；拒绝形态 = 信号含分钟序列成分（im_* 直出、裸列、未折日中间量）。
- B3.4 im_delay 位移 k<=0 拒（负 = 未来、零 = 无意义）；im_* 窗口参数 < 1 拒
  （字面量/常量算术折叠 2-3→-1/顶层常量间接/kw 形态全覆盖）。
"""
from __future__ import annotations

import ast

_CROSS_PREFIXES = ("ts_", "ta_", "cs_", "gp_")
_MINUTE_PREFIXES = ("im_", "day_")
# 日级注入列 v1（B6.1）：组内每行同值——折日表达式的常数成分
_INJECTED_COLS = frozenset({"prev_close", "eod_close", "day_amt", "day_vol",
                            "adv20_amt", "adv20_vol"})
# 分钟序列成分（裸引用 = 非折日）：bars 列 + 帧结构列
_SERIES_COLS = frozenset({"datetime", "minute_index", "session_type", "open",
                          "high", "low", "close", "amount", "volume"})
_DAY_CALLS = frozenset({"day_last", "day_first", "day_sum", "day_mean",
                        "day_max", "day_min"})
_IM_CALLS = frozenset({"im_mean", "im_sum", "im_std", "im_max", "im_min",
                       "im_median", "im_delay"})
# 一元保常数函数（元素级单参；参数折日常数 → 结果折日常数）
_UNARY_CONST = frozenset({"abs", "log", "log1p", "sqrt", "exp", "sign", "floor"})


def _try_num(node: ast.expr, consts: dict[str, int | float]) -> int | float | None:
    """常量算术折叠：字面量/一元 ±/Name-in-consts/BinOp 四则（含整除/取模）→ 数值
    或 None（含非常量成分）。窗口/位移参数的算术形态必须静态可见：2-3 → -1，
    若放行则 codegen 生成 shift(-1) = 取未来行（B3.4 门漏）。Pow 不折叠（幂指数
    展开耗时且门不需要）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _try_num(node.operand, consts)
        return -v if v is not None and isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        left, right = _try_num(node.left, consts), _try_num(node.right, consts)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        return None
    return None


def _top_consts(tree: ast.AST) -> dict[str, int | float]:
    """顶层赋值数值常量表（源序单遍，last-wins；先定义后引用——_a=1;_b=_a+2
    合法）。def/嵌套内不跟踪——保守放行（动态参数留运行时）。"""
    consts: dict[str, int | float] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            v = _try_num(node.value, consts)
            if v is not None:
                consts[node.targets[0].id] = v
    return consts


def _call_arg(node: ast.Call, kw_name: str) -> ast.expr | None:
    """第二位置参数或 kw（im_delay 用 d、窗口族用 window）。"""
    if len(node.args) >= 2:
        return node.args[1]
    for kw in node.keywords:
        if kw.arg == kw_name:
            return kw.value
    return None


def _call_names(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            yield node


def _reject_cross_layer(tree: ast.AST) -> None:
    for node in _call_names(tree):
        if node.func.id.startswith(_CROSS_PREFIXES):
            raise ValueError(
                f"bars_1m 公式不允许跨层算子 {node.func.id}（ts_/ta_/cs_/gp_ 是"
                f"日频通道——日内窗口请用 im_*、折日请用 day_*，见 docs/interface.md"
                f"分钟面）")


def _check_im_params(tree: ast.AST) -> None:
    """B3.4：im_delay 位移 <= 0 拒（负 = 未来/零 = 无意义）；im_* 窗口 < 1 拒。
    覆盖字面量/算术折叠/顶层常量间接/kw 形态（shift 位移负值若放行 = codegen
    生成 polars shift(-1) 取未来行——门即未来函数防线）。"""
    consts = _top_consts(tree)
    for node in _call_names(tree):
        name = node.func.id
        if name == "im_delay":
            arg = _call_arg(node, "d")
            k = _try_num(arg, consts) if arg is not None else None
            if k is not None and k < 0:
                raise ValueError(
                    f"im_delay 不允许负位移（{node.lineno}:{node.col_offset}，"
                    f"k={k}——lookback 只能取过去；日内位移 k>=1）")
            if k is not None and k == 0:
                raise ValueError(
                    f"im_delay 位移不能为 0（{node.lineno}:{node.col_offset}——"
                    f"无意义自引用；日内位移 k>=1）")
        elif name in _IM_CALLS:
            arg = _call_arg(node, "window")
            w = _try_num(arg, consts) if arg is not None else None
            if w is not None and w < 1:
                raise ValueError(
                    f"{name} 窗口参数必须 >= 1 分钟（{node.lineno}:"
                    f"{node.col_offset}，收到 {w}）")


def _top_assigns(tree: ast.AST) -> dict[str, ast.expr]:
    assigns: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            assigns[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigns[node.target.id] = node.value
    return assigns


def _fold_const(node: ast.expr, assigns: dict[str, ast.expr],
                memo: dict[int, bool]) -> bool:
    """折日常数判定：True = 表达式值逐 (code, date) 常数（可安全折日输出）。"""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float, str, bool)) or node.value is None
    if isinstance(node, ast.Name):
        c = node.id
        if c in _INJECTED_COLS:            # 日级注入列：每行同值
            return True
        if c in _SERIES_COLS:              # bars/帧列：分钟序列
            return False
        if c in assigns:
            return _fold_const(assigns[c], assigns, memo)
        return False                       # 未知名保守视为序列（compute 层报列缺失）
    t = type(node)
    if t is ast.BinOp:
        return _fold_const(node.left, assigns, memo) \
            and _fold_const(node.right, assigns, memo)
    if t is ast.UnaryOp:
        return _fold_const(node.operand, assigns, memo)
    if t is ast.BoolOp:
        return all(_fold_const(v, assigns, memo) for v in node.values)
    if t is ast.Compare:
        return all(_fold_const(cmp, assigns, memo) for cmp in node.comparators)
    if t is ast.Call:
        name = node.func.id
        if name in _DAY_CALLS:             # day_*：折日常数（子参数任意）
            return True
        if name in _IM_CALLS:              # im_*：分钟序列（必须经 day_* 折日）
            return False
        if name in _UNARY_CONST:
            return bool(node.args) and _fold_const(node.args[0], assigns, memo)
        if name == "if_else":
            args = [*node.args, *(kw.value for kw in node.keywords)]
            return bool(args) and all(_fold_const(a, assigns, memo) for a in args)
        return False
    return False


def validate_minute_scope(formula: str, outputs: list[str]) -> None:
    """bars_1m scope 静态门（宏/def 展开完成后文本上执行）。ValueError 带指引。"""
    tree = ast.parse(formula)
    _reject_cross_layer(tree)
    _check_im_params(tree)
    assigns = _top_assigns(tree)
    for out in outputs:
        rhs = assigns.get(out)
        if rhs is None:
            continue                        # outputs 声明缺失由 compute_formula 报
        if not _fold_const(rhs, assigns, {}):
            raise ValueError(
                f"输出 {out!r} 不是折日常数表达式（bars_1m 折日语义：信号必须"
                f"逐 (code, date) 常数——用 day_*(...) 折日、日级注入列/常量/"
                f"参数组合；im_* 或裸分钟列直出会被拒）。例如："
                f"vwap30 = im_sum(close*volume, 30)/im_sum(volume, 30); "
                f"signal = day_last(vwap30)/eod_close - 1")


def reject_minute_ops_in_daily(formula: str) -> None:
    """日频 scope（缺省 daily）：公式含 im_*/day_* 调用 → ValueError（分钟算子
    只在 interface: bars_1m；明确报错而非 codegen 深层 NameError）。"""
    tree = ast.parse(formula)
    for node in _call_names(tree):
        if node.func.id.startswith(_MINUTE_PREFIXES):
            raise ValueError(
                f"分钟算子 {node.func.id}（im_*/day_*）只在 interface: bars_1m "
                f"scope 可用（本公式是日频 scope——日内窗口/折日算子是分钟模板"
                f"接口；日频请用 ts_*/cs_*，见 docs/interface.md 分钟面）")
