"""M3（G6/G3 主体）：开放解析器——per-code 属性数据面（industry 等 stock_basic
静态属性）按需 join。

断言来源：design doc §5.2（公式引用列 → 解析器按来源解析组装数据面，duckdb|ch
编译对；引用 industry 才 join；属性空值不进组）+ plan M3 行（引用才 join——
未引用对 rd 零属性调用；双腿 join 等值；属性空值不进组；供给失败文案含可用列
与相似名）+ §5.1（属性是数据列，直接引用/组键引用皆可——"存在即可写"；
成分标志是 §5.2 点名的 per-code 0/1 属性）。

已知边界（引擎 DSL 事实，非 M3 缺陷）：字符串属性只能做组键引用，不能做
字面量比较（expr_codegen sympy 面 parse 不了字符串字面量——'银行' == 写法
SympifyError；行业条件等值用法走 process 层，见 processors.industry_mean）。

夹具：5 只股票 × 6 交易日（daily 全量 + stock_basic 属性全量）：
    A 000001.SZ 银行 flag1（base 10）  B 600519.SH 白酒 flag0（base 50）
    C 600000.SH 银行 flag1（base 30）   D 600036.SH industry=null flag0（base 40）
    E 601988.SH industry='' flag0（base 63）（空串——库中空串等价 null）
首日 close：A 11 / B 51 / C 31 / D 41 / E 64（base+i+1；adj 全 1.0 → qfq 同 raw）。
银行组（A+C）：gp_mean = 21，gp_rank：A=1、C=2。
null 键（D/E 空属性，'' 已规范化为 null）当日互成 null 分区组——**不进任何真实
行业组的统计**（design §5.2 空值不进组=防污染）；null 组 mean = 52.5（D41/E64
互均，取 E=64 使 null 组均值 ≠ B 自组值 51，区分"互组"与"落单自值"）、
rank D=1 / E=2（41 < 64，平均秩）。
"""

import datetime

import polars as pl
import pytest

from factorlab.app.run import run_factor
from factorlab.core.engine.compute import RunContext, compute_formula
from factorlab.core.spec import load_spec
from test_run_factor import _DATES

