"""load_daily 双腿参数化：env（duckdb 平台文件 | CH 临时库）见 tests/conftest.py；
数据以描述给出（date kind：duckdb VARCHAR 'YYYYMMDD' / CH Date，读函数各自 decode）。

duckdb 文件专属语义（锁/只读/缺失文件）留在 duckdb 单腿——ch 后端无文件概念。
"""

import datetime
import os

import duckdb
import polars as pl
import pytest

from factorlab.app.bootstrap import open_read
from factorlab.adapters.read.source import load_daily, load_daily_fill_state

# ---------------------------------------------------------------
# 共享数据集（原 build_db：tushare 原始列名——ts_code 带后缀/trade_date 'YYYYMMDD'/vol）
# ---------------------------------------------------------------
_BASE_TABLES = {
    "daily": ([("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
               ("vol", "f64"), ("amount", "f64")],
              [("000001.SZ", "20240102", 10.0, 11.0, 9.5, 10.5, 10.2, 0.3,
                0.0294, 100000.0, 1e6),
               ("000001.SZ", "20240103", 10.5, 11.5, 10.0, 11.0, 10.5, 0.5,
                0.0476, 110000.0, 1.1e6),
               ("600519.SH", "20240102", 20.0, 21.0, 19.0, 20.5, 19.8, 0.7,
                0.0354, 200000.0, 2e6)]),
    "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                    ("adj_factor", "f64")],
                   [("000001.SZ", "20240102", 1.0),
                    ("000001.SZ", "20240103", 1.0),
                    ("600519.SH", "20240102", 1.2)]),
    "daily_basic": ([("ts_code", "str"), ("trade_date", "date"),
                     ("turnover_rate", "f64"), ("total_mv", "f64")],
                    [("000001.SZ", "20240102", 1.5, 1e6),
                     ("000001.SZ", "20240103", 1.8, 1.1e6),
                     ("600519.SH", "20240102", 0.5, 5e6)]),
    # ch 编译器两层 IN 依赖 stock_basic（duckdb 腿多余表无副作用）
    "stock_basic": ([("symbol", "str"), ("ts_code", "str")],
                    [("000001", "000001.SZ"), ("600519", "600519.SH")]),
}

_DAILY_8_COLS = [("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
                 ("high", "f64"), ("low", "f64"), ("close", "f64"),
                 ("vol", "f64"), ("amount", "f64")]


def _seed_base(env):
    env.seed(_BASE_TABLES)


# ---------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------

def test_load_daily_filters_codes_and_dates(env):
    _seed_base(env)
    df = load_daily(env.rd, ["000001"], date_start="2024-01-03").collect()
    assert df["code"].to_list() == ["000001"]
    assert df["date"].to_list() == [datetime.date(2024, 1, 3)]


def test_load_daily_float32_cast_and_code_string(env):
    _seed_base(env)
    df = load_daily(env.rd, ["000001"]).collect()
    assert df.schema["close"] == pl.Float32
    assert df.schema["code"] == pl.String


def test_load_daily_column_pruning(env):
    _seed_base(env)
    df = load_daily(env.rd, ["000001"], cols=["close"]).collect()
    assert df.columns == ["date", "code", "close"]


def test_load_daily_date_end_inclusive(env):
    _seed_base(env)
    df = load_daily(
        env.rd, ["000001"], date_start="2024-01-02", date_end="2024-01-02"
    ).collect()
    assert df["date"].to_list() == [datetime.date(2024, 1, 2)]


def test_load_daily_multi_code_filter(env):
    _seed_base(env)
    df = load_daily(env.rd, ["000001", "600519"]).collect()
    assert df["code"].to_list() == ["000001", "000001", "600519"]


def test_load_daily_empty_result_keeps_schema(env):
    _seed_base(env)
    df = load_daily(env.rd, ["999999"]).collect()
    assert df.height == 0
    assert df.schema["date"] == pl.Date
    assert df.schema["code"] == pl.String
    assert df.schema["close"] == pl.Float32


def test_load_daily_maps_platform_columns(env):
    _seed_base(env)
    df = load_daily(env.rd, ["000001"], cols=["close", "adj_factor"]).collect()
    assert df.columns == ["date", "code", "close", "adj_factor"]
    assert df["code"].to_list() == ["000001", "000001"]  # ts_code 去后缀
    assert df["date"].dtype == pl.Date


def test_load_daily_maps_volume_column(env):
    """vol → volume 映射 + I7 canonical 单位（duckdb 源 手 ×100 / ch 源 股 恒等）。"""
    raw = [1000.0, 1100.0] if env.backend == "duckdb" else [100000.0, 110000.0]
    env.seed({
        "daily": ([("ts_code", "str"), ("trade_date", "date"), ("close", "f64"),
                   ("vol", "f64")],
                  [("000001.SZ", "20240102", 10.5, raw[0]),
                   ("000001.SZ", "20240103", 11.0, raw[1])]),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"), ("adj_factor", "f64")],
                       [("000001.SZ", "20240102", 1.0),
                        ("000001.SZ", "20240103", 1.0)]),
        "stock_basic": ([("symbol", "str"), ("ts_code", "str")],
                        [("000001", "000001.SZ")]),
    })
    df = load_daily(env.rd, ["000001"], cols=["volume"], float32=False).collect()
    assert "volume" in df.columns  # vol → volume
    assert df["volume"].to_list() == [100000.0, 110000.0]


