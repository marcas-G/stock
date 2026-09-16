"""R21 TOOLS-C1 + DATA-C2 + I1 + delist_date：ingest_daily 派生纯函数测试。

断言来源 = docs/reviews/r01-2026-09-15-strict-review/report.md / findings.md：
- C1 单位：total_mv = close×total_shares（万元）；turnover = vol/(float_shares×1e4)×100
- DATA-C2 除权参考价：pre_close = round((prev - div_cash/10 + rights_price×rights_num/10)
  / (1 + div_bonus/10 + div_transfer/10), 2)，无事件日 = prev；300842.SZ 2024-04-10
  (70.9-0.8)/1.4 = 50.07
- I1：adj_factor NaN/<=0 → NULL；amount NaN → NULL
- delist_date：sidecar（last<max → last+1）+ 断流 >250 交易日兜底
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

pytest.importorskip("clickhouse_connect", reason="ch_ingest 属 T1（平台 venv）")

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import polars as pl  # noqa: E402

import ingest_daily  # noqa: E402


def _row(code, d, close, volume=0.0, total_shares=0.0, float_shares=0.0,
         div_cash=None, div_bonus=None, div_transfer=None, rights_num=None,
         rights_price=None, amount=0.0, adj_factor=1.0):
    return dict(code=code, trade_date=d, close=close, volume=volume,
                total_shares=total_shares, float_shares=float_shares,
                div_cash=div_cash, div_bonus=div_bonus,
                div_transfer=div_transfer, rights_num=rights_num,
                rights_price=rights_price, amount=amount,
                adj_factor=adj_factor)


def _frame(rows: list[dict]) -> pl.DataFrame:
    schema = {
        "code": pl.String, "trade_date": pl.Date, "close": pl.Float64,
        "volume": pl.Float64, "total_shares": pl.Float64,
        "float_shares": pl.Float64, "div_cash": pl.Float64,
        "div_bonus": pl.Float64, "div_transfer": pl.Float64,
        "rights_num": pl.Float64, "rights_price": pl.Float64,
        "amount": pl.Float64, "adj_factor": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema)


# ── C1 单位 ────────────────────────────────────────────────────────────
def test_total_mv_and_turnover_units_600519():
    """600519.SH 2026-07-31：close=1350.6 总股本=125008.16 万股 vol=5512752 股。"""
    d = datetime.date(2026, 7, 31)
    df = _frame([_row("600519.SH", d, 1350.6, volume=5512752.0,
                      total_shares=125008.16, float_shares=125008.16)])
    out = ingest_daily.derive_daily_fields(df)
    mv = out["total_mv"][0]
    tr = out["turnover_rate"][0]
    assert mv == pytest.approx(1350.6 * 125008.16, rel=1e-12)   # ≈1.688e8 万元
    assert mv > 1e8, f"total_mv 单位错（1e4）：{mv}"
    assert tr == pytest.approx(5512752.0 / (125008.16 * 1e4) * 100.0, rel=1e-9)
    assert tr == pytest.approx(0.4410, abs=5e-4), f"turnover 单位错（1e4）：{tr}"


def test_missing_shares_become_null_not_zero():
    d = datetime.date(2024, 1, 2)
    df = _frame([_row("000003.SZ", d, 2.0, volume=100.0,
                      total_shares=float("nan"), float_shares=float("nan"))])
    out = ingest_daily.derive_daily_fields(df)
    assert out["total_mv"][0] is None
    assert out["turnover_rate"][0] is None


# ── DATA-C2 除权参考价 ─────────────────────────────────────────────────
def test_pre_close_ex_rights_cash_plus_bonus_300842():
    """300842.SZ 2024-04-10：div_cash=8（元/10股）div_bonus=4 → (70.9-0.8)/1.4=50.07。"""
    df = _frame([
        _row("300842.SZ", datetime.date(2024, 4, 9), 70.9),
        _row("300842.SZ", datetime.date(2024, 4, 10), 50.98, div_cash=8.0,
             div_bonus=4.0, div_transfer=0.0, rights_num=0.0, rights_price=0.0),
    ])
    out = ingest_daily.derive_daily_fields(df).sort("trade_date")
    assert out["pre_close"][1] == pytest.approx(50.07, abs=1e-9)
    assert out["change"][1] == pytest.approx(50.98 - 50.07, abs=1e-9)
    assert out["pct_chg"][1] == pytest.approx((50.98 / 50.07 - 1) * 100,
                                              abs=1e-6)
    assert out["pct_chg"][1] > 0, "除权日 pct 应为 +1.82%，不是 raw -28%"


def test_pre_close_only_cash():
    df = _frame([
        _row("X.SZ", datetime.date(2024, 5, 9), 33.9),
        _row("X.SZ", datetime.date(2024, 5, 10), 33.0, div_cash=8.0),
    ])
    out = ingest_daily.derive_daily_fields(df).sort("trade_date")
    assert out["pre_close"][1] == pytest.approx(33.1, abs=1e-9)


def test_pre_close_only_transfer():
    df = _frame([
        _row("X.SZ", datetime.date(2024, 5, 9), 30.0),
        _row("X.SZ", datetime.date(2024, 5, 10), 20.0, div_transfer=5.0),
    ])
    out = ingest_daily.derive_daily_fields(df).sort("trade_date")
    assert out["pre_close"][1] == pytest.approx(20.0, abs=1e-9)


def test_pre_close_rights_issue():
    """配股：prev=10，配 3 股/10 股 @4 元 → (10 + 4×0.3)/1 = 11.2。"""
    df = _frame([
        _row("X.SH", datetime.date(2024, 5, 9), 10.0),
        _row("X.SH", datetime.date(2024, 5, 10), 11.0, rights_num=3.0,
             rights_price=4.0),
    ])
    out = ingest_daily.derive_daily_fields(df).sort("trade_date")
    assert out["pre_close"][1] == pytest.approx(11.2, abs=1e-9)


def test_pre_close_half_up_rounding():
    """20.25 / (1+10/10) = 10.125 → half-up 10.13（不是 banker's 10.12）。"""
    df = _frame([
        _row("X.SH", datetime.date(2024, 5, 9), 20.25),
        _row("X.SH", datetime.date(2024, 5, 10), 10.0, div_bonus=10.0),
    ])
    out = ingest_daily.derive_daily_fields(df).sort("trade_date")
    assert out["pre_close"][1] == pytest.approx(10.13, abs=1e-9)


