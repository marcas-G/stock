"""R22 Task 5：run_backtest NEXT_WINDOW 集成 + WINDOW_END_BASED marks。

- 合成 duckdb 日频 fixture（既有 M8 runtime 测试模式）；分钟读适配以
  monkeypatch 注入（Task 3 已用 FakeRd 单测 I/O 边界；本文件验证编排：
  规划参考价 = 窗口首分钟 open、窗口成交、窗口末 close mark、持久化载体）。
- NEXT_OPEN 路径零改动 smoke（对照断言：用 daily open 规划）。
- NEXT_CLOSE 仍显式拒绝；WINDOW_END_BASED 不可用于 NEXT_OPEN。
"""

import datetime

import duckdb
import polars as pl
import pytest

from factorlab.app.backtest import (ExecutionSpec, MarksPolicy, run_backtest)
from factorlab.app.backtest import backtest as backtest_mod
from factorlab.app.bootstrap import open_read
from factorlab.core.domain import (TargetPortfolio, TargetPortfolioMeta)
from factorlab.core.domain.execution import ExecutionDataQualityError, ExecutionTiming
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING

D1 = datetime.date(2024, 1, 2)    # Tue decision
D2 = datetime.date(2024, 1, 3)    # Wed execution 1
D3 = datetime.date(2024, 1, 4)    # Thu execution 2
D4 = datetime.date(2024, 1, 5)    # Fri next open

_MIN_COLS = ["code", "minute_index", "open", "high", "low", "close", "volume",
             "amount", "session_type"]


def _db(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = duckdb.connect(tmp_path / "b.duckdb")
    db.execute("CREATE TABLE trade_cal (cal_date VARCHAR, is_open INT)")
    for d in (D1, D2, D3, D4):
        db.execute("INSERT INTO trade_cal VALUES (?,1)", (d.strftime("%Y%m%d"),))
    db.execute("""CREATE TABLE stock_basic (ts_code VARCHAR, symbol VARCHAR,
        market VARCHAR)""")
    db.execute("INSERT INTO stock_basic VALUES ('000001.SZ','000001','主板')")
    db.execute("CREATE TABLE daily (trade_date VARCHAR, ts_code VARCHAR, "
               "open DOUBLE, pre_close DOUBLE)")
    db.execute("CREATE TABLE stk_limit (trade_date VARCHAR, ts_code VARCHAR, "
               "up_limit DOUBLE, down_limit DOUBLE)")
    db.execute("CREATE TABLE adj_event (trade_date VARCHAR, ts_code VARCHAR)")
    for d, o, pc, up, dn in ((D2, 10.5, 10.5, 11.55, 9.45),
                             (D3, 11.0, 11.0, 12.1, 9.9)):
        db.execute("INSERT INTO daily VALUES (?,?,?,?)",
                   (d.strftime("%Y%m%d"), "000001.SZ", o, pc))
        db.execute("INSERT INTO stk_limit VALUES (?,?,?,?)",
                   (d.strftime("%Y%m%d"), "000001.SZ", up, dn))
    db.close()
    return tmp_path / "b.duckdb"


def _minute_frame(day):
    """D2 买入日窗口 bars（vwap m0=10.0/m1=10.2/m2=10.3）；
    D3 卖出日窗口 bars（vwap m0=11.0/m1=11.1/m2=11.15）。loader 输出 6 位 code。"""
    if day == D2:
        bars = [(0, 10.0, 10.2, 9.9, 10.1, 5000.0, 50000.0),
                (1, 10.1, 10.3, 10.0, 10.2, 5000.0, 51000.0),
                (2, 10.2, 10.4, 10.1, 10.3, 5000.0, 51500.0)]
    elif day == D3:
        bars = [(0, 11.0, 11.1, 10.9, 11.05, 5000.0, 55000.0),
                (1, 11.05, 11.15, 10.95, 11.1, 5000.0, 55500.0),
                (2, 11.1, 11.2, 11.0, 11.15, 5000.0, 55750.0)]
    else:
        bars = []
    rows = [("000001", i, o, h, l, c, v, a, 1) for i, o, h, l, c, v, a in bars]
    frame = pl.DataFrame(rows, schema=_MIN_COLS, orient="row")
    return frame.with_columns(
        pl.col("code").cast(pl.String), pl.col("minute_index").cast(pl.Int64),
        pl.col("open").cast(pl.Float64), pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64), pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64), pl.col("amount").cast(pl.Float64),
        pl.col("session_type").cast(pl.Int64))


