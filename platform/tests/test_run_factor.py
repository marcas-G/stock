"""run_factor 全谱行为（信号计算/labels/多 code/qfq/分块一致性/错误路径）双腿参数化。

env：duckdb|ch（tests/conftest.py）——run_factor 整链经 RunContext(data_backend/
db_path) 双腿真跑；ch 腿不传 db_path（open_read 按 data_backend 分派，db_path
忽略）。duckdb 腿逐断言与转换前一致。

假库形态（平台库风格）：daily 表带 ts_code 后缀、trade_date 'YYYYMMDD'、vol
恒 1000（参考 test_source.py / test_qfq_chunk_invariance.py 模式）。
seed 幂等：中途改表用第二次 env.seed 只替换该表。

仅文件路径语义/构造器默认断言保持单腿 duckdb（test_run_factor_missing_db /
test_run_factor_default_db_is_platform），其余纯函数测试（_formula_columns）无 DB。
"""

import datetime
import json
import warnings
from pathlib import Path

import polars as pl
import pytest

import dualbridge
from factorlab.app.run import run_factor
from factorlab.app.context import RunContext
from factorlab.adapters.read.universe import STDegradedWarning
from factorlab.config import settings
from factorlab.core.engine.compute import _formula_columns
from factorlab.core.factor.errors import FactorDSLError
from factorlab.core.spec import load_spec

_DATES = ["20240102", "20240103", "20240104", "20240105", "20240108", "20240109"]
# 扩展交易日（>6 天场景：周频评估需要信号与 forward 同时有效的行，如 CLI run 测试；
# 24 天延伸供 forward_return_20d 非 null 标签（尾 20 日内无标签，前 4 日有））
_EXTRA_DATES = ["20240110", "20240111", "20240112", "20240115", "20240116", "20240117",
                "20240118", "20240119", "20240122", "20240123", "20240124",
                "20240125", "20240126", "20240129", "20240130", "20240131",
                "20240201", "20240202"]
# 平台库风格代码：ts_code 带后缀（000001.SZ），symbol 为纯数字桥梁
_A = ("000001", "000001.SZ", 10.0)
_B = ("600519", "600519.SH", 20.0)

