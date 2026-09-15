"""R22 Task 7：分钟执行 CH 端到端（integration）——小样本跑通 + 手算对拍。

1. 20 只 × 2024-01（22 交易日逐日等权决策）run_backtest(NEXT_WINDOW
   [0,30] vwap 参与率 0.1) 跑通；随机抽一个有成交的 (event, code)，用 SQL
   从 CH 复算窗口逐分钟 VWAP 与汇总加权价，与 window_fills 明细/ FillBatch
   reference_price 逐值对拍（滑点 1bp → execution_price = ref×(1+1e-4)）。
2. 真实一字涨停日：BUY 不成交（窗口每分钟封板被拦；fallback=none）。

环境：`FACTORLAB_DATA_BACKEND=ch`；CH 不可达 → skip（ch_prod fixture）。
样本选择：全 240 网格 + daily/stk_limit 全覆盖 + 窗口内无 adj_event（CA Gate
分段指引——除权事件跨窗口无定义，避开属合法采样，不是弱化语义）。
"""

import datetime
import random

import polars as pl
import pytest

from factorlab.app.backtest import ExecutionSpec, run_backtest
from factorlab.app.bootstrap import open_read
from factorlab.adapters.read.calendar import trading_calendar
from factorlab.config import settings
from factorlab.core.domain import TargetPortfolio, TargetPortfolioMeta
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING

pytestmark = pytest.mark.integration

_WINDOW = {"start": 0, "end": 30, "price_basis": "vwap", "participation": 0.1}
_COST = {"commission_rate": 0.00025, "minimum_commission": 5.0,
         "stamp_tax_sell_rate": 0.0005, "transfer_fee_rate": 0.00001,
         "slippage_bps": 1.0}


def _spec(cash=10_000_000.0, participation=0.1, fallback="none"):
    return ExecutionSpec.model_validate({
        "initial_cash": cash, "cost_model": _COST,
        "execution_timing": "next_window",
        "minute_window": {**_WINDOW, "participation": participation,
                          "fallback": fallback}})


def _full_grid_codes(rd, start: datetime.date, end: datetime.date,
                     n_days: int) -> list[str]:
    db = settings.ch_database
    rows = rd.query_rows(
        f"SELECT b.code, count() AS n FROM {db}.bars_1m AS b "
        f"WHERE b.trade_date BETWEEN toDate(%(s)s) AND toDate(%(e)s) "
        f"GROUP BY b.code HAVING n = %(k)s ORDER BY b.code",
        {"s": start.isoformat(), "e": end.isoformat(), "k": 240 * n_days})
    return [r[0] for r in rows]


def _no_adj_events(rd, codes: list[str], start: datetime.date,
                   end: datetime.date) -> set[str]:
    db = settings.ch_database
    ph = ", ".join(f"%(c{i})s" for i in range(len(codes)))
    rows = rd.query_rows(
        f"SELECT DISTINCT ts_code FROM {db}.adj_event "
        f"WHERE trade_date BETWEEN toDate(%(s)s) AND toDate(%(e)s) "
        f"AND ts_code IN ({ph})",
        {"s": start.isoformat(), "e": end.isoformat(),
         **{f"c{i}": c for i, c in enumerate(codes)}})
    return {r[0] for r in rows}


def _equal_weight_target(codes, decisions):
    w = 1.0 / len(codes)
    rows = [(d, c, w) for d in decisions for c in codes]
    frame = pl.DataFrame(rows, schema=["decision_date", "code",
                                       "target_weight"], orient="row")
    frame = frame.with_columns(pl.col("decision_date").cast(pl.Date),
                               pl.col("code").cast(pl.String),
                               pl.col("target_weight").cast(pl.Float64))
    return TargetPortfolio(frame=frame, decision_dates=tuple(decisions),
                           meta=TargetPortfolioMeta(
                               strategy_name="r22_e2e", source_signal_name="x",
                               source_timing=DEFAULT_EOD_SIGNAL_TIMING,
                               gross_exposure=1.0))


def _single_target(code, decision):
    frame = pl.DataFrame([(decision, code, 1.0)],
                         schema=["decision_date", "code", "target_weight"],
                         orient="row")
    frame = frame.with_columns(pl.col("decision_date").cast(pl.Date),
                               pl.col("code").cast(pl.String),
                               pl.col("target_weight").cast(pl.Float64))
    return TargetPortfolio(frame=frame, decision_dates=(decision,),
                           meta=TargetPortfolioMeta(
                               strategy_name="r22_e2e", source_signal_name="x",
                               source_timing=DEFAULT_EOD_SIGNAL_TIMING,
                               gross_exposure=1.0))


# ================================================================
# 1. 20 只 × 2024-01 跑通 + SQL 复算对拍
# ================================================================

