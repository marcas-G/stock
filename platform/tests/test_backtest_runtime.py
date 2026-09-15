"""M8-06B：backtest runtime——编排已关闭 execution primitives → BacktestResult。

- run_backtest 只做 orchestration（schedule/orders/assessment/fills/state/
  accounting/overnight/valuation 全复用；零新 execution math）
- MarksPolicy v1 = OPEN_BASED + 停牌冻结（WS4）：有 daily 用当日 raw open；
  持仓 code 缺行（= 停牌）冻结——mark 沿用 run 内最近一次真实 open、
  NAV 不变、无 fills；目标 code 缺行 → order 跳过（停牌语义测试在
  tests/test_backtest_marks_policy.py，本文件只留行为回归锚点）
- memory-only runtime object（无 persistence/DB 写入）
"""

import datetime
import inspect
import re
from pathlib import Path

import duckdb
import polars as pl
import pytest

from factorlab.core.domain import (PortfolioStatePhase, TargetPortfolio,
                              TargetPortfolioMeta)
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
from factorlab.app.bootstrap import open_read
from factorlab.app.backtest import (ExecutionSpec, MarksPolicy, run_backtest)

D1 = datetime.date(2024, 1, 2)    # Tue
D2 = datetime.date(2024, 1, 3)    # Wed
D3 = datetime.date(2024, 1, 4)    # Thu
D8 = datetime.date(2024, 1, 8)    # next Mon
EX1, EX2 = D2, D3                 # decisions D1→EX D2, D2→EX D3


def _cal_db(tmp_path, opens):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = duckdb.connect(tmp_path / "b.duckdb")
    db.execute("CREATE TABLE trade_cal (cal_date VARCHAR, is_open INT)")
    for d, o in opens:
        db.execute("INSERT INTO trade_cal VALUES (?,?)", (d.strftime("%Y%m%d"), o))
    db.execute("""CREATE TABLE stock_basic (ts_code VARCHAR, symbol VARCHAR,
        market VARCHAR)""")
    for c, m in (("000001.SZ", "主板"), ("600000.SH", "主板")):
        db.execute("INSERT INTO stock_basic VALUES (?,?,?)", (c, c[:6], m))
    db.execute("CREATE TABLE daily (trade_date VARCHAR, ts_code VARCHAR, "
               "open DOUBLE, pre_close DOUBLE)")
    db.execute("CREATE TABLE stk_limit (trade_date VARCHAR, ts_code VARCHAR, "
               "up_limit DOUBLE, down_limit DOUBLE)")
    # WS5：CA Gate armed（多事件+持仓）需事件表——空表 = 干净 run 通过
    # （fail-closed 无表场景在 tests/test_backtest_ca_gate.py B1/B8 单独构造）
    db.execute("CREATE TABLE adj_event (trade_date VARCHAR, ts_code VARCHAR)")
    return db


def _add_daily(db, date, code, open_):
    up, dn = round(open_ * 1.1, 4), round(open_ * 0.9, 4)
    db.execute("INSERT INTO daily VALUES (?,?,?,?)",
               (date.strftime("%Y%m%d"), code, open_, open_))
    db.execute("INSERT INTO stk_limit VALUES (?,?,?,?)",
               (date.strftime("%Y%m%d"), code, up, dn))


def _db(tmp_path):
    db = _cal_db(tmp_path, [(D1, 1), (D2, 1), (D3, 1),
                            (datetime.date(2024, 1, 5), 1), (D8, 1),
                            (datetime.date(2024, 1, 9), 1)])
    _add_daily(db, D2, "000001.SZ", 10.0)
    _add_daily(db, D2, "600000.SH", 20.0)
    _add_daily(db, D3, "000001.SZ", 11.0)
    _add_daily(db, D3, "600000.SH", 21.0)
    _add_daily(db, D8, "000001.SZ", 12.0)
    _add_daily(db, D8, "600000.SH", 22.0)
    db.close()
    return tmp_path / "b.duckdb"


