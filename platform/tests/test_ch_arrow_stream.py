"""R31 分钟链读路径优化 ②：bars 批读 Arrow 流读取（`query_arrow_stream`）。

断言来源（任务书 ②）：
- bars 批读改 Arrow 路径（`query_arrow` + `pl.from_arrow`；本实现用
  `query_arrow_stream` + 逐批 `pl.from_arrow`，实测 2.2s vs 4.9s，
  见 evidence read-cache/arrow_stream_probe.txt）；
- dtype/null/精度逐一保持：UInt16/Date/DateTime64(3)/Float32/Float64（+String）；
- 行序语义不变（SQL 显式 `ORDER BY code, datetime`；缓存按原序存）。

无 CH 单测 + ch_prod 真 CH bit-exact 硬门（短窗）。
"""
from __future__ import annotations

import datetime as dt
import json

import polars as pl
import pytest

from factorlab.adapters import ch_read, intraday


# ---------------- 假 client（无 CH） ----------------

def _table(n: int = 4) -> "object":
    frame = pl.DataFrame({
        "datetime": pl.Series(
            [dt.datetime(2024, 1, 2, 9, 30) + dt.timedelta(minutes=i)
             for i in range(n)], dtype=pl.Datetime("ms")),
        "trade_date": pl.Series([dt.date(2024, 1, 2)] * n, dtype=pl.Date),
        "code": pl.Series(["000001.SZ"] * n, dtype=pl.String),
        "minute_index": pl.Series(list(range(n)), dtype=pl.UInt16),
        "session_type": pl.Series([1] * n, dtype=pl.UInt8),
        "open": pl.Series([10.0] * n, dtype=pl.Float32),
        "close": pl.Series([10.5, None, 11.0, 9.5][:n], dtype=pl.Float32),
        "amount": pl.Series([1e7] * n, dtype=pl.Float64),
        "volume": pl.Series([100.0, None, 300.0, 400.0][:n], dtype=pl.Float64),
    })
    return frame.to_arrow()


class _StreamClient:
    """假 CH client：query_arrow 单表；query_arrow_stream 按 batch_rows 切批。"""

    def __init__(self, table, *, batch_rows: int = 2, stream_tables=None):
        self.table = table
        self.batch_rows = batch_rows
        self.stream_tables = stream_tables
        self.arrow_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def query_arrow(self, sql, parameters=None, settings=None):
        self.arrow_calls.append({"sql": sql, "params": parameters,
                                 "settings": settings})
        return self.table

    def query_arrow_stream(self, sql, parameters=None, settings=None):
        self.stream_calls.append({"sql": sql, "params": parameters,
                                  "settings": settings})
        tables = (self.stream_tables if self.stream_tables is not None
                  else self.table.to_batches(max_chunksize=self.batch_rows))

        class _CM:
            def __enter__(self):
                return iter(tables)

            def __exit__(self, *exc):
                return False

        return _CM()


def test_stream_df_concatenates_batches_bit_exact(monkeypatch):
    client = _StreamClient(_table())
    monkeypatch.setattr(ch_read, "get_client", lambda: client)
    out = ch_read.query_arrow_stream_df("SELECT x FROM t",
                                        settings={"max_threads": 8})
    ref = pl.from_arrow(client.table)
    assert out.height == ref.height == 4
    assert out.schema == ref.schema
    assert out.equals(ref)                       # 逐 bit/逐 null
    assert len(client.stream_calls) == 1
    # settings 与 query_df 同款合并（join_use_nulls=1 恒在；旋钮透传）
    assert client.stream_calls[0]["settings"] == {"join_use_nulls": 1,
                                                  "max_threads": 8}
    # 禁止行为：有 stream 能力时不得再走 query_arrow
    assert client.arrow_calls == []


def test_stream_df_empty_stream_falls_back_for_schema(monkeypatch):
    """空结果流实测 0 个 table（不带 schema）→ 回退 query_arrow 取投影 schema。"""
    empty = pl.DataFrame({
        "trade_date": pl.Series([], dtype=pl.Date),
        "code": pl.Series([], dtype=pl.String),
        "minute_index": pl.Series([], dtype=pl.UInt16),
    })
    client = _StreamClient(None, stream_tables=[])
    client.table = empty.to_arrow()
    monkeypatch.setattr(ch_read, "get_client", lambda: client)
    out = ch_read.query_arrow_stream_df("SELECT x FROM t")
    assert out.height == 0
    assert out.schema == empty.schema
    assert len(client.stream_calls) == 1 and len(client.arrow_calls) == 1


