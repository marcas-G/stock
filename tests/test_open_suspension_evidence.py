"""M8-02B：open suspension evidence integration——loader 行为 + source contract。

Loader 从 suspend_d 读取事件行（ts_code/suspend_type/suspend_timing），
经 factorlab.execution.suspension（唯一 temporal authority）推导
is_suspended_at_open；runtime 重新 enforce production source contract：

- suspend_type ∈ {S, R}（不 strip/不 upper）
- timing absence = NULL only；non-null 必须 parse 成功（ValueError 穿透）
- R + non-null timing = source 未证明组合 → fail
- exact duplicate（type, timing）collapse；dedup 后 >1 distinct event → fail
  （production max distinct = 1；未知结构 fail fast，不发明 precedence）

Loader 库测试双腿参数化（env：duckdb|ch，见 tests/conftest.py）；suspend 值经
"str?"（Nullable）描述建模——NULL type/timing 在 ch 腿 = Nullable 列 NULL。
duckdb 文件语义（loader 只读不改库）与跨实例复现性留 duckdb 单腿；
domain/source-audit 测试不触库，保持单腿。
"""

import datetime

import duckdb
import polars as pl
import pytest

from factorlab.data.backend import open_read
from factorlab.data.execution import load_market_open_frame
from factorlab.domain import MarketOpenSnapshot
from factorlab.execution import load_market_open_snapshot

EXEC = datetime.date(2024, 1, 8)
D = EXEC.strftime("%Y%m%d")
FILLER = "601111.SH"   # 无 suspend 事件的填充证券（保持 requested skeleton）

CODES = ["000001.SZ", "600000.SH", "601111.SH"]

_DAILY_COLS = [("trade_date", "date"), ("ts_code", "str"), ("open", "f64"),
               ("pre_close", "f64")]
_LIMIT_COLS = [("trade_date", "date"), ("ts_code", "str"), ("up_limit", "f64"),
               ("down_limit", "f64")]
_CAL_COLS = [("cal_date", "date"), ("is_open", "i64")]


def _seed(env, *, suspends=None, with_timing_col=True, with_type_col=True):
    """daily/stk_limit 各一行（FILLER）+ 可选 suspend_d rows + trade_cal(D open)。"""
    tables = {"trade_cal": (_CAL_COLS, [(D, 1)]),
              "daily": (_DAILY_COLS, [(D, FILLER, 10.0, 9.8)]),
              "stk_limit": (_LIMIT_COLS, [(D, FILLER, 11.0, 9.0)])}
    cols = [("trade_date", "date"), ("ts_code", "str")]
    if with_type_col:
        cols.append(("suspend_type", "str?"))
    if with_timing_col:
        cols.append(("suspend_timing", "str?"))
    tables["suspend_d"] = (cols, list(suspends or []))
    env.seed(tables)


def _load(env, *, suspends=None, with_timing_col=True, with_type_col=True,
          codes=None):
    _seed(env, suspends=suspends, with_timing_col=with_timing_col,
          with_type_col=with_type_col)
    return load_market_open_snapshot(env.rd, execution_date=EXEC,
                                     codes=codes or CODES)


def _row(snap, code):
    return snap.frame.filter(pl.col("code") == code).row(0)


# ---------------- no event / basic semantics ----------------

def test_no_event_false_false(env):
    snap = _load(env)
    r = _row(snap, FILLER)
    assert r[7] is False and r[8] is False      # has_suspend_record / is_suspended_at_open


def test_s_null_true_true(env):
    snap = _load(env, suspends=[(D, "000001.SZ", "S", None)])
    r = _row(snap, "000001.SZ")
    assert r[7] is True and r[8] is True


def test_r_null_true_false(env):
    snap = _load(env, suspends=[(D, "000001.SZ", "R", None)])
    r = _row(snap, "000001.SZ")
    assert r[7] is True and r[8] is False


def test_same_session_covering_open(env):
    snap = _load(env, suspends=[(D, "000001.SZ", "S", "09:30-10:00")])
    r = _row(snap, "000001.SZ")
    assert r[7] is True and r[8] is True


def test_later_intraday(env):
    snap = _load(env, suspends=[(D, "000001.SZ", "S", "10:00-10:30")])
    r = _row(snap, "000001.SZ")
    assert r[7] is True and r[8] is False


def test_full_cycle(env):
    snap = _load(env, suspends=[(D, "000001.SZ", "S", "09:30-09:30")])
    r = _row(snap, "000001.SZ")
    assert r[7] is True and r[8] is True


def test_wrapped_not_covering(env):
    snap = _load(env, suspends=[(D, "000001.SZ", "S", "13:00-9:30")])
    r = _row(snap, "000001.SZ")
    assert r[7] is True and r[8] is False


def test_wrapped_covering_open(env):
    snap = _load(env, suspends=[(D, "000001.SZ", "S", "13:00-10:00")])
    r = _row(snap, "000001.SZ")
    assert r[7] is True and r[8] is True


def test_multi_interval_covering(env):
    snap = _load(env, suspends=[(D, "000001.SZ", "S", "09:30-10:31,10:31-14:57")])
    r = _row(snap, "000001.SZ")
    assert r[7] is True and r[8] is True