_DAILY_COLS = [("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
               ("vol", "f64"), ("amount", "f64")]
_ADJ_COLS = [("ts_code", "str"), ("trade_date", "date"), ("adj_factor", "f64")]
_SB_COLS = [("symbol", "str"), ("ts_code", "str"), ("exchange", "str"),
            ("list_date", "date"), ("industry", "str?")]
_DB_COLS = [("trade_date", "date"), ("ts_code", "str"), ("total_mv", "f64")]
_CAL_COLS = [("cal_date", "date"), ("is_open", "i64")]


def _tables(ex_date: bool = False, n_days: int = 6) -> dict:
    """平台库风格假库（描述式 seed）：daily/adj_factor/daily_basic/stock_basic/
    stock_st/trade_cal 齐全；ts_code 带后缀、vol（参考 test_source.py 模式）。
    ex_date=True 时 000001 第 3 天（01-04）除权：close 11→8、adj 1.0→1.5。
    n_days 可扩展到 _DATES+_EXTRA_DATES 前缀；ex_date 序列前 6 天固定，
    7+ 天延续末尾模式。"""
    dates = (_DATES + _EXTRA_DATES)[:n_days]
    daily_rows, adj_rows = [], []
    for symbol, ts_code, base in (_A, _B):
        closes = [base + i + 1 for i in range(len(dates))]
        if ex_date and symbol == "000001":
            closes = ([10.0, 11.0, 8.0, 9.0, 12.0, 13.0]
                      + [13.0 + i for i in range(len(dates) - 6)])
        for i, d in enumerate(dates):
            daily_rows.append((ts_code, d, closes[i] - 1.0, closes[i] - 0.5,
                               closes[i] - 1.5, closes[i], closes[i] - 1.0,
                               1.0, 0.01, 1000.0, 1e6))
        adjs = [1.0] * len(dates)
        if ex_date and symbol == "000001":
            adjs = ([1.0, 1.0, 1.5, 1.5, 1.5, 1.5]
                    + [1.5] * (len(dates) - 6))
        for i, d in enumerate(dates):
            adj_rows.append((ts_code, d, adjs[i]))
    return {
        "daily": (_DAILY_COLS, daily_rows),
        "adj_factor": (_ADJ_COLS, adj_rows),
        "stock_basic": (_SB_COLS,
                        [("000001", "000001.SZ", "SZSE", "19910101", "银行"),
                         ("600519", "600519.SH", "SSE", "20010101", "白酒")]),
        "daily_basic": (_DB_COLS,
                        [row for d in dates for row in
                         ((d, "000001.SZ", 100.0), (d, "600519.SH", 200.0))]),
        "stock_st": ([("ts_code", "str"), ("trade_date", "date")], []),
        "trade_cal": (_CAL_COLS, [(d, 1) for d in dates]),
    }


def _seed(env, **kw):
    env.seed(_tables(**kw))


def _ctx(env, out_dir, **kw):
    # ch 腿无文件路径概念：open_read 按 data_backend="ch" 分派，db_path 忽略
    if env.backend == "duckdb":
        kw["db_path"] = env.path
    return RunContext(data_backend=env.backend, output_dir=out_dir, **kw)


def build_db(tmp_path, ex_date: bool = False, n_days: int = 6):
    """duckdb 腿平台库文件 seed（CLI/canonical 等文件消费方共享，非本文件被测）。

    本文件双后端化后被测路径全部走 env；build_db 保留给按 settings.platform_db /
    RunContext(db_path=...) 消费 duckdb 文件的跨模块测试：test_cli_run.py 与
    test_canonical_artifact_handoff.py（动态 importlib 加载本模块取用）。
    ex_date/n_days 语义同 _tables（原 build_db 迁移为描述式 seed 的等价物）。
    """
    dualbridge.seed_duckdb(tmp_path / "q.duckdb", _tables(ex_date=ex_date,
                                                         n_days=n_days))


def _spec(tmp_path):
    path = tmp_path / "spec.yaml"
    path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
process:
  - standardize()
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    return load_spec(path)


def test_run_factor_end_to_end(env, tmp_path):
    _seed(env)
    out_dir = tmp_path / "out"
    result = run_factor(_spec(tmp_path), _ctx(env, out_dir))
    panel = result.panel
    assert "signal" in panel.columns and "forward_return_5d" in panel.columns
    assert panel.height > 0
    assert (out_dir / "panel.parquet").exists()
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["name"] == "demo"
    assert summary["universe_count"] == 2
    assert summary["panel_rows"] == panel.height
    assert summary["adjustment"] == "qfq"  # 默认复权口径写入摘要


def test_run_factor_empty_universe_rejected(env, tmp_path):
    _seed(env)
    spec = _spec(tmp_path)
    spec.universe.codes = ["999999.SZ"]  # 不存在
    with pytest.raises(ValueError, match="universe"):
        run_factor(spec, _ctx(env, tmp_path / "out2"))


def test_run_factor_float32_disabled(env, tmp_path):
    _seed(env)
    out_dir = tmp_path / "out"
    result = run_factor(_spec(tmp_path), _ctx(env, out_dir, float32=False))
    assert result.panel["close"].dtype == pl.Float64
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["float32"] is False


def test_run_factor_universe_override_file(env, tmp_path):
    _seed(env)
    pool = tmp_path / "pool_a.yaml"
    pool.write_text("codes: ['000001']", encoding="utf-8")
    out_dir = tmp_path / "out"
    result = run_factor(_spec(tmp_path), _ctx(env, out_dir, universe_override=str(pool)))
    assert result.summary["universe_count"] == 1
    assert result.panel["code"].unique().to_list() == ["000001.SZ"]


def test_run_factor_empty_process_chain(env, tmp_path):
    _seed(env)
    spec = _spec(tmp_path)
    spec.process = []  # 无 process 链：signal 保持原始值
    out_dir = tmp_path / "out"
    result = run_factor(spec, _ctx(env, out_dir))
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["process"] == []
    assert result.panel["signal"].null_count() < result.panel.height


def test_run_factor_empty_date_range_rejected(env, tmp_path):
    _seed(env)
    spec = _spec(tmp_path)
    spec.date.start, spec.date.end = "2020-01-01", "2020-01-31"  # 库中无此范围数据
    with pytest.raises(ValueError, match="无数据"):
        run_factor(spec, _ctx(env, tmp_path / "out3"))


def test_run_factor_formula_without_close_succeeds(env, tmp_path):
    # close 恒加载（forward 依赖 close 列而非公式引用）——纯量价因子不应被拒
    _seed(env)
    spec = _spec(tmp_path)
    spec.formula = "signal = open"
    result = run_factor(spec, _ctx(env, tmp_path / "out4"))
    assert "forward_return_5d" in result.panel.columns  # forward 仍可用


def test_run_factor_factors_combine_rejected(env, tmp_path):
    _seed(env)
    path = tmp_path / "multi.yaml"
    path.write_text("""
name: multi
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
factors:
  - name: f1
    formula: signal = close
combine:
  method: equal_weight
""", encoding="utf-8")
    with pytest.raises(NotImplementedError, match="factors"):
        run_factor(load_spec(path), _ctx(env, tmp_path / "out5"))


def test_run_factor_neutralize_industry(env, tmp_path):
    # 回归：process 链 ctx（数据后端句柄）经 run_factor 完整链路可用（行业来自 stock_basic）
    _seed(env)
    spec_path = tmp_path / "spec_n.yaml"
    spec_path.write_text("""
name: demo_n
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
process:
  - neutralize(by=industry)
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    result = run_factor(load_spec(spec_path), _ctx(env, tmp_path / "out_n"))
    assert "signal" in result.panel.columns
    assert result.panel.height > 0


def test_run_factor_neutralize_size(env, tmp_path):
    # 回归：size 分支的日期 join key 必须与面板 date 同 dtype（run_factor 面板为 pl.Date，
    # 原 cast String 导致 SchemaError）；daily_basic 在 seed fixture 中
    _seed(env)
    spec_path = tmp_path / "spec_size.yaml"
    spec_path.write_text("""
name: demo_size
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
process:
  - neutralize(by=size)
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    result = run_factor(load_spec(spec_path), _ctx(env, tmp_path / "out_s"))
    assert "signal" in result.panel.columns
    assert result.panel.height > 0
    # 每十分位组单只 → 组内 demean 恒 0（截面 N<10 已知局限）
    assert result.panel["signal"].abs().max() < 1e-9


def test_run_factor_missing_db(tmp_path):
    # 单腿 duckdb：文件路径缺失语义（ch 腿无文件路径概念，无对应断言）
    with pytest.raises(FileNotFoundError, match="nope.duckdb"):
        run_factor(_spec(tmp_path), RunContext(db_path=tmp_path / "nope.duckdb",
                                               output_dir=tmp_path / "out6"))


def test_run_factor_syntax_error_rejected(env, tmp_path):
    _seed(env)
    spec = _spec(tmp_path)
    spec.formula = "signal = (close"
    with pytest.raises(ValueError, match="语法错误"):
        run_factor(spec, _ctx(env, tmp_path / "out7"))


def test_run_factor_attribute_call_rejected(env, tmp_path):
    # 属性调用（np.abs）须在装配层被拒绝，而不是被 _formula_columns 误读为列名
    _seed(env)
    spec = _spec(tmp_path)
    spec.formula = "signal = np.abs(close)"
    with pytest.raises(ValueError, match="属性调用"):
        run_factor(spec, _ctx(env, tmp_path / "out8"))


def test_run_factor_qfq_adjustment(env, tmp_path):
    # 除权日（adj 1.0→1.5, close 11→8）：qfq 下因子值连续（用 momentum 类公式验证）
    _seed(env, ex_date=True)
    spec_path = tmp_path / "spec_qfq.yaml"
    spec_path.write_text("""
name: demo_qfq
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
process: []
formula: |
  from polars_ta.prefix.wq import ts_delay
  signal = close / ts_delay(close, 1) - 1
""", encoding="utf-8")
    out_dir = tmp_path / "out_qfq"
    result = run_factor(load_spec(spec_path), _ctx(env, out_dir))
    panel = result.panel.sort(["date"])
    a = panel.filter(pl.col("code") == "000001.SZ")
    # qfq 除权日收益 = 8×1.5/11 - 1（raw 口径会是 8/11 - 1）
    day3 = a.filter(pl.col("date") == datetime.date(2024, 1, 4))["signal"][0]
    assert day3 == pytest.approx(8 * 1.5 / 11 - 1)
    # 前向收益 total_return（raw close×adj 序列，含分红再投资）：13×1.5/10 - 1
    day1 = a.filter(pl.col("date") == datetime.date(2024, 1, 2))["forward_return_5d"][0]
    assert day1 == pytest.approx(13 * 1.5 / 10 - 1)
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["adjustment"] == "qfq"


def test_run_factor_default_db_is_platform():
    # RunContext 默认 db_path 指向平台库路径（旧只读库已废弃，平台库为唯一数据源）
    ctx = RunContext()
    assert ctx.db_path == Path("data/factorlab.duckdb")
    assert ctx.adjustment == "qfq"


def test_run_factor_future_calendar_days_not_padded(env, tmp_path):
    # trade_cal 含未来公告日（20261231）——run_factor 不应补全未来 null 行
    _seed(env)
    # 二次 seed 只替换 trade_cal（seed 幂等：同表先 DROP 再 CREATE）
    env.seed({"trade_cal": (_CAL_COLS, _tables()["trade_cal"][1] + [("20261231", 1)])})
    spec_path = tmp_path / "spec_future.yaml"
    spec_path.write_text("""
name: demo_future
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
process: []
formula: |
  signal = close
""", encoding="utf-8")
    result = run_factor(load_spec(spec_path), _ctx(env, tmp_path / "out_f"))
    assert datetime.date(2026, 12, 31) not in result.panel["date"]
    assert result.panel["date"].max() <= datetime.date.today()


def test_run_factor_consumes_operators_macros(env, tmp_path):
    # spec.operators 内联宏：mom_ratio(x, n) → delay(x, n)/delay(x, 2n) - 1 展开后计算正确
    _seed(env)
    spec_path = tmp_path / "spec_macro.yaml"
    spec_path.write_text("""
name: macro_demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
operators:
  mom_ratio:
    params: [x, n]
    formula: "delay(x, n) / delay(x, 2 * n) - 1"
formula: |
  from polars_ta.prefix.wq import ts_delay as delay
  signal = mom_ratio(close, 1)
""", encoding="utf-8")
    result = run_factor(load_spec(spec_path), _ctx(env, tmp_path / "out"))
    assert result.panel.height > 0
    # 展开语义：mom_ratio(close, 1) → delay(close, 1)/delay(close, 2) - 1 = close[t-1]/close[t-2] - 1
    # 000001 收盘 11,12,13,14,15,16：第 3 个交易日（01-04）signal = 12/11 - 1
    a = result.panel.filter(pl.col("code") == "000001.SZ").sort("date")
    day3 = a.filter(pl.col("date") == datetime.date(2024, 1, 4))["signal"][0]
    assert day3 == pytest.approx(12 / 11 - 1)
    # delay(close, 2) 需要前 2 个交易日：前 2 日 signal 为 null
    assert a.head(2)["signal"].null_count() == 2


def test_run_factor_macro_formula_column_refs_loaded(env, tmp_path):
    # 宏公式内引用的数据列（volume）须纳入列加载（_formula_columns 在展开后提取）
    _seed(env)
    spec_path = tmp_path / "spec_macro_vol.yaml"
    spec_path.write_text("""
name: macro_vol
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
operators:
  vwap_ratio:
    params: [x]
    formula: "x * volume"
formula: |
  signal = vwap_ratio(close)
""", encoding="utf-8")
    result = run_factor(load_spec(spec_path), _ctx(env, tmp_path / "out_v"))
    assert result.panel.height > 0
    a = result.panel.filter(pl.col("code") == "000001.SZ").sort("date")
    # 展开后 signal = close × volume：首日 = 11 × canonical volume（股）
    # I7：duckdb 平台库源 vol=手（seeded 1000）→ 读面 ×100 归一为股；
    # ch 灌入源已是股 → 恒等。两腿 canonical 一致。
    canonical_vol = 1000.0 * (100.0 if env.backend == "duckdb" else 1.0)
    assert a["signal"][0] == pytest.approx(11 * canonical_vol)


def test_run_factor_spec_adjustment_raw(env, tmp_path):
    # spec.adjustment 字段消费：声明 raw 时以 spec 为准（覆盖 qfq 默认），除权日保留假崩
    _seed(env, ex_date=True)
    spec_path = tmp_path / "spec_raw.yaml"
    spec_path.write_text("""
name: demo_raw
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
adjustment: raw
process: []
formula: |
  from polars_ta.prefix.wq import ts_delay
  signal = close / ts_delay(close, 1) - 1
""", encoding="utf-8")
    out_dir = tmp_path / "out_raw"
    result = run_factor(load_spec(spec_path), _ctx(env, out_dir))
    panel = result.panel.sort(["date"])
    a = panel.filter(pl.col("code") == "000001.SZ")
    # raw：除权日 8/11 - 1（qfq 下会是 8×1.5/11 - 1）
    day3 = a.filter(pl.col("date") == datetime.date(2024, 1, 4))["signal"][0]
    assert day3 == pytest.approx(8 / 11 - 1)
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["adjustment"] == "raw"


def test_run_factor_params_substitution(env, tmp_path):
    # spec.params 顶层参数：formula 内 ${win} 文本引用 → 编译期替换为字面量
    _seed(env)
    spec_path = tmp_path / "spec_param.yaml"
    spec_path.write_text("""
name: param_demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
params: {win: 3}
process: []
formula: |
  from polars_ta.prefix.wq import ts_mean
  signal = ts_mean(close, ${win}) - close
""", encoding="utf-8")
    result = run_factor(load_spec(spec_path), _ctx(env, tmp_path / "out_param"))
    assert result.panel.height > 0
    a = result.panel.filter(pl.col("code") == "000001.SZ").sort("date")["signal"]
    # 替换语义：ts_mean(close, 3) - close——000001 收盘 11..16，第 3 日 = mean(11,12,13) - 13 = 0
    assert a[2] == pytest.approx((11 + 12 + 13) / 3 - 13)
    # ts_mean(3) 窗口不足的前 2 日为 null
    assert a.head(2).null_count() == 2


def test_run_factor_params_in_macro_and_def_bodies(env, tmp_path):
    # 边界：operators 宏体与 def 体内的 ${} 一并替换（宏体经 operators 副本、def 体在 formula 文本内）；
    # 同时覆盖 def 调用户宏的展开顺序（params → 用户宏 → def 内联）
    _seed(env)
    spec_path = tmp_path / "spec_param_macro.yaml"
    spec_path.write_text("""
name: param_macro
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
params: {k: 2}
operators:
  scaled:
    params: [x]
    formula: "x * ${k}"
formula: |
  def doubled(x):
      return scaled(x) * ${k}

  signal = doubled(close)
""", encoding="utf-8")
    result = run_factor(load_spec(spec_path), _ctx(env, tmp_path / "out_param_macro"))
    a = result.panel.filter(pl.col("code") == "000001.SZ").sort("date")["signal"]
    # 展开链：${k}→2、scaled(close)→close*2、doubled(close) → (close*2)*2 = close*4
    assert a[0] == pytest.approx(11 * 4)


def test_run_factor_string_param_as_column(env, tmp_path):
    # 边界：参数值为字符串（文本替换 → 公式内作列名引用）
    _seed(env)
    spec_path = tmp_path / "spec_param_col.yaml"
    spec_path.write_text("""
name: param_col
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
params: {col: volume}
process: []
formula: |
  signal = ${col} * 2
""", encoding="utf-8")
    result = run_factor(load_spec(spec_path), _ctx(env, tmp_path / "out_param_col"))
    a = result.panel.filter(pl.col("code") == "000001.SZ").sort("date")["signal"]
    # I7：canonical volume（股）——duckdb 源 vol=手 ×100 / ch 源已是股
    canonical_vol = 1000.0 * (100.0 if env.backend == "duckdb" else 1.0)
    assert a[0] == pytest.approx(canonical_vol * 2)


def test_run_factor_unknown_param_rejected(env, tmp_path):
    # 错误路径：formula 引用 ${nope} 未在 params 声明 → 报错（数据库打开前暴露）
    _seed(env)
    spec_path = tmp_path / "spec_param_bad.yaml"
    spec_path.write_text("""
name: param_bad
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
params: {win: 3}
formula: |
  signal = close * ${nope}
""", encoding="utf-8")
    with pytest.raises(ValueError, match="param"):
        run_factor(load_spec(spec_path), _ctx(env, tmp_path / "out_param_bad"))


def test_run_factor_def_with_window_ops(env, tmp_path):
    # def 内窗口算子合法（内联展开为顶层 ts_ 调用）——窗口按资产分区：每资产首行
    # ts_delay(close, 1) 为 null，B 首行不得借用 A 末行（无跨资产泄漏）
    _seed(env)
    spec_path = tmp_path / "spec_def.yaml"
    spec_path.write_text("""
name: def_demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
process: []
formula: |
  from polars_ta.prefix.wq import ts_mean, ts_delay

  def mom(x, n):
      return ts_mean(x, n) / ts_delay(x, 1) - 1

  signal = mom(close, 3)
""", encoding="utf-8")
    result = run_factor(load_spec(spec_path), _ctx(env, tmp_path / "out_def"))
    panel = result.panel.sort(["code", "date"])
    a = panel.filter(pl.col("code") == "000001.SZ")["signal"]
    b = panel.filter(pl.col("code") == "600519.SH")["signal"]
    # 每资产首行 null（若窗口未按资产分区，B 首行会取到 A 末行的值）
    assert a[0] is None and b[0] is None
    # 000001 收盘 11..16、600519 21..26：第 3 日 ts_mean(3)=中间值、ts_delay(1)=同值 → 0
    assert a[2] == pytest.approx(0.0)
    assert b[2] == pytest.approx(0.0)


def test_run_factor_unknown_adjustment_rejected(env, tmp_path):
    _seed(env)
    spec = _spec(tmp_path)
    spec.adjustment = "bogus"  # spec 级声明非法口径（spec 默认 qfq，ctx 兜底不再生效）
    with pytest.raises(ValueError, match="view"):
        run_factor(spec, _ctx(env, tmp_path / "out_x"))


def test_run_factor_pit_qfq_asof(env, tmp_path):
    # spec.adjustment=pit_qfq：view_prices asof=spec.date.end（研究日视角）——装配不崩且口径生效
    _seed(env, ex_date=True)
    spec_path = tmp_path / "spec_pit.yaml"
    spec_path.write_text("""
name: demo_pit
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
adjustment: pit_qfq
process: []
formula: |
  from polars_ta.prefix.wq import ts_delay
  signal = close / ts_delay(close, 1) - 1
""", encoding="utf-8")
    out_dir = tmp_path / "out_pit"
    result = run_factor(load_spec(spec_path), _ctx(env, out_dir))
    assert result.panel.height > 0
    a = result.panel.filter(pl.col("code") == "000001.SZ").sort("date")
    # asof=2024-01-09（adj=1.5）：除权日 01-04 因子 = 8×1.5/11 - 1（raw 口径会是 8/11 - 1）
    day3 = a.filter(pl.col("date") == datetime.date(2024, 1, 4))["signal"][0]
    assert day3 == pytest.approx(8 * 1.5 / 11 - 1)
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["adjustment"] == "pit_qfq"


def test_run_factor_pit_qfq_without_date_end(env, tmp_path):
    # 边界：spec.date.end 为空时 asof 回落面板最大日期（不崩）
    _seed(env, ex_date=True)
    spec_path = tmp_path / "spec_pit_noend.yaml"
    spec_path.write_text("""
name: demo_pit_noend
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
adjustment: pit_qfq
process: []
formula: |
  signal = close
""", encoding="utf-8")
    out_dir = tmp_path / "out_pit2"
    result = run_factor(load_spec(spec_path), _ctx(env, out_dir))
    assert result.panel.height > 0
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["adjustment"] == "pit_qfq"


def test_run_factor_pit_qfq_full_equals_chunked(env, tmp_path):
    """R02-I9：pit_qfq 全局 asof base 真接线（run_factor 级）——FULL 与 CHUNK
    逐 cell 一致，且首块（除权事件前）已用全局 base（非块内 latest fallback）。

    若 run_factor 未接线 pit_base_adj，chunk-2（01-02..01-03，事件 01-04 之前）
    会退回帧内 latest=1.0 → signal=10.0；接线后 = 10×1.0/1.5（asof=end 全局 base）。
    """
    _seed(env, ex_date=True, n_days=12)
    spec_path = tmp_path / "spec_pit_chunk.yaml"
    spec_path.write_text("""
name: demo_pit_chunk
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-17"
adjustment: pit_qfq
process: []
formula: |
  signal = close
""", encoding="utf-8")
    spec = load_spec(spec_path)
    full = run_factor(spec, _ctx(env, tmp_path / "out_pit_full", float32=False))
    chunked = run_factor(spec, _ctx(env, tmp_path / "out_pit_chunk",
                                    float32=False, chunk_days=2, warmup_days=1))
    joined = full.panel.join(chunked.panel, on=["date", "code"], how="inner",
                             suffix="_c")
    assert joined.height == full.panel.height == 12 * 2
    diff = (joined["signal"] - joined["signal_c"]).abs().max()
    assert float(diff) < 1e-9
    # 显式锚点：首块（事件前）已应用全局 base=1.5（不是帧内 latest=1.0）
    first = chunked.panel.filter(
        (pl.col("code") == "000001.SZ")
        & (pl.col("date") == datetime.date(2024, 1, 2)))
    assert first["signal"][0] == pytest.approx(10 * 1.0 / 1.5)


def test_formula_columns_extracts_data_cols_only():
    formula = '''
from polars_ta.prefix.wq import ts_mean, ts_delay

def helper(x, n):
    return ts_mean(x, n)

_vol = helper(close, 20)
_mom = ts_delay(close, 1)
signal = abs(_vol) + _mom * open - if_else(close > open, 1, 0)
'''
    # def 名/参数、import 名、算子调用名、_ 前缀中间变量、signal 均排除
    assert _formula_columns(formula) == ["close", "open"]


def test_formula_columns_excludes_date_code():
    assert _formula_columns("signal = close / open - 1") == ["close", "open"]
    assert _formula_columns("signal = close / date") == ["close"]


def test_formula_columns_assign_intermediate_variable():
    # 回归：赋值中间变量（非下划线，如 ret）不能误判为数据列（否则 load_daily 报「未知列名: ['ret']」）
    assert _formula_columns("ret = ts_delay(close, 1)\nsignal = ret") == ["close"]
    assert _formula_columns("ret = close * 2\nsignal = ret + open") == ["close", "open"]
    # AnnAssign 目标名同样纳入 defined（target 为单个，非 targets 列表）
    assert _formula_columns("ret: float = close * 2\nsignal = ret") == ["close"]


def test_run_factor_chunked_smoke(env, tmp_path):
    # 分块跑通：12 天日历、chunk 2（6 块）、warmup 1 → panel 行数 = 12 天 × 2 代码
    _seed(env, n_days=12)
    spec = _spec(tmp_path)
    spec.date.end = "2024-01-17"
    out_dir = tmp_path / "out_chunked"
    result = run_factor(spec, _ctx(env, out_dir, chunk_days=2, warmup_days=1))
    assert result.panel.height == 12 * 2
    assert result.panel["date"].min() == datetime.date(2024, 1, 2)
    assert result.panel["date"].max() == datetime.date(2024, 1, 17)
    assert result.panel["signal"].is_not_null().sum() > 0
    assert json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))["panel_rows"] == 24


