import pytest
import polars as pl

from factorlab.core.engine.compute import _ts_window_days, compute_formula


def test_compute_formula_returns_signal_column():
    df = pl.DataFrame({
        "date": ["2020-01-01", "2020-01-01", "2020-01-01"],
        "code": ["A", "B", "C"],
        "close": [10.0, 20.0, 30.0],
        "open": [9.0, 19.0, 29.0],
    })
    formula = '''
from polars_ta.prefix.wq import ts_delay
signal = ts_delay(close, 1)
'''
    result = compute_formula(df, formula)
    assert result.columns == ["date", "code", "signal"]
    assert result.height == 3
    assert result["signal"].null_count() == 3


def test_compute_formula_supports_def_and_ternary():
    df = pl.DataFrame({
        "date": ["2020-01-02", "2020-01-03"],
        "code": ["A", "A"],
        "close": [10.0, 12.0],
        "open": [9.0, 13.0],
    })
    formula = '''
from polars_ta.prefix.wq import ts_delay

def flip(x, n):
    return x * n

_m = flip(close, 2)
signal = _m if _m > 0 else -_m
'''
    result = compute_formula(df, formula)
    assert result.columns == ["date", "code", "signal"]
    assert result.height == 2
    assert result["signal"].to_list() == [20.0, 24.0]


def test_compute_rejects_unknown_operator():
    with pytest.raises(ValueError):
        compute_formula(pl.DataFrame({"date": [], "code": [], "close": []}), "signal = nope(close)")


def test_compute_rejects_negative_lookback():
    with pytest.raises(ValueError):
        compute_formula(pl.DataFrame({"date": [], "code": [], "close": []}), "signal = ts_delay(close, -1)")


def test_compute_accepts_platform_thin_ops():
    df = pl.DataFrame({
        "date": ["2020-01-01", "2020-01-02"],
        "code": ["A", "A"],
        "close": [10.0, 12.0],
        "volume": [1000.0, 1100.0],
    })
    # R03-M1：平台宏/薄封装必须**裸用**（import 被 ast gate 拒绝）
    formula = '''
signal = returns(close) + adv20(volume)
'''
    result = compute_formula(df, formula)
    assert result.columns == ["date", "code", "signal"]


def test_compute_partitions_platform_ops_by_asset():
    # 2 资产面板：returns()/vwap() 展开后必须按 asset 分区，B 首行不得借用 A 数据
    df = pl.DataFrame({
        "date": ["2020-01-01", "2020-01-02", "2020-01-01", "2020-01-02"],
        "code": ["A", "A", "B", "B"],
        "close": [10.0, 12.0, 100.0, 110.0],
        "open": [9.0, 11.0, 90.0, 100.0],
        "volume": [100.0, 120.0, 1000.0, 1100.0],
    })
    r = compute_formula(df, "signal = returns(close)")
    assert r["signal"].null_count() == 2  # 每资产首行应为 null
    values = r.filter(pl.col("code") == "A").select("signal").to_series().to_list()
    assert values[0] is None
    assert values[1] == pytest.approx(0.2)


def test_compute_rejects_platform_macro_import_with_guidance():
    # R03-M1：误 import 平台宏在 codegen 前 fail fast（clearly FactorDSLError，
    # 修复前是 expr_codegen exec 阶段 ImportError 裸堆栈）
    from factorlab.core.factor.errors import FactorDSLError
    df = pl.DataFrame({"date": ["2020-01-01"], "code": ["A"], "close": [10.0]})
    with pytest.raises(FactorDSLError, match="平台宏 returns 请裸用"):
        compute_formula(df, "from polars_ta.prefix.wq import returns\nsignal = returns(close)")


def test_compute_def_with_window_op_inlined():
    # def 内窗口算子合法：compute_formula 内联展开为顶层 ts_ 调用——多资产窗口分区无泄漏
    df = pl.DataFrame({
        "date": ["2020-01-01", "2020-01-02", "2020-01-01", "2020-01-02"],
        "code": ["A", "A", "B", "B"],
        "close": [10.0, 12.0, 100.0, 110.0],
    })
    formula = '''
from polars_ta.prefix.wq import ts_delay

def mom(x, n):
    return ts_delay(x, 1) / x - 1

signal = mom(close, 1)
'''
    result = compute_formula(df, formula)
    assert result.columns == ["date", "code", "signal"]
    assert result["signal"].null_count() == 2  # 每资产首行 ts_delay null
    b = result.filter(pl.col("code") == "B").sort("date")["signal"].to_list()
    assert b[0] is None                        # B 首行不得借用 A 末行（分区正确）
    assert b[1] == pytest.approx(100.0 / 110.0 - 1)


