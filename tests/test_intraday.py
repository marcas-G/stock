"""M3：intraday 读接口（bars_1m / tick_trades / tick_orders / tick_snapshots）。

生产数据仅在 ClickHouse（bars_1m 1.85B×11 列 2020-01..；tick_* ~14B×3 表，
2025-08..2026-08），duckdb 平台文件无 intraday 表 → duckdb 后端显式
ValueError（编译函数不实现，读函数公开 API 单写）。本文件全部用 ch_db 假库
（列型按生产 DDL 建模，seed 走 dualbridge）。

合约（docs/interface.md 同步）：
- load_<表>(rd, code, day=… / date_start=…&date_end=…)（区间闭区间，ISO 日期）
- code 接受平台 6 位纯数字（经 stock_basic.symbol 解析）或带后缀 ts_code；
  输出 code 一律 6 位纯数字（与 daily 读路径一致）
- 输出列 = cols 白名单（默认 _DEFAULT_*，顺序即输出顺序）；snapshots 默认
  排除 10 档盘口与指数统计列
- 排序：bars_1m 按 datetime；tick_* 按 (time_ms, 序号列)；空结果返回空 frame
- datetime 统一 naive Asia/Shanghai 墙钟（ms）；tick price 为 price_x10000 原生
"""
import datetime as dt

import polars as pl
import pytest

import dualbridge
from factorlab.adapters import intraday
from factorlab.app.bootstrap import open_read

_D = "2026-08-21"
_D2 = "2026-08-20"

# 生产 DDL 对齐（列 kind 见 dualbridge 头注释）
_BARS_COLS = [("datetime", "datetime"), ("trade_date", "date"), ("code", "str"),
              ("minute_index", "u16"), ("session_type", "u8"),
              ("open", "f32"), ("high", "f32"), ("low", "f32"), ("close", "f32"),
              ("amount", "f64"), ("volume", "f64")]
_TRADES_COLS = [("trade_date", "date"), ("code", "str"), ("time_ms", "u32"),
                ("trade_no", "u64"), ("bs", "u8"), ("price_x10000", "i32"),
                ("volume", "u32"), ("ask_seq", "u64"), ("bid_seq", "u64")]
_ORDERS_COLS = [("trade_date", "date"), ("code", "str"), ("time_ms", "u32"),
                ("order_no", "u64"), ("exch_order_no", "u64"),
                ("order_type", "str"), ("bs", "str"), ("price_x10000", "i32"),
                ("volume", "u32")]
_SNAP_COLS = [("trade_date", "date"), ("code", "str"), ("time_ms", "u32"),
              ("price", "f64"), ("volume", "f64"), ("amount", "f64"),
              ("n_trades", "f64"), ("iopv", "f64"), ("trade_flag", "str"),
              ("bs", "str"), ("cum_volume", "f64"), ("cum_amount", "f64"),
              ("high", "f64"), ("low", "f64"), ("open", "f64"),
              ("prev_close", "f64"), ("wavg_ask", "f64"), ("wavg_bid", "f64"),
              ("ask_total", "f64"), ("bid_total", "f64"),
              # 盘口/指数统计列（默认投影外）
              ("ask_p1", "f64"), ("bid_p1", "f64"), ("unweighted_index", "f64")]

BARS_DEFAULT = ["datetime", "trade_date", "code", "minute_index", "session_type",
                "open", "high", "low", "close", "amount", "volume"]
TRADES_DEFAULT = ["trade_date", "code", "time_ms", "trade_no", "bs",
                  "price_x10000", "volume", "ask_seq", "bid_seq"]
ORDERS_DEFAULT = ["trade_date", "code", "time_ms", "order_no", "exch_order_no",
                  "order_type", "bs", "price_x10000", "volume"]
SNAP_DEFAULT = ["trade_date", "code", "time_ms", "price", "volume", "amount",
                "n_trades", "iopv", "trade_flag", "bs", "cum_volume",
                "cum_amount", "high", "low", "open", "prev_close", "wavg_ask",
                "wavg_bid", "ask_total", "bid_total"]


def _sb(ch_db, symbols=("000001", "600519")):
    """stock_basic（6 位 symbol → ts_code 后缀桥；industry 可空如生产）。"""
    rows = [("000001", "000001.SZ", "SZSE", "19910101", "银行"),
            ("600519", "600519.SH", "SSE", "20010827", "白酒")]
    return {
        "stock_basic": ([("symbol", "str"), ("ts_code", "str"),
                         ("exchange", "str"), ("list_date", "date"),
                         ("industry", "str?")],
                        [r for r in rows if r[0] in symbols]),
    }


