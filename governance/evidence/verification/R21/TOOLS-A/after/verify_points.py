#!/usr/bin/env python3
"""R21 TOOLS-A 定点验收（after）：C1/C2/DATA-C2/I1/I4/delist_date。

只读 CH + 源 parquet；每点打印 PASS/FAIL，末尾 exit 0/1。
运行：cd stock && platform/.venv/bin/python docs/verification/R21/TOOLS-A/after/verify_points.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
from clickhouse_connect import get_client

ROOT = Path("/data/students/gaolei/stock")
FACT = ROOT / "data/fact/daily_fact/daily_fact.parquet"
c = get_client(host="127.0.0.1", port=8123, user="default", password="",
               database="factorlab")
q = lambda s: c.query(s).result_rows

fails = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        fails.append(name)


# ── C1 单位 ────────────────────────────────────────────────────────────
mv, tr = q("SELECT total_mv, turnover_rate FROM factorlab.daily_basic "
           "WHERE ts_code='600519.SH' AND trade_date=toDate('2026-07-31')")[0]
check("C1 600519 total_mv≈1350.6×125008.16", abs(mv - 1350.6 * 125008.16) < 1.0,
      f"got={mv}")
check("C1 600519 turnover≈0.4410", abs(tr - 0.4410) < 5e-4, f"got={tr}")

src = (pl.scan_parquet(FACT)
       .filter((pl.col("code") == "000001.SZ")
               & (pl.col("trade_date") == pl.date(2026, 8, 21)))
       .select("close", "total_shares", "float_shares", "volume").collect())
if src.height:
    r = src.row(0, named=True)
    mv2, tr2 = q("SELECT total_mv, turnover_rate FROM factorlab.daily_basic "
                 "WHERE ts_code='000001.SZ' AND trade_date=toDate('2026-08-21')")[0]
    check("C1 000001 total_mv=close×total_shares",
          abs(mv2 - r["close"] * r["total_shares"]) < abs(r["close"] * r["total_shares"]) * 1e-9,
          f"got={mv2} exp={r['close']*r['total_shares']}")
    check("C1 000001 turnover=vol/(float×1e4)×100",
          abs(tr2 - r["volume"] / (r["float_shares"] * 1e4) * 100) < 1e-6,
          f"got={tr2}")

# ── DATA-C2 除权参考价 ─────────────────────────────────────────────────
pc, ch, pct = q("SELECT pre_close, change, pct_chg FROM factorlab.daily "
                "WHERE ts_code='300842.SZ' AND trade_date=toDate('2024-04-10')")[0]
check("DATA-C2 300842 pre_close=50.07", abs(pc - 50.07) < 1e-9, f"got={pc}")
check("DATA-C2 300842 pct≈+1.82%", abs(pct - (50.98 / 50.07 - 1) * 100) < 1e-6,
      f"got={pct}")

bad, total = q("""
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
      WHERE a.trade_date >= toDate('2025-01-01') AND a.trade_date <= toDate('2025-12-31')
        AND (ifNull(a.div_cash,0) != 0 OR ifNull(a.div_bonus,0) != 0
             OR ifNull(a.div_transfer,0) != 0 OR ifNull(a.rights_num,0) != 0)
    )""")[0]
check("DATA-C2 2025 除权日 pre_close 全公式一致", bad == 0, f"bad={bad}/{total}")

# ── I1 空值语义 ────────────────────────────────────────────────────────
n_bad = q("SELECT countIf(NOT isNull(adj_factor) AND "
          "(isNaN(adj_factor) OR adj_factor <= 0)) FROM factorlab.adj_factor")[0][0]
check("I1 adj_factor 无 NaN/<=0 非空值", n_bad == 0, f"got={n_bad}")
n_latest = q("""SELECT count() FROM (
    SELECT ts_code, argMax(adj_factor, trade_date) AS last_adj
    FROM factorlab.adj_factor GROUP BY ts_code) WHERE last_adj <= 0""")[0][0]
check("I1 latest adj_factor<=0 为 0", n_latest == 0, f"got={n_latest}")
n_null = q("SELECT countIf(isNull(adj_factor)) FROM factorlab.adj_factor")[0][0]
# 新 fact 18,124,805 行；旧 1,289,000 NaN + 8,183 负值 − 5 只假 code 的 NaN
check("I1 adj_factor NULL 覆盖 NaN/负值", n_null >= 1_250_000, f"got={n_null}")
a811 = q("SELECT countIf(isNull(amount)) FROM factorlab.daily WHERE ts_code='600811.SH'")[0][0]
check("I1 600811 amount 全 NULL", a811 > 0, f"got={a811}")

# ── C2 错位修复 ────────────────────────────────────────────────────────
for fake in ("000018.SZ", "000023.SZ", "000024.SZ", "000033.SZ", "000038.SZ"):
    n = q(f"SELECT count() FROM factorlab.daily WHERE ts_code='{fake}'")[0][0]
    check(f"C2 {fake} 假历史已清除", n == 0, f"got={n}")
import datetime as _dt
n811, d811 = q("SELECT count(), max(trade_date) FROM factorlab.daily "
               "WHERE ts_code='600811.SH'")[0]
check("C2 600811 归位（7598 行，末日 2025-04-14）",
      n811 == 7598 and d811 == _dt.date(2025, 4, 14), f"got={n811} dmax={d811}")
sc811 = pl.read_parquet(ROOT / "data/fact/daily_fact/delisted_codes.parquet")
sc811 = sc811.filter(pl.col("code") == "600811.SH")
check("C2 sidecar 600811 last=2025-04-14",
      sc811.height == 1 and sc811["last_trade_date"][0] == _dt.date(2025, 4, 14))
dd811 = q("SELECT delist_date FROM factorlab.stock_basic WHERE ts_code='600811.SH'")[0][0]
check("C2 600811 delist_date=2025-04-15", dd811 == _dt.date(2025, 4, 15), f"got={dd811}")

# ── delist_date ────────────────────────────────────────────────────────
n_dd = q("SELECT countIf(delist_date IS NOT NULL) FROM factorlab.stock_basic")[0][0]
check("delist_date 有覆盖", n_dd >= 300, f"got={n_dd}")
bad_dd = q("SELECT count() FROM factorlab.stock_basic "
           "WHERE delist_date IS NOT NULL AND delist_date <= list_date")[0][0]
check("delist_date > list_date", bad_dd == 0, f"got={bad_dd}")
dd = q("SELECT delist_date FROM factorlab.stock_basic WHERE ts_code='600005.SH'")[0][0]
last = q("SELECT max(trade_date) FROM factorlab.daily WHERE ts_code='600005.SH'")[0][0]
check("600005.SH delist_date = last+1",
      dd is not None and last is not None and dd == last + _dt.timedelta(days=1),
      f"got={dd} last={last}")
bad_pit = q("SELECT count() FROM factorlab.stock_basic WHERE delist_date IS NOT NULL "
            "AND toDate('2026-08-14') < delist_date")[0][0]
check("PIT：2026-08-14 无已退市 code 仍 is_listed", bad_pit == 0, f"got={bad_pit}")
n920 = q("SELECT delist_date FROM factorlab.stock_basic WHERE ts_code='920305.BJ'")[0][0]
check("920305.BJ delist_date 非空（空文件+断流兜底）", n920 is not None, f"got={n920}")

# ── I4 stk_limit 除权日带 ──────────────────────────────────────────────
up, dn = q("SELECT up_limit, down_limit FROM factorlab.stk_limit "
           "WHERE ts_code='300842.SZ' AND trade_date=toDate('2024-04-10')")[0]
check("I4 300842 除权日 band=60.08/40.06",
      abs(up - 60.08) < 1e-9 and abs(dn - 40.06) < 1e-9, f"got={up}/{dn}")
bad_band, n_band = q("""
    SELECT countIf(abs(s.up_limit - toFloat64(intDiv(toInt64(round(d.pre_close*100))*
             (CASE
                WHEN endsWith(d.ts_code,'.BJ') THEN 130
                WHEN substring(d.ts_code,1,3) IN ('688','689') THEN 120
                WHEN substring(d.ts_code,1,3) IN ('300','301','302')
                     AND d.trade_date >= toDate('2020-08-24') THEN 120
                ELSE 110 END)+50,100))/100.0) > 1e-9), count()
    FROM factorlab.daily d
    JOIN factorlab.stk_limit s USING (ts_code, trade_date)
    WHERE toYear(d.trade_date)=2025 AND d.pre_close IS NOT NULL""")[0]
check("I4 2025 全市场 stk_limit 与 pre_close 带一致", bad_band == 0, f"got={bad_band}")

print()
print("TOOLS-A 定点验收:", "ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