def test_multi_interval_not_covering(env):
    snap = _load(env, suspends=[(D, "000001.SZ", "S", "10:00-10:30,13:00-14:00")])
    r = _row(snap, "000001.SZ")
    assert r[7] is True and r[8] is False


# ---------------- duplicate / multiplicity contract ----------------

def test_exact_duplicate_collapse(env):
    snap = _load(env, suspends=[(D, "000001.SZ", "S", "09:30-10:00"),
                                (D, "000001.SZ", "S", "09:30-10:00")])
    f = snap.frame.filter(pl.col("code") == "000001.SZ")
    assert f.height == 1 and f["has_suspend_record"][0] and f["is_suspended_at_open"][0]


def test_distinct_duplicate_fails(env):
    with pytest.raises(ValueError, match="distinct|多事件|multi"):
        _load(env, suspends=[(D, "000001.SZ", "S", None),
                             (D, "000001.SZ", "R", None)])


def test_two_distinct_s_events_fail(env):
    with pytest.raises(ValueError, match="distinct|多事件|multi"):
        _load(env, suspends=[(D, "000001.SZ", "S", "09:30-10:00"),
                             (D, "000001.SZ", "S", "13:00-14:00")])


def test_r_with_timing_fails(env):
    with pytest.raises(ValueError, match="R|timing|source"):
        _load(env, suspends=[(D, "000001.SZ", "R", "09:30-10:00")])


# ---------------- invalid source values ----------------

@pytest.mark.parametrize("bad_type", [None, "", "X", "S ", " R"])
def test_invalid_suspend_type_fails(env, bad_type):
    with pytest.raises(ValueError, match="suspend_type"):
        _load(env, suspends=[(D, "000001.SZ", bad_type, None)])


def test_invalid_timing_propagates(env):
    """S/foo → parser ValueError 向上穿透（不转为 record=True/open=False）。"""
    with pytest.raises(ValueError, match="segment|timing|suspend"):
        _load(env, suspends=[(D, "000001.SZ", "S", "foo")])


def test_empty_string_timing_fails(env):
    with pytest.raises(ValueError):
        _load(env, suspends=[(D, "000001.SZ", "S", "")])


# ---------------- schema contract（M8-02B0 runtime enforcement） ----------------

def test_missing_suspend_timing_column_fails(env):
    with pytest.raises(ValueError, match="suspend_timing"):
        _load(env, with_timing_col=False,
              suspends=[(D, "000001.SZ", "S")])


def test_missing_suspend_type_column_fails(env):
    with pytest.raises(ValueError, match="suspend_type"):
        _load(env, with_type_col=False,
              suspends=[(D, "000001.SZ", "09:30-10:00")])


def test_skeleton_rows_equal_codes(env):
    snap = _load(env, suspends=[(D, "000001.SZ", "S", None),
                                (D, "600000.SH", "R", None)])
    assert snap.frame.height == 3
    assert snap.frame["code"].to_list() == sorted(CODES)


def test_row_order_determinism(tmp_path):
    """exact duplicate 行序变化 → snapshot equals。

    跨 DB 实例复现性——duckdb 单腿（同一数据描述 seed 两个独立文件比对）。
    """
    import dualbridge

    tables = {"trade_cal": (_CAL_COLS, [(D, 1)]),
              "daily": (_DAILY_COLS, [(D, FILLER, 10.0, 9.8)]),
              "stk_limit": (_LIMIT_COLS, [(D, FILLER, 11.0, 9.0)]),
              "suspend_d": ([("trade_date", "date"), ("ts_code", "str"),
                             ("suspend_type", "str?"), ("suspend_timing", "str?")],
                            [(D, "000001.SZ", "S", "09:30-10:00"),
                             (D, "000001.SZ", "S", "09:30-10:00")])}
    p1 = tmp_path / "d1" / "s.duckdb"
    p2 = tmp_path / "d2" / "s.duckdb"
    p1.parent.mkdir(parents=True)
    p2.parent.mkdir(parents=True)
    dualbridge.seed_duckdb(p1, tables)
    dualbridge.seed_duckdb(p2, tables)
    a = load_market_open_snapshot(open_read(db_path=p1), execution_date=EXEC,
                                  codes=CODES)
    b = load_market_open_snapshot(open_read(db_path=p2), execution_date=EXEC,
                                  codes=CODES)
    assert a.frame.equals(b.frame)


def test_loader_does_not_modify_db(tmp_path):
    """loader 只读：不写库（duckdb 文件语义单腿——rw 打开数行再只读重开比对）。"""
    import dualbridge

    tables = {"trade_cal": (_CAL_COLS, [(D, 1)]),
              "daily": (_DAILY_COLS, [(D, FILLER, 10.0, 9.8)]),
              "stk_limit": (_LIMIT_COLS, [(D, FILLER, 11.0, 9.0)]),
              "suspend_d": ([("trade_date", "date"), ("ts_code", "str"),
                             ("suspend_type", "str?"), ("suspend_timing", "str?")],
                            [(D, "000001.SZ", "S", "09:30-10:00")])}
    p = tmp_path / "s.duckdb"
    dualbridge.seed_duckdb(p, tables)
    load_market_open_frame(open_read(db_path=p), execution_date=EXEC, codes=CODES)
    con = duckdb.connect(p, read_only=True)
    after = con.execute("SELECT count(*) FROM suspend_d").fetchone()[0]
    con.close()
    assert after == 1


