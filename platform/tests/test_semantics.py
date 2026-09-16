import ast

import pytest

from factorlab.core.engine.semantics import NodeInfo, SemanticError, infer
from factorlab.core.ops.classification import default_catalog

CAT = default_catalog()


def info(src: str) -> NodeInfo:
    """最外层目标赋值（signal 优先，否则 _x）的值节点推断结果。

    infer 直接吃 ast.parse 产物（与测试共用同一棵树，id() 键稳定可靠）。
    """
    tree = ast.parse(src)
    nodes = infer(tree, CAT)
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and isinstance(n.targets[0], ast.Name)]
    target = next((n.value for n in assigns if n.targets[0].id == "signal"), None)
    if target is None:
        target = next(n.value for n in assigns if n.targets[0].id == "_x")
    return nodes[id(target)]


def test_nodeinfo_fields():
    i = NodeInfo(level="ts", keys=("code",), order="date", lookback=5,
                 forward=0, unbounded=False)
    assert i.level == "ts" and i.lookback == 5


def test_ts_window():
    assert info("signal = ts_mean(close, 20)").lookback == 20
    assert info("signal = ts_mean(close, 20)").level == "ts"


def test_nested_ts_lookback_additive():
    # 求和口径（父窗 + 子回看），与旧 _ts_window_days 一致（预热只多不少）；
    # 计划字面值 24 是"父窗+子窗-1"口径，会比旧实现少 1 天预热 → 零迁移风险，故取 25。
    assert info("signal = ts_delta(ts_mean(close, 20), 5)").lookback == 25


def test_cs_of_ts_level_cs():
    i = info("signal = cs_rank(ts_delta(close, 1))")
    assert i.level == "cs" and i.lookback == 1


def test_gp_keys():
    # 平台契约 gp_rank(key, x)（platform_ops.py；掩码表 gp_rank=(1,) 即数据在 arg1）
    i = info("signal = gp_rank(industry, ts_mean(close, 20))")
    assert i.level == "gp" and i.lookback == 20
    assert i.keys == ("date", "industry")


def test_method_window():
    i = info("signal = close.rolling_mean(5)")
    assert i.level == "ts" and i.lookback == 5


def test_unbounded():
    assert info("signal = ts_cum_sum(volume)").unbounded is True
    assert info("signal = ts_mean(close, 5)").unbounded is False


def test_unknown_operator_message():
    # R05-I2：指引不得承诺未实现的 op_meta 机制——指向 def / op add 插件
    with pytest.raises(SemanticError, match="op add") as exc:
        infer("signal = my_magic(close)", CAT)
    assert "op_meta" in str(exc.value) and "尚未实现" in str(exc.value)


def test_denied_method_guidance():
    with pytest.raises(SemanticError, match="by="):
        infer("signal = close.rank()", CAT)
    with pytest.raises(SemanticError, match="未来"):
        infer("signal = close.backward_fill()", CAT)


def test_unknown_method_message():
    with pytest.raises(SemanticError, match="op add"):
        infer("signal = close.not_a_method()", CAT)


def test_elementwise_inherits_outermost_level():
    i = info("signal = abs(ts_mean(close, 20)) + 1")
    assert i.level == "ts" and i.lookback == 20


def test_subscript_lookback_and_forward():
    assert info("signal = close[1]").lookback == 1
    assert info("signal = close[-1]").forward == 1
    assert info("signal = close[-0]").forward == 0


def test_negative_window_sets_forward_with_const_folding():
    assert info("signal = ts_delay(close, -1)").forward == 1
    assert info("_n = 3\nsignal = ts_delay(close, -_n)").forward == 3
    assert info("signal = close.shift(-2)").forward == 2


def test_forward_propagates_through_nesting():
    i = info("signal = ts_mean(close.shift(-1), 5)")
    assert i.forward == 1 and i.lookback == 5
    i2 = info("signal = ts_delta(ts_mean(close, 5), -2)")
    assert i2.forward == 2 and i2.lookback == 5


def test_alias_import_resolved():
    i = info("from polars_ta.prefix.wq import ts_mean as tm\nsignal = tm(close, 7)")
    assert i.level == "ts" and i.lookback == 7


def test_defined_function_calls_inherit_children():
    src = "def f(x):\n    return x * 2\nsignal = f(close)"
    i = info(src)
    assert i.level == "el" and i.lookback == 0


def test_error_carries_location():
    with pytest.raises(SemanticError) as exc:
        infer("signal = nope(close)", CAT)
    assert exc.value.line == 1


def test_variable_chain_resolved():
    i = info("_x = ts_mean(close, 20)\nsignal = abs(_x) + 1")
    assert i.level == "ts" and i.lookback == 20


def test_float_window_lookback_ignored_and_negative_forward():
    # 旧 _ts_window_days 只认 int 窗口（2.5 → 0）；负 float 仍进未来门
    assert info("signal = ts_mean(close, 2.5)").lookback == 0
    assert info("signal = ts_delay(close, -1.0)").forward == 1


# ==================== R07-LINT-I7: 库函数 arity 静态校验 ====================


def test_arity_rejects_excess_positional_args():
    # ts_cum_count(x)：只接受 1 个位置参数——多传在静态期报错（含函数名/期望/实际）
    with pytest.raises(SemanticError) as ei:
        infer("signal = ts_cum_count(close, 5)", CAT)
    msg = str(ei.value)
    assert "ts_cum_count" in msg
    assert "1" in msg and "2" in msg
    assert ei.value.line == 1


def test_arity_rejects_missing_required_args():
    with pytest.raises(SemanticError, match="ts_mean"):
        infer("signal = ts_mean()", CAT)


def test_arity_accepts_signature_range():
    assert info("signal = ts_cum_count(close)").level == "ts"
    assert info("signal = ts_mean(close, 20)").lookback == 20
    assert info("signal = ts_corr(close, volume, 10)").lookback == 10
    # keyword 形式窗口（总参数计数覆盖）不误杀
    assert info("signal = ts_delay(close, d=-1)").forward == 1


def test_arity_unknown_metadata_passes():
    # 平台注册面无 arity 元数据 → 保持放行（不误杀已知算子面）
    assert info("signal = cs_rank(close)").level == "cs"