def _seed_bars(ch_db):
    """000001.SZ 两日 × 分钟 + 600519.SH 单行；插入故意乱序（验证读侧排序）。"""
    t = dt.datetime
    tables = _sb(ch_db)
    tables["bars_1m"] = (_BARS_COLS, [
        (t(2026, 8, 21, 10, 31, 0, 500000), "20260821", "000001.SZ", 1, 1,
         11.38, 11.40, 11.37, 11.39, 1.4e7, 1.2e6),
        (t(2026, 8, 21, 10, 30, 0, 500000), "20260821", "000001.SZ", 0, 1,
         11.36, 11.42, 11.35, 11.41, 3.6e7, 3.2e6),
        (t(2026, 8, 21, 10, 32, 0, 500000), "20260821", "000001.SZ", 2, 1,
         11.39, 11.41, 11.36, 11.38, 1.1e7, 1.0e6),
        (t(2026, 8, 20, 14, 59, 0, 500000), "20260820", "000001.SZ", 239, 1,
         11.20, 11.25, 11.19, 11.24, 2.2e7, 2.0e6),
        (t(2026, 8, 21, 10, 30, 0, 500000), "20260821", "600519.SH", 0, 1,
         1290.0, 1295.0, 1288.0, 1292.0, 8.1e7, 6.2e4),
    ])
    client, db = ch_db
    dualbridge.seed_ch(client, db, tables)


def _seed_trades(ch_db):
    """乱序插入 4 行 tick 成交（验证 (time_ms, trade_no) 读侧升序）。"""
    tables = _sb(ch_db, symbols=("000001",))
    tables["tick_trades"] = (_TRADES_COLS, [
        ("20260821", "000001.SZ", 34201001, 1000, 1, 114100, 200, 5000, 4000),
        ("20260821", "000001.SZ", 34200350, 1001, 0, 114000, 300, 5001, 4001),
        ("20260821", "000001.SZ", 34200700, 1002, 1, 114100, 150, 5002, 4002),
        ("20260821", "000001.SZ", 34200010, 1003, 0, 113900, 800, 5003, 4003),
    ])
    client, db = ch_db
    dualbridge.seed_ch(client, db, tables)


def _seed_orders(ch_db):
    tables = _sb(ch_db, symbols=("000001",))
    tables["tick_orders"] = (_ORDERS_COLS, [
        ("20260821", "000001.SZ", 9001000, 42, 1, "A", "B", 114100, 500),
        ("20260821", "000001.SZ", 9002000, 43, 2, "D", "S", 114000, 300),
    ])
    client, db = ch_db
    dualbridge.seed_ch(client, db, tables)


def _seed_snaps(ch_db):
    tables = _sb(ch_db, symbols=("000001",))
    tables["tick_snapshots"] = (_SNAP_COLS, [
        ("20260821", "000001.SZ", 93000000, 11.40, 3200.0, 3.6e7, 120.0,
         1.02, "X", "B", 10000.0, 3.5e7, 11.42, 11.35, 11.36, 11.30,
         11.401, 11.398, 5.0e6, 4.0e6, 11.41, 11.39, 1.001),
        ("20260821", "000001.SZ", 93001000, 11.41, 500.0, 5.6e6, 15.0,
         1.02, "X", "S", 10500.0, 4.06e7, 11.42, 11.35, 11.36, 11.30,
         11.402, 11.399, 4.9e6, 4.1e6, 11.41, 11.39, 1.001),
    ])
    client, db = ch_db
    dualbridge.seed_ch(client, db, tables)


# ---------------- bars_1m ----------------

def test_bars_1m_day_load(ch_db):
    """day 加载：该日该 code 全部分钟行，datetime 升序，列 = 默认投影。"""
    _seed_bars(ch_db)
    df = intraday.load_bars_1m(open_read(data_backend="ch"), "000001", day=_D)
    assert df.columns == BARS_DEFAULT
    assert df.height == 3
    times = df["datetime"].to_list()
    assert times == sorted(times) and times[0] == dt.datetime(2026, 8, 21, 10, 30, 0, 500_000)
    assert set(df["code"].unique()) == {"000001"}          # 6 位归一
    assert df["close"].to_list() == pytest.approx([11.41, 11.39, 11.38], rel=1e-6)
    assert df["minute_index"].to_list() == [0, 1, 2]
    assert df["session_type"].to_list() == [1, 1, 1]


def test_bars_1m_suffixed_code_and_range(ch_db):
    """带后缀 code + 区间加载（闭区间，含两端日）。"""
    _seed_bars(ch_db)
    rd = open_read(data_backend="ch")
    df = intraday.load_bars_1m(rd, "000001.SZ", date_start=_D2, date_end=_D)
    assert df.height == 4
    assert set(df["trade_date"].unique()) == {dt.date(2026, 8, 20), dt.date(2026, 8, 21)}
    df2 = intraday.load_bars_1m(rd, "600519.SH", date_start=_D2, date_end=_D)
    assert df2.height == 1 and df2["code"][0] == "600519"
    assert df2["close"][0] == pytest.approx(1292.0, rel=1e-5)


