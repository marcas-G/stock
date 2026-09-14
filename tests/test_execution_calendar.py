"""M8-02：ExecutionSchedule + resolve_execution_schedule（calendar resolver）。

库数据测试双腿参数化（env：duckdb|ch，见 tests/conftest.py）；纯 domain 测试
（_mk_schedule 等）与 rd 类型门测试不触库，保持单腿。
"""

import datetime
from dataclasses import FrozenInstanceError

import polars as pl
import pytest

from factorlab.core.domain import (ExecutionSchedule, TargetPortfolio,
                              TargetPortfolioMeta)
from factorlab.core.domain.timing import (DEFAULT_EOD_SIGNAL_TIMING,
                                     ExecutionTiming, SignalTiming,
                                     InformationCutoff, SignalAvailability)
from factorlab.execution import resolve_execution_schedule

D0 = datetime.date(2024, 1, 5)    # Fri open
D1 = datetime.date(2024, 1, 6)    # Sat
D2 = datetime.date(2024, 1, 7)    # Sun
D3 = datetime.date(2024, 1, 8)    # Mon open
D4 = datetime.date(2024, 1, 9)    # Tue open
D5 = datetime.date(2024, 1, 10)   # Wed open
NEXT_MON = datetime.date(2024, 1, 15)


def _cal_tables(open_dates):
    """执行层日历环境：trade_cal 行 + daily/stk_limit/suspend_d 空表（_require 探测）。"""
    return {
        "trade_cal": ([("cal_date", "date"), ("is_open", "i64")],
                      [(d.strftime("%Y%m%d"), 1) for d in open_dates]),
        "daily": ([("trade_date", "date"), ("ts_code", "str"),
                   ("open", "f64"), ("pre_close", "f64")], []),
        "stk_limit": ([("trade_date", "date"), ("ts_code", "str"),
                       ("up_limit", "f64"), ("down_limit", "f64")], []),
        "suspend_d": ([("trade_date", "date"), ("ts_code", "str"),
                       ("suspend_type", "str"), ("suspend_timing", "str")], []),
    }


def _cal(env, open_dates):
    env.seed(_cal_tables(open_dates))
    return env.rd


def _target(tmp_path, dates=(D0,), timing=DEFAULT_EOD_SIGNAL_TIMING,
            gross=1.0, positions_rows=None):
    meta = TargetPortfolioMeta(strategy_name="s", source_signal_name="alpha",
                               source_timing=timing, gross_exposure=gross)
    frame = (pl.DataFrame({"decision_date": pl.Series([d for d in dates for _ in range(2)],
                                                      dtype=pl.Date),
                           "code": pl.Series(["000001.SZ", "600000.SH"] * len(dates),
                                             dtype=pl.String),
                           "target_weight": pl.Series([0.5, 0.5] * len(dates),
                                                      dtype=pl.Float64)})
             if positions_rows is None else positions_rows)
    return TargetPortfolio(frame=frame, decision_dates=tuple(dates), meta=meta)


# ---------------- 基本解析 ----------------

def test_basic_next_open(env, tmp_path):
    cal = _cal(env, [D0, D3, D4])   # D1(周六)/D2(周日) 非 open
    s = resolve_execution_schedule(_target(tmp_path), cal)
    assert s.frame.height == 1
    assert s.frame["decision_date"][0] == D0
    assert s.frame["execution_date"][0] == D3
    assert s.frame["execution_timing"][0] == "next_open"


def test_weekend_skip(env, tmp_path):
    cal = _cal(env, [D0, D3, D4])   # D1(周六)/D2(周日) 非 open
    s = resolve_execution_schedule(_target(tmp_path, dates=[D0]), cal)
    assert s.frame["execution_date"][0] == D3


def test_multi_day_holiday(env, tmp_path):
    h0, h4 = datetime.date(2024, 2, 5), datetime.date(2024, 2, 9)
    cal = _cal(env, [h0, h4])   # 2/6-2/8 闭市
    s = resolve_execution_schedule(_target(tmp_path, dates=[h0]), cal)
    assert s.frame["execution_date"][0] == h4


def test_next_close_date_same_next_trading_day(env, tmp_path):
    cal = _cal(env, [D0, D3, D4])
    timing = SignalTiming(information_cutoff=InformationCutoff.CLOSE,
                          available_at=SignalAvailability.AFTER_CLOSE,
                          default_earliest_execution=ExecutionTiming.NEXT_CLOSE)
    s = resolve_execution_schedule(_target(tmp_path, timing=timing), cal)
    assert s.frame["execution_date"][0] == D3
    assert s.frame["execution_timing"][0] == "next_close"


def test_multiple_decisions(env, tmp_path):
    cal = _cal(env, [D0, D3, D4, D5])
    s = resolve_execution_schedule(_target(tmp_path, dates=[D0, D3, D4]), cal)
    assert s.frame["decision_date"].to_list() == [D0, D3, D4]
    assert s.frame["execution_date"].to_list() == [D3, D4, D5]


