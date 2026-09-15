"""Register polars_ta operator families (wq / ta / tdx) into the operator registry."""

from __future__ import annotations

import ast

from polars_ta.prefix import ta, tdx, wq

from factorlab.core.ops.registry import factor_op


_WQ_TS = (
    "ts_delay", "ts_delta", "ts_mean", "ts_std_dev", "ts_sum", "ts_product",
    "ts_min", "ts_max", "ts_median", "ts_rank", "ts_zscore",
    "ts_corr", "ts_covariance", "ts_skewness", "ts_kurtosis",
    "ts_cum_sum", "ts_cum_max", "ts_cum_min", "ts_cum_prod",
    "ts_count",
)

# M6-07C2J：cs_rank 从 vendor 自动注册移除——canonical 名归平台 stable
# dense rank（cs_stable_rank，version 0.2.0，aliases=("cs_rank",)）。
# registry 不得再暴露 vendor 0.1.0 的 cs_rank 双重语义。
_WQ_CS = (
    "cs_zscore", "cs_demean", "cs_scale", "cs_quantile",
    "cs_mad_zscore",
)

_TA_NAMES = ("ts_RSI", "ts_ATR", "ts_MACD", "ts_WILLR", "ts_TRIX")
_TDX_NAMES = ("ts_BIAS", "ts_KDJ", "ts_BOLL", "ts_RSV")

# polars_ta 0.5.17 变更：cs_regression_resid 更名为 cs_resid；CCI 移入 tdx 族。
# R01-ENG-I4：canonical 名 = vendor 符号 cs_resid（注册表 + cs_ 前缀门 + masking
# 声明都按它落地）；旧名 cs_regression_resid 注册为 registry alias（门/掩码/
# catalog 可见），公式文本经 rewrite_polars_ta_aliases 改写为 cs_resid——生成
# 代码只用 `from polars_ta.prefix.wq import *` 的 vendor 符号，直接生成旧名会
# NameError（旧实现只注册旧名，调用即崩）。
_CS_RESID_ALIASES = ("cs_regression_resid",)
_WQ_ALIAS_REWRITES = {"cs_regression_resid": "cs_resid"}
_TA_ALIASES = {"ts_CCI": tdx.ts_CCI}


def register_polars_ta_ops() -> None:
    for name in _WQ_TS:
        factor_op(name, kind="ts", version="0.1.0")(getattr(wq, name))
    for name in _WQ_CS:
        factor_op(name, kind="cs", version="0.1.0")(getattr(wq, name))
    factor_op("cs_resid", kind="cs", version="0.1.0",
              aliases=_CS_RESID_ALIASES)(wq.cs_resid)
    for name in _TA_NAMES:
        factor_op(name, kind="ta", version="0.1.0")(getattr(ta, name))
    for name, func in _TA_ALIASES.items():
        factor_op(name, kind="ta", version="0.1.0")(func)
    for name in _TDX_NAMES:
        factor_op(name, kind="ta", version="0.1.0")(getattr(tdx, name))


def rewrite_polars_ta_aliases(source: str) -> str:
    """把 registry 兼容别名调用改写为 canonical vendor 名（R01-ENG-I4）。

    expr_codegen 生成代码只有 `from polars_ta.prefix.wq import *`——vendor 0.5.17
    无 cs_regression_resid 符号，直接生成该名会 NameError。改写为 cs_resid 后由
    star import 解析；用户 def 同名优先（不改写），import alias 解析后判断。
    """
    tree = ast.parse(source)
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    class _Rewriter(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.expr:
            node = self.generic_visit(node)
            if not isinstance(node.func, ast.Name):
                return node
            name = aliases.get(node.func.id, node.func.id)
            canonical = _WQ_ALIAS_REWRITES.get(name)
            if canonical is not None and name not in defined:
                node.func = ast.Name(id=canonical, ctx=ast.Load())
            return node

    return ast.unparse(_Rewriter().visit(tree))
