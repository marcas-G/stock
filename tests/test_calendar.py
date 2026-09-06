import datetime

import polars as pl
import pytest
from polars.exceptions import ColumnNotFoundError

from factorlab.data.backend import open_read
from factorlab.data.calendar import chunk_calendar, fill_suspensions, trading_calendar


# ---------- trading_calendar（双腿参数化：env 见 tests/conftest.py） ----------

# trade_cal 平台风格：exchange/cal_date('YYYYMMDD')/is_open（ch 端 cal_date 为 Date，
# duckdb 端为 VARCHAR——read 函数各自 decode，断言双腿共享）
_CAL_COLS = [("exchange", "str"), ("cal_date", "date"), ("is_open", "i64")]


def _seed_cal(env, rows):
    env.seed({"trade_cal": (_CAL_COLS, rows)})


def test_trading_calendar_deduplicates(env):
    # 平台库 trade_cal 按交易所分行：SSE/SZSE 同日重复 → 去重
    _seed_cal(env, [("SSE", "20240102", 1), ("SZSE", "20240102", 1),
                    ("SSE", "20240103", 1)])
    cal = trading_calendar(env.rd)
    assert cal.to_list() == [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)]


def test_trading_calendar_ordered_even_with_reverse_insertion(env):
    _seed_cal(env, [("SSE", "20240103", 1), ("SSE", "20240102", 1)])
    cal = trading_calendar(env.rd)
    assert cal.to_list() == [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)]


def test_trading_calendar_date_range_inclusive(env):
    _seed_cal(env, [("SSE", "20240102", 1), ("SSE", "20240103", 1),
                    ("SSE", "20240104", 1)])
    cal = trading_calendar(env.rd, date_start="2024-01-02", date_end="2024-01-03")
    assert cal.to_list() == [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)]


def test_trading_calendar_filters_closed_days(env):
    # is_open=0 的休市日不出现在日历
    _seed_cal(env, [("SSE", "20240102", 1), ("SSE", "20240103", 0),
                    ("SSE", "20240104", 1)])
    cal = trading_calendar(env.rd)
    assert cal.to_list() == [datetime.date(2024, 1, 2), datetime.date(2024, 1, 4)]


def test_trading_calendar_accepts_yyyymmdd_range(env):
    # 日期范围参数支持 ISO 'YYYY-MM-DD' 与 'YYYYMMDD' 双格式
    _seed_cal(env, [("SSE", "20240102", 1), ("SSE", "20240103", 1)])
    cal = trading_calendar(env.rd, date_start="20240102", date_end="20240102")
    assert cal.to_list() == [datetime.date(2024, 1, 2)]


def test_trading_calendar_empty_table_returns_empty_date_series(env):
    _seed_cal(env, [])
    cal = trading_calendar(env.rd)
    assert cal.to_list() == []
    assert cal.dtype == pl.Date


def test_trading_calendar_range_no_match_returns_empty(env):
    _seed_cal(env, [("SSE", "20240102", 1)])
    cal = trading_calendar(env.rd, date_start="20240105")
    assert cal.to_list() == []
    assert cal.dtype == pl.Date


def test_trading_calendar_missing_db_raises(tmp_path):
    # duckdb 文件语义：平台库文件缺失 → FileNotFoundError（ch 后端无文件概念）
    with pytest.raises(FileNotFoundError):
        open_read(db_path=tmp_path / "missing.duckdb")


# ---------- fill_suspensions ----------


def test_fill_suspensions_adds_missing_rows():
    calendar = pl.Series("date", [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)], dtype=pl.Date)
    df = pl.DataFrame({
        "date": [datetime.date(2024, 1, 3)],
        "code": ["A"],
        "close": [10.0],
    })
    out = fill_suspensions(df, calendar).sort(["code", "date"])
    assert out.height == 2
    assert out["close"].to_list() == [None, 10.0]   # 停牌日 close 为 null


def test_fill_suspensions_keeps_existing_data():
    calendar = pl.Series("date", [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)], dtype=pl.Date)
    df = pl.DataFrame({
        "date": [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)],
        "code": ["A", "A"],
        "close": [9.0, 10.0],
    })
    out = fill_suspensions(df, calendar).sort(["date"])
    assert out["close"].to_list() == [9.0, 10.0]


def test_fill_suspensions_multi_code_no_cross_asset_leak():
    # 回归：A 在 1-4 有数据，B 在 1-4 停牌——B 的 1-4 close 必须为 null，不得取到 A 的值
    calendar = pl.Series(
        "date",
        [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3), datetime.date(2024, 1, 4)],
        dtype=pl.Date,
    )
    df = pl.DataFrame({
        "date": [
            datetime.date(2024, 1, 2),
            datetime.date(2024, 1, 4),
            datetime.date(2024, 1, 2),
            datetime.date(2024, 1, 3),
        ],
        "code": ["A", "A", "B", "B"],
        "close": [9.0, 11.0, 20.0, 21.0],
    })
    out = fill_suspensions(df, calendar).sort(["code", "date"])
    assert out.height == 6
    assert out["close"].to_list() == [9.0, None, 11.0, 20.0, 21.0, None]  # A 停牌 1-3，B 停牌 1-4