def _target(dates=(D1, D2), weights=None):
    """dates: (decision, {code: weight})；默认 D1/D2 各 0.5/0.5。"""
    rows = []
    for d, wm in (weights or [(D1, {"000001.SZ": 0.5, "600000.SH": 0.5}),
                              (D2, {"000001.SZ": 1.0})]):
        for c, w in sorted(wm.items()):
            rows.append((d, c, w))
    if dates:
        rows = [r for r in rows if r[0] in dates]
    frame = pl.DataFrame(rows, schema=["decision_date", "code",
                                       "target_weight"], orient="row")
    frame = frame.with_columns(pl.col("decision_date").cast(pl.Date),
                               pl.col("code").cast(pl.String),
                               pl.col("target_weight").cast(pl.Float64))
    return TargetPortfolio(frame=frame, decision_dates=tuple(sorted(set(dates))),
                           meta=TargetPortfolioMeta(
                               strategy_name="strat_x",
                               source_signal_name="alpha_x",
                               source_timing=DEFAULT_EOD_SIGNAL_TIMING,
                               gross_exposure=1.0))


def _run(target=None, db_path=None, **over):
    spec = ExecutionSpec.model_validate({"initial_cash": 1_000_000.0})
    return run_backtest(target if target is not None else _target(),
                        spec, open_read(db_path=db_path), **over)


# ================================================================
# API / guards
# ================================================================

def test_api_exists():
    assert callable(run_backtest)
    assert MarksPolicy.OPEN_BASED.value == "open_based"


def test_type_guards(tmp_path):
    db = _db(tmp_path)
    spec = ExecutionSpec.model_validate({})
    with pytest.raises(TypeError, match="target"):
        run_backtest({"x": 1}, spec, db)
    with pytest.raises(TypeError, match="execution_spec"):
        run_backtest(_target(), {"c": 1}, db)
    with pytest.raises(TypeError, match="rd|读句柄"):
        run_backtest(_target(), spec, str(db))


def test_caller_explicit_marks_not_implemented(tmp_path):
    with pytest.raises(NotImplementedError, match="OPEN_BASED|caller"):
        _run(db_path=_db(tmp_path), marks="caller_explicit")


def test_no_strategy_params():
    params = inspect.signature(run_backtest).parameters
    for forbidden in ("signal", "strategy_spec", "universe"):
        assert forbidden not in params


# ================================================================
# single / multi-event runs
# ================================================================

def test_single_event_run(tmp_path):
    """单 decision：buy 0.5/0.5 @ D2 opens → artifact 1、NAV 1 行。"""
    db = _db(tmp_path)
    t = _target(dates=(D1,))
    r = _run(target=t, db_path=db)
    assert len(r.artifacts) == 1
    a = r.artifacts[0]
    assert a.decision_date == D1 and a.execution_date == D2
    assert a.fills.frame.height == 2
    assert a.post_state.phase is PortfolioStatePhase.POST_EXECUTION
    assert a.accounting.cash_before == 1_000_000.0
    assert a.accounting.cash_after == 0.0
    assert a.accounting.net_cash_delta == -1_000_000.0
    # NAV @ D2 open marks：cash 0 + 50,000×10 + 25,000×20 = 1,000,000
    assert a.nav.nav == 1_000_000.0
    assert r.nav_series.frame.height == 1
    row = r.nav_series.frame.row(0)
    assert row[1] == 0.0 and row[3] == 1_000_000.0
    # final state = PRE at D3
    assert r.final_state.as_of_date == D3
    assert r.final_state.phase is PortfolioStatePhase.PRE_EXECUTION


def test_multi_event_rebalance(tmp_path):
    """D1→D2 buy 0.5/0.5；D2→D3 rebalance to 000001-only（sell 600000）。
    T+1：600000 在 D2 买入 → D3 可卖（隔夜释放）。"""
    db = _db(tmp_path)
    r = _run(db_path=db)
    assert len(r.artifacts) == 2
    a2 = r.artifacts[1]
    assert a2.decision_date == D2 and a2.execution_date == D3
    sides = a2.orders.orders["side"].to_list()
    assert sides == ["buy", "sell"]      # 000001 buy、600000 sell（code ASC）
    assert a2.accounting.cash_after == 300.0
    r1 = r.artifacts[0].nav.nav
    r2 = a2.nav.nav
    assert r1 == 1_000_000.0
    assert r2 == 1_075_000.0             # 550k+525k @11/21
    assert r.nav_series.frame.height == 2
    assert r.final_state.as_of_date == datetime.date(2024, 1, 5)


