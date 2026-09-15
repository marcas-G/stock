import polars as pl
import pytest

from factorlab.core.factor.errors import FactorDSLError
from factorlab.core.ops import registry
from factorlab.core.ops.platform_ops import (
    adv20,
    expand_platform_macros,
    expand_user_macros,
    gp_rank,
    register_platform_ops,
    returns,
    vwap,
)
from factorlab.core.spec import OperatorMacro


def _ops(**kwargs):
    return {name: OperatorMacro(**cfg) for name, cfg in kwargs.items()}


def test_platform_ops_return_expr():
    assert isinstance(returns(pl.col("close")), pl.Expr)
    assert isinstance(vwap(pl.col("high"), pl.col("low"), pl.col("close"), pl.col("volume")), pl.Expr)
    assert isinstance(adv20(pl.col("volume")), pl.Expr)
    assert isinstance(gp_rank(pl.col("industry"), pl.col("close")), pl.Expr)


def test_register_platform_ops_exposes_ops():
    registry.reset_registry()
    register_platform_ops()
    for name, kind in (("returns", "ts"), ("vwap", "ts"), ("adv20", "ts"), ("gp_rank", "gp"), ("gp_mean", "gp")):
        assert registry.get_op(name).kind == kind


def test_platform_macro_names_single_point():
    # R03-M1：ast gate 的宏名单与注册面同源（单点声明，防名单漂移）
    from factorlab.core.ops.platform_ops import PLATFORM_MACRO_NAMES
    registry.reset_registry()
    register_platform_ops()
    assert PLATFORM_MACRO_NAMES == frozenset(
        {"returns", "vwap", "adv20", "gp_rank", "gp_mean"})
    for name in PLATFORM_MACRO_NAMES:
        assert registry.has_op(name)


def test_expand_platform_macros_still_handles_aliases():
    # transform 级契约（gate 之前的语义）：别名调用仍被展开——gate 与展开器互不耦合，
    # 防"门前拒绝"改动误伤展开器本身
    out = expand_platform_macros(
        "from polars_ta.prefix.wq import returns as ret\nsignal = ret(close)")
    assert "ts_delay" in out
    assert "ret(close)" not in out


# ---- expand_user_macros（spec.operators 内联宏） ----


def test_expand_user_macros_basic():
    out = expand_user_macros(
        "signal = mom_ratio(close, 1)",
        _ops(mom_ratio={"params": ["x", "n"], "formula": "delay(x, n) / delay(x, 2 * n) - 1"}),
    )
    assert "mom_ratio" not in out
    assert "delay(close, 1) / delay(close, 2 * 1) - 1" in out


def test_expand_user_macros_no_operators_returns_source():
    src = "signal = close / open - 1"
    assert expand_user_macros(src, {}) is src


def test_expand_user_macros_arg_count_mismatch():
    with pytest.raises(FactorDSLError, match="mom_ratio 需要 2 个参数，实际 1 个"):
        expand_user_macros(
            "signal = mom_ratio(close)",
            _ops(mom_ratio={"params": ["x", "n"], "formula": "delay(x, n) - 1"}),
        )


def test_expand_user_macros_def_same_name_priority():
    # 公式内 def 同名函数优先：不展开宏
    src = "def mom_ratio(x, n):\n    return x - 1\nsignal = mom_ratio(close, 1)"
    out = expand_user_macros(
        src,
        _ops(mom_ratio={"params": ["x", "n"], "formula": "delay(x, n) - 1"}),
    )
    assert "def mom_ratio(x, n):" in out
    assert "delay(" not in out


def test_expand_user_macros_param_substring_safe():
    # 短参数名（n）不能误替换 ts_mean/ts_min 等标识符中的子串
    out = expand_user_macros(
        "signal = ma(close, 5)",
        _ops(ma={"params": ["x", "n"], "formula": "ts_mean(x, n) - ts_min(x, n)"}),
    )
    assert "ts_mean(close, 5) - ts_min(close, 5)" in out


def test_expand_user_macros_precedence_preserved():
    # 实参为复合表达式时运算优先级由 AST 结构保证（无需手工加括号）
    out = expand_user_macros(
        "signal = scale(close - open)",
        _ops(scale={"params": ["x"], "formula": "x / ts_mean(x, 20)"}),
    )
    assert out == "signal = (close - open) / ts_mean(close - open, 20)"


def test_expand_user_macros_can_reference_platform_wrapper():
    # 用户宏公式可引用平台薄封装 returns：随后由 expand_platform_macros 展开为 ts_ 表达式
    out = expand_user_macros(
        "signal = ret(close)",
        _ops(ret={"params": ["x"], "formula": "returns(x)"}),
    )
    assert "returns(close)" in out
    out2 = expand_platform_macros(out)
    assert "returns" not in out2
    assert "close / ts_delay(close, 1) - 1" in out2


def test_expand_user_macros_syntax_error_rejected():
    with pytest.raises(FactorDSLError, match="语法错误"):
        expand_user_macros(
            "signal = mom_ratio(close, 1",
            _ops(mom_ratio={"params": ["x", "n"], "formula": "delay(x, n) - 1"}),
        )


def test_expand_user_macros_macro_formula_syntax_error_rejected():
    with pytest.raises(FactorDSLError, match="展开失败"):
        expand_user_macros(
            "signal = mom_ratio(close, 1)",
            _ops(mom_ratio={"params": ["x", "n"], "formula": "delay(x, n) -"}),
        )
