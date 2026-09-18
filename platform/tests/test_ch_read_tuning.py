"""R09-PERF-P4：bars_1m 批读的 CH 查询设置可配置化（FACTORLAB_CH_MAX_THREADS/
FACTORLAB_CH_MAX_BLOCK_SIZE）——可观测（真注入 query settings）/默认零行为
（未设 = {}）/非法 fail loud。

spike 依据：`governance/evidence/verification/R31/minute-perf/spike/ch_read_spike.json`
（真实 CH 同窗单查询：max_threads 2..16 与 max_block_size 128k..1M 全在噪声带
3.1–3.6s——不设平台默认，只提供实验旋钮）。
"""
from __future__ import annotations

import polars as pl
import pytest

from factorlab.adapters import ch_read, intraday


class _RecordingRead:
    """记录 settings 透传的假 CH 句柄（backend=ch；不触网）。"""

    backend = "ch"

    def __init__(self) -> None:
        self.df_calls: list[tuple] = []

    def query_df(self, sql: str, params=None, settings=None) -> pl.DataFrame:
        self.df_calls.append((sql, params, settings))
        return pl.DataFrame({
            "trade_date": pl.Series([], dtype=pl.Date),
            "code": pl.Series([], dtype=pl.String),
        })

    def query_rows(self, sql: str, params=None):
        raise AssertionError(f"ts_code 输入不应触发 symbol 解析: {sql}")

    def command(self, sql: str, params=None):
        raise AssertionError("读测试不应调用 command")

    def tables(self) -> set[str]:
        raise AssertionError("读测试不应探测 schema")

    def columns(self, table: str) -> set[str]:
        raise AssertionError("读测试不应探测 schema")

    def close(self) -> None:
        pass


def _clear_env(monkeypatch):
    monkeypatch.delenv("FACTORLAB_CH_MAX_THREADS", raising=False)
    monkeypatch.delenv("FACTORLAB_CH_MAX_BLOCK_SIZE", raising=False)


def test_bars_read_settings_default_unset_is_empty(monkeypatch):
    """未设 env → {}（平台默认查询设置，零行为变化）。"""
    _clear_env(monkeypatch)
    assert ch_read.bars_read_settings() == {}


def test_bars_read_settings_env_parsed(monkeypatch):
    """env 设置 → 对应 clickhouse settings 键（int）。"""
    monkeypatch.setenv("FACTORLAB_CH_MAX_THREADS", "4")
    monkeypatch.setenv("FACTORLAB_CH_MAX_BLOCK_SIZE", "524288")
    assert ch_read.bars_read_settings() == {"max_threads": 4,
                                            "max_block_size": 524288}
    monkeypatch.delenv("FACTORLAB_CH_MAX_BLOCK_SIZE")
    assert ch_read.bars_read_settings() == {"max_threads": 4}


@pytest.mark.parametrize("value", ["abc", "0", "-1", "1.5", ""])
def test_bars_read_settings_invalid_rejected(monkeypatch, value):
    """非法值 fail loud（不静默取默认）。"""
    _clear_env(monkeypatch)
    monkeypatch.setenv("FACTORLAB_CH_MAX_THREADS", value)
    with pytest.raises(ValueError, match="FACTORLAB_CH_MAX_THREADS"):
        ch_read.bars_read_settings()


def test_bars_read_settings_startup_on_batch_read_sql(monkeypatch):
    """行为门（非存根）：`_codes_ch` 的批读 SQL 真带 bars_1m + 窗口参数，
    且把 env 设置作为 query settings 传给句柄。"""
    monkeypatch.setenv("FACTORLAB_CH_MAX_THREADS", "8")
    monkeypatch.delenv("FACTORLAB_CH_MAX_BLOCK_SIZE", raising=False)
    rd = _RecordingRead()
    df = intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                            cols=["trade_date", "code", "minute_index", "close"])
    assert df.height == 0
    assert len(rd.df_calls) == 1
    sql, params, settings = rd.df_calls[0]
    assert ".bars_1m " in sql and "ORDER BY code, datetime" in sql
    assert "code IN (%(t0)s)" in sql
    assert params == {"t0": "000001.SZ", "start": "2024-01-02",
                      "end": "2024-01-12"}
    assert settings == {"max_threads": 8}
    # 禁止行为：未设 block size 不得凭空注入该键
    assert "max_block_size" not in settings


def test_bars_read_settings_default_passes_empty(monkeypatch):
    """未设 env 时批读仍显式传 {}（可观测的默认路径，不静默省略通道）。"""
    _clear_env(monkeypatch)
    rd = _RecordingRead()
    intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                       cols=["trade_date", "code", "minute_index", "close"])
    assert rd.df_calls[0][2] == {}


def test_query_df_merges_settings_with_read_settings(monkeypatch):
    """模块 query_df：单查询 settings 与 join_use_nulls=1 合并（后者恒在）。"""
    captured: dict = {}

    class _Client:
        def query_arrow(self, sql, parameters=None, settings=None):
            captured["sql"] = sql
            captured["settings"] = settings
            return pl.DataFrame({"x": [1]}).to_arrow()

    monkeypatch.setattr(ch_read, "get_client", lambda: _Client())
    df = ch_read.query_df("SELECT 1", settings={"max_threads": 3})
    assert df.height == 1
    assert captured["settings"] == {"join_use_nulls": 1, "max_threads": 3}
    ch_read.query_df("SELECT 1")
    assert captured["settings"] == {"join_use_nulls": 1}


def test_clickhouse_read_forwards_settings(monkeypatch):
    """句柄 query_df(settings=...) 透传到模块函数（真接线，不丢参数）。"""
    calls: list[dict] = []

    def spy(sql, params=None, settings=None):
        calls.append({"sql": sql, "params": params, "settings": settings})
        return pl.DataFrame({"x": [1]})

    monkeypatch.setattr(ch_read, "query_df", spy)
    rd = ch_read.ClickHouseRead()
    out = rd.query_df("SELECT 1", {"a": 1}, settings={"max_threads": 2})
    assert out.height == 1
    assert calls == [{"sql": "SELECT 1", "params": {"a": 1},
                      "settings": {"max_threads": 2}}]


def test_get_client_thread_local_and_concurrent_queries(ch_db):
    """R09-PERF-P4 chunk 并行的并发读契约（真 CH）：
    - 同线程重复取客户端 → 同一实例（单例语义保留）；
    - 两个线程 barrier 同步后同时查 → 都成功（每线程独立客户端实例——
      clickhouse-connect 同 session 并发查询会 ProgrammingError）。"""
    import threading
    from factorlab.adapters import ch_read

    assert ch_read.query_df("SELECT 1 AS x").height == 1
    assert ch_read.get_client() is ch_read.get_client()

    barrier = threading.Barrier(2, timeout=10)
    seen: dict[int, object] = {}
    errors: list[BaseException] = []

    def work(tag: int) -> None:
        try:
            barrier.wait()
            df = ch_read.query_df(f"SELECT {tag} AS x")
            seen[tag] = (ch_read.get_client(), df["x"].to_list())
        except BaseException as exc:  # noqa: BLE001 - 测试收集全部失败形态
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert seen[1][1] == [1] and seen[2][1] == [2]
    assert seen[1][0] is not seen[2][0]              # 每线程独立实例
    assert ch_read.get_client() not in (seen[1][0], seen[2][0])  # 主线程自有实例
