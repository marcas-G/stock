"""process 链处理器测试。

纯链测试（合成面板、无 DB、ctx=None）单写；DB 取数处理器
（neutralize by industry/size、fillna industry_mean）双腿参数化
（env：duckdb|ch，见 tests/conftest.py）——env.seed 数据描述灌数 + ProcessCtx(db=env.rd)
传读句柄。表列型映射对齐生产 CH DDL：stock_basic 带 ts_code 列（ch 腿
neutralize(size) 的 daily_codes_clause 子查询需要，duckdb 腿前缀匹配不读它）、
industry 可空（"str?"）、daily_basic.trade_date 为 "date"（duckdb VARCHAR
'YYYYMMDD' / ch Date，处理器各自解码）。
"""

import polars as pl
import pytest

from factorlab.adapters import process_ops  # noqa: F401  # 注册副作用
from factorlab.core.process.registry import (ProcessCtx, get_processor,
                                        parse_chain_item, run_process_chain)


def test_parse_chain_item_keyword():
    assert parse_chain_item("winsorize(quantile=0.99)") == ("winsorize", {"quantile": 0.99})


def test_parse_chain_item_no_args():
    assert parse_chain_item("standardize()") == ("standardize", {})


def test_parse_chain_item_positional_and_types():
    name, kwargs = parse_chain_item("clip(-3, 3)")
    assert name == "clip" and kwargs["lower"] == -3.0 and kwargs["upper"] == 3.0


def test_parse_chain_item_edges():
    # bool/字符串字面量
    assert parse_chain_item("foo(flag=true, name=abc)") == ("foo", {"flag": True, "name": "abc"})
    # key=value 与位置参数混用（位置参数按序命名 lower/upper/value）
    assert parse_chain_item("clip(-3, upper=3)") == ("clip", {"lower": -3, "upper": 3})
    # 无括号裸名视为无参
    assert parse_chain_item("winsorize") == ("winsorize", {})
    # 多余逗号（空参数段）拒绝
    with pytest.raises(ValueError):
        parse_chain_item("clip(-3, 3,)")
    # 纯空白项拒绝
    with pytest.raises(ValueError):
        parse_chain_item("   ")


def test_parse_chain_item_invalid():
    with pytest.raises(ValueError):
        parse_chain_item("winsorize(quantile=")


def test_unknown_processor_rejected():
    with pytest.raises(KeyError, match="nope"):
        get_processor("nope")


def test_run_chain_applies_sequentially():
    df = pl.DataFrame({
        "date": ["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"],
        "code": ["A", "B", "A", "B"],
        "signal": [1.0, 1000.0, 2.0, 3.0],
    })
    out = run_process_chain(df, ["winsorize(quantile=0.5)", "standardize()"], ctx=None)
    assert out.columns == ["date", "code", "signal"]
    assert out["signal"].abs().max() < 5  # 去极值后 z-score 有界


def _panel():
    return pl.DataFrame({
        "date": ["2024-01-02"] * 4 + ["2024-01-03"] * 4,
        "code": ["A", "B", "C", "D"] * 2,
        "signal": [1.0, 2.0, 3.0, 100.0, 1.0, 2.0, 3.0, 4.0],
    })


def test_winsorize_clips_extremes():
    out = run_process_chain(_panel(), ["winsorize(quantile=0.5)"], ctx=None)
    assert out["signal"].max() < 100.0


def test_standardize_cross_section():
    out = run_process_chain(_panel(), ["standardize()"], ctx=None)
    per_date = out.group_by("date").agg(
        mean=pl.col("signal").mean(),
        std=pl.col("signal").std(),
    )
    assert per_date["mean"].abs().max() < 1e-9
    assert per_date["std"].abs().max() > 0.9


def test_csranknorm_in_unit_interval():
    out = run_process_chain(_panel(), ["csranknorm()"], ctx=None)
    assert out["signal"].min() > 0.0 and out["signal"].max() < 1.0  # rank/(N+1) 最大 N/(N+1) < 1


def test_robustzscore_bounds_extremes():
    out = run_process_chain(_panel(), ["robustzscore()"], ctx=None)
    # 01-02 截面 [1,2,3,100]：中位数 2.5、MAD 1.0，极端值 100.0 → 稳健 z ≈ 65.8
    # （标准 MAD 公式下 <10 在数学上不可能）；断言极端值仍远小于原始幅度、其余在 ±1 附近
    abs_sig = out["signal"].abs()
    assert abs_sig.max() < 100.0
    assert abs_sig.filter(abs_sig < abs_sig.max()).max() < 1.1


