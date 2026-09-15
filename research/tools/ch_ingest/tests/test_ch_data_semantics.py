"""R21 TOOLS-C1/C2/DATA-C2/I1/I7：CH 生产数据语义（T1 集成，CH 不可用则 skip）。

每条断言对应 findings.md 的修复口径（300842 除权参考价、600519 单位、adj NULL 语义、
600811 归位、delist_date、stk_limit 除权日带）。reconcile 覆盖检查锁 stdout 契约。
"""
from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("clickhouse_connect", reason="ch_ingest 属 T1（平台 venv）")

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))


@pytest.fixture(scope="module")
def ch():
    from common import connect
    try:
        c = connect()
        c.query("SELECT 1")
    except Exception as exc:  # noqa: BLE001 —— 无 CH 环境跳过而非假通过
        pytest.skip(f"ClickHouse 不可用：{exc}")
    return c


def _one(ch, sql):
    return ch.query(sql).result_rows[0]


# ── C1 单位 ────────────────────────────────────────────────────────────
def test_total_mv_and_turnover_rate_units(ch):
    mv, tr = _one(ch, "SELECT total_mv, turnover_rate FROM factorlab.daily_basic "
                     "WHERE ts_code='600519.SH' AND trade_date=toDate('2026-07-31')")
    assert mv == pytest.approx(1350.6 * 125008.16, rel=1e-9), mv
    assert tr == pytest.approx(0.4410, abs=5e-4), tr


# ── DATA-C2 除权参考价 ─────────────────────────────────────────────────
def test_ex_rights_pre_close_300842(ch):
    pc, change, pct = _one(
        ch, "SELECT pre_close, change, pct_chg FROM factorlab.daily "
            "WHERE ts_code='300842.SZ' AND trade_date=toDate('2024-04-10')")
    assert pc == pytest.approx(50.07, abs=1e-9)
    assert change == pytest.approx(50.98 - 50.07, abs=1e-9)
    assert pct == pytest.approx((50.98 / 50.07 - 1) * 100, abs=1e-6)
    assert pct > 0


def test_2025_ex_event_pre_close_matches_formula(ch):
    """2025 全部除权事件日：daily.pre_close == 除权参考价公式（raw 前收必败）。"""
    bad, total = _one(ch, """
        SELECT countIf(abs(pre_close - expected) > 0.011), count() FROM (
          SELECT x.pre_close,
                 toFloat64(round((x.prev - ifNull(a.div_cash,0)/10
                        + ifNull(a.rights_price,0)*ifNull(a.rights_num,0)/10)
                       / (1 + ifNull(a.div_bonus,0)/10 + ifNull(a.div_transfer,0)/10), 2)) AS expected
          FROM (
            SELECT ts_code, trade_date, close, pre_close,
                   lagInFrame(close) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev
            FROM factorlab.daily
          ) AS x
          JOIN factorlab.adj_detail a USING (ts_code, trade_date)
          WHERE a.trade_date >= toDate('2025-01-01')
            AND a.trade_date <= toDate('2025-12-31')
            AND (ifNull(a.div_cash,0) != 0 OR ifNull(a.div_bonus,0) != 0
                 OR ifNull(a.div_transfer,0) != 0 OR ifNull(a.rights_num,0) != 0)
        )""")
    assert total > 200, f"2025 事件样本量异常：{total}"
    assert bad == 0, f"{bad}/{total} 除权日 pre_close 仍为 raw 前收"


# ── I1 空值语义 ────────────────────────────────────────────────────────
def test_adj_factor_nonpositive_and_nan_are_null(ch):
    bad = _one(ch, "SELECT countIf(NOT isNull(adj_factor) AND "
                   "(isNaN(adj_factor) OR adj_factor <= 0)) FROM factorlab.adj_factor")[0]
    assert bad == 0
    bad_latest = _one(ch, """
        SELECT count() FROM (
          SELECT ts_code, argMax(adj_factor, trade_date) AS last_adj
          FROM factorlab.adj_factor GROUP BY ts_code
        ) WHERE last_adj <= 0""")[0]
    assert bad_latest == 0, "仍存在 latest adj_factor<=0（qfq 基准破坏）"
    # 600811.SH 全部来自退市文件（无复权源）→ adj_factor 全 NULL
    n_null, n_all = _one(ch, "SELECT countIf(isNull(adj_factor)), count() "
                             "FROM factorlab.adj_factor WHERE ts_code='600811.SH'")
    assert n_all > 0 and n_null == n_all