def test_import_no_cycle():
    """data.execution → execution.suspension 无循环（parser 仅 stdlib import）。"""
    import inspect
    import re
    from factorlab.execution.suspension import parse_suspend_timing
    mod = inspect.getmodule(parse_suspend_timing)
    src = inspect.getsource(mod)
    for forbidden in ("duckdb", "polars", "platform_db", "MarketOpenSnapshot",
                      "OrderBatch", "PortfolioState"):
        assert not re.search(rf"^\s*(import|from)\s+{forbidden}", src, re.M), \
            f"suspension.py 不得 import {forbidden}"


def test_empty_codes_typed_empty_9_columns(env):
    _seed(env)
    frame = load_market_open_frame(env.rd, execution_date=EXEC, codes=[])
    assert frame.columns == ["code", "open", "pre_close", "up_limit", "down_limit",
                             "has_daily", "has_limit", "has_suspend_record",
                             "is_suspended_at_open"]
    assert frame.schema["is_suspended_at_open"] == pl.Boolean
    assert frame.height == 0


# ---------------- domain（MarketOpenSnapshot 9 列契约） ----------------

def _mk_snapshot(records=(True, True), open_flags=(False, False), flags_daily=None,
                 flags_limit=None):
    """手工构造 9 列 snapshot（绕过 loader 测 domain）。"""
    codes = ["000001.SZ", "600000.SH"]
    rows = []
    for i, c in enumerate(codes):
        fd = flags_daily[i] if flags_daily else (True, True)[i]
        fl = flags_limit[i] if flags_limit else (False, False)[i]
        fr = records[i] if records else False
        fo = open_flags[i] if open_flags else False
        open_ = 10.0 if fd else None
        pc = 9.8 if fd else None
        up = 11.0 if fl else None
        dn = 9.0 if fl else None
        rows.append((c, open_, pc, up, dn, fd, fl, fr, fo))
    frame = pl.DataFrame(rows, schema=["code", "open", "pre_close", "up_limit",
                                       "down_limit", "has_daily", "has_limit",
                                       "has_suspend_record", "is_suspended_at_open"],
                         orient="row")
    frame = frame.with_columns(
        pl.col("open").cast(pl.Float64), pl.col("pre_close").cast(pl.Float64),
        pl.col("up_limit").cast(pl.Float64), pl.col("down_limit").cast(pl.Float64),
        pl.col("has_daily").cast(pl.Boolean), pl.col("has_limit").cast(pl.Boolean),
        pl.col("has_suspend_record").cast(pl.Boolean),
        pl.col("is_suspended_at_open").cast(pl.Boolean))
    return MarketOpenSnapshot(execution_date=EXEC, frame=frame)


def test_domain_legal_combinations():
    assert _mk_snapshot(records=(False, False), open_flags=(False, False)).frame.height == 2
    assert _mk_snapshot(records=(True, True), open_flags=(False, False)).frame.height == 2
    assert _mk_snapshot(records=(True, True), open_flags=(True, True)).frame.height == 2


def test_domain_illegal_record_false_open_true():
    with pytest.raises(ValueError, match="has_suspend_record|implication|implies"):
        _mk_snapshot(records=(False, False), open_flags=(True, False))


def test_domain_missing_column_fails():
    frame = _mk_snapshot(records=(False, False)).frame.drop("is_suspended_at_open")
    with pytest.raises(ValueError, match="9|columns|is_suspended"):
        MarketOpenSnapshot(execution_date=EXEC, frame=frame)


def test_domain_extra_column_fails():
    frame = _mk_snapshot(records=(False, False)).frame.with_columns(
        pl.Series([False, False], dtype=pl.Boolean).alias("can_trade"))
    with pytest.raises(ValueError, match="columns|can_trade|is_tradable"):
        MarketOpenSnapshot(execution_date=EXEC, frame=frame)


@pytest.mark.parametrize("dtype", [pl.Int64, pl.String, pl.Float64])
def test_domain_wrong_dtype_fails(dtype):
    frame = _mk_snapshot(records=(False, False)).frame.with_columns(
        pl.Series([0, 0]).cast(dtype).alias("is_suspended_at_open"))
    with pytest.raises(ValueError, match="Boolean"):
        MarketOpenSnapshot(execution_date=EXEC, frame=frame)


def test_domain_null_flag_fails():
    frame = _mk_snapshot(records=(True, True)).frame.with_columns(
        pl.Series([True, None], dtype=pl.Boolean).alias("is_suspended_at_open"))
    with pytest.raises(ValueError, match="is_suspended_at_open|Boolean|null"):
        MarketOpenSnapshot(execution_date=EXEC, frame=frame)
