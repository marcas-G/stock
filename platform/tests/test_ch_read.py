"""R04-P4：ClickHouseRead schema 探测缓存（tables()/columns() 按 (db,table) memoize）。

背景（R04 实测）：M8 回测 356 次 CH 查询 / 21 decisions——`system.tables` 100 次、
`system.columns` 60+ 次（market_open._require_tables/_columns 每 decision 重探）。
契约：缓存生命周期 = 句柄实例；按当前 `settings.ch_database` 隔离（换库重新探测，
换回命中各自 key）；**同库内 DDL 变更不自动失效**（实例内 schema 视为静态——
`open_read` 的句柄对应一次 run；需要看到新 DDL 请新开句柄）。

本文件：纯桩计数（不依赖 CH）+ ch_db 真库集成各一组。
"""
import polars as pl
import pytest

from factorlab.adapters import ch_read
from factorlab.adapters.ch_read import ClickHouseRead
from factorlab.config import settings


def _stub_query_rows(monkeypatch, tables=("daily", "stock_basic"),
                     columns=("trade_date", "close")):
    """桩 query_rows：system.tables/columns 各返回固定集合，记录 SQL。"""
    calls: list[str] = []

    def fake(sql, params=None):
        calls.append(sql)
        if "system.tables" in sql:
            return [(t,) for t in tables]
        if "system.columns" in sql:
            return [(c,) for c in columns]
        raise AssertionError(f"意外 SQL: {sql}")

    monkeypatch.setattr(ch_read, "query_rows", fake)
    return calls


def test_schema_probes_memoized_per_db_table(monkeypatch):
    """同实例重复 tables()/columns() → 每个 (db[,table]) 键恰一次探测查询。"""
    monkeypatch.setattr(settings, "ch_database", "db1")
    calls = _stub_query_rows(monkeypatch)
    rd = ClickHouseRead()
    assert rd.tables() == {"daily", "stock_basic"}
    assert rd.columns("daily") == {"trade_date", "close"}
    assert len(calls) == 2
    assert rd.tables() == {"daily", "stock_basic"}      # 命中缓存
    assert rd.columns("daily") == {"trade_date", "close"}
    assert rd.columns("stock_basic") == {"trade_date", "close"}  # 另一键 → 1 次
    assert len(calls) == 3
    assert sum("system.tables" in s for s in calls) == 1
    assert sum("system.columns" in s for s in calls) == 2


def test_schema_probes_isolated_per_db(monkeypatch):
    """ch_database 切换 → 按库名 key 重新探测；换回原库命中各自缓存。"""
    monkeypatch.setattr(settings, "ch_database", "db1")
    calls = _stub_query_rows(monkeypatch)
    rd = ClickHouseRead()
    assert "daily" in rd.tables()
    monkeypatch.setattr(settings, "ch_database", "db2")
    assert "daily" in rd.tables()                        # 换库 → 重探
    assert len(calls) == 2
    assert "database = 'db2'" in calls[1]
    monkeypatch.setattr(settings, "ch_database", "db1")
    assert "daily" in rd.tables()                        # 回 db1 命中旧 key
    assert len(calls) == 2


def test_schema_probe_cache_returns_defensive_copies(monkeypatch):
    """返回副本——调用方修改结果不得污染缓存（多 decision 共享）。"""
    monkeypatch.setattr(settings, "ch_database", "db1")
    _stub_query_rows(monkeypatch)
    rd = ClickHouseRead()
    t = rd.tables()
    t.add("hacked")
    assert "hacked" not in rd.tables()
    c = rd.columns("daily")
    c.add("hacked")
    assert "hacked" not in rd.columns("daily")


def test_schema_probe_failure_not_cached(monkeypatch):
    """探测失败（CH 不可达）不写缓存——重试仍会真查（失败不被记忆）。"""
    monkeypatch.setattr(settings, "ch_database", "db1")
    calls: list[str] = []

    def flaky(sql, params=None):
        calls.append(sql)
        if len(calls) == 1:
            raise RuntimeError("ClickHouse 不可达")
        return [("daily",)]

    monkeypatch.setattr(ch_read, "query_rows", flaky)
    rd = ClickHouseRead()
    with pytest.raises(RuntimeError):
        rd.tables()
    assert rd.tables() == {"daily"}
    assert len(calls) == 2


def test_schema_probes_single_query_on_real_ch(ch_db, monkeypatch):
    """真 CH 集成（M8 场景雏形）：同一句柄连续 5 次 tables()/columns() 探测
    只发 1+1 条 system 查询（此前 5+5）；结果与真实 schema 一致。"""
    client, db = ch_db
    client.command(f"CREATE TABLE {db}.probe_tbl (x UInt8, y String) "
                   f"ENGINE=MergeTree ORDER BY x")
    rd = ClickHouseRead()
    real = ch_read.query_rows
    calls: list[str] = []

    def spy(sql, params=None):
        calls.append(sql)
        return real(sql, params)

    monkeypatch.setattr(ch_read, "query_rows", spy)
    for _ in range(5):
        assert {"probe_tbl"} <= rd.tables()
        assert rd.columns("probe_tbl") == {"x", "y"}
    assert sum("system.tables" in s for s in calls) == 1
    assert sum("system.columns" in s for s in calls) == 1
    assert all(f"'{db}'" in s for s in calls)
