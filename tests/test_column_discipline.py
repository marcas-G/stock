"""M5（G4）：数据入库校验——引擎读面表列命名纪律（设计 §5.3-2/§7-3 双重锁的数据侧）。

设计断言源（docs/superpowers/specs/2026-09-06-factorlab-dsl-shape-design.md）：
- "凡是内容含未来信息的表列，命名必须落在这类前缀下——约定进活文档与数据入库校验"
- 关闭面三类 = 语法/效率门、未来函数门、内部保留列（__factorlab_*、in_universe）
- 读面列供给语义：daily/daily_basic/属性面列按 PIT 供给公式，引擎无字段白名单

本文件锁：引擎读面表（daily/daily_basic/adj_factor/index_daily/stock_basic/
trade_cal/stock_st/stk_limit/suspend_d——engine 读路径盘点）不允许出现
（a）引擎内部保留名列（__factorlab_* 前缀 / in_universe 精确名）——
    注入/join 会与引擎内部列碰撞毒化面板；
（b）未来前缀列（forward_*/future_* 前缀 / target/label 精确名）——
    读面按构造即 PIT 数据面；未来/标签数据只由评估运行时在内存计算
    （engine/forward.compute_forward_returns）或落研究侧数据表，不进读面表。
违例给修法指引；`verify_all` 报告 `column_discipline` 键；重建收口拒绝。

「禁止行为」保证：把校验实现替换为恒返回 [] 的存根，以下违例断言全部失败。
"""

import polars as pl
import pytest

from factorlab.data.platform_db import PlatformDB
from factorlab.data.verify import (
    ENGINE_SURFACE_TABLES,
    validate_engine_surface,
    validate_surface_columns,
    verify_all,
)

# 引擎读面表之外的平台表（重建会建但引擎读路径不消费——不受纪律约束）
_NON_SURFACE = "moneyflow"


def _mk_clean_db(path):
    db = PlatformDB(path)
    db.upsert("daily", pl.DataFrame({
        "trade_date": ["20240102", "20240103"],
        "ts_code": ["000001.SZ", "000001.SZ"],
        "close": [10.0, 11.0],
    }), keys=["trade_date", "ts_code"])
    db.upsert("trade_cal", pl.DataFrame({"cal_date": ["20240102"], "is_open": [1]}), keys=[])
    return db


def _mk_violating_db(path, bad_cols):
    """daily 建表即带违例列（PlatformDB.upsert 按 key 合并、表已存在时不会新增
    列——违例态必须从建表那一刻构造，正如真实 pipeline 整表入暂存再收口）。"""
    db = PlatformDB(path)
    db.upsert("daily", pl.DataFrame({
        "trade_date": ["20240102", "20240103"],
        "ts_code": ["000001.SZ", "000001.SZ"],
        "close": [10.0, 11.0],
        **{c: [1.0, 1.0] for c in bad_cols},
    }), keys=["trade_date", "ts_code"])
    db.upsert("trade_cal", pl.DataFrame({"cal_date": ["20240102"], "is_open": [1]}), keys=[])
    return db


# ---------------- 纯函数层（无 DB，双腿同语义） ----------------

def test_pure_fn_surface_list_matches_engine_read_tables():
    """ENGINE_SURFACE_TABLES 是 engine 读路径盘点快照（daily/daily_basic/adj_factor/
    index_daily/stock_basic/trade_cal/stock_st/stk_limit/suspend_d）。"""
    assert set(ENGINE_SURFACE_TABLES) == {
        "daily", "daily_basic", "adj_factor", "index_daily", "stock_basic",
        "trade_cal", "stock_st", "stk_limit", "suspend_d",
    }


def test_pure_fn_clean_surface_no_violations():
    assert validate_surface_columns({
        "daily": ["trade_date", "ts_code", "close", "volume", "pct_chg"],
        "stock_basic": ["symbol", "ts_code", "industry"],
    }) == []


def test_pure_fn_internal_name_rejected():
    """引擎内部名（__factorlab_* / in_universe）作为读面列 → 违例 + 修法指引。"""
    out = validate_surface_columns({
        "daily": ["trade_date", "ts_code", "close", "in_universe"],
    })
    assert len(out) == 1
    assert "daily" in out[0] and "in_universe" in out[0]
    assert "内部" in out[0]            # 指明命名空间冲突
    assert "in_universe" in out[0]
    assert "重命名" in out[0] or "移出" in out[0]  # 有修法指引，不是一句空报


def test_pure_fn_internal_prefix_column_rejected():
    out = validate_surface_columns({"daily": ["__factorlab_universe_active"]})
    assert len(out) == 1 and "__factorlab_universe_active" in out[0]


def test_pure_fn_future_prefixed_columns_rejected():
    """未来前缀列（forward_*/future_* 精确前缀 + target/label 精确名）→ 违例。"""
    for bad in ("forward_return_5d", "future_gain", "target", "label"):
        out = validate_surface_columns({"daily": ["trade_date", bad]})
        assert len(out) == 1, bad
        assert bad in out[0]
        assert "未来" in out[0]
        assert "forward_*" in out[0] or "未来前缀" in out[0]


