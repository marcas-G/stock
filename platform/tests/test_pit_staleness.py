"""R01-DATA-C1：长期断流仍标记 listed 的运行期防护（staleness gate）。

背景：生产 ch stock_basic 无 delist_date 列（TOOLS-A 负责灌入），
resolve_universe_frame 的 is_listed 只基于 list_date → 退市股永不退市，
run 链 forward-fill 死价格入截面（实测 600005.SH 最后成交 2017-02-13
仍 in_universe=True @2026-08-14）。

本 gate：面板最后日期仍 is_listed=true、但最后非空 close 早于 N 个交易日
（默认 250）的 code → fail loudly（列 code + 最后数据日）。
一旦 TOOLS-A 灌入 delist_date，这些 code is_listed=False，不再触发。
"""

import datetime

import polars as pl
import pytest

from factorlab.adapters.read.staleness import (STALE_LISTED_MAX_TRADING_DAYS,
                                               assert_no_stale_listed,
                                               stale_listed_codes)
from factorlab.adapters.read.universe import align_to_listing, resolve_universe_frame
from factorlab.core.spec import FactorSpec


def _weekdays(n: int, start="2024-01-02") -> list[datetime.date]:
    d = datetime.date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def _panel(dates, traded_days: int, code="600005"):
    """dates 全骨架；close 仅前 traded_days 行非 null（模拟退市断流）。"""
    return pl.DataFrame({
        "date": dates,
        "code": [code] * len(dates),
        "close": [10.0] * traded_days + [None] * (len(dates) - traded_days),
    }, schema={"date": pl.Date, "code": pl.String, "close": pl.Float64})


def _uf(dates, listed=True, code="600005"):
    return pl.DataFrame({
        "date": dates,
        "code": [code] * len(dates),
        "is_listed": [listed] * len(dates),
        "in_universe": [listed] * len(dates),
    }, schema={"date": pl.Date, "code": pl.String,
               "is_listed": pl.Boolean, "in_universe": pl.Boolean})


# ---------------------------------------------------------------- 纯函数行为

def test_stale_listed_fails_loudly_with_code_and_last_close():
    """2017 年就断流、is_listed=True 的假面板 → ValueError（列 code + 最后数据日）。

    对应生产 600005.SH：最后成交 2017-02-13，面板到 2026 仍 listed。
    """
    dates = _weekdays(300)
    panel = _panel(dates, traded_days=5)
    uf = _uf(dates)
    stale = stale_listed_codes(panel, uf)
    assert stale == [("600005", dates[4])]
    with pytest.raises(ValueError) as exc:
        assert_no_stale_listed(panel, uf)
    msg = str(exc.value)
    assert "600005" in msg
    assert str(dates[4]) in msg            # 最后数据日
    assert "delist_date" in msg            # 给出根因/修法指向
    assert str(STALE_LISTED_MAX_TRADING_DAYS) in msg


def test_normal_suspension_not_flagged():
    """普通停牌（最后数据 < N 交易日）不误伤——chunk 边界停牌是合法语义。"""
    dates = _weekdays(300)
    panel = _panel(dates, traded_days=270)   # 最后 30 交易日停牌
    assert stale_listed_codes(panel, _uf(dates)) == []
    assert_no_stale_listed(panel, _uf(dates))  # 不抛


def test_delisted_code_not_flagged():
    """已正确退市（is_listed=false）的 code 即使长期无数据也不触发。"""
    dates = _weekdays(300)
    panel = _panel(dates, traded_days=5)
    assert_no_stale_listed(panel, _uf(dates, listed=False))


def test_threshold_boundary():
    """阈值语义：恰 N 个交易日无成交不触发；N+1 触发。"""
    dates = _weekdays(300)
    n = 250
    panel = _panel(dates, traded_days=len(dates) - n)      # 50 行数据 → 250 天无成交
    assert stale_listed_codes(panel, _uf(dates),
                              max_stale_trading_days=n) == []
    assert_no_stale_listed(panel, _uf(dates), max_stale_trading_days=n)
    panel = _panel(dates, traded_days=len(dates) - n - 1)  # 251 天无成交
    assert stale_listed_codes(panel, _uf(dates),
                              max_stale_trading_days=n) == [("600005", dates[48])]


