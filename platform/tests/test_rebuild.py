from pathlib import Path

import polars as pl
import pytest

from factorlab.config import Settings
from factorlab.adapters.fetcher import TeaJoinClient
from factorlab.adapters.mirror_db import PlatformDB
from factorlab.adapters.rebuild import (INDEX_CODES, RebuildScope, build_final_db,
                                        load_manifest, rebuild_all, save_manifest)


def _sb_l():
    return pl.DataFrame({
        "ts_code": ["000001.SZ"], "symbol": ["000001"], "name": ["甲"], "list_status": ["L"],
        "list_date": ["20240101"], "delist_date": [None], "industry": [None],
        "market": [None], "act_name": [None], "act_ent_type": [None],
        "area": [None], "cnspell": [None]})


def _sb_d():
    return pl.DataFrame({
        "ts_code": ["600001.SH"], "symbol": ["600001"], "name": ["丁"], "list_status": ["D"],
        "list_date": ["20200101"], "delist_date": ["20240601"], "industry": [None],
        "market": [None], "act_name": [None], "act_ent_type": [None],
        "area": [None], "cnspell": [None]})


def _fake_client(monkeypatch, tables, fail=None, calls=None) -> TeaJoinClient:
    """fake client：按 (api_name, 日期参数) 精确匹配返回；未注册接口返回空 DataFrame。

    fail 为接口名集合，命中即抛错（模拟拉取失败）；calls 收集 (api_name, params)。
    rebuild 同时使用 client.fetch（行情/日历/指数）与 fetch_paged（stock_basic），都替换。
    """
    client = TeaJoinClient(token="t", interval=0.0)

    def responder(api_name, params, fields=None):
        if api_name == "stock_basic":
            return _sb_l() if params.get("list_status") == "L" else _sb_d()
        if calls is not None:
            calls.append((api_name, dict(params)))
        if fail and api_name in fail:
            raise RuntimeError(f"{api_name} 拉取失败")
        if api_name == "stock_basic" and params.get("list_status") == "D":
            return _sb_d()
        key = params.get("trade_date") or params.get("report_date") or params.get("cal_date") or ""
        df = tables.get((api_name, key))
        if df is None:
            df = tables.get((api_name, ""))
        return df if df is not None else pl.DataFrame()

    monkeypatch.setattr(client, "fetch", responder)
    monkeypatch.setattr(client, "fetch_paged", responder)
    return client


def _tables():
    return {
        ("trade_cal", ""): pl.DataFrame({
            "exchange": ["SSE", "SSE"],
            "cal_date": ["20240102", "20240103"],
            "is_open": [1, 1],
        }),
        ("stock_basic", ""): _sb_l(),
        ("daily", "20240102"): pl.DataFrame({"trade_date": ["20240102"], "ts_code": ["A.SZ"], "close": [10.0]}),
        ("daily", "20240103"): pl.DataFrame({"trade_date": ["20240103"], "ts_code": ["A.SZ"], "close": [11.0]}),
        ("daily_basic", "20240102"): pl.DataFrame({"trade_date": ["20240102"], "ts_code": ["A.SZ"], "total_mv": [100.0]}),
        ("daily_basic", "20240103"): pl.DataFrame({"trade_date": ["20240103"], "ts_code": ["A.SZ"], "total_mv": [110.0]}),
        ("adj_factor", "20240102"): pl.DataFrame({"trade_date": ["20240102"], "ts_code": ["A.SZ"], "adj_factor": [1.0]}),
        ("adj_factor", "20240103"): pl.DataFrame({"trade_date": ["20240103"], "ts_code": ["A.SZ"], "adj_factor": [1.0]}),
    }


def test_manifest_roundtrip(tmp_path):
    path = tmp_path / "nested" / "manifest.json"
    save_manifest(path, {"daily": {"completed": ["20240102"]}})
    assert load_manifest(path) == {"daily": {"completed": ["20240102"]}}


def test_manifest_missing_defaults(tmp_path):
    assert load_manifest(tmp_path / "nope.json") == {}


# ================================================================
# R01-DATA-I6：manifest 原子写 + 截断容错
# ================================================================

def test_load_manifest_truncated_json_quarantined(tmp_path):
    """写一半的截断 manifest（旧非原子写崩溃残留）→ 容错返回 {} + 隔离坏文件。"""
    path = tmp_path / "manifest.json"
    path.write_text('{"daily": {"completed": ["20240102"], "fail', encoding="utf-8")
    assert load_manifest(path) == {}
    assert not path.exists()                              # 坏文件不残留原位
    quarantined = list(tmp_path.glob("manifest.json.corrupt*"))
    assert len(quarantined) == 1                          # 证据保留可审计
    assert "20240102" in quarantined[0].read_text(encoding="utf-8")