def test_weekly_gap_redate(tmp_path):
    """decisions D1(exec 1/3) 与 D5=1/5 all-cash(exec D8=1/8)：
    1/3 后 advance→1/4，再 re-date→1/8；D1 买入的持仓在 D8 全部清算。"""
    db = _db(tmp_path)
    D5 = datetime.date(2024, 1, 5)
    t = _target(dates=(D1, D5), weights=[
        (D1, {"000001.SZ": 0.5, "600000.SH": 0.5}), (D5, {})])
    r = _run(target=t, db_path=db)
    assert len(r.artifacts) == 2
    a1, a2 = r.artifacts
    assert a2.execution_date == D8
    # 两笔 SELL（000001/600000 全清）——T+1：1/3 买入 600000 在 1/8 可卖
    assert a2.orders.orders["side"].to_list() == ["sell", "sell"]
    assert a1.nav.nav == 1_000_000.0
    # D8: SELL @12/22 → cash = 600,000 + 550,000 = 1,150,000
    assert a2.accounting.cash_after == 1_150_000.0
    assert a2.nav.nav == 1_150_000.0


def test_empty_event_day(tmp_path):
    """现金账户 + all-cash decision → empty orders/fills、cash 不变。"""
    db = _db(tmp_path)
    D5 = datetime.date(2024, 1, 5)
    t = _target(dates=(D5,), weights=[(D5, {})])
    r = _run(target=t, db_path=db)
    assert len(r.artifacts) == 1
    a = r.artifacts[0]
    assert a.execution_date == D8          # D5(Fri) → D8(Mon)
    assert a.orders.orders.height == 0
    assert a.fills.frame.height == 0
    assert a.accounting.net_cash_delta == 0.0
    assert a.accounting.cash_after == a.accounting.cash_before == 1_000_000.0
    assert a.nav.nav == 1_000_000.0


def test_decision_range(tmp_path):
    db = _db(tmp_path)
    r = _run(db_path=db, decision_range=(D2, D2))
    assert len(r.artifacts) == 1
    assert r.artifacts[0].decision_date == D2


def test_decision_range_empty_fails(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ValueError, match="decision"):
        _run(db_path=db, decision_range=(datetime.date(2024, 5, 1),
                                         datetime.date(2024, 5, 2)))


def _trailing_db(tmp_path):
    """日历止于 D3：decisions (D1,D2,D3) 中 D3 为 trailing unresolved。"""
    db = _cal_db(tmp_path, [(D1, 1), (D2, 1), (D3, 1)])
    _add_daily(db, D2, "000001.SZ", 10.0)
    _add_daily(db, D3, "000001.SZ", 11.0)
    db.close()
    return tmp_path / "b.duckdb"


def test_decision_range_ignores_out_of_range_trailing(tmp_path):
    """R01-M8-I4：decision_range 只解析范围内 decisions——范围外尾部未决
    决策（D3 后无下一开放日）不得拖垮范围内 run；全量 run 仍在 D3 fail。"""
    db = _trailing_db(tmp_path)
    t = _target(dates=(D1, D2, D3), weights=[
        (D1, {"000001.SZ": 1.0}), (D2, {"000001.SZ": 1.0}),
        (D3, {"000001.SZ": 1.0})])
    with pytest.raises(ValueError, match="无下一开放日|trailing"):
        _run(target=t, db_path=db)
    r = _run(target=t, db_path=db, decision_range=(D1, D1))
    assert len(r.artifacts) == 1
    assert r.artifacts[0].decision_date == D1
    assert r.artifacts[0].execution_date == D2
    assert r.final_state.as_of_date == D3
    assert r.trailing_unresolved is False


def test_in_range_trailing_decision_still_fails(tmp_path):
    """R01-M8-I4 边界（spec 原文）：范围内 decision 本身无下一开放日（无
    execution date 可解析）仍是硬错误——§6.3 合法终止只适用于已有 execution
    的 trailing advance，不适用于无 event 可跑的 decision。"""
    db = _trailing_db(tmp_path)
    t = _target(dates=(D1, D2, D3), weights=[
        (D1, {"000001.SZ": 1.0}), (D2, {"000001.SZ": 1.0}),
        (D3, {"000001.SZ": 1.0})])
    with pytest.raises(ValueError, match="无下一开放日|trailing"):
        _run(target=t, db_path=db, decision_range=(D3, D3))