def test_clickhouse_read_forwards_stream(monkeypatch):
    calls: list[dict] = []

    def spy(sql, params=None, settings=None):
        calls.append({"sql": sql, "params": params, "settings": settings})
        return pl.DataFrame({"x": [1]})

    monkeypatch.setattr(ch_read, "query_arrow_stream_df", spy)
    rd = ch_read.ClickHouseRead()
    out = rd.query_arrow_stream_df("SELECT 1", {"a": 1},
                                   settings={"max_threads": 2})
    assert out.height == 1
    assert calls == [{"sql": "SELECT 1", "params": {"a": 1},
                      "settings": {"max_threads": 2}}]


def test_codes_ch_prefers_stream_when_available(monkeypatch):
    """`_codes_ch` 在句柄有 Arrow 流能力时走流读取（settings 仍注入）。"""
    monkeypatch.setenv("FACTORLAB_READ_CACHE", "0")

    class _StreamRead:
        backend = "ch"

        def __init__(self):
            self.stream_calls: list[dict] = []
            self.df_calls: list[dict] = []

        def query_rows(self, sql, params=None):
            raise AssertionError("带后缀 code 不应触发 symbol 解析")

        def query_arrow_stream_df(self, sql, params=None, settings=None):
            self.stream_calls.append({"sql": sql, "params": params,
                                      "settings": settings})
            return pl.DataFrame({
                "trade_date": pl.Series([], dtype=pl.Date),
                "code": pl.Series([], dtype=pl.String),
            })

        def query_df(self, sql, params=None, settings=None):
            self.df_calls.append({"sql": sql, "settings": settings})
            raise AssertionError("有流能力时不应回退 query_df")

    monkeypatch.setenv("FACTORLAB_CH_MAX_THREADS", "8")
    rd = _StreamRead()
    out = intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                             cols=["trade_date", "code"])
    assert out.height == 0
    assert len(rd.stream_calls) == 1 and rd.df_calls == []
    call = rd.stream_calls[0]
    assert ".bars_1m " in call["sql"] and "ORDER BY code, datetime" in call["sql"]
    assert call["settings"] == {"max_threads": 8}
    monkeypatch.delenv("FACTORLAB_CH_MAX_THREADS")


def test_codes_ch_falls_back_to_query_df_without_stream(monkeypatch):
    """无流能力的句柄（测试桩/未来后端）→ 原 `query_df` 路径不变。"""
    monkeypatch.setenv("FACTORLAB_READ_CACHE", "0")

    class _PlainRead:
        backend = "ch"

        def __init__(self):
            self.df_calls: list[dict] = []

        def query_rows(self, sql, params=None):
            raise AssertionError

        def query_df(self, sql, params=None, settings=None):
            self.df_calls.append({"settings": settings})
            return pl.DataFrame({"trade_date": pl.Series([], dtype=pl.Date),
                                 "code": pl.Series([], dtype=pl.String)})

    rd = _PlainRead()
    out = intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                             cols=["trade_date", "code"])
    assert out.height == 0 and len(rd.df_calls) == 1


# ---------------- 真 CH bit-exact 硬门（短窗） ----------------

_COLS = ["datetime", "trade_date", "code", "minute_index", "session_type",
         "open", "high", "low", "close", "amount", "volume"]
_WINDOW = ("2024-01-02", "2024-01-04")
_CODES = ["000001.SZ", "600519.SH"]


def _prod_rd():
    from factorlab.app.bootstrap import open_read
    return open_read(data_backend="ch")


def _direct_sql(codes, date_start, date_end, cols) -> tuple[str, dict]:
    from factorlab.config import settings
    ph = ", ".join(f"%(t{i})s" for i in range(len(codes)))
    params = {f"t{i}": c for i, c in enumerate(codes)}
    params["start"], params["end"] = date_start, date_end
    sql = (f"SELECT {', '.join(cols)} FROM {settings.ch_database}.bars_1m "
           f"WHERE code IN ({ph}) "
           f"AND trade_date >= toDate(%(start)s) "
           f"AND trade_date <= toDate(%(end)s) ORDER BY code, datetime")
    return sql, params