def test_load_manifest_non_dict_json_quarantined(tmp_path):
    """合法 JSON 但非映射（null/[]）同样不容忍——否则下游 .get 崩溃。"""
    path = tmp_path / "manifest.json"
    path.write_text("null", encoding="utf-8")
    assert load_manifest(path) == {}
    assert list(tmp_path.glob("manifest.json.corrupt*"))
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_manifest(path) == {}


def test_save_manifest_atomic_failure_keeps_previous(tmp_path, monkeypatch):
    """原子写单点（R13）：提交失败 → 旧 manifest 原样、无 tmp 残留。"""
    import factorlab.adapters.atomicio as atomicio

    path = tmp_path / "manifest.json"
    save_manifest(path, {"keep": True})

    def boom(tmp, target):
        raise OSError("disk full")

    monkeypatch.setattr(atomicio, "_commit", boom)
    with pytest.raises(OSError, match="disk full"):
        save_manifest(path, {"new": True})
    assert load_manifest(path) == {"keep": True}          # 旧内容完整
    assert list(tmp_path.glob("*.tmp")) == []             # 失败不留痕


def test_save_manifest_via_atomicio(tmp_path, monkeypatch):
    """save_manifest 必须走 atomicio（R13 单点）——不得直接 write_text。"""
    import factorlab.adapters.rebuild as rebuild_mod
    import factorlab.adapters.atomicio as atomicio

    calls = []
    real = atomicio.atomic_write_text

    def spy(path, text, encoding="utf-8"):
        calls.append((str(path), encoding))
        return real(path, text, encoding)

    monkeypatch.setattr(rebuild_mod, "atomic_write_text", spy, raising=False)
    path = tmp_path / "manifest.json"
    save_manifest(path, {"x": 1})
    assert calls and calls[0][0] == str(path)
    assert load_manifest(path) == {"x": 1}


def test_rebuild_all_populates_tables(tmp_path, monkeypatch):
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, _tables())
    report = rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"),
                         manifest_path=tmp_path / "manifest.json")
    assert set(db.list_tables()) >= {"trade_cal", "stock_basic", "daily", "daily_basic", "adj_factor"}
    assert db.query("SELECT count(*) AS n FROM daily")["n"][0] == 2
    assert report["tables"]["daily"]["rows"] == 2


def test_rebuild_resume_skips_completed_dates(tmp_path, monkeypatch):
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, _tables())
    manifest_path = tmp_path / "manifest.json"
    save_manifest(manifest_path, {"daily": {"completed": ["20240102"], "failed": []}})
    report = rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"),
                         manifest_path=manifest_path)
    # 20240102 已 completed，只拉 20240103
    assert report["tables"]["daily"]["dates_fetched"] == ["20240103"]
    manifest = load_manifest(manifest_path)
    assert manifest["daily"]["completed"] == ["20240102", "20240103"]
    assert manifest["last_updated"] == "20240103"


def test_rebuild_requires_token():
    settings = Settings(teajoin_token="")
    with pytest.raises(ValueError, match="token"):
        rebuild_all(PlatformDB(Path("x.duckdb")), TeaJoinClient(token=settings.teajoin_token),
                    scope=RebuildScope(), manifest_path=None)


def test_rebuild_empty_calendar_raises(tmp_path, monkeypatch):
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, {})
    with pytest.raises(ValueError, match="无交易日"):
        rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"),
                    manifest_path=tmp_path / "manifest.json")
    assert db.list_tables() == []


def test_rebuild_stock_basic_empty_fails(tmp_path, monkeypatch):
    """M6-07B1：stock_basic source 空（L/D 任一为空）→ rebuild fail fast（不建表、不静默继续）。"""
    db = PlatformDB(tmp_path / "staging.duckdb")
    tables = {("trade_cal", ""): _tables()[("trade_cal", "")],
              ("daily", "20240102"): _tables()[("daily", "20240102")]}
    client = TeaJoinClient(token="t", interval=0.0)
    def responder(api_name, params, fields=None):
        if api_name == "stock_basic":
            return pl.DataFrame()   # 空 stock_basic（L/D 都空）
        return tables.get((api_name, params.get("trade_date") or params.get("cal_date") or ""), pl.DataFrame())
    monkeypatch.setattr(client, "fetch", responder)
    monkeypatch.setattr(client, "fetch_paged", responder)
    with pytest.raises(ValueError, match="返回空"):
        rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"),
                    manifest_path=tmp_path / "m.json")
    assert "stock_basic" not in db.list_tables()  # 空返回不建表