def test_window_execution_on_ch_small_universe(ch_prod):
    rd = open_read(data_backend="ch")
    cal = trading_calendar(rd, date_start="2024-01-01",
                           date_end="2024-01-31")
    days = cal.to_list()
    if len(days) < 10:
        pytest.skip(f"2024-01 交易日不足（{len(days)}）")
    decisions = days[:-1]                       # 末日决策的执行日可能在 2 月
    codes = _full_grid_codes(rd, days[0], days[-1], len(days))
    if len(codes) < 20:
        pytest.skip(f"完整 240 网格样本不足 20（{len(codes)}）")
    adj = _no_adj_events(rd, codes, days[0], days[-1])
    codes = [c for c in codes if c not in adj][:20]
    if len(codes) < 20:
        pytest.skip(f"剔除窗口内除权事件后样本不足 20（{len(codes)}）")

    target = _equal_weight_target(codes, decisions)
    r = run_backtest(target, _spec(), rd)
    assert len(r.artifacts) == len(decisions)
    assert len(r.window_fills) == len(r.artifacts)
    # 每个 execution event 都真实产出（不是空跑）
    assert all(a.orders.orders.height > 0 for a in r.artifacts)
    fill_events = sum(1 for a in r.artifacts if a.fills.frame.height)
    assert fill_events >= max(1, len(decisions) * 3 // 4)

    # ---- 随机抽一个有成交的 (event, code)，SQL 复算窗口 VWAP 逐值对拍 ----
    pairs = sorted((i, code) for i, a in enumerate(r.artifacts)
                   for code in a.fills.frame["code"].to_list())
    assert pairs
    i, code = random.Random(20260915).choice(pairs)
    a = r.artifacts[i]
    fb = a.fills.frame.filter(pl.col("code") == code).row(0)
    filled, ref, exec_px = int(fb[3]), float(fb[4]), float(fb[5])
    detail = r.window_fills[i].filter(pl.col("code") == code)
    assert detail.height > 0
    assert int(detail["quantity"].sum()) == filled
    assert detail["side"].unique().to_list() == ["buy"]

    db = settings.ch_database
    rows = rd.query_rows(
        f"SELECT minute_index, amount, volume FROM {db}.bars_1m "
        f"WHERE code = %(c)s AND trade_date = toDate(%(d)s) "
        f"AND minute_index BETWEEN 0 AND 30",
        {"c": code, "d": a.execution_date.isoformat()})
    sql_px = {int(r[0]): (float(r[1]), float(r[2])) for r in rows}
    for mi, q, px in zip(detail["minute_index"].to_list(),
                         detail["quantity"].to_list(),
                         detail["price"].to_list()):
        amount, volume = sql_px[mi]
        assert volume > 0
        assert px == pytest.approx(amount / volume, rel=1e-9, abs=1e-12), \
            f"{code}@{a.execution_date} minute {mi} 窗口 VWAP 对拍失败"
        # 参与率约束（单分钟成交 <= 10% × 该分钟量）真实成立
        assert q <= int(volume * 0.1)
    sql_avg = sum(sql_px[mi][0] / sql_px[mi][1] * q
                  for mi, q in zip(detail["minute_index"].to_list(),
                                   detail["quantity"].to_list())) / filled
    assert ref == pytest.approx(sql_avg, rel=1e-9)
    assert exec_px == pytest.approx(ref * (1 + 1e-4), rel=1e-12)   # 1bp 滑点


# ================================================================
# 2. 真实一字涨停日：BUY 不成交
# ================================================================

def test_sealed_limit_case_on_ch(ch_prod):
    rd = open_read(data_backend="ch")
    db = settings.ch_database
    rows = rd.query_rows(
        f"SELECT b.code, b.trade_date FROM {db}.bars_1m AS b "
        f"INNER JOIN {db}.stk_limit AS l "
        f"  ON b.code = l.ts_code AND b.trade_date = l.trade_date "
        f"WHERE b.trade_date BETWEEN toDate('2024-01-01') "
        f"  AND toDate('2024-01-31') "
        f"  AND b.open = b.high AND b.high = b.low AND b.low = b.close "
        f"  AND b.open = l.up_limit "
        f"GROUP BY b.code, b.trade_date HAVING count() = 240 "
        f"ORDER BY b.trade_date LIMIT 1")
    if not rows:
        pytest.skip("2024-01 无可用的真实一字涨停日样本")
    code, day = rows[0]
    if isinstance(day, str):
        day = datetime.date.fromisoformat(day)
    cal = trading_calendar(rd, date_start=(day - datetime.timedelta(days=30)).isoformat(),
                           date_end=day.isoformat())
    open_days = cal.to_list()
    if day not in open_days or open_days.index(day) < 1:
        pytest.skip("一字涨停日无前一交易日（日历样本不足）")
    decision = open_days[open_days.index(day) - 1]

    r = run_backtest(_single_target(code, decision), _spec(cash=1_000_000.0,
                                                           participation=0.5), rd)
    a = r.artifacts[0]
    assert a.execution_date == day
    assert a.orders.orders.height == 1
    assert a.orders.orders["side"].to_list() == ["buy"]
    # 一字封板：窗口每分钟 open==high==low==up_limit → 不成交
    assert a.fills.frame.height == 0
    assert r.window_fills[0].height == 0
    assert a.post_state.cash == a.pre_state.cash == 1_000_000.0
    assert a.nav.nav == pytest.approx(1_000_000.0)
