"""Task 6：窗口/分块统一——lookback 由语义推断驱动；unbounded 与分块互斥。

- `required_lookback(formula, pool, catalog)`：公式与池公式 lookback 最大值
  （变量引用链、嵌套窗口、方法窗口全部经 infer）；
- `unbounded_ops(formula, pool, catalog)`：全历史累计/递归算子清单（分类表驱动）；
- `reject_cumulative_chunking` 改由该清单驱动（消息保留"累计算子"指引）；
- `_ts_window_days` 兼容入口 = max(推断 lookback, 旧前缀回退)——插件 ts_ 算子
  与 `wq.ts_sum` 模块限定名仍可提取（存量测试契约），catalog 算子走推断。
"""

import pytest

from factorlab.core.engine.compute import (_ts_window_days, reject_cumulative_chunking,
                                           required_lookback, unbounded_ops)
from factorlab.core.ops.classification import default_catalog

CAT = default_catalog()


def test_lookback_via_inference():
    assert required_lookback("signal = ts_delta(ts_mean(close, 20), 5)", None, CAT) >= 24
    # 窗口在第 3 位（ts_corr(x, y, d)）——签名感知推断
    assert required_lookback("signal = ts_corr(volume, amount, 120)", None, CAT) == 120


def test_lookback_follows_variable_chain():
    # 存量因子形态：中间变量引用链必须叠加（旧 _ts_window_days 的核心行为）
    assert required_lookback("_x = ts_mean(close, 20)\nsignal = _x * 2", None, CAT) == 20


def test_lookback_pool_takes_max():
    pool = "signal = ts_mean(close, 60) > 0"
    assert required_lookback("signal = ts_mean(close, 5)", pool, CAT) == 60


def test_unbounded_blocks_chunking():
    assert "ts_cum_sum" in unbounded_ops("signal = ts_cum_sum(volume)", None, CAT)


def test_unbounded_method_and_recursive_and_override():
    assert ".cum_sum" in unbounded_ops("signal = close.cum_sum()", None, CAT)
    assert ".ewm_mean" in unbounded_ops("signal = close.ewm_mean(span=5)", None, CAT)
    # 分类表人工覆盖：keyword-only 窗口的滚动回归保守计 unbounded
    assert "ts_resid" in unbounded_ops("signal = ts_resid(close, volume)", None, CAT)


def test_unbounded_pool_and_import_alias():
    pool = ("from polars_ta.prefix.wq import ts_cum_sum as cs\n"
            "signal = cs(close) > 0")
    assert unbounded_ops("signal = close", pool, CAT) == ["ts_cum_sum"]


def test_finite_windows_not_unbounded():
    assert unbounded_ops("signal = ts_mean(close, 20)", None, CAT) == []
    assert unbounded_ops("signal = close.shift(5)", None, CAT) == []


def test_reject_cumulative_chunking_catalog_driven():
    with pytest.raises(ValueError, match="累计算子"):
        reject_cumulative_chunking("signal = close.cum_sum()")
    with pytest.raises(ValueError, match="累计算子"):
        reject_cumulative_chunking("signal = ts_mean(close, 5)",
                                   "signal = ts_cum_max(close) > 0")
    reject_cumulative_chunking("signal = ts_mean(close, 20)")


def test_ts_window_days_backward_compat_prefix_fallback():
    # 存量契约：插件 ts_ 前缀窗口/模块限定名/float 非整窗
    assert _ts_window_days("signal = ts_mean(close, 20)") == 20
    assert _ts_window_days("signal = ts_op(close, 60)") == 60
    assert _ts_window_days("signal = ts_mean(close, 2.5)") == 0
    assert _ts_window_days("signal = wq.ts_sum(close, 10) + ta_MA(close, 5)") == 10
    # 新增：推断链上的窗口（变量引用）同样可见
    assert _ts_window_days("_x = ts_delta(ts_mean(close, 20), 5)\nsignal = _x") == 25
