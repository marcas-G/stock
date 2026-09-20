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
    # 600811.SH：退市文件无复权源；R08-DATA-I2 sidecar 补灌 2022+（pre-2022 仍 NULL；
    # 源 hfq<=0 的极少数日剔除 → 允许 <1% 的 2022+ 空洞）
    n_null, n_all, n_recent_null, n_recent_ok = _one(ch, """
        SELECT countIf(isNull(adj_factor)), count(),
               countIf(isNull(adj_factor) AND trade_date >= toDate('2022-01-01')),
               countIf(NOT isNull(adj_factor) AND trade_date >= toDate('2022-01-01'))
        FROM factorlab.adj_factor WHERE ts_code='600811.SH'""")
    assert n_all > 0 and n_recent_ok > 0, "退市股 2022+ adj 未补灌（sidecar 未消费？）"
    assert n_recent_null / (n_recent_null + n_recent_ok) < 0.01, \
        f"2022+ 空洞 {n_recent_null} 过大"
    assert n_null < n_all, "pre-2022 应保持 NULL（恢复范围=2022 起，不伪造）"


def test_daily_amount_nan_is_null(ch):
    # R37 范围收窄（1996+ 非 BJ）：NULL 计数随范围下降（实测 1,218,471；原 ≥1.25M 为全量口径）
    n_null = _one(ch, "SELECT countIf(isNull(amount)) FROM factorlab.daily")[0]
    assert n_null >= 1_200_000, f"amount NULL 数不足：{n_null}"
    # NaN 必须落成 true NULL（既不残留 NaN，也不是 0 填充）
    nan = _one(ch, "SELECT countIf(isNotNull(amount) AND isNaN(amount)) "
                   "FROM factorlab.daily")[0]
    assert nan == 0, f"daily.amount 残留 NaN：{nan}"
    # 退市无源股（600811）scope 内 amount 应为 NULL 而非 0/NaN
    n811_null = _one(ch, "SELECT countIf(isNull(amount)) FROM factorlab.daily "
                         "WHERE ts_code='600811.SH'")[0]
    assert n811_null > 0, "600811 源缺 amount 未落 NULL（scope 内 1996+）"


# ── C2 错位修复 ────────────────────────────────────────────────────────
def test_fake_codes_gone_and_600811_has_full_history(ch):
    n_fake = _one(ch, "SELECT count() FROM factorlab.daily WHERE ts_code='000018.SZ'")[0]
    assert n_fake == 0, "000018.SZ 假历史仍在"
    # R37 范围收窄后 daily 重建：600811 计数 7,598→7,098（−500 前 1996 行，范围外留盘不入库）
    n811, dmin, dmax = _one(ch, "SELECT count(), min(trade_date), max(trade_date) "
                                "FROM factorlab.daily WHERE ts_code='600811.SH'")
    assert n811 == 7098
    assert dmin == datetime.date(1996, 1, 2), dmin
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
    # R37 范围收窄：BJ 不纳入范围（stock_basic 零 .BJ；原 920305.BJ 侧车样例改为范围+覆盖面断言）
    n_bj = _one(ch, "SELECT count() FROM factorlab.stock_basic "
                     "WHERE endsWith(ts_code, '.BJ')")[0]
    assert n_bj == 0, "范围收窄后 stock_basic 不应再含 .BJ"
    n_dd = _one(ch, "SELECT count() FROM factorlab.stock_basic "
                     "WHERE delist_date IS NOT NULL")[0]
    assert n_dd > 100, f"退市目录覆盖面异常：{n_dd}"


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


# ── I7 reconcile 覆盖派生表（收口：daily 层按 clean staging 账本，--source）──
def test_reconcile_daily_covers_derived_tables():
    """I1 收口：ingest 消费 clean staging 后，daily 层对账须传 --source（期望=clean），
    rc=0 且输出 explained delta（raw−clean = quarantine + deduped，不判红）。"""
    from factorlab.core.factio import paths

    staged = sorted((paths.DATA_ROOT / "staging" / "ashare_daily").glob(
        "*/daily_fact.parquet"))
    cmd = [sys.executable, str(TOOL / "reconcile.py"), "daily"]
    if staged:
        cmd += ["--source", str(staged[-1])]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(TOOL), timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    if staged:
        assert "explained delta" in r.stdout, (
            f"clean staging 口径必须输出 explained delta：\n{r.stdout}")
    for t in ("stk_limit", "adj_detail", "adj_event"):
        assert t in r.stdout, f"reconcile 未覆盖派生表 {t}：\n{r.stdout}"