def test_rebuild_failed_table_does_not_block_others(tmp_path, monkeypatch):
    db = PlatformDB(tmp_path / "staging.duckdb")
    manifest_path = tmp_path / "manifest.json"
    client = _fake_client(monkeypatch, _tables(), fail={"adj_factor"})
    report = rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"),
                         manifest_path=manifest_path)
    assert report["tables"]["daily"]["rows"] == 2          # 其他表不受影响
    assert report["tables"]["adj_factor"]["failed"] == ["20240102", "20240103"]
    assert "adj_factor" not in db.list_tables()
    assert load_manifest(manifest_path)["adj_factor"]["failed"] == ["20240102", "20240103"]


def test_rebuild_resume_retries_failed_and_removes(tmp_path, monkeypatch):
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, _tables())
    manifest_path = tmp_path / "manifest.json"
    save_manifest(manifest_path, {"daily": {"completed": [], "failed": ["20240102"]}})
    report = rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"),
                         manifest_path=manifest_path)
    assert report["tables"]["daily"]["dates_fetched"] == ["20240102", "20240103"]  # failed 日期重试
    manifest = load_manifest(manifest_path)
    assert manifest["daily"]["failed"] == []               # 重试成功后从 failed 移除
    assert manifest["daily"]["completed"] == ["20240102", "20240103"]


def test_rebuild_resume_false_ignores_manifest(tmp_path, monkeypatch):
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, _tables())
    manifest_path = tmp_path / "manifest.json"
    save_manifest(manifest_path, {"daily": {"completed": ["20240102", "20240103"], "failed": []}})
    report = rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"),
                         resume=False, manifest_path=manifest_path)
    assert report["tables"]["daily"]["dates_fetched"] == ["20240102", "20240103"]  # 全量重拉
    assert load_manifest(manifest_path)["daily"]["completed"] == ["20240102", "20240103"]


def test_index_weight_fetches_month_last_trading_day(tmp_path, monkeypatch):
    tables = {
        ("trade_cal", ""): pl.DataFrame({
            "exchange": ["SSE"] * 4,
            "cal_date": ["20240102", "20240103", "20240201", "20240202"],
            "is_open": [1, 1, 1, 1],
        }),
        ("stock_basic", ""): _sb_l(),
    }
    calls = []
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, tables, calls=calls)
    rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240202"),
                manifest_path=tmp_path / "manifest.json")
    iw = [params for name, params in calls if name == "index_weight"]
    assert len(iw) == 8  # 2 个月 × 4 指数，每月 1 次
    assert sorted({p["trade_date"] for p in iw}) == ["20240103", "20240202"]  # 每月最后一个交易日
    assert {p["index_code"] for p in iw} == set(INDEX_CODES)  # 真实 API 必填参数是 index_code


def test_rebuild_filters_future_cal_dates(tmp_path, monkeypatch):
    """trade_cal 返回未来公告日（真实 API 实测含未来日）时 dates 截断到 today：
    不为未来日白拉请求，last_updated 取截断后的最近交易日。"""
    tables = _tables()
    tables[("trade_cal", "")] = pl.DataFrame({
        "exchange": ["SSE", "SSE", "SSE"],
        "cal_date": ["20240102", "20240103", "20991231"],
        "is_open": [1, 1, 1],
    })
    calls = []
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, tables, calls=calls)
    rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"),
                manifest_path=tmp_path / "manifest.json")
    daily_dates = {p["trade_date"] for name, p in calls if name == "daily"}
    assert "20991231" not in daily_dates          # 未来日不拉
    assert load_manifest(tmp_path / "manifest.json")["last_updated"] == "20240103"  # 截断后最近交易日


def test_rebuild_default_manifest_path_uses_data_dir(tmp_path, monkeypatch):
    from factorlab.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, _tables())
    rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"))
    assert (tmp_path / "manifest.json").exists()
    assert load_manifest(tmp_path / "manifest.json")["last_updated"] == "20240103"


