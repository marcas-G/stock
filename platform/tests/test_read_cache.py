"""R31 分钟链读路径：bars_1m 批读 chunk 级磁盘缓存（指纹失效/原子/回退/开关）。

断言来源：任务书 ①（键=sha256(codes 集|窗口|columns 集|源指纹)；指纹 =
system.parts(active Σrows+max(modification_time)) + max(datetime)；目录/上限/TTL/LRU；
.part+fsync+os.replace 原子写；读时 sha256 校验，损坏/半成品/元数据缺失回退直读；
命中/未命中/回退写 run.log 与 --profile 段；FACTORLAB_READ_CACHE=0 关闭）。

本文件全部为无 CH 单测（真 CH bit-exact 硬门见 test_ch_arrow_stream.py）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import polars as pl
import pytest

from factorlab.adapters.read import chunk_cache as cc


# ---------------- 帧构造 ----------------

def _frame(n: int = 4) -> pl.DataFrame:
    """含生产关键 dtype 的小帧：Date/String/UInt16/Float32/Float64 + null 槽。"""
    return pl.DataFrame({
        "trade_date": pl.Series([dt.date(2024, 1, 2)] * n, dtype=pl.Date),
        "code": pl.Series(["000001"] * n, dtype=pl.String),
        "minute_index": pl.Series(list(range(n)), dtype=pl.UInt16),
        "close": pl.Series([10.5, None, 11.25, 9.75][:n], dtype=pl.Float32),
        "volume": pl.Series([100.0, None, 300.0, 400.0][:n], dtype=pl.Float64),
    })


class _FpRead:
    """指纹假句柄：记录 SQL，返回可控的 parts/max(datetime)。"""

    backend = "ch"

    def __init__(self, rows: int = 100, mtime: str = "2026-09-18 01:00:00",
                 maxdt: object = dt.datetime(2026, 9, 17, 23, 0, 0)):
        self.rows, self.mtime, self.maxdt = rows, mtime, maxdt
        self.sqls: list[str] = []
        self.params: list[dict] = []

    def query_rows(self, sql, params=None):
        self.sqls.append(sql)
        self.params.append(dict(params or {}))
        if "system.parts" in sql:
            return [(self.rows, self.mtime)]
        if "max(datetime)" in sql:
            return [(self.maxdt,)]
        raise AssertionError(f"意外的 query_rows: {sql}")


# ---------------- 键与指纹 ----------------

def test_key_stable_and_sensitive_to_every_input():
    base = dict(codes=["000001.SZ", "600519.SH"], date_start="2024-01-02",
                date_end="2024-01-12",
                columns=["code", "datetime", "close"], fingerprint="fp-1")
    k0 = cc.chunk_cache_key(**base)
    assert cc.chunk_cache_key(**base) == k0
    assert len(k0) == 64 and all(c in "0123456789abcdef" for c in k0)

    # codes 集：顺序无关，内容敏感
    assert cc.chunk_cache_key(**{**base, "codes": ["600519.SH", "000001.SZ"]}) == k0
    assert cc.chunk_cache_key(**{**base, "codes": ["000001.SZ"]}) != k0
    # columns 集：顺序无关，内容敏感
    assert cc.chunk_cache_key(**{**base,
                                 "columns": ["close", "code", "datetime"]}) == k0
    assert cc.chunk_cache_key(**{**base, "columns": ["code", "datetime"]}) != k0
    # 窗口 / 指纹：敏感
    assert cc.chunk_cache_key(**{**base, "date_start": "2024-01-03"}) != k0
    assert cc.chunk_cache_key(**{**base, "date_end": "2024-01-13"}) != k0
    assert cc.chunk_cache_key(**{**base, "fingerprint": "fp-2"}) != k0


def test_fingerprint_sensitive_to_parts_rows_mtime_and_maxdt():
    """源变化（回填/新数据）→ 指纹变化 → 键变化（自动失效）。"""
    cc.reset_fingerprint_cache()
    f0 = cc.bars_source_fingerprint(_FpRead(), ttl_s=0)
    f1 = cc.bars_source_fingerprint(_FpRead(rows=101), ttl_s=0)
    f2 = cc.bars_source_fingerprint(_FpRead(mtime="2026-09-18 02:00:00"), ttl_s=0)
    f3 = cc.bars_source_fingerprint(
        _FpRead(maxdt=dt.datetime(2026, 9, 18, 15, 0, 0)), ttl_s=0)
    assert len({f0, f1, f2, f3}) == 4
    # 同源状态 → 同指纹（确定性；缓存可跨 run 命中）
    assert f0 == cc.bars_source_fingerprint(_FpRead(), ttl_s=0)


def test_fingerprint_queries_parts_and_max_datetime():
    """非存根锁：真的查 system.parts（active）与 bars_1m 的 max(datetime)。"""
    cc.reset_fingerprint_cache()
    rd = _FpRead()
    cc.bars_source_fingerprint(rd, ttl_s=0)
    assert len(rd.sqls) == 2
    assert "system.parts" in rd.sqls[0] and "active" in rd.sqls[0]
    assert "bars_1m" in rd.sqls[1] and "max(datetime)" in rd.sqls[1]
    assert rd.params[0].get("db"), "parts 查询必须按当前库过滤（params.db）"


def test_fingerprint_memoized_within_ttl_and_refreshed_after():
    cc.reset_fingerprint_cache()
    rd = _FpRead()
    a = cc.bars_source_fingerprint(rd, ttl_s=100.0, now=1000.0)
    b = cc.bars_source_fingerprint(rd, ttl_s=100.0, now=1050.0)
    assert a == b and len(rd.sqls) == 2       # 第二次走 memo（不重查）
    c = cc.bars_source_fingerprint(rd, ttl_s=100.0, now=1201.0)
    assert c == a and len(rd.sqls) == 4       # 超 TTL → 重查
    cc.reset_fingerprint_cache()


# ---------------- 配置 / 开关 ----------------

def test_config_defaults_enabled():
    cfg = cc.read_cache_config({})
    assert cfg.enabled is True
    assert cfg.root.name == "bars_1m" and cfg.root.parent.name == "factorlab"
    assert cfg.max_bytes == 30 * 1024 ** 3
    assert cfg.ttl_seconds == 7 * 86400


def test_config_env_overrides_and_invalid_fail_loud(tmp_path):
    env = {
        "FACTORLAB_READ_CACHE": "0",
        "FACTORLAB_READ_CACHE_DIR": str(tmp_path / "mine"),
        "FACTORLAB_READ_CACHE_MAX_GB": "1.5",
        "FACTORLAB_READ_CACHE_TTL_DAYS": "2",
    }
    cfg = cc.read_cache_config(env)
    assert cfg.enabled is False
    assert cfg.root == tmp_path / "mine"
    assert cfg.max_bytes == int(1.5 * 1024 ** 3)
    assert cfg.ttl_seconds == 2 * 86400

    for key, bad in (("FACTORLAB_READ_CACHE", "maybe"),
                     ("FACTORLAB_READ_CACHE_MAX_GB", "0"),
                     ("FACTORLAB_READ_CACHE_MAX_GB", "abc"),
                     ("FACTORLAB_READ_CACHE_TTL_DAYS", "-1")):
        with pytest.raises(ValueError, match=key):
            cc.read_cache_config({**env, key: bad})


def test_switch_off_returns_none_and_creates_nothing(tmp_path):
    env = {"FACTORLAB_READ_CACHE": "0",
           "FACTORLAB_READ_CACHE_DIR": str(tmp_path / "off")}
    assert cc.get_chunk_cache(env=env) is None
    assert not (tmp_path / "off").exists()
    # 显式 enabled=True 覆盖 env 关（测试/CLI 反向注入用）
    env_on = {"FACTORLAB_READ_CACHE_DIR": str(tmp_path / "on")}
    cache = cc.get_chunk_cache(enabled=True, env=env_on)
    assert cache is not None and cache.root == tmp_path / "on"
    # 关闭时不落盘、不建目录
    assert cc.get_chunk_cache(env={"FACTORLAB_READ_CACHE": "0",
                                   "FACTORLAB_READ_CACHE_DIR": str(tmp_path / "off2")}) is None
    assert not (tmp_path / "off2").exists()
    cc.reset_chunk_cache()


# ---------------- 存储 / 命中 / 回退 ----------------

def _cache(tmp_path, *, max_bytes=10 ** 9, ttl=7 * 86400) -> cc.ChunkCache:
    return cc.ChunkCache(tmp_path / "bars_1m", max_bytes=max_bytes,
                         ttl_seconds=ttl)


def test_store_then_load_bit_exact_with_manifest_fields(tmp_path):
    cache = _cache(tmp_path)
    frame = _frame()
    entry = cache.store("k1", frame, "fp-1", now=1000.0)

    stored = json.loads((cache.root / "manifest.json").read_text())
    e = stored["entries"]["k1"]
    assert e["fingerprint"] == "fp-1" and e["size"] > 0
    assert e["sha256"] == entry["sha256"] and e["hits"] == 0
    assert e["created_at"] == 1000.0
    data_file = cache.root / e["file"]
    assert data_file.is_file() and data_file.suffix == ".arrow"
    assert data_file.stat().st_size == e["size"]
    assert not list(cache.root.glob("*.tmp")) and not list(cache.root.glob("*.part"))

    lk = cache.load("k1", now=1001.0)
    assert lk.status == "hit" and lk.frame is not None
    assert lk.frame.equals(frame) and lk.frame.schema == frame.schema
    assert lk.frame["close"].null_count() == frame["close"].null_count()
    # 命中计数落 manifest（LRU/TTL 的输入）
    assert json.loads((cache.root / "manifest.json").read_text()
                      )["entries"]["k1"]["hits"] == 1


def test_load_unknown_key_is_miss(tmp_path):
    cache = _cache(tmp_path)
    lk = cache.load("nope")
    assert lk.status == "miss" and lk.frame is None
    assert not cache.root.exists()          # 从未 store → 不建目录


def test_corrupted_file_falls_back_and_drops_entry(tmp_path):
    cache = _cache(tmp_path)
    cache.store("k1", _frame(), "fp-1")
    path = cache.root / json.loads((cache.root / "manifest.json").read_text()
                                   )["entries"]["k1"]["file"]
    path.write_bytes(b"not-an-arrow-file")

    lk = cache.load("k1")
    assert lk.status == "fallback" and lk.frame is None
    assert not path.exists()
    assert "k1" not in json.loads((cache.root / "manifest.json").read_text())["entries"]
    assert cache.load("k1").status == "miss"       # 二次不复发


def test_truncated_half_written_file_falls_back(tmp_path):
    """半成品（size 不符/sha 不符）→ 回退直读，不 fail。"""
    cache = _cache(tmp_path)
    cache.store("k1", _frame(), "fp-1")
    e = json.loads((cache.root / "manifest.json").read_text())["entries"]["k1"]
    (cache.root / e["file"]).write_bytes(b"x" * 10)
    assert cache.load("k1").status == "fallback"


def test_missing_file_falls_back(tmp_path):
    cache = _cache(tmp_path)
    cache.store("k1", _frame(), "fp-1")
    e = json.loads((cache.root / "manifest.json").read_text())["entries"]["k1"]
    (cache.root / e["file"]).unlink()
    assert cache.load("k1").status == "fallback"


def test_corrupt_manifest_falls_back_not_fail(tmp_path):
    cache = _cache(tmp_path)
    cache.store("k1", _frame(), "fp-1")
    (cache.root / "manifest.json").write_text("{broken json")
    lk = cache.load("k1")
    assert lk.status == "fallback" and lk.frame is None


def test_ttl_expired_is_miss_and_prunes(tmp_path):
    cache = _cache(tmp_path, ttl=10.0)
    cache.store("k1", _frame(), "fp-1", now=1000.0)
    lk = cache.load("k1", now=1011.0)
    assert lk.status == "miss" and lk.frame is None
    entries = json.loads((cache.root / "manifest.json").read_text())["entries"]
    assert "k1" not in entries
    assert not list(cache.root.glob("*.arrow"))


def test_lru_eviction_keeps_recently_accessed(tmp_path):
    cache1 = _cache(tmp_path, max_bytes=10 ** 9)
    cache1.store("a", _frame(), "fp", now=1.0)
    cache1.store("b", _frame(), "fp", now=2.0)
    size = json.loads((cache1.root / "manifest.json").read_text()
                      )["entries"]["a"]["size"]

    # 触达 a（last_access 更新为 2.5）→ b 成为最久未用
    assert cache1.load("a", now=2.5).status == "hit"
    cache2 = cc.ChunkCache(cache1.root, max_bytes=2 * size + 10, ttl_seconds=3600)
    cache2.store("c", _frame(), "fp", now=3.0)

    entries = json.loads((cache1.root / "manifest.json").read_text())["entries"]
    assert "b" not in entries                       # LRU 淘汰 b（非 FIFO）
    assert {"a", "c"} <= set(entries)
    assert sum(e["size"] for e in entries.values()) <= 2 * size + 10
    assert cache2.load("a", now=4.0).status == "hit"
    assert cache2.load("c", now=4.0).status == "hit"


def test_put_is_atomic_on_write_failure(tmp_path, monkeypatch):
    """write_ipc 失败（半成品已落 tmp）→ 目标文件不出现、manifest 不更新。"""
    cache = _cache(tmp_path)
    orig = pl.DataFrame.write_ipc

    def broken(self, file, **kw):
        Path(file).write_bytes(b"half-written-garbage")
        raise RuntimeError("boom")

    monkeypatch.setattr(pl.DataFrame, "write_ipc", broken)
    with pytest.raises(RuntimeError, match="boom"):
        cache.store("k1", _frame(), "fp-1")
    monkeypatch.setattr(pl.DataFrame, "write_ipc", orig)

    assert not (cache.root / "k1.arrow").exists()
    assert cache.load("k1").status == "miss"
    assert "k1" not in json.loads((cache.root / "manifest.json").read_text()
                                  )["entries"] if (cache.root / "manifest.json").exists() else True


def test_empty_frame_roundtrip(tmp_path):
    cache = _cache(tmp_path)
    empty = _frame(0)
    cache.store("k0", empty, "fp")
    lk = cache.load("k0")
    assert lk.status == "hit" and lk.frame is not None
    assert lk.frame.height == 0 and lk.frame.schema == empty.schema


# ---------------- _codes_ch 接线（miss→hit / 指纹失效 / 开关 / 回退 / 审计） ----------------

_COLS = ["trade_date", "code", "minute_index", "close", "volume"]


class _BarsRead:
    """批读假句柄：记录 SQL（指纹/批读），返回可控 frame。"""

    backend = "ch"

    def __init__(self, frame: pl.DataFrame | None = None, rows: int = 10):
        self.frame = _frame() if frame is None else frame
        self.parts_rows = rows
        self.mtime = "2026-09-18 01:00:00"
        self.maxdt = dt.datetime(2026, 9, 17, 23, 0, 0)
        self.rows_calls: list[tuple[str, dict]] = []
        self.df_calls: list[tuple[str, dict, dict]] = []

    def query_rows(self, sql, params=None):
        self.rows_calls.append((sql, dict(params or {})))
        if "system.parts" in sql:
            return [(self.parts_rows, self.mtime)]
        if "max(datetime)" in sql:
            return [(self.maxdt,)]
        if "stock_basic" in sql:
            return [("000001", "000001.SZ")]
        raise AssertionError(f"意外的 query_rows: {sql}")

    def query_df(self, sql, params=None, settings=None):
        self.df_calls.append((sql, params, settings or {}))
        select = sql.split(" FROM ", 1)[0][len("SELECT "):]
        return self.frame.select([c.strip() for c in select.split(",")])


def _enable_cache(monkeypatch, tmp_path) -> Path:
    root = tmp_path / "rc"
    monkeypatch.setenv("FACTORLAB_READ_CACHE", "1")
    monkeypatch.setenv("FACTORLAB_READ_CACHE_DIR", str(root))
    cc.reset_chunk_cache()
    cc.reset_fingerprint_cache()
    return root


def test_codes_ch_miss_then_hit_no_refetch(monkeypatch, tmp_path):
    from factorlab.adapters import intraday
    _enable_cache(monkeypatch, tmp_path)
    rd = _BarsRead()
    a = intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                           cols=_COLS)
    b = intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                           cols=_COLS)
    assert a.equals(b)
    assert len(rd.df_calls) == 1                    # 第二次零 CH 批读
    assert any("system.parts" in sql for sql, _ in rd.rows_calls)
    assert any("max(datetime)" in sql for sql, _ in rd.rows_calls)
    cache = cc.get_chunk_cache(env={"FACTORLAB_READ_CACHE_DIR":
                                    str(tmp_path / "rc")})
    assert cache.stats()["entries"] == 1
    cc.reset_chunk_cache()


def test_codes_ch_fingerprint_change_invalidates(monkeypatch, tmp_path):
    """回填/新数据改指纹 → 不命中（键变）——自动失效硬门。"""
    from factorlab.adapters import intraday
    _enable_cache(monkeypatch, tmp_path)
    rd = _BarsRead()
    intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12", cols=_COLS)
    intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12", cols=_COLS)
    assert len(rd.df_calls) == 1
    rd.parts_rows += 1                              # 源变化（如回填一行）
    cc.reset_fingerprint_cache()                    # 模拟新 run（跨进程重查指纹）
    intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12", cols=_COLS)
    assert len(rd.df_calls) == 2                    # 指纹变 → 重读
    cc.reset_chunk_cache()


def test_codes_ch_cache_off_by_flag_and_env(monkeypatch, tmp_path):
    from factorlab.adapters import intraday
    _enable_cache(monkeypatch, tmp_path)
    rd = _BarsRead()
    # 显式 read_cache=False：不查指纹、不建目录、直读
    intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                       cols=_COLS, read_cache=False)
    assert rd.df_calls and not rd.rows_calls
    assert not (tmp_path / "rc").exists()
    # env 关闭（默认开）
    monkeypatch.setenv("FACTORLAB_READ_CACHE", "0")
    cc.reset_chunk_cache()
    rd2 = _BarsRead()
    intraday._codes_ch(rd2, ["000001.SZ"], "2024-01-02", "2024-01-12", cols=_COLS)
    intraday._codes_ch(rd2, ["000001.SZ"], "2024-01-02", "2024-01-12", cols=_COLS)
    assert len(rd2.df_calls) == 2 and not rd2.rows_calls
    assert not (tmp_path / "rc").exists()


def test_codes_ch_corrupt_falls_back_direct_and_recovers(monkeypatch, tmp_path,
                                                         capfd):
    from factorlab.adapters import intraday
    root = _enable_cache(monkeypatch, tmp_path)
    rd = _BarsRead()
    direct = intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                                cols=_COLS, read_cache=False)
    intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12", cols=_COLS)
    entry = json.loads((root / "manifest.json").read_text())["entries"]
    key = next(iter(entry))
    (root / entry[key]["file"]).write_bytes(b"corrupt")
    capfd.readouterr()

    out = intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                             cols=_COLS)
    assert out.equals(direct)                       # 回退直读，不 fail
    assert len(rd.df_calls) == 3                    # 直读基线 + miss + fallback 直读
    err = capfd.readouterr().err
    assert "fallback" in err
    # 回退时已修复（store 重写）→ 下一次命中
    out2 = intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                              cols=_COLS)
    assert out2.equals(direct) and len(rd.df_calls) == 3
    cc.reset_chunk_cache()


def test_codes_ch_events_logged(monkeypatch, tmp_path, capfd):
    from factorlab.adapters import intraday
    _enable_cache(monkeypatch, tmp_path)
    rd = _BarsRead()
    intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12", cols=_COLS)
    err1 = capfd.readouterr().err
    assert "[read-cache]" in err1 and "miss" in err1
    intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12", cols=_COLS)
    err2 = capfd.readouterr().err
    assert "hit" in err2
    cc.reset_chunk_cache()


def test_codes_ch_profile_segments(monkeypatch, tmp_path):
    from factorlab.adapters import intraday
    from factorlab.app.profile import Profiler
    _enable_cache(monkeypatch, tmp_path)
    prof = Profiler()
    rd = _BarsRead()
    intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12", cols=_COLS,
                       profiler=prof)
    seg = prof.report()["segments"]
    assert seg["cache_miss"]["calls"] == 1 and seg["cache_lookup"]["calls"] == 1
    intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12", cols=_COLS,
                       profiler=prof)
    seg = prof.report()["segments"]
    assert seg["cache_hit"]["calls"] == 1
    cc.reset_chunk_cache()


def test_codes_ch_column_set_reorder_is_hit_with_requested_order(monkeypatch,
                                                                 tmp_path):
    from factorlab.adapters import intraday
    _enable_cache(monkeypatch, tmp_path)
    rd = _BarsRead()
    a = intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                           cols=["code", "close", "trade_date"])
    b = intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                           cols=["trade_date", "close", "code"])
    assert len(rd.df_calls) == 1                    # 列集同 → 命中
    assert b.columns == ["trade_date", "close", "code"]  # 输出列序 = 请求序
    assert a.equals(b.select(a.columns))
    cc.reset_chunk_cache()


def test_codes_ch_empty_frame_cached(monkeypatch, tmp_path):
    from factorlab.adapters import intraday
    _enable_cache(monkeypatch, tmp_path)
    rd = _BarsRead(frame=_frame(0))
    a = intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                           cols=_COLS)
    b = intraday._codes_ch(rd, ["000001.SZ"], "2024-01-02", "2024-01-12",
                           cols=_COLS)
    assert a.height == 0 and b.height == 0 and a.schema == b.schema
    assert len(rd.df_calls) == 1
    cc.reset_chunk_cache()