def test_load_daily_joins_daily_basic_when_requested(env):
    _seed_base(env)
    df = load_daily(env.rd, ["000001"], cols=["close", "turnover"]).collect()
    assert "turnover" in df.columns
    assert df["turnover"].to_list() == pytest.approx([1.5, 1.8])  # float32 精度


def test_load_daily_joins_total_mv(env):
    _seed_base(env)
    df = load_daily(env.rd, ["000001"], cols=["close", "total_mv"]).collect()
    assert df["total_mv"].to_list() == [1000000.0, 1100000.0]


def test_load_daily_close_always_loaded(env):
    # close 恒加载（forward/评估依赖——M3a 契约），即使 cols 未请求
    _seed_base(env)
    df = load_daily(env.rd, ["000001"], cols=["open"]).collect()
    assert df.columns == ["date", "code", "open", "close"]


def test_load_daily_basic_columns_not_joined_by_default(env):
    # daily_basic 仅按需 join：cols 不含 turnover/total_mv 时无 basic 列
    _seed_base(env)
    df = load_daily(env.rd, ["000001"], cols=["open"]).collect()
    assert "turnover" not in df.columns
    assert "total_mv" not in df.columns


def test_load_daily_mixed_daily_and_basic_cols(env):
    _seed_base(env)
    df = load_daily(env.rd, ["000001"], cols=["open", "turnover", "close"]).collect()
    assert df["open"].to_list() == [10.0, 10.5]
    assert df["turnover"].to_list() == pytest.approx([1.5, 1.8])
    assert df["close"].to_list() == [10.5, 11.0]


def test_load_daily_accepts_yyyymmdd_date_range(env):
    # 日期过滤参数化：'YYYYMMDD' 与 'YYYY-MM-DD' 均接受（平台库 trade_date 为 YYYYMMDD）
    _seed_base(env)
    df = load_daily(
        env.rd, ["000001", "600519"],
        date_start="20240102", date_end="20240102",
    ).collect()
    assert df["date"].to_list() == [datetime.date(2024, 1, 2), datetime.date(2024, 1, 2)]


def test_load_daily_float32_disabled(env):
    _seed_base(env)
    df = load_daily(
        env.rd, ["000001"], cols=["close", "adj_factor"], float32=False
    ).collect()
    assert df.schema["close"] == pl.Float64
    assert df.schema["adj_factor"] == pl.Float64
    assert df.schema["date"] == pl.Date
    assert df.schema["code"] == pl.String


# ---------------------------------------------------------------
# 边界路径
# ---------------------------------------------------------------

def test_load_daily_drops_daily_row_without_adj_factor(env):
    # adj_factor 恒 join（inner）：daily 行缺 adj_factor 时被排除（复权不可消费）
    tables = {t: (cols, list(rows)) for t, (cols, rows) in _BASE_TABLES.items()}
    tables["daily"][1].append(
        ("000001.SZ", "20240104", 11.5, 12.0, 11.0, 12.0, 11.0, 1.0, 0.0909,
         120000.0, 1.2e6))  # adj_factor 无此日
    env.seed(tables)
    df = load_daily(env.rd, ["000001"]).collect()
    assert df["date"].to_list() == [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)]


def test_daily_basic_extended_columns_loaded(env):
    """扩展字段（pe_ttm/pb/dv_ratio）经 daily_basic join 加载。"""
    env.seed({
        "daily": (_DAILY_8_COLS,
                  [("000001.SZ", d, 10, 11, 9, 10.5, 1000, 100000)
                   for d in ["20240102", "20240103", "20240104"]]),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")],
                       [("000001.SZ", d, 1.0)
                        for d in ["20240102", "20240103", "20240104"]]),
        "daily_basic": ([("ts_code", "str"), ("trade_date", "date"),
                         ("turnover_rate", "f64"), ("total_mv", "f64"),
                         ("circ_mv", "f64"), ("pe_ttm", "f64"), ("pb", "f64"),
                         ("dv_ratio", "f64")],
                        [("000001.SZ", d, 1.0, 1e10, 5e9, 12.0, 1.5, 0.02)
                         for d in ["20240102", "20240103", "20240104"]]),
        "stock_basic": ([("symbol", "str"), ("ts_code", "str")],
                        [("000001", "000001.SZ")]),
    })
    df = load_daily(env.rd, ["000001"], cols=["close", "pe_ttm", "pb", "dv_ratio"]).collect()
    assert df["pe_ttm"].to_list() == pytest.approx([12.0] * 3)
    assert df["pb"].to_list() == pytest.approx([1.5] * 3)
    assert df["dv_ratio"].to_list() == pytest.approx([0.02] * 3)