_DAILY_COLS = [("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
               ("vol", "f64"), ("amount", "f64")]
_ADJ_COLS = [("ts_code", "str"), ("trade_date", "date"), ("adj_factor", "f64")]
_SB_COLS = [("symbol", "str"), ("ts_code", "str"), ("exchange", "str"),
            ("list_date", "date"), ("industry", "str?"), ("flag", "f64")]
_CAL_COLS = [("cal_date", "date"), ("is_open", "i64")]

# (symbol, ts_code, exchange, list_date, industry, flag(成分 0/1), base close)
# flag：数值属性直接引用载体（§5.2 成分标志语义）——A/C=1，B/D/E=0；
# industry null/''（D/E）不传染 flag（属性列空值彼此独立）。
_STOCKS = [
    ("000001", "000001.SZ", "SZSE", "19910101", "银行", 1.0, 10.0),
    ("600519", "600519.SH", "SSE", "20010101", "白酒", 0.0, 50.0),
    ("600000", "600000.SH", "SSE", "19990101", "银行", 1.0, 30.0),
    ("600036", "600036.SH", "SSE", "20020101", None, 0.0, 40.0),
    ("601988", "601988.SH", "SSE", "20060701", "", 0.0, 63.0),
]


def _attr_tables() -> dict:
    daily_rows, adj_rows = [], []
    for _s, ts_code, _ex, _ld, _ind, _flag, base in _STOCKS:
        for i, d in enumerate(_DATES):
            close = base + i + 1
            daily_rows.append((ts_code, d, close - 1.0, close - 0.5,
                               close - 1.5, close, close - 1.0, 1.0, 0.01,
                               1000.0, 1e6))
            adj_rows.append((ts_code, d, 1.0))
    return {
        "daily": (_DAILY_COLS, daily_rows),
        "adj_factor": (_ADJ_COLS, adj_rows),
        "stock_basic": (_SB_COLS,
                        [(s, t, e, l, i, f) for s, t, e, l, i, f, _b in _STOCKS]),
        "daily_basic": ([("trade_date", "date"), ("ts_code", "str"),
                         ("total_mv", "f64")],
                        [row for d in _DATES for row in
                         ((d, "000001.SZ", 100.0), (d, "600519.SH", 200.0))]),
        "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
        "trade_cal": (_CAL_COLS, [(d, 1) for d in _DATES]),
    }


_CODES = [t for _s, t, _e, _l, _i, _f, _b in _STOCKS]  # ts_code 形态


def _seed(env):
    env.seed(_attr_tables())


def _ctx(env, out_dir, **kw):
    if env.backend == "duckdb":
        kw["db_path"] = env.path
    return RunContext(data_backend=env.backend, output_dir=out_dir, **kw)


def _spec(tmp_path, formula: str, name: str = "demo"):
    path = tmp_path / f"{name}.yaml"
    path.write_text(f"""
name: {name}
category: custom
direction: 1
universe:
  codes: {_CODES!r}
date:
  start: "2024-01-02"
  end: "2024-01-09"
formula: |
  {formula}
""", encoding="utf-8")
    return load_spec(path)


def _day_rows(panel: pl.DataFrame, day: str) -> dict[str, float]:
    """某交易日 panel：code → signal 值映射（None 值原样保留——null 断言用）。"""
    day_df = panel.filter(pl.col("date") == datetime.date.fromisoformat(day))
    return dict(zip(day_df["code"].to_list(), day_df["signal"].to_list()))


_GROUP_MEAN = "signal = gp_mean(industry, close)"
_GROUP_RANK = "signal = gp_rank(industry, close)"


# ================================================================
# 1. 组算子数据面：属性进面板 → 组内真值 + 空属性不进组（组统计不含它）
# ================================================================

def test_group_mean_over_industry_attribute(env, tmp_path):
    """gp_mean(industry, close)：银行组 = (A+C)/2（组真含别股——A 值 != 自身
    close），白酒单股自组 = 自身；空属性（null 与空串→同一 null 键）股票不进任何
    真实组（若进组 C 的组均值会被 D/E 拉偏）——当日空键行互成 null 分区组互均
    （D 41 与 E 64 → 52.5；落单自值则为 41/64，断言值区分互组与落单）。"""
    _seed(env)
    panel = run_factor(_spec(tmp_path, _GROUP_MEAN), _ctx(env, tmp_path / "out")).panel
    d1 = _day_rows(panel, "2024-01-02")
    assert d1["000001.SZ"] == 21.0          # (A 11 + C 31) / 2 —— 非 A 自身 11
    assert d1["600000.SH"] == 21.0          # C 同组同均值（D/E 若混入会变 51/64 分量）
    assert d1["600519.SH"] == 51.0          # 白酒单只自组
    assert d1["600036.SH"] == 52.5          # null 分区组互均 (41 + 64) / 2
    assert d1["601988.SH"] == 52.5          # 空串→null：与 D 同组互均（不进脏组）
    # 禁止行为：C 值若等于 (A+C+D)/3 或组含 E → 断言失败（证明空属性被排除）；
    # D/E 若落单自值（41/64）而非互均 52.5 → 断言失败（证明 null 键互组成组）
    assert d1["000001.SZ"] == pytest.approx((11.0 + 31.0) / 2)
    assert d1["600036.SH"] == pytest.approx((41.0 + 64.0) / 2)


def test_group_rank_over_industry_attribute(env, tmp_path):
    """gp_rank(industry, close)：银行组秩 A=1/C=2；空属性（null 与空串）→ 当日
    null 分区组内排秩：D(41) < E(64) → D=1、E=2——E 空串若不规范化成 null 则
    与 D 异组（各落单=1），秩 2 断言锁定 ''→null 归一。"""
    _seed(env)
    panel = run_factor(_spec(tmp_path, _GROUP_RANK), _ctx(env, tmp_path / "out")).panel
    d1 = _day_rows(panel, "2024-01-02")
    assert d1["000001.SZ"] == 1.0
    assert d1["600000.SH"] == 2.0
    assert d1["600519.SH"] == 1.0
    assert d1["600036.SH"] == 1.0
    assert d1["601988.SH"] == 2.0


def test_direct_numeric_attribute_reference(env, tmp_path):
    """数值属性是普通数据列：直接参与条件运算（§5.2 成分标志 0/1 语义）——join
    键 symbol→code 映射正确、值进公式面；industry null/''（D/E）**不传染** flag
    （属性列空值彼此独立——flag 0 仍参与条件假分支）。"""
    _seed(env)
    f = "signal = if_else(flag > 0.5, close, 0.0)"
    panel = run_factor(_spec(tmp_path, f), _ctx(env, tmp_path / "out")).panel
    d1 = _day_rows(panel, "2024-01-02")
    assert d1["000001.SZ"] == 11.0          # flag 1（银行）→ close
    assert d1["600000.SH"] == 31.0          # flag 1（银行）→ close
    assert d1["600519.SH"] == 0.0           # flag 0 → 0.0（条件假，非 null）
    assert d1["600036.SH"] == 0.0           # industry null 不传染：flag 0 正常参与
    assert d1["601988.SH"] == 0.0           # industry '' 不传染：flag 0 正常参与
    # 禁止行为：flag 若未 join（或 join 错键）→ 全行 null/0，A/C 断言失败
    assert d1["000001.SZ"] == pytest.approx(11.0)
    assert d1["600519.SH"] != 51.0          # 值必须来自 flag 条件，非 close 直通


# ================================================================
# 2. 按需供给：引用才 join；双腿 join 等值（同断言双腿各自成立）
# ================================================================

def test_no_attribute_reference_no_attribute_read(env, tmp_path, monkeypatch):
    """公式未引用属性 → 零属性读取调用（对 rd 无 stock_basic 属性 SELECT）。"""
    _seed(env)
    import factorlab.app.run as compute_mod  # run_factor 所在模块（WS3c-2 上移）
    calls = []
    monkeypatch.setattr(compute_mod, "load_code_attributes",
                        lambda *a, **k: calls.append(1) or [], raising=False)
    spec = _spec(tmp_path, "signal = close / open - 1", name="plain")
    result = run_factor(spec, _ctx(env, tmp_path / "out"))
    assert result.panel.height > 0
    assert len(calls) == 0, f"未引用属性却发生 {len(calls)} 次属性读取"


def test_attribute_referenced_reads_once(env, tmp_path, monkeypatch):
    """引用属性 → run_factor 恰一次属性读取（属性列被解析器分流供给）。

    spy 包装真实实现（计数并透传结果——本测试验证调用次数，不替换供给行为；
    红阶段原函数未实现 → 调用即断言失败）。
    """
    _seed(env)
    import factorlab.app.run as compute_mod  # run_factor 所在模块（WS3c-2 上移）
    calls = []
    real = getattr(compute_mod, "load_code_attributes", None)
    if real is None:
        raise AssertionError("load_code_attributes 未实现——红阶段预期（无法计数真实调用）")

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(compute_mod, "load_code_attributes", spy)
    panel = run_factor(_spec(tmp_path, _GROUP_MEAN), _ctx(env, tmp_path / "out")).panel
    assert panel.height > 0
    assert len(calls) == 1, f"属性读取次数 {len(calls)} != 1（按需 join 应为单次全量供给）"


# ================================================================
# 3. 供给失败 → M1 报错助手（可用列清单含属性面 + 最相似候选）
# ================================================================

def test_unknown_attribute_like_column_helpful_error(env, tmp_path):
    """错拼属性列（industr）→ 报错助手：可用列清单须含属性面真实列（industry）
    与最相似候选建议。"""
    _seed(env)
    spec = _spec(tmp_path, "signal = industri + close", name="typo")
    with pytest.raises(ValueError) as exc:
        run_factor(spec, _ctx(env, tmp_path / "out"))
    msg = str(exc.value)
    assert "未知列名" in msg
    assert "industry" in msg            # 可用列清单含属性面（stock_basic 实探）
    assert "最接近的列" in msg          # difflib 命中属性面（错拼名相似候选）


# ================================================================
# 4. 引擎层 codegen：group 算子符号全链可用（extra_codes 注入）
# ================================================================

def test_compute_formula_group_ops_resolve():
    """compute_formula（无 rd）上 gp_mean/gp_rank 全链可解析——gp_ 前缀经
    expr_codegen 翻译产物符号（cs_mean/cs_rank）注入生成代码 exec 作用域
    （此前 NameError not defined）。"""
    df = pl.DataFrame({
        "date": [datetime.date(2024, 1, 2)] * 3,
        "code": ["A", "B", "C"],
        "close": [11.0, 51.0, 31.0],
        "industry": ["银行", "白酒", "银行"],
    })
    r = compute_formula(df, _GROUP_MEAN)
    assert r["signal"].to_list() == [21.0, 51.0, 21.0]
    r2 = compute_formula(df, _GROUP_RANK)
    assert r2["signal"].to_list() == [1.0, 1.0, 2.0]
