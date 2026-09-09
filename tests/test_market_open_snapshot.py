"""M8-02：MarketOpenSnapshot + load_market_open_frame/snapshot。

库数据测试双腿参数化（env：duckdb|ch，见 tests/conftest.py）；daily/stk_limit/
suspend_d 无 stock_basic——M8 契约输入恒为 canonical ts_code，SQL 精确匹配无需
两层 IN。domain 测试（_mk_snapshot 手工构造）不触库，保持单腿。
"""

import datetime
from dataclasses import FrozenInstanceError

import polars as pl
import pytest

from factorlab.data.execution import load_market_open_frame
from factorlab.domain import MarketOpenSnapshot
from factorlab.data.backend import open_read
from factorlab.execution import load_market_open_snapshot

EXEC = datetime.date(2024, 1, 8)
PREV = datetime.date(2024, 1, 5)

CODES = ["000001.SZ", "600000.SH", "600519.SH"]

_DAILY_COLS = [("trade_date", "date"), ("ts_code", "str"), ("open", "f64"),
               ("pre_close", "f64")]
_LIMIT_COLS = [("trade_date", "date"), ("ts_code", "str"), ("up_limit", "f64"),
               ("down_limit", "f64")]
_SUSPEND_COLS = [("trade_date", "date"), ("ts_code", "str"),
                 ("suspend_type", "str"), ("suspend_timing", "str?")]
_CAL_COLS = [("cal_date", "date"), ("is_open", "i64")]

_D = EXEC.strftime("%Y%m%d")
_P = PREV.strftime("%Y%m%d")


def _seed(env, *, daily=(), limits=(), suspends=(), with_daily=True,
          with_limit=True, with_suspend=True):
    """trade_cal(EXEC/PREV open) + 可选 daily/stk_limit/suspend_d 表灌入。"""
    tables = {"trade_cal": (_CAL_COLS, [(_D, 1), (_P, 1)])}
    if with_daily:
        tables["daily"] = (_DAILY_COLS, list(daily))
    if with_limit:
        tables["stk_limit"] = (_LIMIT_COLS, list(limits))
    if with_suspend:
        tables["suspend_d"] = (_SUSPEND_COLS, list(suspends))
    env.seed(tables)


def _golden_tables():
    """§74 golden：000001 全证据、600000 仅 suspend、600519 全证据（全表描述）。"""
    return {
        "trade_cal": (_CAL_COLS, [(_D, 1), (_P, 1)]),
        "daily": (_DAILY_COLS, [(_D, "000001.SZ", 10.0, 9.8),
                                (_D, "600519.SH", 100.0, 99.0)]),
        "stk_limit": (_LIMIT_COLS, [(_D, "000001.SZ", 10.78, 8.82),
                                    (_D, "600519.SH", 108.9, 89.1)]),
        "suspend_d": (_SUSPEND_COLS, [(_D, "600000.SH", "S", None)]),
    }


def _seed_golden(env):
    env.seed(_golden_tables())


# ---------------- golden ----------------

def test_golden_three_code(env):
    _seed_golden(env)
    snap = load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)
    f = snap.frame
    assert f.height == 3
    r1 = f.filter(pl.col("code") == "000001.SZ")
    assert r1["has_daily"][0] and r1["has_limit"][0] and not r1["has_suspend_record"][0]
    assert r1["open"][0] == 10.0 and r1["pre_close"][0] == 9.8
    assert r1["up_limit"][0] == 10.78 and r1["down_limit"][0] == 8.82
    r2 = f.filter(pl.col("code") == "600000.SH")
    assert not r2["has_daily"][0] and not r2["has_limit"][0] and r2["has_suspend_record"][0]
    assert r2["open"][0] is None and r2["up_limit"][0] is None
    r3 = f.filter(pl.col("code") == "600519.SH")
    assert r3["has_daily"][0] and r3["has_limit"][0] and not r3["has_suspend_record"][0]


# ---------------- schema / dtypes ----------------

def test_exact_schema(env):
    _seed_golden(env)
    snap = load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)
    assert snap.frame.columns == ["code", "open", "pre_close", "up_limit",
                                  "down_limit", "has_daily", "has_limit",
                                  "has_suspend_record", "is_suspended_at_open"]
    assert snap.frame.schema["open"] == pl.Float64
    assert snap.frame.schema["pre_close"] == pl.Float64
    assert snap.frame.schema["up_limit"] == pl.Float64
    assert snap.frame.schema["down_limit"] == pl.Float64
    assert snap.frame.schema["has_daily"] == pl.Boolean
    assert snap.frame.schema["has_limit"] == pl.Boolean
    assert snap.frame.schema["has_suspend_record"] == pl.Boolean
    assert snap.frame.schema["is_suspended_at_open"] == pl.Boolean
    assert snap.execution_date == EXEC