def test_pre_close_no_event_equals_prev_close_and_not_rounded():
    """无事件日 = prev_close 原值（不做 round，避免伪造精度）。"""
    df = _frame([
        _row("X.SH", datetime.date(2024, 5, 9), 10.123456),
        _row("X.SH", datetime.date(2024, 5, 10), 10.5),
    ])
    out = ingest_daily.derive_daily_fields(df).sort("trade_date")
    assert out["pre_close"][1] == pytest.approx(10.123456, abs=1e-12)


def test_pre_close_all_null_event_columns_delisted_style():
    """退市股无事件列（全 NULL）：pre_close = prev_close，除权逻辑不误触发。"""
    df = _frame([
        _row("000003.SZ", datetime.date(2002, 6, 12), 2.0),
        _row("000003.SZ", datetime.date(2002, 6, 13), 2.1),
    ])
    out = ingest_daily.derive_daily_fields(df).sort("trade_date")
    assert out["pre_close"][1] == pytest.approx(2.0, abs=1e-12)
    assert out["change"][1] == pytest.approx(0.1, abs=1e-12)


def test_pre_close_first_row_null():
    df = _frame([_row("X.SH", datetime.date(2024, 5, 9), 10.0)])
    out = ingest_daily.derive_daily_fields(df)
    assert out["pre_close"][0] is None
    assert out["change"][0] is None
    assert out["pct_chg"][0] is None


# ── I1 空值语义 ────────────────────────────────────────────────────────
def test_nullify_invalid_adj_factor_and_amount():
    d = datetime.date(2024, 1, 2)
    df = _frame([
        _row("A.SZ", d, 1.0, adj_factor=float("nan"), amount=float("nan")),
        _row("B.SZ", d, 1.0, adj_factor=-3.5, amount=5.0),
        _row("C.SZ", d, 1.0, adj_factor=0.0, amount=0.0),
        _row("D.SZ", d, 1.0, adj_factor=2.5, amount=7.0),
    ]).sort("code")
    out = ingest_daily.nullify_invalid(df)
    assert out["adj_factor"].to_list() == [None, None, None, 2.5]
    assert out["amount"].to_list() == [None, 5.0, 0.0, 7.0], \
        "amount=0 是真实值，不允许被吞"


# ── delist_date ────────────────────────────────────────────────────────
def _d(i: int) -> datetime.date:
    return datetime.date(2024, 1, 1) + datetime.timedelta(days=i)


def test_compute_delist_dates_sidecar_and_stale_fallback():
    dates = [_d(i) for i in range(300)]
    rows = ([_row("CAL.SZ", d, 1.0) for d in dates] +   # 全市场日历（生产帧形态）
            [_row("A.SZ", dates[10], 1.0)] +
            [_row("B.SZ", dates[10], 1.0)] +
            [_row("C.SZ", dates[-1], 1.0)] +
            [_row("D.SZ", dates[200], 1.0)] +
            [_row("E.SZ", dates[40], 1.0)])
    df = _frame(rows)
    sidecar = pl.DataFrame({
        "code": ["A.SZ", "C.SZ", "NOT_IN_FACT.SZ"],
        "last_trade_date": [dates[10], dates[-1], dates[-1]]},
        schema={"code": pl.String, "last_trade_date": pl.Date})
    out = ingest_daily.compute_delist_dates(df, sidecar, stale_trading_days=250)
    m = {r["code"]: r["delist_date"] for r in out.iter_rows(named=True)}
    assert m["A.SZ"] == dates[11], "sidecar 权威：last<max → last+1"
    assert m["B.SZ"] == dates[11], "兜底：断流 289 交易日 > 250 → last+1"
    assert m["C.SZ"] is None, "仍交易（last==max）→ 不退市"
    assert m["D.SZ"] is None, "断流 99 ≤ 250 → 不判退市"
    assert m["E.SZ"] == dates[41], "兜底：断流 259 > 250 → last+1"
    assert "NOT_IN_FACT.SZ" not in m


def test_compute_delist_dates_without_sidecar():
    dates = [_d(i) for i in range(300)]
    df = _frame([_row("CAL.SZ", d, 1.0) for d in dates] +
                [_row("A.SZ", dates[10], 1.0), _row("B.SZ", dates[-1], 1.0)])
    out = ingest_daily.compute_delist_dates(df, None, stale_trading_days=250)
    m = {r["code"]: r["delist_date"] for r in out.iter_rows(named=True)}
    assert m["A.SZ"] == dates[11]
    assert m["B.SZ"] is None