def test_compute_elementwise_names_resolve():
    # 逐名核对：白名单内的元素级函数必须在 codegen 作用域真实可解析
    df = pl.DataFrame({
        "date": ["2020-01-01", "2020-01-02"],
        "code": ["A", "A"],
        "close": [10.0, 12.0],
    })
    for name, args in (
        ("abs", "(close - 11)"),
        ("log", "(close)"),
        ("log1p", "(close)"),
        ("sqrt", "(close)"),
        ("exp", "(close)"),
        ("sign", "(close)"),
        ("floor", "(close)"),
        ("if_else", "(close > 11, close, 0)"),
    ):
        result = compute_formula(df, f"signal = {name}{args}")
        assert result.columns == ["date", "code", "signal"]


def test_compute_vwap_cumulative_by_asset():
    df = pl.DataFrame({
        "date": ["2020-01-01", "2020-01-02", "2020-01-01", "2020-01-02"],
        "code": ["A", "A", "B", "B"],
        "close": [10.0, 12.0, 100.0, 110.0],
        "volume": [100.0, 120.0, 1000.0, 1100.0],
    })
    r = compute_formula(df, "signal = vwap(close, close, close, volume)")
    b = r.filter(pl.col("code") == "B").sort("date")["signal"].to_list()
    assert b[0] == pytest.approx(100.0)                      # 资产内累计首行 = 自身
    assert b[1] == pytest.approx(105.238095)                 # 不含 A 的数据


def test_compute_rejects_aliased_platform_macro_import():
    # R03-M1：别名 import 同样在门前拒绝（含别名形态）
    from factorlab.core.factor.errors import FactorDSLError
    df = pl.DataFrame({"date": ["2020-01-01"], "code": ["A"], "close": [10.0]})
    with pytest.raises(FactorDSLError, match="平台宏 returns 请裸用"):
        compute_formula(
            df, "from factorlab.core.ops.platform_ops import returns as ret\nsignal = ret(close)")


# ---------- _ts_window_days ----------


def test_ts_window_single():
    assert _ts_window_days("signal = ts_mean(close, 20)") == 20


def test_ts_window_takes_max_of_multiple():
    formula = """
_a = ts_mean(close, 5)
_b = ts_std_dev(close, 60)
signal = _a + _b
"""
    assert _ts_window_days(formula) == 60


def test_ts_window_no_window_ops_returns_zero():
    assert _ts_window_days("signal = cs_rank(-close)") == 0


def test_ts_window_variable_window_ignored():
    # 参数化 ${w} 已在展开链替换为字面量；未替换的变量窗口不参与提取
    assert _ts_window_days("signal = ts_mean(close, w)") == 0


def test_ts_window_float_window_ignored():
    assert _ts_window_days("signal = ts_mean(close, 2.5)") == 0


def test_ts_window_qualified_name_and_ta_family():
    assert _ts_window_days("signal = wq.ts_sum(close, 10) + ta_MA(close, 5)") == 10


# ---------- R03-I4 / R02-I1：codegen 执行期守卫（常量折叠 / 分组键全空） ----------

from factorlab.core.factor.errors import FactorDSLError  # noqa: E402


def _guard_panel():
    return pl.DataFrame({
        "date": ["2024-01-01"] * 4 + ["2024-01-02"] * 4,
        "code": ["a", "b", "c", "d"] * 2,
        "close": [1.0, 2.0, 3.0, 4.0, 2.0, 3.0, 4.0, 5.0],
    })


def test_rejects_folded_constant_data_arg():
    """R03-I4：`x - x + 1.0` 被 sympy 折叠为常量 → 必须 FactorDSLError（非 AttributeError）。"""
    with pytest.raises(FactorDSLError, match="折叠|常量"):
        compute_formula(_guard_panel(), "signal = ts_cum_sum(close - close + 1.0)",
                        outputs=["signal"])


def test_rejects_literal_constant_data_arg():
    with pytest.raises(FactorDSLError, match="字面量|常量"):
        compute_formula(_guard_panel(), "signal = ts_cum_sum(1.0)", outputs=["signal"])


def test_positive_control_cum_sum_expression():
    out = compute_formula(_guard_panel(), "signal = ts_cum_sum(close)", outputs=["signal"])
    assert out["signal"].to_list() == [1.0, 2.0, 3.0, 4.0, 3.0, 5.0, 7.0, 9.0]


def test_gp_key_all_null_fails_loud():
    """R02-I1：生产 industry 全空 → gp_* 不得静默塌成单组全市场统计。"""
    df = _guard_panel().with_columns(pl.lit(None, dtype=pl.String).alias("industry"))
    with pytest.raises(ValueError, match="全空"):
        compute_formula(df, "signal = gp_rank(industry, close)", outputs=["signal"])


def test_gp_key_with_values_ok():
    df = _guard_panel().with_columns(
        pl.Series("industry", ["银行", "银行", "白酒", "白酒"] * 2))
    out = compute_formula(df, "signal = gp_rank(industry, close)", outputs=["signal"])
    assert out["signal"].null_count() == 0
    # 组内排名：银行组 a=1,b=2；白酒组 c=1,d=2（每日各两组独立）
    day0 = out.filter(pl.col("date") == "2024-01-01").sort("code")["signal"].to_list()
    assert day0 == [1.0, 2.0, 1.0, 2.0]