def test_canonical_code_guard(env):
    _seed_golden(env)
    with pytest.raises(ValueError):
        load_market_open_snapshot(env.rd, execution_date=EXEC, codes=["000001"])


def test_duplicate_input_code_fails(env):
    _seed_golden(env)
    with pytest.raises(ValueError):
        load_market_open_snapshot(env.rd, execution_date=EXEC,
                                  codes=["000001.SZ", "000001.SZ"])


def test_input_order_invariant(env):
    _seed_golden(env)
    a = load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)
    b = load_market_open_snapshot(env.rd, execution_date=EXEC,
                                  codes=list(reversed(CODES)))
    assert a.frame.equals(b.frame)


# ---------------- duplicate policies ----------------

def test_daily_duplicate_fails(env):
    d = (_D, "000001.SZ", 10.0, 9.8)
    _seed(env, daily=[d, d], limits=[(_D, "600000.SH", 100.0, 90.0)])
    with pytest.raises(ValueError, match="重复|duplicate"):
        load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)


def test_limit_duplicate_fails(env):
    l = (_D, "000001.SZ", 10.78, 8.82)
    _seed(env, daily=[(_D, "000001.SZ", 10.0, 9.8)], limits=[l, l])
    with pytest.raises(ValueError, match="重复|duplicate"):
        load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)


def test_suspend_duplicates_collapse(env):
    _seed(env, daily=[(_D, "000001.SZ", 10.0, 9.8)],
           limits=[(_D, "600000.SH", 100.0, 90.0)],
           suspends=[(_D, "600000.SH", "S", None),
                     (_D, "600000.SH", "S", None)])
    snap = load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)
    r = snap.frame.filter(pl.col("code") == "600000.SH")
    assert r.height == 1 and r["has_suspend_record"][0]


# ---------------- missing-data semantics ----------------

def test_single_code_missing_daily_represented(env):
    """600000 无 daily → has_daily=False 且 open=null（不 drop、不自动 suspend）。"""
    _seed(env, daily=[(_D, "000001.SZ", 10.0, 9.8)],
          limits=[(_D, "600000.SH", 100.0, 90.0)])
    snap = load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)
    f = snap.frame
    assert f.height == 3
    r = f.filter(pl.col("code") == "600000.SH")
    assert not r["has_daily"][0] and not r["has_suspend_record"][0]
    assert r["open"][0] is None and r["pre_close"][0] is None


def test_single_code_missing_limit_represented(env):
    _seed(env, daily=[(_D, "000001.SZ", 10.0, 9.8)],
          limits=[(_D, "600000.SH", 100.0, 90.0)])
    snap = load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)
    r = snap.frame.filter(pl.col("code") == "000001.SZ")
    assert r["has_limit"][0] is False and r["up_limit"][0] is None


def test_zero_suspend_rows_valid(env):
    _seed_golden(env)
    snap = load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)
    assert snap.frame["has_suspend_record"].sum() == 1


# ---------------- invariants / raw price ----------------

def test_raw_open_exactness(env):
    _seed(env, daily=[(_D, "000001.SZ", 12.345678, 9.8)],
          limits=[(_D, "600000.SH", 100.0, 90.0)])
    snap = load_market_open_snapshot(env.rd, execution_date=EXEC,
                                     codes=["000001.SZ"])
    assert snap.frame["open"][0] == 12.345678


def test_has_daily_true_requires_finite_positive(env):
    """M8-04B：invalid daily evidence → ExecutionDataQualityError（data quality，
    非结构错误）。4 个坏值各占一个 code、逐 code 请求（同库分请求隔离）。"""
    from factorlab.domain import ExecutionDataQualityError
    bad_codes = ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"]
    _seed(env,
          daily=[(_D, c, bad, 9.8) for c, bad in zip(bad_codes,
                                                     (0.0, -1.0, float("nan"),
                                                      float("inf")))],
          limits=[(_D, "600000.SH", 100.0, 90.0)])
    for c in bad_codes:
        with pytest.raises(ExecutionDataQualityError):
            load_market_open_snapshot(env.rd, execution_date=EXEC, codes=[c])


def test_has_limit_true_invariant(env):
    """M8-04B：invalid limit evidence（down > up）→ ExecutionDataQualityError。"""
    from factorlab.domain import ExecutionDataQualityError
    _seed(env, daily=[(_D, "000001.SZ", 10.0, 9.8)],
          limits=[(_D, "000001.SZ", 8.0, 10.0)])   # down > up
    with pytest.raises(ExecutionDataQualityError, match="down|up"):
        load_market_open_snapshot(env.rd, execution_date=EXEC, codes=["000001.SZ"])