def test_rebuild_concurrent_fetch_completes_all_dates(tmp_path, monkeypatch):
    dates = ["20240102", "20240103", "20240104", "20240105", "20240108", "20240109"]
    tables = {
        ("daily", d): pl.DataFrame({"trade_date": [d], "ts_code": ["A.SZ"], "close": [10.0]})
        for d in dates
    }
    tables[("trade_cal", "")] = pl.DataFrame(
        {"exchange": ["SSE"] * len(dates), "cal_date": dates, "is_open": [1] * len(dates)}
    )
    tables[("stock_basic", "")] = _sb_l()
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, tables)
    report = rebuild_all(db, client, scope=RebuildScope(start=dates[0], end=dates[-1]),
                         manifest_path=tmp_path / "m.json")
    assert db.query("SELECT count(*) AS n FROM daily")["n"][0] == len(dates)
    manifest = load_manifest(tmp_path / "m.json")
    assert sorted(manifest["daily"]["completed"]) == dates
    assert report["tables"]["daily"]["failed"] == []


def test_rebuild_concurrent_records_failed(tmp_path, monkeypatch):
    dates = ["20240102", "20240103"]
    tables = {
        ("daily", "20240102"): pl.DataFrame({"trade_date": ["20240102"], "ts_code": ["A.SZ"], "close": [10.0]}),
        ("trade_cal", ""): pl.DataFrame({"exchange": ["SSE"] * 2, "cal_date": dates, "is_open": [1, 1]}),
    }
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, tables)
    # 20240103 无数据 → fetch 返回空 → 行数 0（不算失败）；再构造抛异常场景：
    def flaky(api_name, params, fields=None):
        if api_name == "stock_basic" and params.get("list_status") == "D":
            return _sb_d()
        if api_name == "daily" and params.get("trade_date") == "20240103":
            raise RuntimeError("boom")
        return tables.get((api_name, params.get("trade_date") or params.get("cal_date") or ""), pl.DataFrame())
    monkeypatch.setattr(client, "fetch", flaky)
    report = rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"),
                         manifest_path=tmp_path / "m.json")
    assert "20240103" in report["tables"]["daily"]["failed"]
    assert db.query("SELECT count(*) AS n FROM daily")["n"][0] == 1


def test_rebuild_concurrent_is_faster_than_serial(tmp_path, monkeypatch):
    import time
    dates = ["20240102", "20240103", "20240104", "20240105", "20240108", "20240109"]
    tables = {
        ("daily", d): pl.DataFrame({"trade_date": [d], "ts_code": ["A.SZ"], "close": [10.0]})
        for d in dates
    }
    tables[("trade_cal", "")] = pl.DataFrame(
        {"exchange": ["SSE"] * len(dates), "cal_date": dates, "is_open": [1] * len(dates)}
    )
    tables[("stock_basic", "")] = _sb_l()

    def make_client(delay):
        client = TeaJoinClient(token="t", interval=0.0)
        def responder(api_name, params, fields=None):
            if api_name == "stock_basic":
                return _sb_l() if params.get("list_status") == "L" else _sb_d()
            time.sleep(delay)
            if api_name == "stock_basic" and params.get("list_status") == "D":
                return _sb_d()
            return tables.get((api_name, params.get("trade_date") or params.get("cal_date") or ""), pl.DataFrame())
        monkeypatch.setattr(client, "fetch", responder)
        monkeypatch.setattr(client, "fetch_paged", responder)  # stock_basic 走 fetch_paged
        return client

    db1 = PlatformDB(tmp_path / "s1.duckdb")
    t0 = time.monotonic()
    rebuild_all(db1, make_client(0.2), scope=RebuildScope(start="20240102", end="20240109"),
                manifest_path=tmp_path / "m1.json", max_workers=1)
    serial = time.monotonic() - t0

    db5 = PlatformDB(tmp_path / "s5.duckdb")
    t0 = time.monotonic()
    rebuild_all(db5, make_client(0.2), scope=RebuildScope(start="20240102", end="20240109"),
                manifest_path=tmp_path / "m5.json", max_workers=5)
    concurrent = time.monotonic() - t0

    assert concurrent < serial * 0.8  # 5 路并发应显著快于串行


def test_rebuild_accepts_string_is_open(tmp_path, monkeypatch):
    """fetcher 统一 String 构造后 trade_cal.is_open 是 String——rebuild 需 cast 后比较。"""
    dates = ["20240102", "20240103"]
    tables = {
        ("trade_cal", ""): pl.DataFrame(
            {"exchange": ["SSE"] * 2, "cal_date": dates, "is_open": ["1", "1"]}  # String 类型
        ),
        ("stock_basic", ""): _sb_l(),
        ("daily", "20240102"): pl.DataFrame({"trade_date": ["20240102"], "ts_code": ["A.SZ"], "close": [10.0]}),
        ("daily", "20240103"): pl.DataFrame({"trade_date": ["20240103"], "ts_code": ["A.SZ"], "close": [11.0]}),
    }
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, tables)
    report = rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"),
                         manifest_path=tmp_path / "m.json")
    assert db.query("SELECT count(*) AS n FROM daily")["n"][0] == 2