def test_run_factor_chunked_invalid_chunk_days(env, tmp_path):
    _seed(env, n_days=12)
    spec = _spec(tmp_path)
    with pytest.raises(ValueError, match="chunk_days"):
        run_factor(spec, _ctx(env, tmp_path / "out_bad", chunk_days=0))


# ---------- 分块一致性回归 ----------


def _chunk_spec(tmp_path):
    path = tmp_path / "spec_chunk.yaml"
    path.write_text("""
name: demo_chunk
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-17"
process:
  - standardize()
formula: |
  from polars_ta.prefix.wq import ts_mean, ts_std_dev, cs_rank
  _m = ts_mean(close, 3)
  _v = ts_std_dev(close, 3)
  signal = cs_rank(-_m) + log(close) - _v
""", encoding="utf-8")
    return load_spec(path)


def test_chunked_consistency_with_full_run(env, tmp_path):
    # M6-04 关键回归：分块（含 qfq 归一 + label right lookahead）vs 整段跑——
    # signal 逐 cell 相等；forward 对**全部 full 非 null 行**逐 cell 一致；
    # **内部 chunk boundary 不再产生 extra null**（旧语义"块边界 null 是接受的差异"已删除）
    _seed(env, ex_date=True, n_days=12)
    spec = _chunk_spec(tmp_path)
    full = run_factor(spec, _ctx(env, tmp_path / "out_full", float32=False))
    chunked = run_factor(spec, _ctx(env, tmp_path / "out_chunked",
                                    float32=False, chunk_days=6, warmup_days=1))
    joined = full.panel.join(chunked.panel, on=["date", "code"], how="inner", suffix="_c")
    # 行数一致（12 天 × 2 代码；warmup 段行已在分块内丢弃）
    assert joined.height == full.panel.height == 12 * 2
    # signal 逐 cell 相等（float64：1e-9；含 log(close) 绝对水平输入 → 验证 qfq 归一）
    diff = (joined["signal"] - joined["signal_c"]).abs().max()
    assert float(diff) < 1e-9
    # forward：full 非 null 的行 chunked 必须完全一致（12 天 → 前 7 天 × 2 代码非 null）
    mask = joined["forward_return_5d"].is_not_null()
    assert mask.sum() == (12 - 5) * 2
    fdiff = (joined.filter(mask)["forward_return_5d"]
             - joined.filter(mask)["forward_return_5d_c"]).abs().max()
    assert float(fdiff) < 1e-9
    # 内部 chunk boundary 不再产生 extra null（M6-04 核心——right lookahead 修复）
    extra = joined.filter(joined["forward_return_5d"].is_not_null()
                          & joined["forward_return_5d_c"].is_null())
    assert extra.height == 0