# ---------------- coverage gates / required tables ----------------

def test_missing_daily_table_fails(env):
    _seed(env, with_daily=False)
    with pytest.raises(ValueError, match="daily"):
        load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)


def test_missing_stk_limit_table_fails(env):
    _seed(env, with_limit=False)
    with pytest.raises(ValueError, match="stk_limit"):
        load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)


def test_missing_suspend_d_table_optional(env):
    """WS4：suspend_d 表不存在 → 事件证据全 False（停牌 = 缺行推断，事件表
    不再被要求——不 fail），daily/stk_limit flags 照常。"""
    _seed(env, with_suspend=False,
          daily=[(_D, "000001.SZ", 10.0, 9.8)],
          limits=[(_D, "600000.SH", 100.0, 90.0)])
    snap = load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)
    f = snap.frame
    assert f.height == 3
    assert f["has_suspend_record"].sum() == 0
    assert f["is_suspended_at_open"].sum() == 0
    r = f.filter(pl.col("code") == "000001.SZ")
    assert r["has_daily"][0] and r["open"][0] == 10.0


def test_non_open_execution_date_fails(env):
    _seed_golden(env)
    with pytest.raises(ValueError, match="开放|open"):
        load_market_open_snapshot(env.rd, execution_date=datetime.date(2024, 1, 6),
                                  codes=CODES)


def test_global_daily_coverage_zero_fails(env):
    """trade_cal 当天开市但 daily 全市场 0 行 → fail（不假装全停牌）。"""
    _seed(env, daily=[], limits=[])
    with pytest.raises(ValueError, match="coverage"):
        load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)


def test_global_limit_coverage_zero_fails(env):
    _seed(env, daily=[(_D, "000001.SZ", 10.0, 9.8)], limits=[])
    with pytest.raises(ValueError, match="stk_limit.*coverage|coverage"):
        load_market_open_snapshot(env.rd, execution_date=EXEC,
                                  codes=["000001.SZ"])


def test_typed_empty_snapshot(env):
    _seed_golden(env)
    snap = load_market_open_snapshot(env.rd, execution_date=EXEC, codes=[])
    assert snap.frame.height == 0
    assert snap.frame.schema["open"] == pl.Float64
    assert snap.frame.schema["has_daily"] == pl.Boolean
    assert snap.frame.schema["is_suspended_at_open"] == pl.Boolean


def test_all_null_numeric_columns_float64(env):
    """所有请求证券都无 limit → up/down 全 null 仍 Float64（非 Null dtype）。"""
    _seed(env, daily=[(_D, "000001.SZ", 10.0, 9.8)],
          limits=[(_D, "600000.SH", 100.0, 90.0)])
    snap = load_market_open_snapshot(env.rd, execution_date=EXEC,
                                     codes=["000001.SZ"])
    assert snap.frame.schema["up_limit"] == pl.Float64
    assert snap.frame["up_limit"].null_count() == 1


def test_row_order_invariant(tmp_path):
    """daily/limit/suspend 行序变化 → snapshot frame.equals 相同。

    跨 DB 实例复现性——duckdb 单腿（同一数据描述 seed 两个独立文件比对；
    ch 腿单实例内由查询语义天然确定，等同质行为由其余双腿测试覆盖）。
    """
    import dualbridge

    p1 = tmp_path / "d1" / "m.duckdb"
    p2 = tmp_path / "d2" / "m.duckdb"
    p1.parent.mkdir(parents=True)
    p2.parent.mkdir(parents=True)
    dualbridge.seed_duckdb(p1, _golden_tables())
    dualbridge.seed_duckdb(p2, _golden_tables())
    a = load_market_open_snapshot(open_read(db_path=p1), execution_date=EXEC,
                                  codes=CODES)
    b = load_market_open_snapshot(open_read(db_path=p2), execution_date=EXEC,
                                  codes=CODES)
    assert a.frame.equals(b.frame)


def test_frozen_snapshot(env):
    _seed_golden(env)
    snap = load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)
    with pytest.raises(FrozenInstanceError):
        snap.frame = pl.DataFrame()


def test_no_is_tradable_field(env):
    _seed_golden(env)
    snap = load_market_open_snapshot(env.rd, execution_date=EXEC, codes=CODES)
    assert "is_tradable" not in snap.frame.columns
    assert "can_buy" not in snap.frame.columns