def test_fill_suspensions_duplicate_code_rows_kept():
    # 同一 code 同一日的重复行保留（补全只建码集，不去重 df 本身）
    calendar = pl.Series("date", [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)], dtype=pl.Date)
    df = pl.DataFrame({
        "date": [datetime.date(2024, 1, 3), datetime.date(2024, 1, 3)],
        "code": ["A", "A"],
        "close": [10.0, 11.0],
    })
    out = fill_suspensions(df, calendar).sort(["date", "close"])
    assert out.height == 3
    assert out["close"].to_list() == [None, 10.0, 11.0]


def test_fill_suspensions_empty_calendar_returns_empty():
    calendar = pl.Series("date", [], dtype=pl.Date)
    df = pl.DataFrame({
        "date": [datetime.date(2024, 1, 3)],
        "code": ["A"],
        "close": [10.0],
    })
    out = fill_suspensions(df, calendar)
    assert out.height == 0
    assert out.columns == ["date", "code", "close"]


def test_fill_suspensions_empty_df_returns_empty():
    calendar = pl.Series("date", [datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)], dtype=pl.Date)
    df = pl.DataFrame(schema={"date": pl.Date, "code": pl.String, "close": pl.Float64})
    out = fill_suspensions(df, calendar)
    assert out.height == 0
    assert out.columns == ["date", "code", "close"]


def test_fill_suspensions_missing_columns_raises():
    calendar = pl.Series("date", [datetime.date(2024, 1, 2)], dtype=pl.Date)
    df = pl.DataFrame({"date": [datetime.date(2024, 1, 2)], "close": [10.0]})  # 缺 code
    with pytest.raises(ColumnNotFoundError):
        fill_suspensions(df, calendar)


# ---------- chunk_calendar ----------


def _cal(days: list[str]) -> pl.Series:
    return pl.Series("date", [datetime.date.fromisoformat(d) for d in days], dtype=pl.Date)


def test_chunk_calendar_basic_chunks():
    cal = _cal(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
                "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11",
                "2024-01-12", "2024-01-15", "2024-01-16", "2024-01-17"])
    chunks = chunk_calendar(cal, chunk_days=5)
    assert chunks == [
        (datetime.date(2024, 1, 2), datetime.date(2024, 1, 2), datetime.date(2024, 1, 8)),
        (datetime.date(2024, 1, 9), datetime.date(2024, 1, 9), datetime.date(2024, 1, 15)),
        (datetime.date(2024, 1, 16), datetime.date(2024, 1, 16), datetime.date(2024, 1, 17)),
    ]


def test_chunk_calendar_warmup_overlaps_previous_chunk():
    # 12 天日历、chunk 5、warmup 2：块 1/2 的 load 段向块首前推 2 个交易日
    cal = _cal(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
                "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11",
                "2024-01-12", "2024-01-15", "2024-01-16", "2024-01-17"])
    chunks = chunk_calendar(cal, chunk_days=5, warmup_days=2)
    assert chunks[1] == (
        datetime.date(2024, 1, 5), datetime.date(2024, 1, 9), datetime.date(2024, 1, 15),
    )
    assert chunks[2] == (
        datetime.date(2024, 1, 12), datetime.date(2024, 1, 16), datetime.date(2024, 1, 17),
    )


def test_chunk_calendar_warmup_truncated_at_head():
    # 首块 load 段越界（warmup 超过日历起点）→ 截断到日历首日
    cal = _cal(["2024-01-02", "2024-01-03", "2024-01-04"])
    chunks = chunk_calendar(cal, chunk_days=2, warmup_days=10)
    assert chunks[0] == (datetime.date(2024, 1, 2), datetime.date(2024, 1, 2), datetime.date(2024, 1, 3))
    assert chunks[1] == (datetime.date(2024, 1, 2), datetime.date(2024, 1, 4), datetime.date(2024, 1, 4))


def test_chunk_calendar_exact_division():
    cal = _cal(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"])
    chunks = chunk_calendar(cal, chunk_days=3)
    assert chunks == [
        (datetime.date(2024, 1, 2), datetime.date(2024, 1, 2), datetime.date(2024, 1, 4)),
        (datetime.date(2024, 1, 5), datetime.date(2024, 1, 5), datetime.date(2024, 1, 9)),
    ]


def test_chunk_calendar_empty_calendar_returns_empty():
    assert chunk_calendar(pl.Series("date", [], dtype=pl.Date), chunk_days=5) == []


def test_chunk_calendar_invalid_params_raise():
    cal = _cal(["2024-01-02", "2024-01-03"])
    with pytest.raises(ValueError, match="chunk_days"):
        chunk_calendar(cal, chunk_days=0)
    with pytest.raises(ValueError, match="warmup_days"):
        chunk_calendar(cal, chunk_days=5, warmup_days=-1)