def test_chunked_pure_cs_consistency(env, tmp_path):
    # 纯 CS 公式（无 ts_ 窗口）→ warmup 自动=0；分块 vs 整段一致
    _seed(env, n_days=12)
    spec = _chunk_spec(tmp_path)
    spec.formula = "signal = cs_rank(-close)"
    full = run_factor(spec, _ctx(env, tmp_path / "out_pure_full", float32=False))
    chunked = run_factor(spec, _ctx(env, tmp_path / "out_pure_chunked",
                                    float32=False, chunk_days=3))
    joined = full.panel.join(chunked.panel, on=["date", "code"], how="inner", suffix="_c")
    assert joined.height == full.panel.height == 12 * 2
    diff = (joined["signal"] - joined["signal_c"]).abs().max()
    assert float(diff) < 1e-9


# ---------- R04-P2：universe frame 复用（同历一次；分块按并集切片） ----------


def _uf_call_spy(monkeypatch):
    """run_factor 内 resolve_universe_frame 调用计数（包装真函数，只记录 dates）。"""
    import factorlab.app.run as run_mod
    real = run_mod.resolve_universe_frame
    calls = []

    def _spy(spec, rd, dates, **kw):
        calls.append(list(dates))
        return real(spec, rd, dates, **kw)

    monkeypatch.setattr(run_mod, "resolve_universe_frame", _spy)
    return calls