# ================================================================
# R01-DATA-I5：崩溃续跑不产生 (trade_date, ts_code) 重复行
# ================================================================

def test_rebuild_resume_after_crash_no_duplicate_rows(tmp_path, monkeypatch):
    """崩溃窗口复现：上次已 INSERT 但 manifest 未记录（每 20 批落盘）的日期，
    续跑必须不产生重复行——staging 表已落日期（按日事务原子）对账为已完成，
    不重拉不重插。"""
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, _tables())
    manifest_path = tmp_path / "manifest.json"
    # 上次跑到 20240103 已写入但 crash（manifest 只记录到 20240102）
    db.upsert("daily", pl.DataFrame({
        "trade_date": ["20240102"], "ts_code": ["A.SZ"], "close": [10.0]}),
        keys=["trade_date", "ts_code"])
    db.upsert("daily", pl.DataFrame({
        "trade_date": ["20240103"], "ts_code": ["A.SZ"], "close": [10.5]}),
        keys=["trade_date", "ts_code"])
    save_manifest(manifest_path, {"daily": {"completed": ["20240102"], "failed": []}})
    report = rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"),
                         manifest_path=manifest_path)
    assert report["tables"]["daily"]["dates_fetched"] == []      # 已在库 → 不重拉
    assert db.query("SELECT count(*) AS n FROM daily")["n"][0] == 2      # 无重复
    dup = db.query("""SELECT count(*) AS n FROM (
        SELECT trade_date, ts_code, count(*) c FROM daily GROUP BY 1, 2 HAVING c > 1)""")["n"][0]
    assert dup == 0
    assert db.query("SELECT close FROM daily WHERE trade_date = '20240103'")["close"][0] == 10.5
    manifest = load_manifest(manifest_path)
    assert manifest["daily"]["completed"] == ["20240102", "20240103"]   # 对账补记


def test_rebuild_resume_false_replaces_existing_rows(tmp_path, monkeypatch):
    """resume=False 语义 = 忽略既有进度全量重拉：先清空目标表，旧行不得残留/重复。"""
    db = PlatformDB(tmp_path / "staging.duckdb")
    client = _fake_client(monkeypatch, _tables())
    manifest_path = tmp_path / "manifest.json"
    db.upsert("daily", pl.DataFrame({
        "trade_date": ["20240102"], "ts_code": ["A.SZ"], "close": [9.9]}),
        keys=["trade_date", "ts_code"])
    save_manifest(manifest_path, {"daily": {"completed": ["20240102", "20240103"], "failed": []}})
    report = rebuild_all(db, client, scope=RebuildScope(start="20240102", end="20240103"),
                         resume=False, manifest_path=manifest_path)
    assert report["tables"]["daily"]["dates_fetched"] == ["20240102", "20240103"]
    assert db.query("SELECT count(*) AS n FROM daily")["n"][0] == 2
    assert db.query("SELECT close FROM daily WHERE trade_date = '20240102'")["close"][0] == 10.0


def test_build_final_db_aborts_on_duplicate_daily(tmp_path):
    """staging 含重复 (trade_date, ts_code) → build_final_db 拒绝产出最终库。"""
    staging = PlatformDB(tmp_path / "staging.duckdb")
    staging.upsert("daily", pl.DataFrame({
        "trade_date": ["20240102", "20240102"], "ts_code": ["A.SZ", "A.SZ"],
        "close": [10.0, 10.5]}), keys=[])   # keys=[] 普通 INSERT → 重复行
    assert staging.query("SELECT count(*) AS n FROM daily")["n"][0] == 2
    with pytest.raises(ValueError, match="重复组"):
        build_final_db(staging, tmp_path / "final.duckdb")
    assert not (tmp_path / "final.duckdb").exists()   # 不产出会污染读面的最终库


def test_build_final_db_ok_without_duplicates(tmp_path):
    """对照：无重复时正常产出（gate 不误伤合法 rebuild 产物）。"""
    staging = PlatformDB(tmp_path / "staging.duckdb")
    staging.upsert("daily", pl.DataFrame({
        "trade_date": ["20240102"], "ts_code": ["A.SZ"], "close": [10.0]}),
        keys=["trade_date", "ts_code"])
    out = build_final_db(staging, tmp_path / "final.duckdb")
    assert "daily" in out["tables"]
    assert PlatformDB(tmp_path / "final.duckdb").query(
        "SELECT count(*) AS n FROM daily")["n"][0] == 1