def test_data_layer_frame(env):
    """load_market_open_frame 返回 9 列原始 frame（不经 domain）。"""
    _seed_golden(env)
    frame = load_market_open_frame(env.rd, execution_date=EXEC, codes=CODES)
    assert frame.columns == ["code", "open", "pre_close", "up_limit",
                             "down_limit", "has_daily", "has_limit",
                             "has_suspend_record", "is_suspended_at_open"]
    assert frame.height == 3


# ================================================================
# M8-02A：MarketOpenSnapshot evidence non-null Boolean hardening
# ================================================================

def _mk_snapshot(flags_daily=None, flags_limit=None, flags_suspend=None,
                 flags_open=None, with_daily_ok=True, with_limit_ok=True):
    """手工构造 snapshot frame（绕过 loader 测 domain 独立防护）。"""
    codes = ["000001.SZ", "600000.SH"]
    rows = []
    for i, c in enumerate(codes):
        fd = flags_daily[i] if flags_daily else (with_daily_ok, False)[i]
        fl = flags_limit[i] if flags_limit else (with_limit_ok, False)[i]
        fs = flags_suspend[i] if flags_suspend else (False, False)[i]
        fo = flags_open[i] if flags_open else (False, False)[i]
        open_ = 10.0 if fd else None
        pc = 9.8 if fd else None
        up = 11.0 if fl else None
        dn = 9.0 if fl else None
        rows.append((c, open_, pc, up, dn, fd, fl, fs, fo))
    frame = pl.DataFrame(rows, schema=["code", "open", "pre_close", "up_limit",
                                       "down_limit", "has_daily", "has_limit",
                                       "has_suspend_record",
                                       "is_suspended_at_open"], orient="row")
    frame = frame.with_columns(
        pl.col("open").cast(pl.Float64), pl.col("pre_close").cast(pl.Float64),
        pl.col("up_limit").cast(pl.Float64), pl.col("down_limit").cast(pl.Float64),
        pl.col("has_daily").cast(pl.Boolean), pl.col("has_limit").cast(pl.Boolean),
        pl.col("has_suspend_record").cast(pl.Boolean),
        pl.col("is_suspended_at_open").cast(pl.Boolean))
    return MarketOpenSnapshot(execution_date=EXEC, frame=frame)


def test_has_daily_null_fails():
    with pytest.raises(ValueError, match="has_daily|Boolean|null"):
        _mk_snapshot(flags_daily=[True, None])


def test_has_limit_null_fails():
    with pytest.raises(ValueError, match="has_limit|Boolean|null"):
        _mk_snapshot(flags_limit=[None, False])


def test_has_suspend_record_null_fails():
    with pytest.raises(ValueError, match="has_suspend_record|Boolean|null"):
        _mk_snapshot(flags_suspend=[False, None])


def test_mixed_null_evidence_fails_whole():
    """A: has_daily=True、B: has_daily=null——whole snapshot fail（不 drop B）。"""
    with pytest.raises(ValueError, match="has_daily|Boolean|null"):
        _mk_snapshot(flags_daily=[True, None])


def test_all_false_valid():
    s = _mk_snapshot(flags_daily=[False, False], flags_limit=[False, False],
                     flags_suspend=[False, False])
    assert s.frame.height == 2
    assert s.frame["open"].null_count() == 2


def test_all_true_valid():
    s = _mk_snapshot(flags_daily=[True, True], flags_limit=[True, True],
                     flags_suspend=[True, True])
    assert s.frame.height == 2
    assert s.frame["has_daily"].all() and s.frame["has_limit"].all()


# ================================================================
# M8-02B：is_suspended_at_open evidence flag hardening
# ================================================================

def test_open_suspended_null_fails():
    with pytest.raises(ValueError, match="is_suspended_at_open|Boolean|null"):
        _mk_snapshot(flags_open=[True, None], flags_suspend=[True, True])


def test_open_suspended_true_requires_record():
    """record=False / open=True 非法（implication invariant）。"""
    with pytest.raises(ValueError, match="has_suspend_record|implication|implies"):
        _mk_snapshot(flags_open=[True, False])


def test_record_true_open_false_legal():
    """record=True / open=False 合法（R/NULL、later intraday S）。"""
    s = _mk_snapshot(flags_suspend=[True, True])
    assert s.frame.height == 2
    assert s.frame["has_suspend_record"].all()
    assert not s.frame["is_suspended_at_open"].any()


def test_record_true_open_true_legal():
    """record=True / open=True 合法（S/NULL、S covering 09:30）。"""
    s = _mk_snapshot(flags_suspend=[True, True], flags_open=[True, True])
    assert s.frame["is_suspended_at_open"].all()