def test_clip_bounds():
    out = run_process_chain(_panel(), ["clip(-1, 1)"], ctx=None)
    assert out["signal"].min() >= -1.0 and out["signal"].max() <= 1.0


def test_fillna_value():
    df = _panel().with_columns(pl.when(pl.col("code") == "D").then(None).otherwise(pl.col("signal")).alias("signal"))
    out = run_process_chain(df, ["fillna(method=value, value=0.0)"], ctx=None)
    assert out["signal"].null_count() == 0
    assert out.filter(pl.col("code") == "D")["signal"].to_list() == [0.0, 0.0]


def test_fillna_forward_within_asset():
    df = pl.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        "code": ["A", "A", "A", "A"],
        "signal": [1.0, None, 3.0, None],
    })
    out = run_process_chain(df, ["fillna(method=forward)"], ctx=None)
    assert out["signal"].to_list() == [1.0, 1.0, 3.0, 3.0]


def test_fillna_invalid_method():
    with pytest.raises(ValueError, match="fillna"):
        run_process_chain(_panel(), ["fillna(method=bogus)"], ctx=None)


def test_parse_chain_item_colon_separator():
    assert parse_chain_item("neutralize(by: industry)") == ("neutralize", {"by": "industry"})


def test_parse_chain_item_bad_key_rejected():
    with pytest.raises(ValueError):
        parse_chain_item("clip(=3)")


def test_parse_chain_item_keyword_before_positional_rejected():
    with pytest.raises(ValueError):
        parse_chain_item("clip(upper=3, -3)")


def test_run_chain_requires_signal_column():
    df = pl.DataFrame({"date": ["2024-01-02"], "code": ["A"]})
    with pytest.raises(ValueError, match="signal"):
        run_process_chain(df, ["standardize()"], ctx=None)


def test_fillna_forward_no_cross_asset_leak():
    df = pl.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-01-02", "2024-01-03"],
        "code": ["A", "A", "B", "B"],
        "signal": [1.0, None, None, 4.0],
    })
    out = run_process_chain(df, ["fillna(method=forward)"], ctx=None)
    assert out.filter(pl.col("code") == "A")["signal"].to_list() == [1.0, 1.0]
    assert out.filter(pl.col("code") == "B")["signal"].to_list() == [None, 4.0]  # B 首行不借用 A


def test_zscore_alias_registered():
    assert get_processor("zscore").name == "zscore"


def test_winsorize_rejects_quantile_one():
    with pytest.raises(ValueError):
        run_process_chain(_panel(), ["winsorize(quantile=1.0)"], ctx=None)


def test_standardize_null_preserved_for_constant_section():
    df = pl.DataFrame({
        "date": ["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"],
        "code": ["A", "B", "A", "B"],
        "signal": [5.0, 5.0, 1.0, 2.0],
    })
    out = run_process_chain(df, ["standardize()"], ctx=None)
    a2 = out.filter((pl.col("code") == "A") & (pl.col("date") == "2024-01-02"))["signal"]
    assert a2.to_list() == [None]  # 01-02 截面零方差 → null
    assert out["signal"].drop_nulls().len() == 2


def test_robustzscore_null_for_mad_zero_section():
    df = pl.DataFrame({
        "date": ["2024-01-02", "2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"],
        "code": ["A", "B", "C", "A", "B"],
        "signal": [5.0, 5.0, 5.0, 1.0, 2.0],
    })
    out = run_process_chain(df, ["robustzscore()"], ctx=None)
    sec1 = out.filter(pl.col("date") == "2024-01-02")["signal"]
    assert sec1.to_list() == [None, None, None]  # MAD=0 截面 → null（不产生 inf）
    assert out["signal"].drop_nulls().len() == 2  # 01-03 截面 [1,2] MAD>0 → 有值


def test_neutralize_market_demean():
    df = _panel()
    out = run_process_chain(df, ["neutralize(by=market)"], ctx=None)
    per_date = out.group_by("date").agg(pl.col("signal").mean())
    assert per_date["signal"].abs().max() < 1e-9