def test_pure_fn_non_surface_tables_ignored():
    """读面之外的表（如 moneyflow）不受纪律约束——不误伤研究/写入面表。"""
    out = validate_surface_columns({
        "daily": ["trade_date", "close"],
        _NON_SURFACE: ["in_universe", "forward_return_5d"],
    })
    assert out == []


def test_pure_fn_all_surface_tables_checked():
    """纪律对每个引擎读面表逐表生效（不只 daily）。"""
    out = validate_surface_columns({t: ["in_universe"] for t in ENGINE_SURFACE_TABLES})
    assert len(out) == len(ENGINE_SURFACE_TABLES)
    for t in ENGINE_SURFACE_TABLES:
        assert any(f"{t}" in v for v in out)


def test_pure_fn_multi_violations_listed():
    out = validate_surface_columns({
        "daily": ["trade_date", "in_universe", "forward_return_5d"],
        "stock_basic": ["symbol", "label"],
    })
    assert len(out) == 3


# ---------------- Rd 层双腿（duckdb|ch 同一函数同一语义） ----------------

def test_engine_surface_clean_on_rd(env):
    env.seed({
        "daily": ([("trade_date", "date"), ("ts_code", "str"), ("close", "f64")],
                  [("20240102", "000001", 10.0)]),
    })
    assert validate_engine_surface(env.rd) == []


def test_engine_surface_reports_violations_via_rd(env):
    """读面实探（rd.columns）驱动——双腿同文案。"""
    env.seed({
        "daily": ([("trade_date", "date"), ("ts_code", "str"), ("close", "f64"),
                   ("in_universe", "f64"), ("forward_return_5d", "f64")],
                  [("20240102", "000001", 10.0, 1.0, 0.05)]),
    })
    out = validate_engine_surface(env.rd)
    assert len(out) == 2
    joined = " | ".join(out)
    assert "in_universe" in joined and "forward_return_5d" in joined


def test_engine_surface_repair_returns_clean(env):
    """修复（移除违例列）后重探 → 零违例（校验不是一次性状态）。"""
    env.seed({
        "daily": ([("trade_date", "date"), ("ts_code", "str"), ("close", "f64"),
                   ("in_universe", "f64")],
                  [("20240102", "000001", 10.0, 1.0)]),
    })
    assert len(validate_engine_surface(env.rd)) == 1
    env.seed({  # 同文件再态：移除违例列
        "daily": ([("trade_date", "date"), ("ts_code", "str"), ("close", "f64")],
                  [("20240102", "000001", 10.0)]),
    })
    assert validate_engine_surface(env.rd) == []


# ---------------- verify_all 报告（平台库完整性链路） ----------------

def test_verify_all_clean_reports_empty_discipline(tmp_path):
    report = verify_all(_mk_clean_db(tmp_path / "p.duckdb"))
    assert report["column_discipline"] == []


def test_verify_all_reports_violations(tmp_path):
    db = _mk_violating_db(tmp_path / "p.duckdb", ["in_universe", "forward_return_5d"])
    report = verify_all(db)
    assert len(report["column_discipline"]) == 2
    assert "in_universe" in report["column_discipline"][0]


def test_verify_all_empty_db_discipline_empty(tmp_path):
    report = verify_all(PlatformDB(tmp_path / "empty.duckdb"))
    assert report["column_discipline"] == []


# ---------------- 重建收口（build_final_db 拒绝违例读面） ----------------

def test_build_final_db_rejects_violating_surface(tmp_path):
    """数据入库收口：最终库读面带违例列 → build_final_db 抛错点名（fail fast，
    不产出会污染读面的最终库）。"""
    from factorlab.data.rebuild import build_final_db

    staging = PlatformDB(tmp_path / "staging.duckdb")
    staging.upsert("daily", pl.DataFrame({
        "trade_date": ["20240102"],
        "ts_code": ["000001.SZ"],
        "close": [10.0],
        "in_universe": [1.0],
    }), keys=["trade_date", "ts_code"])
    final_path = tmp_path / "final.duckdb"
    with pytest.raises(ValueError, match="in_universe"):
        build_final_db(staging, final_path)
    assert not final_path.exists()  # fail fast：不产出会污染读面的最终库

    # 修复后重建成功（staging 换成不带违例列的全新整表——真实 pipeline 中违例
    # 来自上游入库，修复在上游重灌，最终库收口随之放行）
    clean_staging = PlatformDB(tmp_path / "staging_clean.duckdb")
    clean_staging.upsert("daily", pl.DataFrame({
        "trade_date": ["20240102"],
        "ts_code": ["000001.SZ"],
        "close": [10.0],
    }), keys=["trade_date", "ts_code"])
    report = build_final_db(clean_staging, final_path)
    assert "daily" in report["tables"]
