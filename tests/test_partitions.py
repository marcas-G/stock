import pytest

from factorlab.core.engine.partitions import reject_future_shifts, validate_partition_calls
from factorlab.core.factor.errors import FactorDSLError
from factorlab.core.ops import registry
from factorlab.core.ops.platform_ops import inline_defs, register_platform_ops
from factorlab.core.ops.polars_ta_wrappers import register_polars_ta_ops


@pytest.fixture(autouse=True)
def _registered_ops():
    registry.reset_registry()
    register_polars_ta_ops()
    register_platform_ops()
    from factorlab.core.ops.stable_rank import register_stable_rank_ops
    register_stable_rank_ops()   # M6-07C2J：cs_rank canonical 归平台 stable


def test_allows_known_prefixed_calls():
    validate_partition_calls("signal = ts_mean(close, 20) + cs_rank(close)")


def test_allows_platform_thin_ops():
    validate_partition_calls("signal = returns(close) + adv20(volume)")


def test_allows_elementwise_functions():
    validate_partition_calls("signal = abs(close - open) + log(volume) / sqrt(abs(close))")


def test_allows_inline_def_with_elementwise_ops():
    validate_partition_calls(
        "def flip(x, n):\n    return x * n\n"
        "signal = flip(close, 5)"
    )


def test_rejects_window_op_inside_def():
    # guard 保留为安全网：直接路径（不经 inline_defs）的 def 内窗口算子仍拒绝
    with pytest.raises(ValueError, match="def"):
        validate_partition_calls(
            "def momentum(x, n):\n    return ts_delay(x, n) / ts_delay(x, 2 * n) - 1\n"
            "signal = momentum(close, 5)"
        )


def test_inlined_def_with_window_ops_allowed():
    # def 内窗口算子经 inline_defs 内联后合法：def 已删除 → guard 不触发
    src = ("def momentum(x, n):\n    return ts_delay(x, n) / ts_delay(x, 2 * n) - 1\n"
           "signal = momentum(close, 5)")
    out = inline_defs(src)
    assert "def momentum" not in out  # 内联后 def 无残留
    validate_partition_calls(out)


def test_rejects_unknown_operator():
    with pytest.raises(ValueError):
        validate_partition_calls("signal = not_real_operator(close)")


def test_rejects_negative_delay():
    with pytest.raises(ValueError):
        reject_future_shifts("signal = ts_delay(close, -1)")


def test_rejects_negative_delta():
    with pytest.raises(ValueError):
        reject_future_shifts("signal = ts_delta(close, -5)")


def test_allows_positive_delay():
    reject_future_shifts("signal = ts_delay(close, 5)")


def test_errors_carry_source_location():
    with pytest.raises(FactorDSLError) as exc_info:
        reject_future_shifts("signal = ts_delay(close, -1)")
    assert exc_info.value.line == 1


def test_rejects_folded_negative_delay():
    with pytest.raises(ValueError):
        reject_future_shifts("signal = ts_delay(close, 1 - 2)")


def test_rejects_keyword_negative_delay():
    with pytest.raises(ValueError):
        reject_future_shifts("signal = ts_delay(close, d=-1)")


def test_rejects_float_negative_delay():
    with pytest.raises(ValueError):
        reject_future_shifts("signal = ts_delay(close, -1.0)")


def test_allows_variable_shift_and_positive_expr():
    reject_future_shifts("signal = ts_delay(close, n)")


def test_rejects_negative_delay_via_top_level_const():
    # AI 生成常见形态：位移量走命名常量——常量赋值间接的负位移必须同样拒绝
    with pytest.raises(ValueError, match="负位移"):
        reject_future_shifts("_d = -3\nsignal = ts_delay(close, _d)")
    with pytest.raises(ValueError, match="负位移"):
        reject_future_shifts("_d = -2\nsignal = ts_delta(close, d=_d)")


def test_rejects_negative_delay_via_folded_const_expr():
    with pytest.raises(ValueError, match="负位移"):
        reject_future_shifts("_d = 1 - 4\nsignal = ts_delay(close, _d)")


def test_rejects_negative_delay_via_alias_import():
    # import 别名绕过函数名匹配（validate_partition_calls 有 alias 解析，此门缺失）
    with pytest.raises(ValueError, match="负位移"):
        reject_future_shifts(
            "from polars_ta.prefix import ts_delay as td\nsignal = td(close, -2)")
    with pytest.raises(ValueError, match="负位移"):
        reject_future_shifts(
            "from polars_ta.prefix import ts_delta as d\n"
            "signal = ts_delay(close, 5) + d(close, d=-3)")


def test_allows_positive_const_and_reassignment():
    # 正常量放行；末尾重赋值覆盖为正值时不再拒绝（last-wins 静态近似）
    reject_future_shifts("_d = 3\nsignal = ts_delay(close, _d)")
    reject_future_shifts("_d = -3\n_d = 5\nsignal = ts_delay(close, _d)")


def test_import_alias_resolves_to_known_op():
    validate_partition_calls(
        "from factorlab.core.ops.platform_ops import returns as ret\nsignal = ret(close)"
    )


def test_import_alias_of_window_op_inside_def_rejected():
    with pytest.raises(ValueError, match="def"):
        validate_partition_calls(
            "from polars_ta.prefix.wq import ts_delay as d\n"
            "def f(x):\n    return d(x, 1)\n"
            "signal = f(close)"
        )