# ================================================================
# DB 取数处理器双腿参数化（env：duckdb|ch）：seed 数据描述 + ProcessCtx(db=env.rd)
# ================================================================

def _basic_tables():
    """基础库：stock_basic（A/B 银行、C/D 白酒）+ daily_basic（仅 000001.SZ 两日）。

    stock_basic 多一列 ts_code：ch 腿 neutralize(size) 的 daily_codes_clause
    子查询（SELECT ts_code FROM stock_basic WHERE symbol IN (...)）需要——duckdb 腿
    mv 切片按 ts_code 前缀直接匹配，不读 stock_basic，额外列无影响。
    industry 可空列 → "str?"；trade_date → "date"（duckdb VARCHAR 'YYYYMMDD'）。
    """
    return {
        "stock_basic": (
            [("symbol", "str"), ("ts_code", "str"), ("industry", "str?")],
            [("A", "A.SZ", "银行"), ("B", "B.SZ", "银行"),
             ("C", "C.SZ", "白酒"), ("D", "D.SZ", "白酒")]),
        "daily_basic": (
            [("trade_date", "date"), ("ts_code", "str"), ("total_mv", "f64")],
            [("20240102", "000001.SZ", 100.0), ("20240103", "000001.SZ", 120.0)]),
    }


def _seed_basic(env):
    env.seed(_basic_tables())


def test_neutralize_industry_group_mean_zero(env):
    _seed_basic(env)
    df = _panel()
    out = run_process_chain(df, ["neutralize(by: industry)"],
                            ctx=ProcessCtx(db=env.rd))
    # A/B 同行业（银行）组内均值应为 0；C/D 同行业（白酒）同理
    means = out.join(
        pl.DataFrame({"code": ["A", "B"], "industry": ["银行", "银行"]}),
        on="code",
    ).group_by("date").agg(pl.col("signal").mean())
    assert means["signal"].abs().max() < 1e-9


def test_neutralize_unknown_by():
    with pytest.raises(ValueError, match="neutralize"):
        run_process_chain(_panel(), ["neutralize(by=bogus)"], ctx=None)


def test_neutralize_requires_db_context():
    with pytest.raises(ValueError, match="ctx"):
        run_process_chain(_panel(), ["neutralize(by: industry)"], ctx=None)


def test_fillna_industry_mean(env):
    _seed_basic(env)
    df = _panel().with_columns(
        pl.when(pl.col("code") == "D").then(None).otherwise(pl.col("signal")).alias("signal")
    )
    out = run_process_chain(df, ["fillna(method: industry_mean)"],
                            ctx=ProcessCtx(db=env.rd))
    assert out["signal"].null_count() == 0
    # D 属于白酒组（C/D），组内均值 (3+100)/2=51.5 填 D 的 null——但 100 是极端值？
    # 注意：组内均值包含 D 自身的 null（不计入），用 C 的 3.0 与 A/B 无关
    # 更稳的断言：D 的填充值 = C 在对应日期的值（同组唯一非 null）
    d_vals = out.filter(pl.col("code") == "D")["signal"].to_list()
    c_vals = out.filter(pl.col("code") == "C")["signal"].to_list()
    assert d_vals == c_vals


def test_neutralize_size_decile_demean(env):
    # 按 date 内 total_mv 排名十分位分桶、组内 demean：
    # 20 只市值各异的股票 → 每分位组恰好 2 只 → demean 后恰为 ±0.5（非退化、非全零）；
    # 第二日市值放大 ×1000（非反转：反转的市值多重集与首日相同，全局排名泄漏下分桶
    # 结果不变、测不出泄漏）→ 全局排名泄漏时跨日期分桶改变 → 验证排名按日期隔离。
    _seed_basic(env)
    codes = [chr(ord("A") + i) for i in range(20)]
    rows = []
    for i in range(20):
        rows.append(("20240102", f"{codes[i]}.SZ", float(i + 1) * 10.0))
        rows.append(("20240103", f"{codes[i]}.SZ", float(i + 1) * 10000.0))
    # 中途换表：env.seed 幂等（同表 DROP+CREATE，只替换列出的表）——daily_basic
    # 换 20 票 ×2 日；stock_basic 补 E..T（ch 腿 daily_codes_clause 需 symbol 全命中）
    env.seed({
        "daily_basic": ([("trade_date", "date"), ("ts_code", "str"),
                         ("total_mv", "f64")], rows),
        "stock_basic": ([("symbol", "str"), ("ts_code", "str"),
                         ("industry", "str?")],
                        [(c, f"{c}.SZ", "银行") for c in codes]),
    })
    df = pl.DataFrame({
        "date": ["2024-01-02"] * 20 + ["2024-01-03"] * 20,
        "code": codes * 2,
        "signal": [float(i) for i in range(20)] * 2,
    })
    out = run_process_chain(df, ["neutralize(by: size)"],
                            ctx=ProcessCtx(db=env.rd))
    assert out["signal"].abs().max() > 0.1  # 非退化：不是全 0
    assert set(out["signal"].to_list()) == {-0.5, 0.5}  # 每分位组 2 只 → 恰好 ±0.5
    per_date = out.group_by("date").agg(pl.col("signal").mean())
    assert per_date["signal"].abs().max() < 1e-9  # 各分位组均值 0 → 每日截面和 0