def test_run_factor_universe_resolved_once_nonchunked(env, tmp_path, monkeypatch):
    """R04-P2：非分块 signal/label 日历相同（全窗）→ resolve_universe_frame
    恰 1 次（此前 2 次完全相同输入）；请求日期集仍为全窗日历。"""
    _seed(env)
    calls = _uf_call_spy(monkeypatch)
    result = run_factor(_spec(tmp_path), _ctx(env, tmp_path / "out"))
    assert len(calls) == 1
    assert [str(d) for d in calls[0]] == ["2024-01-02", "2024-01-03",
                                          "2024-01-04", "2024-01-05",
                                          "2024-01-08", "2024-01-09"]
    assert result.panel.height == 12 * 1


def test_run_factor_universe_resolved_once_per_chunk_union(env, tmp_path,
                                                           monkeypatch):
    """R04-P2：分块下 label 带右 lookahead（日历不同）→ 每块按并集解析 1 次
    （此前每块 2 次）；并集含 lookahead 日期；chunk vs 整段 signal 逐 cell 一致。"""
    _seed(env, ex_date=True, n_days=12)
    calls = _uf_call_spy(monkeypatch)
    spec = _chunk_spec(tmp_path)
    full = run_factor(spec, _ctx(env, tmp_path / "out_full", float32=False))
    assert len(calls) == 1                     # 整段：signal == label 全窗
    calls.clear()
    chunked = run_factor(spec, _ctx(env, tmp_path / "out_chunked",
                                    float32=False, chunk_days=6, warmup_days=1))
    assert len(calls) == 2                     # 2 块 × 1 次（此前 2 块 × 2 次）
    # 首块并集 = [chunk_start..label_end]（12 日）> signal 窗（6 日）——证明是并集
    assert len(calls[0]) == 12
    assert len(calls[1]) == 7                  # [load_start(cal5)..label_end(cal11)]
    joined = full.panel.join(chunked.panel, on=["date", "code"], how="inner",
                             suffix="_c")
    assert joined.height == full.panel.height == 12 * 2
    assert float((joined["signal"] - joined["signal_c"]).abs().max()) < 1e-9