@pytest.fixture
def loader_calls(monkeypatch):
    calls = []

    def fake(rd, codes, day, start, end):
        calls.append({"codes": list(codes), "day": day, "start": start,
                      "end": end})
        day_date = datetime.date.fromisoformat(day)
        return _minute_frame(day_date)

    monkeypatch.setattr(backtest_mod, "load_execution_window", fake)
    return calls


def _target(dates=(D1,), weights=None):
    rows = []
    for d, wm in (weights or [(D1, {"000001.SZ": 1.0}),
                              (D2, {})]):
        for c, w in sorted(wm.items()):
            rows.append((d, c, w))
    rows = [r for r in rows if r[0] in dates]
    frame = pl.DataFrame(rows, schema=["decision_date", "code",
                                       "target_weight"], orient="row")
    frame = frame.with_columns(pl.col("decision_date").cast(pl.Date),
                               pl.col("code").cast(pl.String),
                               pl.col("target_weight").cast(pl.Float64))
    return TargetPortfolio(frame=frame, decision_dates=tuple(sorted(set(dates))),
                           meta=TargetPortfolioMeta(
                               strategy_name="s", source_signal_name="x",
                               source_timing=DEFAULT_EOD_SIGNAL_TIMING,
                               gross_exposure=1.0))


def _window_spec(**over):
    base = {"initial_cash": 10_000.0,
            "execution_timing": "next_window",
            "minute_window": {"start": 0, "end": 2, "price_basis": "vwap",
                              "participation": 0.5}}
    base.update(over)
    return ExecutionSpec.model_validate(base)


def _run(target, db, spec, marks=None):
    kw = {} if marks is None else {"marks": marks}
    return run_backtest(target, spec, open_read(db_path=db), **kw)


# ================================================================
# NEXT_WINDOW end-to-end（合成）
# ================================================================

def test_next_window_end_to_end_synthetic(tmp_path, loader_calls):
    db = _db(tmp_path)
    r = _run(_target((D1,)), db, _window_spec())
    assert len(r.artifacts) == 1
    a = r.artifacts[0]
    assert a.decision_date == D1 and a.execution_date == D2
    # 规划参考价 = 窗口首分钟 open=10.0（daily open=10.5 会得到 952 股）：
    # initial_cash 10000 → target shares = floor(10000/10.0) = 1000
    assert a.orders.orders["quantity"].to_list() == [1000]
    # 窗口 m0 vwap=10.0、cap=0.5×5000=2500 ≥ 1000 → 1000@10.0
    fb = a.fills.frame
    assert fb["filled_quantity"].to_list() == [1000]
    assert fb["reference_price"].to_list() == pytest.approx([10.0])
    assert fb["execution_price"].to_list() == pytest.approx([10.0])
    assert a.accounting.cash_after == pytest.approx(0.0)
    # marks = 窗口末分钟 close（m2 close=10.3）→ NAV = 1000×10.3
    assert a.nav.nav == pytest.approx(10300.0)
    # 恒等式：nav == cash + market_value
    assert a.nav.nav == a.nav.cash + a.nav.market_value
    # 持久化载体：逐 event 窗口明细
    assert isinstance(r, backtest_mod.WindowBacktestResult)
    assert len(r.window_fills) == 1
    detail = r.window_fills[0]
    assert detail["minute_index"].to_list() == [0]
    assert detail["quantity"].to_list() == [1000]
    assert detail["price"].to_list() == pytest.approx([10.0])
    # loader 以 canonical code + 执行日 + 窗口边界调用（不是硬编码路径）
    assert loader_calls == [{"codes": ["000001.SZ"], "day": "2024-01-03",
                             "start": 0, "end": 2}]