def test_daily_amount_nan_is_null(ch):
    n_null = _one(ch, "SELECT countIf(isNull(amount)) FROM factorlab.daily")[0]
    assert n_null >= 1_250_000, f"amount NULL 数不足：{n_null}"
    v = _one(ch, "SELECT amount FROM factorlab.daily WHERE ts_code='600811.SH' "
                 "AND trade_date=toDate('1994-01-06')")[0]
    assert v is None


# ── C2 错位修复 ────────────────────────────────────────────────────────
def test_fake_codes_gone_and_600811_has_full_history(ch):
    n_fake = _one(ch, "SELECT count() FROM factorlab.daily WHERE ts_code='000018.SZ'")[0]
    assert n_fake == 0, "000018.SZ 假历史仍在"
    n811, dmax = _one(ch, "SELECT count(), max(trade_date) FROM factorlab.daily "
                           "WHERE ts_code='600811.SH'")
    assert n811 == 7598
    assert dmax == datetime.date(2025, 4, 14), dmax
    dd811 = _one(ch, "SELECT delist_date FROM factorlab.stock_basic "
                     "WHERE ts_code='600811.SH'")[0]
    assert dd811 == datetime.date(2025, 4, 15), dd811


# ── delist_date ────────────────────────────────────────────────────────
def test_delist_date_ingested_and_pit_semantics(ch):
    cols = {r[0] for r in ch.query(
        "SELECT name FROM system.columns WHERE database='factorlab' "
        "AND table='stock_basic'").result_rows}
    assert "delist_date" in cols
    bad = _one(ch, "SELECT count() FROM factorlab.stock_basic "
                   "WHERE delist_date IS NOT NULL AND delist_date <= list_date")[0]
    assert bad == 0
    dd = _one(ch, "SELECT delist_date FROM factorlab.stock_basic "
                  "WHERE ts_code='600005.SH'")[0]
    last = _one(ch, "SELECT max(trade_date) FROM factorlab.daily "
                    "WHERE ts_code='600005.SH'")[0]
    assert dd is not None and last is not None
    assert dd == last + datetime.timedelta(days=1)
    # PIT：2026-08-14 时 600005 已退市（is_listed = t < delist_date）
    alive = _one(ch, "SELECT count() FROM factorlab.stock_basic "
                     "WHERE ts_code='600005.SH' AND delist_date IS NOT NULL "
                     "AND toDate('2026-08-14') < delist_date")[0]
    assert alive == 0
    # 侧车/兜底覆盖 920305.BJ（空文件 + 断流）
    dd2 = _one(ch, "SELECT delist_date FROM factorlab.stock_basic "
                   "WHERE ts_code='920305.BJ'")[0]
    assert dd2 is not None


# ── I4 stk_limit 除权日带 ──────────────────────────────────────────────
def test_stk_limit_ex_date_band_uses_adjusted_pre_close(ch):
    up, dn = _one(ch, "SELECT up_limit, down_limit FROM factorlab.stk_limit "
                      "WHERE ts_code='300842.SZ' AND trade_date=toDate('2024-04-10')")
    assert up == pytest.approx(60.08, abs=1e-9)
    assert dn == pytest.approx(40.06, abs=1e-9)
    bad = _one(ch, """
        SELECT countIf(abs(s.up_limit - toFloat64(intDiv(toInt64(round(d.pre_close*100))*120+50,100))/100.0) > 1e-9)
        FROM factorlab.daily d JOIN factorlab.stk_limit s USING (ts_code, trade_date)
        JOIN factorlab.adj_event e USING (ts_code, trade_date)
        WHERE toYear(d.trade_date)=2025
          AND (startsWith(d.ts_code,'300') OR startsWith(d.ts_code,'301') OR startsWith(d.ts_code,'302'))""")[0]
    assert bad == 0, f"{bad} 个 2025 创业板除权日 stk_limit 未按除权参考价派生"


# ── I7 reconcile 覆盖派生表 ────────────────────────────────────────────
def test_reconcile_daily_covers_derived_tables():
    r = subprocess.run([sys.executable, str(TOOL / "reconcile.py"), "daily"],
                       capture_output=True, text=True, cwd=str(TOOL), timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    for t in ("stk_limit", "adj_detail", "adj_event"):
        assert t in r.stdout, f"reconcile 未覆盖派生表 {t}：\n{r.stdout}"