# ---------- R01-ENG C1/C2：未来函数门 E2E ----------


def _formula_spec(tmp_path, formula: str, name: str = "demo_guard",
                  end: str = "2024-01-17"):
    body = "\n".join("  " + line for line in formula.splitlines())
    path = tmp_path / f"{name}.yaml"
    path.write_text(f"""
name: {name}
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "{end}"
formula: |
{body}
process: []
""", encoding="utf-8")
    return load_spec(path)


def test_run_factor_rejects_negative_subscript(env, tmp_path):
    # C1 E2E：close[-1] 即 ts_delay(close,-1)，引擎必须拒绝（不能算出 close(t+1)）
    _seed(env, n_days=12)
    spec = _formula_spec(tmp_path, "signal = close[-1]")
    with pytest.raises(FactorDSLError, match="负位移"):
        run_factor(spec, _ctx(env, tmp_path / "out_sub"))


def test_run_factor_rejects_negative_shift_via_named_const(env, tmp_path):
    # C2 E2E：-_n 内嵌 Name 的负位移必须拒绝（实测旧代码产出 shift(-3)）
    _seed(env, n_days=12)
    spec = _formula_spec(tmp_path, "_n = 3\nsignal = ts_delay(close, -_n)")
    with pytest.raises(FactorDSLError, match="负位移"):
        run_factor(spec, _ctx(env, tmp_path / "out_const"))


