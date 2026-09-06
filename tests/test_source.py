"""load_daily 双腿参数化：env（duckdb 平台文件 | CH 临时库）见 tests/conftest.py；
数据以描述给出（date kind：duckdb VARCHAR 'YYYYMMDD' / CH Date，读函数各自 decode）。

duckdb 文件专属语义（锁/只读/缺失文件）留在 duckdb 单腿——ch 后端无文件概念。
"""

import datetime
import os

import duckdb
import polars as pl
import pytest

from factorlab.data.backend import open_read
from factorlab.data.source import load_daily

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
    _seed_base(env)
    df = load_daily(env.rd, ["000001"], cols=["volume"]).collect()
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