# ---------------- input guards ----------------

def test_target_type_guard(env, tmp_path):
    cal = _cal(env, [D0, D3])
    with pytest.raises((TypeError, ValueError)):
        resolve_execution_schedule({"decision_dates": [D0]}, cal)


def test_db_path_type_guard(env, tmp_path):
    cal = _cal(env, [D0, D3])
    with pytest.raises((TypeError, ValueError)):
        resolve_execution_schedule(_target(tmp_path), "not_a_path")


# ---------------- 边界 ----------------

def test_empty_target(env, tmp_path):
    cal = _cal(env, [D0, D3])
    meta = TargetPortfolioMeta(strategy_name="s", source_signal_name="alpha",
                               source_timing=DEFAULT_EOD_SIGNAL_TIMING,
                               gross_exposure=1.0)
    t = TargetPortfolio(frame=pl.DataFrame({"decision_date": pl.Series([], dtype=pl.Date),
                                            "code": pl.Series([], dtype=pl.String),
                                            "target_weight": pl.Series([], dtype=pl.Float64)}),
                        decision_dates=(), meta=meta)
    s = resolve_execution_schedule(t, cal)
    assert s.frame.height == 0
    assert s.frame.schema["decision_date"] == pl.Date
    assert s.frame.schema["execution_date"] == pl.Date
    assert s.frame.schema["execution_timing"] == pl.String


def test_all_cash_date_retained(env, tmp_path):
    """all-cash decision date（0 positions）仍产生 execution schedule。"""
    cal = _cal(env, [D0, D3, D4])
    f = pl.DataFrame({"decision_date": pl.Series([D0, D0, D3], dtype=pl.Date),
                      "code": pl.Series(["000001.SZ", "600000.SH", "000001.SZ"],
                                        dtype=pl.String),
                      "target_weight": pl.Series([0.5, 0.5, 1.0], dtype=pl.Float64)})
    t = TargetPortfolio(frame=f, decision_dates=(D0, D3), meta=TargetPortfolioMeta(
        strategy_name="s", source_signal_name="alpha",
        source_timing=DEFAULT_EOD_SIGNAL_TIMING, gross_exposure=1.0))
    s = resolve_execution_schedule(t, cal)
    assert s.frame["decision_date"].to_list() == [D0, D3]
    assert s.frame["execution_date"].to_list() == [D3, D4]


def test_weekly_target_only_fridays(env, tmp_path):
    """weekly target：只有 decision_dates（Fridays）进入 execution schedule。"""
    fri1, fri2 = datetime.date(2024, 1, 5), datetime.date(2024, 1, 12)
    mon, next_mon = datetime.date(2024, 1, 8), datetime.date(2024, 1, 15)
    cal = _cal(env, [fri1, mon, fri2, next_mon])
    s = resolve_execution_schedule(_target(tmp_path, dates=[fri1, fri2]), cal)
    assert s.frame["decision_date"].to_list() == [fri1, fri2]
    assert s.frame["execution_date"].to_list() == [mon, next_mon]


def test_non_open_decision_fails(env, tmp_path):
    """decision date 非开放交易日 → fail（不自动取 Monday）。"""
    cal = _cal(env, [D0, D3, D4])
    sat = datetime.date(2024, 1, 6)   # 不在 trade_cal open
    with pytest.raises(ValueError, match="开放交易日|open"):
        resolve_execution_schedule(_target(tmp_path, dates=[sat]), cal)


def test_no_next_open_fails(env, tmp_path):
    """decision 后无下一开放日 → fail whole（不 drop trailing）。"""
    cal = _cal(env, [D0])
    with pytest.raises(ValueError, match="无下一开放日|trailing"):
        resolve_execution_schedule(_target(tmp_path, dates=[D0]), cal)


# ---------------- output contract ----------------

def test_output_schema_exact(env, tmp_path):
    cal = _cal(env, [D0, D3])
    s = resolve_execution_schedule(_target(tmp_path), cal)
    assert s.frame.columns == ["decision_date", "execution_date", "execution_timing"]
    assert s.frame.schema["decision_date"] == pl.Date
    assert s.frame.schema["execution_date"] == pl.Date
    assert s.frame.schema["execution_timing"] == pl.String


def test_decision_unique(env, tmp_path):
    cal = _cal(env, [D0, D3])
    s = resolve_execution_schedule(_target(tmp_path, dates=[D0]), cal)
    assert s.frame.height == 1   # 每 decision 恰一个 execution event


def test_execution_greater_than_decision_all_rows(env, tmp_path):
    cal = _cal(env, [D0, D3, D4, D5])
    s = resolve_execution_schedule(_target(tmp_path, dates=[D0, D3, D4]), cal)
    assert (s.frame["execution_date"] > s.frame["decision_date"]).all()


def test_stable_order(env, tmp_path):
    cal = _cal(env, [D0, D3, D4, D5])
    s = resolve_execution_schedule(_target(tmp_path, dates=[D0, D3, D4]), cal)
    assert s.frame.equals(s.frame.sort(["decision_date"]))