def test_neutralize_size_missing_mv_raises(env):
    # daily_basic 无匹配（total_mv 为 null）→ 报错而非静默 demean 0（M3a spec §5）
    _seed_basic(env)  # daily_basic 只有 000001.SZ，_panel 的 A/B/C/D 全部缺失
    with pytest.raises(ValueError, match="total_mv"):
        run_process_chain(_panel(), ["neutralize(by: size)"],
                          ctx=ProcessCtx(db=env.rd))


def test_neutralize_industry_missing_info(env):
    # 股票不在 stock_basic → 行业缺失 → 报错（不做静默按全截面 demean）
    _seed_basic(env)
    df = _panel().with_columns(
        pl.when(pl.col("code") == "D").then(pl.lit("E")).otherwise(pl.col("code")).alias("code")
    )
    with pytest.raises(ValueError, match="缺少行业信息"):
        run_process_chain(df, ["neutralize(by: industry)"],
                          ctx=ProcessCtx(db=env.rd))


# ================================================================
# NaN 安全（2026-09-14 实跑抓到：polars `NaN > 0` 为 True → NaN std 毒化整个截面）
# ================================================================

def _nan_section() -> pl.DataFrame:
    """同一截面：3 只有效值 + 1 只 NaN（模拟退市股 close 缺失 → signal NaN）。"""
    return pl.DataFrame({
        "date": ["2024-01-02"] * 4,
        "code": ["A", "B", "C", "D"],
        "signal": [1.0, 2.0, 3.0, float("nan")],
    })


def test_standardize_nan_does_not_poison_cross_section():
    out = run_process_chain(_nan_section(), ["standardize()"], ctx=None)
    vals = out.sort("code")["signal"].to_list()
    # 有效三只仍被标准化（均值 2、std 1 → -1/0/1），NaN 行 → null（不参与统计）
    assert vals[0] == pytest.approx(-1.0) and vals[1] == pytest.approx(0.0) \
        and vals[2] == pytest.approx(1.0)
    assert vals[3] is None
    # 负行为：若 NaN 未被隔离，整列会全为 NaN（历史缺陷）
    assert out["signal"].is_nan().sum() == 0


def test_winsorize_nan_safe():
    df = pl.DataFrame({
        "date": ["2024-01-02"] * 5,
        "code": ["A", "B", "C", "D", "E"],
        "signal": [1.0, 2.0, 3.0, 100.0, float("nan")],
    })
    out = run_process_chain(df, ["winsorize(quantile=0.5)"], ctx=None).sort("code")
    vals = out["signal"].to_list()
    assert vals[4] is None                       # NaN → null
    assert out["signal"].is_nan().sum() == 0
    assert vals[3] < 100.0                       # 极值被 clip（分位数在有限值上算）


def test_robustzscore_nan_safe():
    out = run_process_chain(_nan_section(), ["robustzscore()"], ctx=None).sort("code")
    vals = out["signal"].to_list()
    assert vals[3] is None and out["signal"].is_nan().sum() == 0
    assert vals[1] == pytest.approx(0.0)         # 中位数 B 归零


def test_csranknorm_nan_safe():
    out = run_process_chain(_nan_section(), ["csranknorm()"], ctx=None).sort("code")
    vals = out["signal"].to_list()
    assert vals[3] is None
    assert out["signal"].is_nan().sum() == 0