def test_run_factor_accepts_positive_subscript(env, tmp_path):
    # 正向控制：close[1] = ts_delay(close,1) 是合法 lookback（不得误杀）
    _seed(env, n_days=12)
    spec = _formula_spec(tmp_path, "signal = close[1]")
    result = run_factor(spec, _ctx(env, tmp_path / "out_pos"))
    frame = result.signal_artifact.frame.sort(["code", "date"])
    assert frame.height > 0
    assert 0 < frame["signal"].null_count() < frame.height


def test_run_factor_accepts_positive_named_const_shift(env, tmp_path):
    _seed(env, n_days=12)
    spec = _formula_spec(tmp_path, "_n = 3\nsignal = ts_delay(close, _n)")
    result = run_factor(spec, _ctx(env, tmp_path / "out_pos_named"))
    assert result.signal_artifact.frame.height > 0


# ---------- R01-ENG I1：分块 × 累计算子 fail fast ----------


def test_chunk_days_rejects_cumulative_operator(env, tmp_path):
    # I1：ts_cum_sum 每块重置 → 分块违反"逐 cell 一致"文档承诺。
    # 整段跑合法；分块路径必须在跑之前 fail fast（指引单块跑）。
    _seed(env, n_days=12)
    spec = _formula_spec(tmp_path, "signal = ts_cum_sum(close)")
    full = run_factor(spec, _ctx(env, tmp_path / "out_cum_full"))
    assert full.signal_artifact.frame.height > 0
    with pytest.raises(ValueError, match="累计算子"):
        run_factor(spec, _ctx(env, tmp_path / "out_cum_chunk",
                              chunk_days=3, warmup_days=1))