def test_no_close_in_window_flagged():
    """窗口内完全没有非空 close 且仍 listed（窗口跨度 > N）→ 断流是断流，触发。"""
    dates = _weekdays(300)
    panel = _panel(dates, traded_days=0)
    stale = stale_listed_codes(panel, _uf(dates))
    assert stale == [("600005", None)]
    with pytest.raises(ValueError, match="600005"):
        assert_no_stale_listed(panel, uf=_uf(dates))


def test_short_window_no_close_not_flagged():
    """短窗口（< N 交易日）完全无 close 不触发——无法区分“上市未久/停牌”。"""
    dates = _weekdays(100)
    panel = _panel(dates, traded_days=0)
    assert stale_listed_codes(panel, _uf(dates)) == []


def test_non_listed_codes_and_missing_uf_rows_ignored():
    """面板里的非 listed code（如 pre-list 未上市）不参与检查。"""
    dates = _weekdays(300)
    panel = _panel(dates, traded_days=5, code="600005")
    uf = _uf(dates, listed=False)
    assert stale_listed_codes(panel, uf) == []


# ---------------------------------------------------------------- 与真实 PIT 路径集成

_SB_COLS_NO_DELIST = [("ts_code", "str"), ("symbol", "str"), ("exchange", "str"),
                      ("list_date", "date"), ("industry", "str?")]


def _seed_no_delist(env, with_delist_value: str | None):
    """stock_basic 无 delist_date 列（生产现状）或带正确 delist_date。"""
    cols = list(_SB_COLS_NO_DELIST)
    rows = [("600005.SH", "600005", "SSE", "19990803", "钢铁")]
    if with_delist_value is not None:
        cols = cols + [("delist_date", "str?")]
        rows = [(r[0], r[1], r[2], r[3], r[4], with_delist_value) for r in rows]
    env.seed({
        "stock_basic": (cols, rows),
        "daily": ([("ts_code", "str"), ("trade_date", "date"), ("close", "f64")], []),
    })


def _pit_panel(env, dates):
    """真实链路：resolve_universe_frame → align_to_listing → gate。"""
    spec = FactorSpec.model_validate({
        "name": "stale", "category": "custom", "direction": 1,
        "universe": {"codes": ["600005"]}, "formula": "signal = close"})
    uf = resolve_universe_frame(spec, env.rd, dates)
    # daily 仅前 5 个交易日有真实成交（模拟 2017-02 后退市断流）
    raw = pl.DataFrame({
        "date": dates[:5],
        "code": ["600005"] * 5,
        "close": [10.0] * 5,
    }, schema={"date": pl.Date, "code": pl.String, "close": pl.Float64})
    panel = align_to_listing(raw, uf)
    return panel, uf


def test_prod_like_no_delist_column_gate_fires(env):
    """生产复现：无 delist_date 列 + 前 5 日断流 → gate 触发（双腿）。"""
    dates = _weekdays(300)
    _seed_no_delist(env, with_delist_value=None)
    panel, uf = _pit_panel(env, dates)
    assert uf.filter(pl.col("date") == dates[-1])["is_listed"][0] is True
    with pytest.raises(ValueError, match="600005"):
        assert_no_stale_listed(panel, uf)


def test_prod_like_delist_column_ingested_gate_stops(env):
    """TOOLS-A 灌入 delist_date 后同一面板不再触发（退市日 2017-02-14）。"""
    dates = _weekdays(300)
    _seed_no_delist(env, with_delist_value="20170214")
    panel, uf = _pit_panel(env, dates)
    assert uf.filter(pl.col("date") == dates[-1])["is_listed"][0] is False
    assert_no_stale_listed(panel, uf)