def test_market_index_ret_loaded(env):
    """idx_ret（市场指数日收益）按需 join 加载。"""
    env.seed({
        "daily": (_DAILY_8_COLS,
                  [("000001.SZ", d, 10, 11, 9, 10.5, 1000, 100000)
                   for d in ["20240102", "20240103", "20240104"]]),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")],
                       [("000001.SZ", d, 1.0)
                        for d in ["20240102", "20240103", "20240104"]]),
        "index_daily": ([("ts_code", "str"), ("trade_date", "date"),
                         ("pct_chg", "f64")],
                        [("000852.SH", d, 1.0)
                         for d in ["20240102", "20240103", "20240104"]]),
        "stock_basic": ([("symbol", "str"), ("ts_code", "str")],
                        [("000001", "000001.SZ")]),
    })
    df = load_daily(env.rd, ["000001"], cols=["close", "idx_ret"]).collect()
    assert df["idx_ret"].to_list() == pytest.approx([0.01] * 3)  # pct_chg=1.0 → 0.01


# ---------------------------------------------------------------
# R01-DATA-I7：duckdb 源单位（teajoin 原始：手/千元）→ canonical（股/元）
# ---------------------------------------------------------------

def test_load_daily_normalizes_duckdb_units(env):
    """duckdb 平台库由 data rebuild 落 teajoin 原始单位（vol=手、amount=千元），
    ch 灌入侧已是 股/元——读面必须把 duckdb 腿 ×100/×1000 归一，两腿 canonical
    一致。恒等映射（不转换）的实现会让 duckdb 腿断言失败。"""
    raw_vol = 1234.0 if env.backend == "duckdb" else 123400.0     # 手 vs 股
    raw_amt = 5678.0 if env.backend == "duckdb" else 5678000.0    # 千元 vs 元
    env.seed({
        "daily": ([("ts_code", "str"), ("trade_date", "date"), ("close", "f64"),
                   ("vol", "f64"), ("amount", "f64")],
                  [("000001.SZ", "20240102", 10.0, raw_vol, raw_amt)]),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")],
                       [("000001.SZ", "20240102", 1.0)]),
        "stock_basic": ([("symbol", "str"), ("ts_code", "str")],
                        [("000001", "000001.SZ")]),
    })
    df = load_daily(env.rd, ["000001"], cols=["close", "volume", "amount"],
                    float32=False).collect()
    assert df["volume"].to_list() == [123400.0]
    assert df["amount"].to_list() == [5678000.0]


def test_load_daily_fill_state_normalizes_duckdb_units(env):
    """fill_state（跨 chunk 左边界 seed）与 load_daily 同单位契约。"""
    raw_vol = 12.0 if env.backend == "duckdb" else 1200.0
    env.seed({
        "daily": ([("ts_code", "str"), ("trade_date", "date"), ("close", "f64"),
                   ("vol", "f64")],
                  [("000001.SZ", "20240102", 10.0, raw_vol)]),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")],
                       [("000001.SZ", "20240102", 1.0)]),
        "stock_basic": ([("symbol", "str"), ("ts_code", "str")],
                        [("000001", "000001.SZ")]),
    })
    fs = load_daily_fill_state(env.rd, ["000001"], before="2024-01-03",
                               cols=["close", "volume"], float32=False)
    assert fs["volume"].to_list() == [1200.0]

# ---------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------

def test_load_daily_rejects_empty_codes(env):
    _seed_base(env)  # rd 惰性打开：duckdb 腿需文件存在
    with pytest.raises(ValueError, match="universe"):
        load_daily(env.rd, [])


def test_load_daily_unknown_col_raises_value_error(env):
    _seed_base(env)
    with pytest.raises(ValueError, match="未知列名"):
        load_daily(env.rd, ["000001"], cols=["nonexistent"]).collect()


def test_load_daily_rejects_raw_platform_vol_name(env):
    # 平台库原始列名 vol 不暴露：请求引擎列名 volume（错误消息给提示）
    _seed_base(env)
    with pytest.raises(ValueError, match="未知列名"):
        load_daily(env.rd, ["000001"], cols=["vol"]).collect()