def test_next_window_sell_and_rebalance(tmp_path, loader_calls):
    db = _db(tmp_path)
    r = _run(_target((D1, D2)), db, _window_spec())
    assert len(r.artifacts) == 2
    a1, a2 = r.artifacts
    assert a1.execution_date == D2 and a2.execution_date == D3
    # D2→D3 all-cash：SELL 1000（T+1 隔夜释放）
    assert a2.orders.orders["side"].to_list() == ["sell"]
    # D3 窗口 m0 vwap=11.0 → proceeds 11000
    assert a2.fills.frame["filled_quantity"].to_list() == [1000]
    assert a2.fills.frame["reference_price"].to_list() == pytest.approx([11.0])
    assert a2.accounting.cash_after == pytest.approx(11000.0)
    assert a2.nav.nav == pytest.approx(11000.0)
    assert r.nav_series.frame["nav"].to_list() == pytest.approx([10300.0,
                                                                 11000.0])
    assert r.final_state.as_of_date == D4
    assert r.final_state.cash == pytest.approx(11000.0)


def test_next_window_marks_flag_accepted(tmp_path, loader_calls):
    db = _db(tmp_path)
    r = _run(_target((D1,)), db, _window_spec(),
             marks=MarksPolicy.WINDOW_END_BASED)
    assert r.artifacts[0].nav.nav == pytest.approx(10300.0)


def test_window_marks_rejected_for_next_open(tmp_path):
    db = _db(tmp_path)
    spec = ExecutionSpec.model_validate({"initial_cash": 10_000.0})
    with pytest.raises(ValueError, match="WINDOW_END_BASED"):
        _run(_target((D1,)), db, spec, marks=MarksPolicy.WINDOW_END_BASED)


def test_window_requires_minute_open_fails_fast(tmp_path, monkeypatch):
    db = _db(tmp_path)

    def empty_loader(rd, codes, day, start, end):
        return _minute_frame(datetime.date(2024, 1, 10))   # 无 bars

    monkeypatch.setattr(backtest_mod, "load_execution_window", empty_loader)
    with pytest.raises(ExecutionDataQualityError, match="首分钟|open"):
        _run(_target((D1,)), db, _window_spec())


def test_window_unfilled_when_volume_capped(tmp_path, loader_calls):
    db = _db(tmp_path)
    # participation 0.05 → m0 cap=250、m1 cap=250、m2 cap=250 → 750 成交
    spec = _window_spec(minute_window={"start": 0, "end": 2,
                                       "price_basis": "vwap",
                                       "participation": 0.05})
    r = _run(_target((D1,)), db, spec)
    a = r.artifacts[0]
    assert a.fills.frame["filled_quantity"].to_list() == [750]
    # 未成完不减现金：10000 - (250×10.0+250×10.2+250×10.3)=10000-7625=2375
    assert a.accounting.cash_after == pytest.approx(2375.0)
    assert a.nav.nav == pytest.approx(2375.0 + 750 * 10.3)


# ================================================================
# NEXT_OPEN 零改动回归 与 NEXT_CLOSE 拒绝
# ================================================================

def test_next_open_unchanged_smoke(tmp_path, loader_calls):
    db = _db(tmp_path)
    spec = ExecutionSpec.model_validate({"initial_cash": 10_000.0})
    r = _run(_target((D1,)), db, spec)
    a = r.artifacts[0]
    # NEXT_OPEN：daily open=10.5 规划 → floor(10000/10.5)=952 → 整手投影 900；
    # 成交 @10.5；mark @open
    assert a.orders.orders["quantity"].to_list() == [900]
    assert a.fills.frame["reference_price"].to_list() == pytest.approx([10.5])
    assert a.accounting.cash_after == pytest.approx(550.0)
    assert a.nav.nav == pytest.approx(10000.0)
    assert not isinstance(r, backtest_mod.WindowBacktestResult)
    assert loader_calls == []          # NEXT_OPEN 绝不读分钟数据


def test_next_close_still_rejected(tmp_path):
    db = _db(tmp_path)
    spec = ExecutionSpec.model_validate({"execution_timing": "next_close"})
    with pytest.raises(NotImplementedError, match="NEXT_CLOSE|next_close"):
        _run(_target((D1,)), db, spec)


def test_next_window_requires_duckdb_minutes_fail_fast(tmp_path):
    """真实 loader（未 monkeypatch）：duckdb 后端 → NotImplementedError（仅 CH）。"""
    db = _db(tmp_path)
    with pytest.raises(NotImplementedError, match="CH"):
        _run(_target((D1,)), db, _window_spec())


def test_api_exists():
    assert MarksPolicy.WINDOW_END_BASED.value == "window_end_based"
    assert ExecutionTiming.NEXT_WINDOW.value == "next_window"
