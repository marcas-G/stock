"""Task 5：未来函数门统一（全形态）——check_causality / reject_future_shifts。

规则（设计 §7.1）：任何 `forward > 0` 的调用/下标拒绝，含
- 字面量 / 常量折叠（`1-3`、`-_n`）/ import 别名 / keyword 窗口；
- 方法窗体（`.shift(-1)` / `.diff(-2)` / `.rolling_mean(-5)` / `.pct_change(-1)`）；
- 下标语法糖（`close[-1]` / `close[-_n]`）。

合法对照：`close[-0]` / `close[1]` / `close.shift(1)` / `ts_delay(close, _n)`（_n=3）。
"""

import pytest

from factorlab.core.engine.partitions import check_causality, reject_future_shifts
from factorlab.core.factor.errors import FactorDSLError
from factorlab.core.ops.classification import default_catalog

CAT = default_catalog()

BAD = [
    "signal = ts_delay(close, -1)",
    "_n = 3\nsignal = ts_delay(close, -_n)",
    "signal = ts_delay(close, 1-3)",
    "signal = close[-1]",
    "_n = 3\nsignal = close[-_n]",
    "signal = close.shift(-1)",
    "signal = close.diff(-2)",
    "signal = close.rolling_mean(-5)",
    "signal = ts_rank(close, -20)",
    "_n = 3\nsignal = ts_delta(close, -_n)",
    "signal = close.pct_change(-1)",
    "signal = close[-2] + open",
]

GOOD = [
    "signal = close[-0]",
    "signal = close[1]",
    "signal = close.shift(1)",
    "_n = 3\nsignal = ts_delay(close, _n)",
]


@pytest.mark.parametrize("src", BAD)
def test_rejects_future_all_forms(src):
    with pytest.raises(FactorDSLError, match="负位移"):
        check_causality(src, CAT)


@pytest.mark.parametrize("src", BAD)
def test_reject_future_shifts_legacy_entry(src):
    with pytest.raises(FactorDSLError, match="负位移"):
        reject_future_shifts(src)


@pytest.mark.parametrize("src", GOOD)
def test_allows_past_and_unknown_forms(src):
    check_causality(src, CAT)
    reject_future_shifts(src)


def test_error_carries_source_location():
    with pytest.raises(FactorDSLError) as exc:
        check_causality("signal = close.shift(-1)", CAT)
    assert exc.value.line == 1
    assert exc.value.col is not None


def test_error_location_on_later_line():
    with pytest.raises(FactorDSLError) as exc:
        check_causality("_x = close + 1\nsignal = ts_delay(close, -2)", CAT)
    assert exc.value.line == 2


def test_nested_negative_window_caught_at_innermost():
    with pytest.raises(FactorDSLError, match="shift"):
        check_causality("signal = ts_mean(close.shift(-1), 5)", CAT)


def test_positive_nested_windows_allowed():
    check_causality("signal = ts_mean(ts_delay(close, 3), 5)", CAT)


def test_unknown_op_lenient_in_legacy_entry():
    # 旧入口保持"未知算子不因未来门报错"的既有分工（未知由 validate 管），
    # 但未知算子**包着的**负位移仍必须被抓住（旧门独立遍历的等价行为）
    reject_future_shifts("signal = my_plugin(close)")
    with pytest.raises(FactorDSLError, match="负位移"):
        reject_future_shifts("signal = my_plugin(ts_delay(close, -1))")


def test_unknown_op_strict_entry_rejects():
    from factorlab.core.engine.semantics import SemanticError
    with pytest.raises(SemanticError, match="op_meta"):
        check_causality("signal = my_plugin(close)", CAT)