# ---------------------------------------------------------------
# R02-C2 / R02-I4：staleness 锚点 + adv20 warm-start 行情行补足（双腿）
# ---------------------------------------------------------------

def test_load_last_close_dates_picks_latest_non_null(env):
    """R02-C2：每 code 最后非空 close 日期（daily ⨝ adj_factor）；bounds 语义。"""
    from factorlab.adapters.read.source import load_last_close_dates
    _seed_base(env)
    lc = load_last_close_dates(env.rd, ["000001", "600519"], before="2024-02-01")
    got = {r["code"]: r["last_close"] for r in lc.iter_rows(named=True)}
    assert got == {"000001": datetime.date(2024, 1, 3),
                   "600519": datetime.date(2024, 1, 2)}
    # after 含入下界：000001 最后 close 恰在 01-03（>= after → 命中）
    lc2 = load_last_close_dates(env.rd, ["000001"], before="2024-02-01",
                                after="2024-01-03")
    assert lc2["last_close"][0] == datetime.date(2024, 1, 3)
    # 更晚下界 → 窗口内无 close → 缺席（不是 None 行）
    lc3 = load_last_close_dates(env.rd, ["000001"], before="2024-02-01",
                                after="2024-01-04")
    assert lc3.height == 0
    assert lc3.columns == ["code", "last_close"]


def test_load_daily_tail_dates_counts_quote_rows(env):
    """R02-I4：warm-start = start 前第 n 个最近**行情行**日期（停牌日不占行）。"""
    from factorlab.adapters.read.source import load_daily_tail_dates
    _seed_base(env)
    tail = load_daily_tail_dates(env.rd, ["000001", "600519"],
                                 before="2024-02-01", n=1)
    got = {r["code"]: r["warm_start"] for r in tail.iter_rows(named=True)}
    assert got == {"000001": datetime.date(2024, 1, 3),
                   "600519": datetime.date(2024, 1, 2)}
    tail2 = load_daily_tail_dates(env.rd, ["000001"], before="2024-02-01", n=2)
    assert tail2["warm_start"][0] == datetime.date(2024, 1, 2)
    # 历史行 < n：回落到最早一行（不缺席、不报错）
    tail3 = load_daily_tail_dates(env.rd, ["600519"], before="2024-02-01", n=20)
    assert tail3["warm_start"][0] == datetime.date(2024, 1, 2)
    with pytest.raises(ValueError, match="n"):
        load_daily_tail_dates(env.rd, ["000001"], before="2024-02-01", n=0)


# ---------------------------------------------------------------
# duckdb 文件语义（单腿）：只读并发 / 错误后无残留锁 / 缺失文件
# ---------------------------------------------------------------

def test_load_daily_opens_read_only(tmp_path):
    db_path = tmp_path / "t.duckdb"
    _seed_legacy(tmp_path)
    rd = open_read(db_path=db_path)
    # 持有一个只读连接；若 load_daily 以读写模式打开，会因配置不同抛 ConnectionException
    ro = duckdb.connect(db_path, read_only=True)
    try:
        df = load_daily(rd, ["000001"]).collect()
        assert df.height == 2
    finally:
        ro.close()


def test_load_daily_unknown_col_does_not_lock_file(tmp_path):
    _seed_legacy(tmp_path)
    db_path = tmp_path / "t.duckdb"
    rd = open_read(db_path=db_path)
    try:
        with pytest.raises(ValueError, match="未知列名"):
            load_daily(rd, ["000001"], cols=["nonexistent"]).collect()
    finally:
        rd.close()
    # 错误后无残留锁：可重新以读写模式打开并删除文件（Windows 锁回归验证）
    db = duckdb.connect(db_path, read_only=False)
    db.execute("CREATE TABLE x (a INT)")
    db.close()
    os.remove(db_path)


def test_load_daily_query_error_does_not_lock_file(tmp_path):
    # 连接内部报错（daily 表不存在 -> BinderException）也必须释放连接
    db_path = tmp_path / "t.duckdb"
    db = duckdb.connect(db_path)
    db.execute("CREATE TABLE other (x INT)")
    db.close()
    rd = open_read(db_path=db_path)
    try:
        with pytest.raises(duckdb.Error):
            load_daily(rd, ["000001"]).collect()
    finally:
        rd.close()
    db2 = duckdb.connect(db_path, read_only=False)
    db2.execute("CREATE TABLE y (a INT)")
    db2.close()
    db_path.unlink()


def _seed_legacy(tmp_path):
    """duckdb 单腿测试用的旧式建库（复用共享数据描述写 duckdb 文件）。"""
    import dualbridge

    dualbridge.seed_duckdb(tmp_path / "t.duckdb", _BASE_TABLES)
