"""R31 Task 3：data 组（全库读取 + 大数据落盘契约）测试。

断言来源：knowledge/design/platform/specs/2026-09-18-research-api-design.md
§3（data 命令表）、§4（JSON 契约：>200 行默认落 parquet 返回 path+head(5)+schema；
--out/--limit/--inline 覆盖；缺失表→DATA）、§5（默认值）、§7（禁止行为断言：
data 命令必须真实调用 ReadPort——spy 记录，禁止硬编码）。
计划：knowledge/design/platform/plans/2026-09-18-research-api.md Task 3。
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from _doubles import MemoryRead
from factorlab.app import bootstrap
from factorlab.research import registry
from factorlab.research.data import (
    data_adj,
    data_calendar,
    data_daily,
    data_daily_basic,
    data_fundamentals,
    data_limit,
    data_members,
    data_minute,
    data_moneyflow,
    data_schema,
    data_sector,
    data_status,
    data_stock_basic,
    data_tables,
    data_tick,
    data_universe,
    result_frame,
)
from factorlab.surfaces.cli.main import app as cli_app

runner = CliRunner()

DATA_COMMANDS = {
    "data.tables", "data.schema", "data.status", "data.calendar", "data.daily",
    "data.minute", "data.tick", "data.daily_basic", "data.adj", "data.limit",
    "data.stock_basic", "data.universe", "data.moneyflow", "data.sector",
    "data.members", "data.fundamentals",
}


# ================================================================
# 工具：ReadPort spy（禁止行为断言：命令必须真实走 ReadPort）
# ================================================================

class SpyRead:
    """ReadPort 代理：记录每次真实调用（SQL/参数），透传给底层句柄。

    硬编码/存根实现不会产生任何查询记录 → 相关断言失败。
    """

    def __init__(self, inner):
        self.inner = inner
        self.queries: list[tuple[str, object]] = []
        self.closed = False

    @property
    def backend(self):
        return self.inner.backend

    def query_df(self, sql, params=None):
        self.queries.append((sql, params))
        return self.inner.query_df(sql, params)

    def query_rows(self, sql, params=None):
        self.queries.append((sql, params))
        return self.inner.query_rows(sql, params)

    def command(self, sql, params=None):
        self.queries.append((sql, params))
        return self.inner.command(sql, params)

    def tables(self):
        return self.inner.tables()

    def columns(self, table):
        return self.inner.columns(table)

    def close(self):
        self.closed = True
        self.inner.close()

    def sql_texts(self) -> list[str]:
        return [f"{sql} {params!r}" for sql, params in self.queries]


def patch_read(monkeypatch, rd) -> SpyRead:
    """把门面 open_read 换成 spy(rd)；返回 spy（调用前先取 env.rd）。"""
    spy = SpyRead(rd)
    monkeypatch.setattr(bootstrap, "open_read", lambda *a, **k: spy)
    return spy


def _args(**kw) -> argparse.Namespace:
    base = dict(out=None, limit=None, inline=False, artifacts_dir=None)
    base.update(kw)
    return argparse.Namespace(**base)


# ================================================================
# 双腿种子（duckdb 平台文件 | CH 临时库）
# ================================================================

def _stock_basic():
    return ([("symbol", "str"), ("ts_code", "str"), ("list_date", "date"),
             ("delist_date", "date?")],
            [("000001", "000001.SZ", "20200102", None),
             ("600519", "600519.SH", "20200102", None)])


_DAILY_COLS = [("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
               ("vol", "f64"), ("amount", "f64")]


def _basic_seed(**extra):
    tables = {
        "stock_basic": _stock_basic(),
        "daily": (_DAILY_COLS,
                  [("000001.SZ", "20240102", 10.0, 11.0, 9.5, 10.5, 10.2, 0.3, 0.03, 1000.0, 1e6),
                   ("000001.SZ", "20240103", 10.5, 11.5, 10.0, 11.0, 10.5, 0.5, 0.05, 1100.0, 1.1e6)]),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"), ("adj_factor", "f64")],
                       [("000001.SZ", "20240102", 1.0), ("000001.SZ", "20240103", 1.5)]),
    }
    tables.update(extra)
    return tables


def _frame(n: int) -> pl.DataFrame:
    return pl.DataFrame({
        "i": list(range(n)),
        "v": [float(i) for i in range(n)],
        "d": [datetime.date(2024, 1, 1) + datetime.timedelta(days=i) for i in range(n)],
    })


# ================================================================
# result_frame：大数据落盘契约（spec §4）
# ================================================================

def test_result_frame_inline_small_has_rows_no_artifact(tmp_path):
    env = result_frame(_frame(3), command="data.daily", artifacts_dir=tmp_path, inline=True)
    assert env.ok and env.command == "data.daily"
    assert env.data["n_rows"] == 3
    assert len(env.data["rows"]) == 3
    assert "path" not in env.data
    assert env.artifacts == {}
    assert not list(tmp_path.rglob("*.parquet"))


def test_result_frame_large_defaults_to_parquet_with_head_and_schema(tmp_path):
    env = result_frame(_frame(201), command="data.daily", artifacts_dir=tmp_path)
    assert env.ok
    path = Path(env.data["path"])
    assert path.exists() and path.name == "data.parquet"
    assert (tmp_path / "data" / "daily") in path.parents
    assert pl.read_parquet(path).height == 201
    assert env.data["n_rows"] == 201
    assert len(env.data["head"]) == 5
    assert [r["i"] for r in env.data["head"]] == [0, 1, 2, 3, 4]
    assert "rows" not in env.data
    assert env.data["schema"]["i"] == "Int64"
    assert env.artifacts["data"] == str(path)


def test_result_frame_limit_truncates_before_inline_decision(tmp_path):
    env = result_frame(_frame(250), command="data.daily", artifacts_dir=tmp_path,
                       limit=10)
    assert env.data["n_rows"] == 10
    assert len(env.data["rows"]) == 10
    assert "path" not in env.data
    assert not list(tmp_path.rglob("*.parquet"))


def test_result_frame_inline_forces_rows_for_large_frame(tmp_path):
    env = result_frame(_frame(250), command="data.daily", artifacts_dir=tmp_path,
                       inline=True)
    assert env.data["n_rows"] == 250
    assert len(env.data["rows"]) == 250
    assert not list(tmp_path.rglob("*.parquet"))


def test_result_frame_out_overrides_artifact_path(tmp_path):
    out = tmp_path / "custom" / "mine.parquet"
    env = result_frame(_frame(3), command="data.daily", artifacts_dir=tmp_path, out=out)
    assert Path(env.data["path"]) == out
    assert out.exists()
    assert pl.read_parquet(out).height == 3
    assert env.artifacts["data"] == str(out)


def test_result_frame_rows_are_json_safe():
    env = result_frame(_frame(3), command="data.daily", artifacts_dir="/tmp", inline=True)
    doc = json.dumps(env.to_doc(), ensure_ascii=False)
    assert "2024-01-01" in doc


# ================================================================
# data tables / schema / status / calendar
# ================================================================

def test_data_tables_lists_readport_tables(monkeypatch, tmp_path):
    rd = MemoryRead({"daily": {"ts_code", "trade_date"},
                     "trade_cal": {"cal_date", "is_open"}})
    monkeypatch.setattr(bootstrap, "open_read", lambda *a, **k: rd)
    env = data_tables(_args(inline=True, artifacts_dir=tmp_path))
    assert env.ok, env.error
    assert [r["table"] for r in env.data["rows"]] == ["daily", "trade_cal"]
    # 硬编码表清单必败：换 schema 输出必须跟着变
    rd2 = MemoryRead({"fundamentals": {"ts_code", "updated_date"}})
    monkeypatch.setattr(bootstrap, "open_read", lambda *a, **k: rd2)
    env2 = data_tables(_args(inline=True, artifacts_dir=tmp_path))
    assert [r["table"] for r in env2.data["rows"]] == ["fundamentals"]


def test_data_tables_env_counts_rows_and_columns(env, monkeypatch, tmp_path):
    env.seed({"daily": (_DAILY_COLS, [("000001.SZ", "20240102", *([1.0] * 9)),
                                      ("000001.SZ", "20240103", *([1.0] * 9))]),
              "trade_cal": ([("cal_date", "date"), ("is_open", "u8")],
                            [("20240102", 1), ("20240103", 1)])})
    patch_read(monkeypatch, env.rd)
    out = data_tables(_args(inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    rows = {r["table"]: r for r in out.data["rows"]}
    assert rows["daily"]["n_rows"] == 2
    assert rows["daily"]["n_cols"] == 11
    assert rows["trade_cal"]["n_rows"] == 2


def test_data_schema_columns_and_types(env, monkeypatch, tmp_path):
    env.seed(_basic_seed())
    spy = patch_read(monkeypatch, env.rd)
    out = data_schema(_args(table="daily", inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    names = [r["column"] for r in out.data["rows"]]
    assert names[0] == "ts_code" and "close" in names
    assert all(r["type"] for r in out.data["rows"])
    assert any("daily" in s for s in spy.sql_texts())


def test_data_schema_missing_table_is_data_error(env, monkeypatch, tmp_path):
    env.seed({"stock_basic": _stock_basic()})
    patch_read(monkeypatch, env.rd)
    out = data_schema(_args(table="no_such_table", inline=True, artifacts_dir=tmp_path))
    assert out.ok is False
    assert out.error["code"] == "DATA"
    assert "no_such_table" in out.error["message"]
    assert out.error["hint"]


def test_data_calendar_filters_open_dates(env, monkeypatch, tmp_path):
    env.seed({"trade_cal": ([("cal_date", "date"), ("is_open", "u8")],
                            [("20240102", 1), ("20240103", 1),
                             ("20240104", 0), ("20240105", 1)])})
    patch_read(monkeypatch, env.rd)
    out = data_calendar(_args(start="2024-01-03", end="2024-01-05",
                              inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    assert [r["date"] for r in out.data["rows"]] == ["2024-01-03", "2024-01-05"]


_FRESH_SEED = {
    "daily": (_DAILY_COLS,
              [("000001.SZ", "20240102", *([1.0] * 9)),
               ("000001.SZ", "20240103", *([1.0] * 9))]),
    "trade_cal": ([("cal_date", "date"), ("is_open", "u8")],
                  [("20240102", 1), ("20240103", 1), ("20240104", 1),
                   ("20240105", 1)]),
}


def test_data_status_reports_max_date_and_calendar_gap(env, monkeypatch, tmp_path):
    env.seed(_FRESH_SEED)
    patch_read(monkeypatch, env.rd)
    out = data_status(_args(inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    rows = {r["table"]: r for r in out.data["rows"]}
    assert rows["daily"]["max_date"] == "2024-01-03"
    assert rows["daily"]["behind_trading_days"] == 2  # 0104/0105 开市但 daily 未见
    assert rows["trade_cal"]["max_date"] == "2024-01-05"
    assert "moneyflow" not in rows  # 缺表不虚报


# ================================================================
# data daily（ReadPort 真调用 + 复权视图 + 落盘）
# ================================================================

def test_data_daily_reads_values_via_readport(env, monkeypatch, tmp_path):
    env.seed(_basic_seed())
    spy = patch_read(monkeypatch, env.rd)
    out = data_daily(_args(codes=["000001"], start="2024-01-02", end="2024-01-03",
                           view="raw", cols=None, inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    assert out.command == "data.daily"
    rows = out.data["rows"]
    assert [r["close"] for r in rows] == [10.5, 11.0]  # 种子值，硬编码必败
    assert [r["date"] for r in rows] == ["2024-01-02", "2024-01-03"]
    assert any("daily" in s for s in spy.sql_texts())  # 真实走 ReadPort


def test_data_daily_qfq_view_scales_with_adj(env, monkeypatch, tmp_path):
    env.seed(_basic_seed())
    patch_read(monkeypatch, env.rd)
    out = data_daily(_args(codes=["000001"], start="2024-01-02", end="2024-01-03",
                           view="qfq", cols=["close"], inline=True,
                           artifacts_dir=tmp_path))
    assert out.ok, out.error
    rows = out.data["rows"]
    # 固定 base=qfq 基准 = 期末 adj（1.5）：0102 × 1.0/1.5，0103 × 1.5/1.5（=raw）
    assert [r["close"] for r in rows] == pytest.approx([10.5 / 1.5, 11.0], rel=1e-6)
    assert "adj_factor" not in rows[0]  # 内部消费列不得泄漏给用户


def test_data_daily_hfq_view_multiplies(env, monkeypatch, tmp_path):
    env.seed(_basic_seed())
    patch_read(monkeypatch, env.rd)
    out = data_daily(_args(codes=["000001"], start="2024-01-02", end="2024-01-03",
                           view="hfq", cols=["close"], inline=True,
                           artifacts_dir=tmp_path))
    assert out.ok, out.error
    assert [r["close"] for r in out.data["rows"]] == pytest.approx([10.5, 16.5], rel=1e-6)


def test_data_daily_invalid_view_is_usage(env, monkeypatch, tmp_path):
    env.seed(_basic_seed())
    patch_read(monkeypatch, env.rd)
    out = data_daily(_args(codes=["000001"], start=None, end=None, view="nope",
                           cols=None, inline=True, artifacts_dir=tmp_path))
    assert out.ok is False
    assert out.error["code"] == "USAGE"


def test_data_daily_large_result_lands_parquet(env, monkeypatch, tmp_path):
    n = 250
    dates = [(datetime.date(2024, 1, 1) + datetime.timedelta(days=i)).strftime("%Y%m%d")
             for i in range(n)]
    env.seed({"stock_basic": _stock_basic(),
              "daily": (_DAILY_COLS,
                        [("000001.SZ", d, 1.0, 2.0, 0.5, 1.5, 1.0, 0.5, 0.5, 10.0, 100.0)
                         for d in dates]),
              "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                              ("adj_factor", "f64")],
                             [("000001.SZ", d, 1.0) for d in dates])})
    spy = patch_read(monkeypatch, env.rd)
    out = data_daily(_args(codes=["000001"], start="2024-01-01", end=None,
                           view="raw", cols=None, inline=False, artifacts_dir=tmp_path))
    assert out.ok, out.error
    path = Path(out.data["path"])
    assert path.exists()
    assert (tmp_path / "data" / "daily") in path.parents
    assert pl.read_parquet(path).height == n
    assert out.data["n_rows"] == n
    assert len(out.data["head"]) == 5
    assert "rows" not in out.data
    assert any("daily" in s for s in spy.sql_texts())


def test_data_daily_missing_table_is_data_error(env, monkeypatch, tmp_path):
    env.seed({"stock_basic": _stock_basic()})
    patch_read(monkeypatch, env.rd)
    out = data_daily(_args(codes=["000001"], start=None, end=None, view="raw",
                           cols=None, inline=True, artifacts_dir=tmp_path))
    assert out.ok is False
    assert out.error["code"] == "DATA"
    assert out.error["hint"]


# ================================================================
# data minute / tick（仅 ch；duckdb 腿 → DATA）
# ================================================================

_BARS_COLS = [("datetime", "datetime"), ("trade_date", "date"), ("code", "str"),
              ("minute_index", "u16"), ("session_type", "u8"), ("open", "f32"),
              ("high", "f32"), ("low", "f32"), ("close", "f32"),
              ("amount", "f64"), ("volume", "f64")]
_BARS_ROWS = [("2024-01-02 09:30:00", "20240102", "000001.SZ", 1, 1,
               10.0, 10.5, 9.9, 10.2, 1e6, 1000.0),
              ("2024-01-02 09:31:00", "20240102", "000001.SZ", 2, 1,
               10.2, 10.6, 10.1, 10.4, 2e6, 2000.0)]


def test_data_minute_duckdb_is_data_error_with_ch_hint(env, monkeypatch, tmp_path):
    if env.backend != "duckdb":
        pytest.skip("duckdb 腿专用（ch 腿读路径见下一条）")
    env.seed(_basic_seed())
    patch_read(monkeypatch, env.rd)
    out = data_minute(_args(codes=["000001"], start="2024-01-02", end="2024-01-02",
                            cols=None, inline=True, artifacts_dir=tmp_path))
    assert out.ok is False
    assert out.error["code"] == "DATA"
    blob = f"{out.error['message']} {out.error['hint']}".lower()
    assert "clickhouse" in blob or "ch" in blob


def test_data_minute_ch_reads_bars(env, monkeypatch, tmp_path):
    if env.backend != "ch":
        pytest.skip("bars_1m 仅 CH 后端")
    env.seed({**_basic_seed(), "bars_1m": (_BARS_COLS, _BARS_ROWS)})
    spy = patch_read(monkeypatch, env.rd)
    out = data_minute(_args(codes=["000001"], start="2024-01-02", end="2024-01-02",
                            session=None, cols=None, inline=True,
                            artifacts_dir=tmp_path))
    assert out.ok, out.error
    assert [r["close"] for r in out.data["rows"]] == pytest.approx([10.2, 10.4], rel=1e-6)
    assert any("bars_1m" in s for s in spy.sql_texts())


_TICK_COLS = [("trade_date", "date"), ("code", "str"), ("time_ms", "u32"),
              ("trade_no", "u64"), ("bs", "u8"), ("price_x10000", "i32"),
              ("volume", "u32"), ("ask_seq", "u64"), ("bid_seq", "u64")]
_TICK_ROWS = [("20240102", "000001.SZ", 34200000, 1, 1, 102000, 100, 1, 2),
              ("20240102", "000001.SZ", 34201000, 2, 0, 101500, 200, 1, 3)]


def test_data_tick_duckdb_is_data_error_with_ch_hint(env, monkeypatch, tmp_path):
    if env.backend != "duckdb":
        pytest.skip("duckdb 腿专用（ch 腿读路径见下一条）")
    env.seed(_basic_seed())
    patch_read(monkeypatch, env.rd)
    out = data_tick(_args(codes=["000001"], date="2024-01-02", kind="trades",
                          cols=None, inline=True, artifacts_dir=tmp_path))
    assert out.ok is False
    assert out.error["code"] == "DATA"
    blob = f"{out.error['message']} {out.error['hint']}".lower()
    assert "clickhouse" in blob or "ch" in blob


def test_data_tick_ch_reads_trades(env, monkeypatch, tmp_path):
    if env.backend != "ch":
        pytest.skip("tick_* 仅 CH 后端")
    env.seed({**_basic_seed(), "tick_trades": (_TICK_COLS, _TICK_ROWS)})
    spy = patch_read(monkeypatch, env.rd)
    out = data_tick(_args(codes=["000001"], date="2024-01-02", kind="trades",
                          cols=None, inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    assert [r["price_x10000"] for r in out.data["rows"]] == [102000, 101500]
    assert any("tick_trades" in s for s in spy.sql_texts())


# ================================================================
# 其余 data 命令（raw 表：真实过滤 + 值断言）
# ================================================================

def test_data_daily_basic_filters_codes_and_window(env, monkeypatch, tmp_path):
    env.seed(_basic_seed(**{
        "daily_basic": ([("ts_code", "str"), ("trade_date", "date"),
                         ("total_mv", "f64?"), ("turnover_rate", "f64?")],
                        [("000001.SZ", "20240102", 100.0, 1.0),
                         ("000001.SZ", "20240103", 110.0, 2.0),
                         ("600519.SH", "20240102", 900.0, 3.0)])}))
    spy = patch_read(monkeypatch, env.rd)
    out = data_daily_basic(_args(codes=["000001"], start="2024-01-03",
                                 end="2024-01-03", inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    rows = out.data["rows"]
    assert len(rows) == 1 and rows[0]["total_mv"] == 110.0
    assert any("daily_basic" in s for s in spy.sql_texts())


def test_data_adj_factor_rows(env, monkeypatch, tmp_path):
    env.seed(_basic_seed())
    patch_read(monkeypatch, env.rd)
    out = data_adj(_args(kind="factor", codes=["000001"], start="2024-01-02",
                         end="2024-01-03", inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    assert [r["adj_factor"] for r in out.data["rows"]] == [1.0, 1.5]


def test_data_adj_detail_window(env, monkeypatch, tmp_path):
    env.seed(_basic_seed(**{
        "adj_detail": ([("ts_code", "str"), ("trade_date", "date"),
                        ("div_cash", "f64?"), ("div_bonus", "f64?"),
                        ("div_transfer", "f64?"), ("rights_num", "f64?"),
                        ("rights_price", "f64?")],
                       [("000001.SZ", "20240103", 3.0, 0.0, 0.0, None, None)])}))
    patch_read(monkeypatch, env.rd)
    out = data_adj(_args(kind="detail", codes=["000001"], start="2024-01-02",
                         end="2024-01-04", inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    rows = out.data["rows"]
    assert len(rows) == 1 and rows[0]["div_cash"] == 3.0
    assert rows[0]["trade_date"] == "2024-01-03"


def test_data_adj_event_window(env, monkeypatch, tmp_path):
    env.seed(_basic_seed(**{
        "adj_event": ([("ts_code", "str"), ("trade_date", "date")],
                      [("000001.SZ", "20240103"), ("600519.SH", "20240103")])}))
    patch_read(monkeypatch, env.rd)
    out = data_adj(_args(kind="event", codes=["000001"], start="2024-01-02",
                         end="2024-01-04", inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    rows = out.data["rows"]
    assert len(rows) == 1 and rows[0]["code"] == "000001.SZ"
    assert rows[0]["trade_date"] == "2024-01-03"


def test_data_adj_unknown_kind_is_usage(env, monkeypatch, tmp_path):
    env.seed(_basic_seed())
    patch_read(monkeypatch, env.rd)
    out = data_adj(_args(kind="bogus", codes=["000001"], start=None, end=None,
                         inline=True, artifacts_dir=tmp_path))
    assert out.ok is False and out.error["code"] == "USAGE"


def test_data_limit_window(env, monkeypatch, tmp_path):
    env.seed(_basic_seed(**{
        "stk_limit": ([("ts_code", "str"), ("trade_date", "date"),
                       ("up_limit", "f64"), ("down_limit", "f64")],
                      [("000001.SZ", "20240102", 11.0, 9.0),
                       ("000001.SZ", "20240103", 12.0, 10.0),
                       ("600519.SH", "20240102", 100.0, 80.0)])}))
    patch_read(monkeypatch, env.rd)
    out = data_limit(_args(codes=["000001"], start="2024-01-03", end="2024-01-03",
                           inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    rows = out.data["rows"]
    assert len(rows) == 1 and rows[0]["up_limit"] == 12.0 and rows[0]["down_limit"] == 10.0


def test_data_stock_basic_rows(env, monkeypatch, tmp_path):
    env.seed({"stock_basic": _stock_basic()})
    spy = patch_read(monkeypatch, env.rd)
    out = data_stock_basic(_args(codes=None, inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    rows = out.data["rows"]
    assert [r["ts_code"] for r in rows] == ["000001.SZ", "600519.SH"]
    assert any("stock_basic" in s for s in spy.sql_texts())


def test_data_moneyflow_filters(env, monkeypatch, tmp_path):
    env.seed(_basic_seed(**{
        "moneyflow": ([("ts_code", "str"), ("trade_date", "date"),
                       ("main_net_inflow", "f64?")],
                      [("000001.SZ", "20240102", 1.23e8),
                       ("000001.SZ", "20240103", -4.5e7),
                       ("600519.SH", "20240102", 9.9e8)])}))
    patch_read(monkeypatch, env.rd)
    out = data_moneyflow(_args(codes=["000001"], start="2024-01-02", end=None,
                               inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    assert [r["main_net_inflow"] for r in out.data["rows"]] == [1.23e8, -4.5e7]


def test_data_sector_filters_board_type(env, monkeypatch, tmp_path):
    env.seed({"moneyflow_sector": (
        [("trade_date", "date"), ("board_type", "str"), ("board_code", "str"),
         ("board_name", "str"), ("main_net_inflow", "f64?")],
        [("20240102", "industry", "BK0465", "银行", 1.0),
         ("20240102", "concept", "BK0490", "云计算", 2.0)])})
    patch_read(monkeypatch, env.rd)
    out = data_sector(_args(board_type="industry", board_code=None,
                            start="2024-01-02", end="2024-01-02",
                            inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    rows = out.data["rows"]
    assert len(rows) == 1 and rows[0]["board_name"] == "银行"


def test_data_members_filter_board(env, monkeypatch, tmp_path):
    env.seed({"concept_members": (
        [("trade_date", "date"), ("board_code", "str"), ("board_name", "str"),
         ("ts_code", "str")],
        [("20240102", "BK0490", "云计算", "000001.SZ"),
         ("20240102", "BK0465", "银行", "000001.SZ")])})
    patch_read(monkeypatch, env.rd)
    out = data_members(_args(board_code="BK0490", start=None, end=None,
                             inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    rows = out.data["rows"]
    assert len(rows) == 1 and rows[0]["board_name"] == "云计算"


def test_data_fundamentals_filters_code(env, monkeypatch, tmp_path):
    env.seed({**_basic_seed(), "fundamentals": (
        [("ts_code", "str"), ("updated_date", "date"), ("industry", "str?")],
        [("000001.SZ", "20240102", "银行"), ("600519.SH", "20240102", "白酒")])})
    patch_read(monkeypatch, env.rd)
    out = data_fundamentals(_args(codes=["000001"], start=None, end=None,
                                  inline=True, artifacts_dir=tmp_path))
    assert out.ok, out.error
    rows = out.data["rows"]
    assert len(rows) == 1 and rows[0]["industry"] == "银行"


def test_data_universe_pit_membership(env, monkeypatch, tmp_path):
    env.seed({"stock_basic": _stock_basic(),
              "trade_cal": ([("cal_date", "date"), ("is_open", "u8")],
                            [("20240102", 1)])})
    spy = patch_read(monkeypatch, env.rd)
    out = data_universe(_args(date="2024-01-02", layer=None, inline=True,
                              artifacts_dir=tmp_path))
    assert out.ok, out.error
    rows = out.data["rows"]
    assert {r["code"] for r in rows} == {"000001", "600519"}
    assert all(r["in_universe"] is True for r in rows)
    assert all(r["is_listed"] is True for r in rows)
    assert rows[0]["list_days"] >= 0
    assert rows[0]["exchange"] in ("SSE", "SZSE")
    assert any("stock_basic" in s for s in spy.sql_texts())


# ================================================================
# registry / describe / CLI 单 JSON
# ================================================================

def test_data_commands_registered_with_docs():
    names = {n for n in registry.COMMANDS if n.startswith("data.")}
    assert names == DATA_COMMANDS
    for name in sorted(names):
        spec = registry.COMMANDS[name]
        assert spec.description, name
        assert spec.params, name
        assert spec.examples, name
        assert spec.output_schema, name
        assert name in registry._HANDLERS


def test_dispatch_data_daily_single_json(env, monkeypatch, tmp_path, capsys):
    env.seed(_basic_seed())
    patch_read(monkeypatch, env.rd)
    code = registry.dispatch(["data.daily", "--codes", "000001", "--view", "raw",
                              "--inline"])
    assert code == 0
    captured = capsys.readouterr()
    assert len(captured.out.strip().splitlines()) == 1
    doc = json.loads(captured.out)
    assert doc["ok"] is True and doc["command"] == "data.daily"
    assert [r["close"] for r in doc["data"]["rows"]] == [10.5, 11.0]


def test_cli_research_data_tables_single_json(monkeypatch):
    rd = MemoryRead({"daily": {"ts_code", "trade_date"}})
    monkeypatch.setattr(bootstrap, "open_read", lambda *a, **k: rd)
    result = runner.invoke(cli_app, ["research", "data", "tables"])
    assert result.exit_code == 0
    doc = json.loads(result.stdout)
    assert doc["ok"] is True and doc["command"] == "data.tables"
    assert [r["table"] for r in doc["data"]["rows"]] == ["daily"]


def test_cli_research_data_missing_table_exit_8(env, monkeypatch, tmp_path):
    env.seed({"stock_basic": _stock_basic()})
    patch_read(monkeypatch, env.rd)
    result = runner.invoke(cli_app, ["research", "data", "schema", "--table", "nope"])
    assert result.exit_code == 8
    doc = json.loads(result.stdout)
    assert doc["ok"] is False and doc["error"]["code"] == "DATA"