def test_chunk_days_rejects_vwap_macro_expansion(env, tmp_path):
    # vwap 经 expand_platform_macros 展开为 ts_cum_sum → 同样拦截（展开后扫描）
    _seed(env, n_days=12)
    spec = _formula_spec(tmp_path, "signal = vwap(high, low, close, volume)")
    with pytest.raises(ValueError, match="累计算子"):
        run_factor(spec, _ctx(env, tmp_path / "out_vwap_chunk",
                              chunk_days=3, warmup_days=1))


def test_chunk_days_allows_window_operator_control(env, tmp_path):
    # 负向控制：窗口算子（非累计）分块继续可用（不得把门扩大成"分块全禁"）
    _seed(env, n_days=12)
    spec = _formula_spec(tmp_path, "signal = ts_mean(close, 3)")
    result = run_factor(spec, _ctx(env, tmp_path / "out_win_chunk",
                                   chunk_days=3, warmup_days=1))
    assert result.signal_artifact.frame.height > 0


def test_chunk_days_rejects_cumulative_in_pool_formula(env, tmp_path):
    # I1：累计算子出现在池公式（universe.formula）同样拦截（helper 同时扫 pool）
    _seed(env, n_days=12)
    path = tmp_path / "pool_cum.yaml"
    path.write_text("""
name: demo_pool_cum
category: custom
direction: 1
universe:
  formula: "ts_cum_sum(close) > 0"
date:
  start: "2024-01-02"
  end: "2024-01-17"
formula: |
  signal = close
process: []
""", encoding="utf-8")
    spec = load_spec(path)
    with pytest.raises(ValueError, match="累计算子"):
        run_factor(spec, _ctx(env, tmp_path / "out_pool_cum",
                              chunk_days=3, warmup_days=1))


def test_cumulative_ops_used_resolves_import_alias():
    from factorlab.core.engine.compute import cumulative_ops_used
    src = ("from polars_ta.prefix.wq import ts_cum_sum as cs\n"
           "signal = cs(close) + ts_cum_max(open)")
    assert cumulative_ops_used(src) == ["ts_cum_max", "ts_cum_sum"]
    assert cumulative_ops_used("signal = ts_mean(close, 3)") == []


# ---------- R03-I1：exclude_st 缺 stock_st 的显式降级 ----------


def _st_degrade_spec(tmp_path, name="demo_st", outputs=None):
    """rules 池带 exclude_st 的 spec；outputs 非 None 时多输出（signal/neg）。"""
    if outputs:
        outs_line = f"outputs: [{', '.join(outputs)}]\n"
        body = "  signal = close / open - 1\n  neg = -(close / open - 1)\n"
    else:
        outs_line = ""
        body = "  signal = close / open - 1\n"
    path = tmp_path / f"{name}.yaml"
    path.write_text(f"""
name: {name}
category: custom
direction: 1
universe:
  rules: {{exclude_st: true, exchanges: ["SSE", "SZSE"]}}
date:
  start: "2024-01-02"
  end: "2024-01-09"
{outs_line}formula: |
{body}""", encoding="utf-8")
    return load_spec(path)


def _seed_no_st(env):
    """生产 CH 形态：无 stock_st 表（exclude_st 的降级触发条件）。"""
    tables = _tables()
    tables.pop("stock_st")
    env.seed(tables)


@pytest.mark.parametrize("outputs", [None, ["signal", "neg"]])
def test_run_factor_st_degrade_allow_summary_and_warning(env, tmp_path, monkeypatch, outputs):
    """开关 allow + 无 stock_st：run 正常产出 + STDegradedWarning + summary st_degrade=true
    （单输出/多输出两条 summary 分支都审计）。"""
    _seed_no_st(env)
    monkeypatch.setattr(settings, "st_degrade", "allow")
    spec = _st_degrade_spec(tmp_path, outputs=outputs)
    out = tmp_path / ("out_multi" if outputs else "out_single")
    with pytest.warns(STDegradedWarning, match="ST 未知按非 ST 处理，结果为无 ST 口径"):
        result = run_factor(spec, _ctx(env, out))
    assert result.panel.height > 0
    assert result.summary["st_degrade"] is True
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["st_degrade"] is True


def test_run_factor_st_degrade_default_fails(env, tmp_path):
    """默认（fail）+ 无 stock_st：run 入口 fail fast（不得静默产出无 ST 结果）。"""
    _seed_no_st(env)
    spec = _st_degrade_spec(tmp_path)
    with pytest.raises(ValueError, match="stock_st"):
        run_factor(spec, _ctx(env, tmp_path / "out"))


def test_run_factor_st_degrade_noop_when_table_present(env, tmp_path, monkeypatch):
    """有 stock_st 时开关 allow 无副作用：不告警、summary st_degrade=false、ST 股照常剔除。"""
    tables = _tables()
    # 000001 全窗 ST（coverage = 样本窗）→ exclude_st 生效把它剔出面板
    tables["stock_st"] = ([("ts_code", "str"), ("trade_date", "date")],
                          [("000001.SZ", d) for d in _DATES])
    env.seed(tables)
    monkeypatch.setattr(settings, "st_degrade", "allow")
    spec = _st_degrade_spec(tmp_path)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        result = run_factor(spec, _ctx(env, tmp_path / "out"))
    assert not [w for w in rec if issubclass(w.category, STDegradedWarning)]
    assert result.summary["st_degrade"] is False
    # 候选集不受 exclude_st 影响（动态 PIT 条件）；面板成员才是 ST 过滤的结果
    assert result.summary["codes"] == ["000001.SZ", "600519.SH"]
    assert set(result.panel["code"].unique().to_list()) == {"600519.SH"}
