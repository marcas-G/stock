"""R22 Task 3：分钟执行窗口读取适配——CH only + 契约校验。

断言来源：design.md §3/§4（仅 CH、240 网格契约、raw 价）与 plan.md Task 3
Interfaces（窗口过滤/重复 fail/负量 fail/duckdb fail fast）。
"""

import polars as pl
import pytest

from factorlab.adapters.read.minute_window import load_execution_window

_COLS = ["code", "minute_index", "open", "high", "low", "close", "volume",
         "amount", "session_type"]


class FakeRd:
    """最小读句柄双（只实现 load_bars_1m_codes 走到的两个方法）。"""

    def __init__(self, backend, frame=None):
        self.backend = backend
        self._frame = frame
        self.calls = []

    def query_rows(self, sql, params=None):
        self.calls.append((sql, params))
        if "stock_basic" in sql:
            return [(v, f"{v}.SZ") for v in (params or {}).values()]
        return []

    def query_df(self, sql, params=None, settings=None):
        self.calls.append((sql, params))
        return self._frame

    def command(self, sql, params=None):
        return None

    def tables(self):
        return set()

    def columns(self, table):
        return set()

    def close(self):
        return None


def _minute_frame():
    return pl.DataFrame({
        "code": ["000001", "000001", "000001"],
        "minute_index": [0, 1, 5],
        "open": [10.0, 10.1, 10.2], "high": [10.2, 10.3, 10.4],
        "low": [9.9, 10.0, 10.1], "close": [10.1, 10.2, 10.3],
        "volume": [1.0, 2.0, 3.0], "amount": [10.1, 20.4, 30.9],
        "session_type": [0, 1, 1],
    })


def test_duckdb_fail_fast():
    with pytest.raises(NotImplementedError, match="CH"):
        load_execution_window(FakeRd("duckdb"), ["000001"], "2024-01-02", 0, 10)


def test_window_filter_and_schema():
    rd = FakeRd("ch", _minute_frame())
    out = load_execution_window(rd, ["000001"], "2024-01-02", 0, 1)
    assert out["minute_index"].to_list() == [0, 1]          # 5 被窗口剔除
    assert out.columns == _COLS
    # 证明真实走了 bars_1m 批读（不是硬编码/短路）
    df_calls = [c for c in rd.calls if "bars_1m" in c[0]]
    assert len(df_calls) == 1
    sql, params = df_calls[0]
    assert "code IN" in sql and "trade_date >= toDate" in sql
    assert params["start"] == "2024-01-02" and params["end"] == "2024-01-02"


def test_window_bounds_and_empty():
    with pytest.raises(ValueError, match="start|end"):
        load_execution_window(FakeRd("ch", _minute_frame()), ["000001"],
                              "2024-01-02", 5, 4)
    with pytest.raises(ValueError, match="239"):
        load_execution_window(FakeRd("ch", _minute_frame()), ["000001"],
                              "2024-01-02", 0, 240)
    out = load_execution_window(FakeRd("ch", _minute_frame()), ["000001"],
                                "2024-01-02", 100, 200)
    assert out.height == 0 and out.columns == _COLS
    assert out.schema["minute_index"] == pl.Int64
    assert out.schema["open"] == pl.Float64


def test_negative_volume_rejected():
    bad = _minute_frame().with_columns(pl.when(pl.col("minute_index") == 1)
                                       .then(-1.0).otherwise(pl.col("volume")).alias("volume"))
    with pytest.raises(ValueError, match="volume"):
        load_execution_window(FakeRd("ch", bad), ["000001"], "2024-01-02", 0, 10)


def test_negative_amount_rejected():
    bad = _minute_frame().with_columns(pl.when(pl.col("minute_index") == 1)
                                       .then(-0.01).otherwise(pl.col("amount")).alias("amount"))
    with pytest.raises(ValueError, match="amount"):
        load_execution_window(FakeRd("ch", bad), ["000001"], "2024-01-02", 0, 10)


def test_null_volume_amount_rejected():
    bad = _minute_frame().with_columns(
        pl.when(pl.col("minute_index") == 1).then(None)
        .otherwise(pl.col("volume")).alias("volume"))
    with pytest.raises(ValueError, match="volume"):
        load_execution_window(FakeRd("ch", bad), ["000001"], "2024-01-02", 0, 10)


def test_duplicate_minute_rejected():
    bad = pl.concat([_minute_frame(), _minute_frame().filter(
        pl.col("minute_index") == 1)])
    with pytest.raises(ValueError, match="minute_index"):
        load_execution_window(FakeRd("ch", bad), ["000001"], "2024-01-02", 0, 10)


def test_invalid_session_type_rejected():
    bad = _minute_frame().with_columns(
        pl.when(pl.col("minute_index") == 1).then(9)
        .otherwise(pl.col("session_type")).alias("session_type"))
    with pytest.raises(ValueError, match="session_type"):
        load_execution_window(FakeRd("ch", bad), ["000001"], "2024-01-02", 0, 10)


def test_null_ohlc_kept_for_caller_to_skip():
    frame = _minute_frame().with_columns(
        pl.when(pl.col("minute_index") == 1).then(None)
        .otherwise(pl.col("open")).alias("open"))
    out = load_execution_window(FakeRd("ch", frame), ["000001"],
                                "2024-01-02", 0, 1)
    assert out.filter(pl.col("minute_index") == 1)["open"].to_list() == [None]


def test_codes_and_day_guards():
    with pytest.raises(TypeError):
        load_execution_window("not-rd", ["000001"], "2024-01-02", 0, 10)
    with pytest.raises(ValueError, match="codes"):
        load_execution_window(FakeRd("ch", _minute_frame()), [], "2024-01-02", 0, 10)


def test_suffix_code_input_normalized():
    frame = _minute_frame().with_columns(pl.lit("600000").alias("code"))
    rd = FakeRd("ch", frame)
    out = load_execution_window(rd, ["600000.SH"], "2024-01-02", 0, 5)
    assert set(out["code"].to_list()) == {"600000"}
    # 带后缀 code 不经 symbol 解析
    assert not any("stock_basic" in c[0] for c in rd.calls)