def test_trailing_unresolved_legal_termination_preserves_results(tmp_path):
    """R01-M8-I5（m8-06a §6.3）：最后一个 execution 后无下一开放日 → 合法
    终止：返回全部中间 artifacts/nav（不 drop、不 fail 全 run）；
    final_state = 最后 POST state + trailing_unresolved=True。"""
    db = _trailing_db(tmp_path)
    t = _target(dates=(D1, D2), weights=[
        (D1, {"000001.SZ": 1.0}), (D2, {"000001.SZ": 1.0})])
    r = _run(target=t, db_path=db)
    assert len(r.artifacts) == 2
    assert [a.execution_date for a in r.artifacts] == [D2, D3]
    assert r.nav_series.frame.height == 2
    assert r.nav_series.frame["nav"].to_list() == [1_000_000.0, 1_100_000.0]
    assert r.trailing_unresolved is True
    assert r.final_state.phase is PortfolioStatePhase.POST_EXECUTION
    assert r.final_state.as_of_date == D3
    assert r.final_state.cash == r.artifacts[-1].post_state.cash
    assert r.final_state.positions.equals(r.artifacts[-1].post_state.positions)


# ================================================================
# marks / data gates
# ================================================================

def test_missing_open_freeze_held(tmp_path):
    """WS4：持仓 600000 在 execution date 缺 daily/stk_limit 行（= 停牌，
    无 suspend_d 事件表）→ 冻结不 fail：无 fills、mark 沿用 1/3 买入 open
    （20）、NAV 不变、持仓保持——旧"缺 open → fail run"语义废止。"""
    db = _db(tmp_path)
    con = duckdb.connect(db)
    con.execute("DELETE FROM daily WHERE trade_date='20240104' AND ts_code='600000.SH'")
    con.execute("DELETE FROM stk_limit WHERE trade_date='20240104' AND ts_code='600000.SH'")
    con.close()
    t = _target(dates=(D1, D2), weights=[
        (D1, {"600000.SH": 1.0}), (D2, {})])   # 1/3 买 600000；1/4 停牌+all-cash
    r = _run(target=t, db_path=db)
    assert len(r.artifacts) == 2
    a2 = r.artifacts[1]
    assert a2.execution_date == D3
    assert a2.fills.frame.height == 0
    assert a2.nav.nav == 1_000_000.0          # 50,000 × 20（沿用买入日 open mark）
    assert r.nav_series.frame["nav"].to_list() == [1_000_000.0, 1_000_000.0]
    pos = a2.post_state.positions.filter(pl.col("code") == "600000.SH")
    assert pos["quantity"].to_list() == [50_000]


def test_determinism(tmp_path):
    db = _db(tmp_path)
    a = _run(db_path=db)
    b = _run(db_path=db)
    assert len(a.artifacts) == len(b.artifacts)
    for x, y in zip(a.artifacts, b.artifacts):
        assert x.post_state.cash == y.post_state.cash
        assert x.post_state.positions.equals(y.post_state.positions)
        assert x.fills.frame.equals(y.fills.frame)
        assert x.nav.nav == y.nav.nav
    assert a.nav_series.frame.equals(b.nav_series.frame)
    assert a.final_state.positions.equals(b.final_state.positions)


def test_no_db_writes(tmp_path):
    db = _db(tmp_path)
    before = {t: duckdb.connect(db).execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("daily", "stk_limit", "stock_basic")}
    _run(db_path=db)
    after = {t: duckdb.connect(db).execute(f"SELECT count(*) FROM {t}").fetchone()[0]
             for t in ("daily", "stk_limit", "stock_basic")}
    assert before == after


def test_source_audit_no_strategy_engine():
    from factorlab.app.backtest import backtest as mod
    src = inspect.getsource(mod)
    for forbidden in ("strategy", "engine", "SignalArtifact", "StrategySpec",
                      "duckdb"):
        assert not re.search(rf"^\s*(import|from)\s+[^\s]*{forbidden}", src,
                             re.M), f"backtest.py 不得 import {forbidden}"