def test_stream_matches_query_arrow_real_ch_bit_exact(ch_prod):
    """真 CH：`query_arrow_stream_df` == `query_df`（全 11 列）逐 bit/null 一致；
    dtype 组合保持生产 DDL 原样（UInt16/Date/DateTime64(3)/Float32/Float64）。"""
    rd = _prod_rd()
    try:
        sql, params = _direct_sql(_CODES, *_WINDOW, _COLS)
        a = rd.query_df(sql, params, settings=ch_read.bars_read_settings())
        b = rd.query_arrow_stream_df(sql, params,
                                     settings=ch_read.bars_read_settings())
    finally:
        rd.close()
    assert a.height == b.height > 0
    assert a.schema == b.schema
    assert a.equals(b)                                  # 逐 bit（含 null 槽）
    for col, dtype in (("minute_index", pl.UInt16),
                       ("session_type", pl.UInt8),
                       ("trade_date", pl.Date),
                       ("open", pl.Float32), ("close", pl.Float32),
                       ("amount", pl.Float64), ("volume", pl.Float64)):
        assert b.schema[col] == dtype, (col, b.schema[col])
    assert b.schema["datetime"].base_type() == pl.Datetime
    for col in _COLS:
        assert a[col].null_count() == b[col].null_count() == 0


def test_loader_stream_equals_direct_real_ch_bit_exact(ch_prod):
    """真 CH：`load_bars_1m_codes`（流路径 + decode）== 同 SQL query_df + decode。"""
    rd = _prod_rd()
    try:
        got = intraday.load_bars_1m_codes(rd, _CODES, date_start=_WINDOW[0],
                                          date_end=_WINDOW[1], cols=_COLS)
        sql, params = _direct_sql(_CODES, *_WINDOW, _COLS)
        ref = intraday._decode(rd.query_df(
            sql, params, settings=ch_read.bars_read_settings()))
    finally:
        rd.close()
    assert got.equals(ref) and got.schema == ref.schema
    assert got["code"].unique().sort().to_list() == ["000001", "600519"]
    assert got["datetime"].dtype == pl.Datetime("ms")
    assert got["datetime"].dtype.time_zone is None


def test_cache_hit_and_switch_off_match_direct_real_ch_bit_exact(
        ch_prod, tmp_path, monkeypatch):
    """真 CH bit-exact 硬门：直读 vs 缓存首次 miss vs 缓存二次 hit vs 关开关，
    四态逐 bit/null 一致；第二次命中不再查 CH 批读（可观测）。"""
    from factorlab.adapters.read import chunk_cache as cc
    monkeypatch.setenv("FACTORLAB_READ_CACHE_DIR", str(tmp_path / "rc"))
    monkeypatch.setenv("FACTORLAB_READ_CACHE", "1")
    cc.reset_chunk_cache()
    cc.reset_fingerprint_cache()
    rd = _prod_rd()
    try:
        direct = intraday.load_bars_1m_codes(rd, _CODES, date_start=_WINDOW[0],
                                             date_end=_WINDOW[1], cols=_COLS,
                                             read_cache=False)
        first = intraday.load_bars_1m_codes(rd, _CODES, date_start=_WINDOW[0],
                                            date_end=_WINDOW[1], cols=_COLS)
        second = intraday.load_bars_1m_codes(rd, _CODES, date_start=_WINDOW[0],
                                             date_end=_WINDOW[1], cols=_COLS)
        off = intraday.load_bars_1m_codes(rd, _CODES, date_start=_WINDOW[0],
                                          date_end=_WINDOW[1], cols=_COLS,
                                          read_cache=False)
    finally:
        rd.close()
        cc.reset_chunk_cache()
        cc.reset_fingerprint_cache()
    for other, tag in ((first, "miss"), (second, "hit"), (off, "off")):
        assert other.schema == direct.schema, tag
        assert other.equals(direct), tag                      # bit-exact
        assert [other[c].null_count() for c in _COLS] == \
               [direct[c].null_count() for c in _COLS], tag
    # 二次读确为命中：manifest hits ≥ 1 且数据文件为 IPC
    manifest = json.loads((tmp_path / "rc" / "manifest.json"
                           ).read_text(encoding="utf-8"))
    entry = next(iter(manifest["entries"].values()))
    assert entry["hits"] >= 1 and entry["size"] > 0
    assert (tmp_path / "rc" / entry["file"]).suffix == ".arrow"
