"""测试用算子模块（非插件目录，故绕过插件命名门）：裸名 vs ts_ 前缀同名实现。"""
from factorlab.core.ops.registry import factor_op


@factor_op("bare_op", kind="ts", version="0.1.0")
def bare_op(x, n):
    return x.rolling_mean(window_size=n)


@factor_op("ts_op", kind="ts", version="0.1.0")
def ts_op(x, n):
    return x.rolling_mean(window_size=n)
