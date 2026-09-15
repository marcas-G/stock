import pytest

from factorlab.core.factor.ast_gate import validate_formula
from factorlab.core.factor.errors import FactorDSLError


def test_allows_def_import_assignment_and_ternary():
    source = '''
from polars_ta.prefix.wq import ts_delay, ts_mean

def mom(x, n):
    return ts_delay(x, n) / ts_delay(x, 2 * n) - 1

_m = ts_mean(close, 20)
signal = _m if _m > 0 else -_m
'''
    validate_formula(source)


def test_rejects_for_loop():
    with pytest.raises(FactorDSLError):
        validate_formula("for i in range(10):\n    pass\n")


def test_rejects_os_import():
    with pytest.raises(FactorDSLError):
        validate_formula("import os\nsignal = close\n")


def test_rejects_eval_call():
    with pytest.raises(FactorDSLError):
        validate_formula("signal = eval('close')\n")


def test_rejects_attribute_call_io():
    with pytest.raises(FactorDSLError):
        validate_formula("import polars as pl\nsignal = pl.read_csv('x.csv')\n")


def test_allows_elementwise_method_chain_on_call():
    # free-form 设计（§2.1）：纯元素级方法可链式调用在表达式结果上（ts_delta(x, 1).abs()）
    validate_formula("signal = ts_delta(close, 1).abs()\n")


def test_rejects_window_method_attribute_call():
    # 窗口方法（rolling_mean）不在纯元素级白名单——窗口语义必须走 ts_* 算子（分区安全）
    with pytest.raises(FactorDSLError, match="属性调用"):
        validate_formula("signal = close.rolling_mean(20)\n")


def test_rejects_attribute_call_on_bare_name():
    # 模块/对象属性调用（np.abs）仍被拒：基表达式为裸 Name（非函数调用结果）
    with pytest.raises(FactorDSLError, match="属性调用"):
        validate_formula("signal = np.abs(close)\n")


# ── R03-M1：平台宏（returns/vwap/adv20/gp_rank/gp_mean）必须裸用，import 即报清晰错误 ──

def test_rejects_platform_macro_import_from_polars_ta():
    # 误 import 宏 → 修复前走 expr_codegen exec 裸堆栈（ImportError）
    with pytest.raises(FactorDSLError, match="平台宏 returns 请裸用"):
        validate_formula("from polars_ta.prefix.wq import returns\nsignal = returns(close)\n")


def test_rejects_platform_macro_import_from_platform_ops():
    # “从任何模块 import 平台宏名”都在门前拒绝（含定义模块）——统一裸用入口
    with pytest.raises(FactorDSLError, match="平台宏 gp_rank 请裸用"):
        validate_formula(
            "from factorlab.core.ops.platform_ops import gp_rank\n"
            "signal = gp_rank(industry, close)\n")


def test_rejects_platform_macro_import_alias():
    with pytest.raises(FactorDSLError, match="平台宏 vwap 请裸用"):
        validate_formula(
            "from polars_ta.prefix.wq import vwap as v\n"
            "signal = v(high, low, close, volume)\n")


def test_rejects_import_of_every_platform_macro_name():
    for name in ("returns", "vwap", "adv20", "gp_rank", "gp_mean"):
        with pytest.raises(FactorDSLError, match=f"平台宏 {name} 请裸用"):
            validate_formula(f"from polars_ta.prefix.wq import {name}\nsignal = close\n")


def test_allows_bare_platform_macro_and_regular_polars_ta_import():
    # 正向控制：宏裸用 + polars_ta 常规算子 import 仍放行（不得把门放宽成全拒/收紧成误杀）
    validate_formula(
        "from polars_ta.prefix.wq import ts_mean\n"
        "signal = returns(close) + ts_mean(close, 20)\n")