def test_bars_1m_datetime_naive_ms(ch_db):
    """datetime 输出统一 naive（Asia/Shanghai 墙钟，ms）——可被每日对齐。"""
    _seed_bars(ch_db)
    df = intraday.load_bars_1m(open_read(data_backend="ch"), "000001", day=_D)
    assert df["datetime"].dtype == pl.Datetime("ms")
    assert df["datetime"].dtype.time_zone is None


def test_bars_1m_empty_and_error_paths(ch_db):
    """空结果（无数据日）→ 空 frame 不抛；参数错误/未知列/未知 code → ValueError。"""
    _seed_bars(ch_db)
    rd = open_read(data_backend="ch")
    e = intraday.load_bars_1m(rd, "000001", day="2026-08-19")
    assert e.height == 0 and e.columns == BARS_DEFAULT
    with pytest.raises(ValueError, match="day|date_start|date_end"):
        intraday.load_bars_1m(rd, "000001")          # 无日期窗
    with pytest.raises(ValueError, match="bogus"):
        intraday.load_bars_1m(rd, "000001", day=_D, cols=["datetime", "bogus"])
    with pytest.raises(ValueError, match="000999"):
        intraday.load_bars_1m(rd, "000999", day=_D)  # 6 位无 stock_basic 映射


def test_bars_1m_cols_whitelist(ch_db):
    """cols 白名单：输出顺序 = 请求顺序。"""
    _seed_bars(ch_db)
    df = intraday.load_bars_1m(open_read(data_backend="ch"), "000001", day=_D,
                               cols=["close", "datetime"])
    assert df.columns == ["close", "datetime"] and df.height == 3


# ---------------- load_bars_1m_codes（批读；引擎分钟装配与研究批算共用） ----------------

def _sortlike(df):
    """批读语义排序 (code, datetime)：单读结果 concat 后同键可比。"""
    return df.sort(["code", "datetime"])


def test_bars_1m_codes_batch_matches_single_loader(ch_db):
    """批读 == 单读逐行（多 code 同窗）；行数 > 任一单 code（非单 code 存根）。"""
    _seed_bars(ch_db)
    rd = open_read(data_backend="ch")
    codes = ["000001", "600519"]
    batch = intraday.load_bars_1m_codes(rd, codes, date_start=_D2, date_end=_D)
    single = pl.concat([intraday.load_bars_1m(rd, c, date_start=_D2, date_end=_D)
                        for c in codes])
    assert _sortlike(batch).equals(_sortlike(single))          # 逐行全等
    assert batch.height == single.height == 5                  # 000001 4 行 + 600519 1 行
    assert batch.height > single.filter(pl.col("code") == "000001").height  # 非单 code 存根
    assert batch["code"].dtype == pl.String
    assert sorted(batch["code"].unique().to_list()) == ["000001", "600519"]
    assert batch["code"].value_counts().sort("code")["count"].to_list() == [4, 1]
    assert batch.group_by("code").agg(
        pl.col("datetime").is_not_null().all()).height == 2    # 两 code 都到


def test_bars_1m_codes_mixed_code_forms_normalized(ch_db):
    """混合 6 位 + 带后缀输入；输出 code 一律 6 位归一。"""
    _seed_bars(ch_db)
    rd = open_read(data_backend="ch")
    batch = intraday.load_bars_1m_codes(rd, ["000001", "600519.SH"],
                                        date_start=_D2, date_end=_D)
    assert batch.height == 5
    assert set(batch["code"].unique()) == {"000001", "600519"}
    assert not batch["code"].str.contains(r"\.").any()


def test_bars_1m_codes_empty_window_empty_frame(ch_db):
    """无数据窗 → 同投影空 frame（不抛）；空窗与单读一致。"""
    _seed_bars(ch_db)
    rd = open_read(data_backend="ch")
    e = intraday.load_bars_1m_codes(rd, ["000001", "600519"], date_start=_D2,
                                    date_end=_D2)
    assert e.height == 1                                   # 仅 000001 的 08-20 行
    assert set(e["code"].unique()) == {"000001"}
    e2 = intraday.load_bars_1m_codes(rd, ["000001"], date_start="2026-08-19",
                                     date_end="2026-08-19")
    assert e2.height == 0 and e2.columns == BARS_DEFAULT


def test_bars_1m_codes_requires_closed_window(ch_db):
    """防全表扫描：date_start/date_end 任一缺失 → ValueError（比单 code 更严）。"""
    _seed_bars(ch_db)
    rd = open_read(data_backend="ch")
    with pytest.raises(ValueError, match="date_start|date_end"):
        intraday.load_bars_1m_codes(rd, ["000001"])
    with pytest.raises(ValueError, match="date_start|date_end"):
        intraday.load_bars_1m_codes(rd, ["000001"], date_start=_D2)
    with pytest.raises(ValueError, match="date_start|date_end"):
        intraday.load_bars_1m_codes(rd, ["000001"], date_end=_D)