def test_frozen(env, tmp_path):
    cal = _cal(env, [D0, D3])
    s = resolve_execution_schedule(_target(tmp_path), cal)
    with pytest.raises(FrozenInstanceError):
        s.frame = pl.DataFrame()


def test_timing_values_exact(env, tmp_path):
    cal = _cal(env, [D0, D3])
    s = resolve_execution_schedule(_target(tmp_path), cal)
    assert s.frame["execution_timing"][0] == "next_open"
    assert ExecutionTiming(s.frame["execution_timing"][0]) == ExecutionTiming.NEXT_OPEN


def test_independent_of_position_rows(env, tmp_path):
    """schedule 只由 decision_dates 决定——positions 内容不影响。"""
    cal = _cal(env, [D0, D3])
    t1 = _target(tmp_path, dates=[D0])
    f2 = pl.DataFrame({"decision_date": pl.Series([D0], dtype=pl.Date),
                       "code": pl.Series(["000001.SZ"], dtype=pl.String),
                       "target_weight": pl.Series([1.0], dtype=pl.Float64)})
    t2 = TargetPortfolio(frame=f2, decision_dates=(D0,), meta=t1.meta)
    assert resolve_execution_schedule(t1, cal).frame.equals(
        resolve_execution_schedule(t2, cal).frame)


# ================================================================
# M8-02 集成：TargetPortfolio → ExecutionSchedule → MarketOpenSnapshot
# ================================================================

def test_integration_schedule_to_snapshot(env, tmp_path):
    """§95：真实 TargetPortfolio → schedule → snapshot（同 execution_date、
    canonical codes、raw open evidence）。"""
    from factorlab.adapters.read.execution import load_market_open_frame
    from factorlab.execution import load_market_open_snapshot

    tables = _cal_tables([D0, D3])
    tables["daily"][1].append((D3.strftime("%Y%m%d"), "000001.SZ", 10.5, 10.0))
    tables["stk_limit"][1].append((D3.strftime("%Y%m%d"), "000001.SZ", 11.5, 9.5))
    env.seed(tables)
    cal = env.rd
    s = resolve_execution_schedule(_target(tmp_path, dates=[D0]), cal)
    assert s.frame["execution_date"][0] == D3
    snap = load_market_open_snapshot(cal, execution_date=D3,
                                     codes=["000001.SZ"])
    assert snap.execution_date == D3
    assert snap.frame["has_daily"][0] and snap.frame["open"][0] == 10.5
    assert snap.frame["has_limit"][0] and snap.frame["up_limit"][0] == 11.5
    assert snap.frame["has_suspend_record"][0] is False


# ================================================================
# M8-02A：ExecutionSchedule domain hardening（strict + non-null）
# ================================================================

def _mk_schedule(decisions, executions, timing="next_open"):
    return ExecutionSchedule(frame=pl.DataFrame({
        "decision_date": pl.Series(decisions, dtype=pl.Date),
        "execution_date": pl.Series(executions, dtype=pl.Date),
        "execution_timing": pl.Series([timing] * len(decisions), dtype=pl.String),
    }))


def test_duplicate_execution_date_fails():
    """整体 execution 序列重复（即使每行 individually execution > decision）→ fail。"""
    with pytest.raises(ValueError, match="严格递增|strictly"):
        _mk_schedule([D0, D3], [D4, D4])


def test_decision_date_null_fails():
    f = pl.DataFrame({
        "decision_date": pl.Series([None, D3], dtype=pl.Date),
        "execution_date": pl.Series([D3, D4], dtype=pl.Date),
        "execution_timing": pl.Series(["next_open", "next_open"], dtype=pl.String),
    })
    with pytest.raises(ValueError, match="null"):
        ExecutionSchedule(frame=f)


def test_execution_date_null_fails():
    f = pl.DataFrame({
        "decision_date": pl.Series([D0, D3], dtype=pl.Date),
        "execution_date": pl.Series([D3, None], dtype=pl.Date),
        "execution_timing": pl.Series(["next_open", "next_open"], dtype=pl.String),
    })
    with pytest.raises(ValueError, match="null"):
        ExecutionSchedule(frame=f)


def test_execution_timing_null_fails():
    f = pl.DataFrame({
        "decision_date": pl.Series([D0, D3], dtype=pl.Date),
        "execution_date": pl.Series([D3, D4], dtype=pl.Date),
        "execution_timing": pl.Series(["next_open", None], dtype=pl.String),
    })
    with pytest.raises(ValueError, match="null"):
        ExecutionSchedule(frame=f)


def test_valid_strictly_increasing_passes():
    s = _mk_schedule([D0, D3, D4], [D3, D4, D5])
    assert s.frame.height == 3


def test_decision_sequence_decreasing_fails():
    """decision 序列递减 → 严格递增检查拦截（不依赖 sorted 表达 strict）。"""
    with pytest.raises(ValueError, match="严格递增|strictly"):
        _mk_schedule([D3, D0], [D4, D5])