def test_bars_1m_codes_empty_codes_rejected(ch_db):
    _seed_bars(ch_db)
    rd = open_read(data_backend="ch")
    with pytest.raises(ValueError, match="codes"):
        intraday.load_bars_1m_codes(rd, [], date_start=_D2, date_end=_D)


def test_bars_1m_codes_unknown_code_fails_fast(ch_db):
    """未知 code（无 stock_basic 映射）→ 整批 ValueError（防静默丢 code）。"""
    _seed_bars(ch_db)
    rd = open_read(data_backend="ch")
    with pytest.raises(ValueError, match="000999"):
        intraday.load_bars_1m_codes(rd, ["000001", "000999"], date_start=_D2,
                                    date_end=_D)


def test_bars_1m_codes_cols_whitelist_and_unknown_col(ch_db):
    """cols 白名单顺序输出；未知列 ValueError（沿用 _TABLE_COLS 门）。"""
    _seed_bars(ch_db)
    rd = open_read(data_backend="ch")
    df = intraday.load_bars_1m_codes(rd, ["000001", "600519"], date_start=_D2,
                                     date_end=_D, cols=["close", "code"])
    assert df.columns == ["close", "code"] and df.height == 5
    with pytest.raises(ValueError, match="bogus"):
        intraday.load_bars_1m_codes(rd, ["000001"], date_start=_D2, date_end=_D,
                                    cols=["close", "bogus"])


# ---------------- duckdb 后端显式拒绝 ----------------

def test_intraday_duckdb_backend_raises(tmp_path):
    """duckdb 平台文件无 intraday 表——读接口显式 ValueError（非底层表缺失错误）。"""
    dualbridge.seed_duckdb(tmp_path / "q.duckdb", {})   # 空平台文件（read_only 需已存在）
    rd = open_read(db_path=tmp_path / "q.duckdb")
    with pytest.raises(ValueError, match="ClickHouse"):
        intraday.load_bars_1m(rd, "000001", day=_D)
    with pytest.raises(ValueError, match="ClickHouse"):
        intraday.load_tick_trades(rd, "000001", day=_D)
    with pytest.raises(ValueError, match="ClickHouse"):
        intraday.load_tick_snapshots(rd, "000001.SZ", day=_D)


# ---------------- tick_trades ----------------

def test_tick_trades_day_sorted_and_types(ch_db):
    """乱序插入 → 读回 (time_ms) 升序；原生类型保留（price_x10000 整数语义）。"""
    _seed_trades(ch_db)
    df = intraday.load_tick_trades(open_read(data_backend="ch"), "000001", day=_D)
    assert df.columns == TRADES_DEFAULT
    assert df.height == 4
    assert df["time_ms"].to_list() == sorted(df["time_ms"].to_list())
    assert df["time_ms"].to_list()[0] == 34200010
    assert df["price_x10000"].dtype == pl.Int32
    assert df["trade_no"].dtype == pl.UInt64
    assert df["bs"].dtype == pl.UInt8
    assert df["volume"].dtype == pl.UInt32


# ---------------- tick_orders ----------------

def test_tick_orders_day_load(ch_db):
    _seed_orders(ch_db)
    df = intraday.load_tick_orders(open_read(data_backend="ch"), "000001", day=_D)
    assert df.columns == ORDERS_DEFAULT
    assert df.height == 2
    assert df["order_no"].to_list() == [42, 43]
    assert df["order_type"].to_list() == ["A", "D"]
    assert df["bs"].to_list() == ["B", "S"]
    assert df["price_x10000"].to_list() == [114100, 114000]


# ---------------- tick_snapshots（默认投影排除盘口） ----------------

def test_tick_snapshots_default_projection_excludes_book(ch_db):
    """默认投影 = 核心快照列（无 10 档盘口/指数统计）；可 cols 请求盘口列。"""
    _seed_snaps(ch_db)
    rd = open_read(data_backend="ch")
    df = intraday.load_tick_snapshots(rd, "000001", day=_D)
    assert df.columns == SNAP_DEFAULT
    assert df.height == 2
    assert "ask_p1" not in df.columns and "unweighted_index" not in df.columns
    assert df["time_ms"].to_list() == [93000000, 93001000]
    wide = intraday.load_tick_snapshots(rd, "000001", day=_D,
                                        cols=[*SNAP_DEFAULT, "ask_p1", "bid_p1",
                                              "unweighted_index"])
    assert wide.columns[-3:] == ["ask_p1", "bid_p1", "unweighted_index"]
    assert wide["ask_p1"].to_list() == [11.41, 11.41]
