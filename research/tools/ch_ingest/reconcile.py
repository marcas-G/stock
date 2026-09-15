"""全库对账：CH 行数 vs 源（daily_fact.parquet / bars / tick parquet metadata）。

R21 TOOLS-I7 扩展（旧版只对 5 张 daily 表行数）：
- daily 恒等式（pre_close/change/pct_chg；pre_close NULL 仅组内首行）与日期范围；
- 派生表：
  - stk_limit —— 期望行数由 `derive_stk_limit.expected_rows_sql` 独立复算（同一
    谓词单点，防 INSERT 半量/夹带）；日期范围 ⊂ daily；up/down>0 且 down<=up；
    无 <1996-12-16 行；每行都能 join 到 daily 的非空 pre_close；
  - adj_detail —— 行数 == daily_fact 全量；uniq(ts_code,trade_date) == 行数；
    日期范围 == daily；
  - adj_event —— 行数 == parquet 事件谓词复算（div_cash/div_bonus/div_transfer/
    rights_num 任一非 0）；uniq == 行数。

用法：python reconcile.py        # 全表对账
      python reconcile.py daily  # daily 层 5 表 + 派生表
      python reconcile.py bars   # bars_1m
      python reconcile.py tick   # tick 3 表
退出码 0=全一致，1=有差异。
"""
from __future__ import annotations

import os
import sys

import polars as pl
import pyarrow.parquet as pq

from factorlab.core.factio import paths  # R8：路径单点

from common import connect, load_config

DAILY_SRC = str(paths.daily_fact_path())   # R8：取 factio.paths（原硬编码绝对路径）
EVENT_COLS = ["div_cash", "div_bonus", "div_transfer", "rights_num"]
MIN_LIMIT_DATE = "1996-12-16"

# (table, 源行数或 None=用 SQL 求, 说明)
DAILY_TABLES = [
    ("daily", None, "18M 全量"),
    ("adj_factor", None, "拆列（NaN/<=0 → NULL）"),
    ("daily_basic", None, "total_mv/turnover_rate + 占位"),
    ("trade_cal", None, "distinct 交易日"),
    ("stock_basic", None, "distinct 代码 + delist_date"),
]


def _source_event_count() -> int:
    """parquet 侧除权事件行数（与 adj_backfill 的 ≠0 语义一致）。"""
    lf = pl.scan_parquet(DAILY_SRC)
    cond = None
    for c in EVENT_COLS:
        e = pl.col(c).fill_null(0.0).fill_nan(0.0) != 0
        cond = e if cond is None else (cond | e)
    return int(lf.select(cond.sum().alias("n")).collect().item())


def _source_date_range() -> tuple:
    r = (pl.scan_parquet(DAILY_SRC)
         .select(pl.col("trade_date").min().alias("dmin"),
                 pl.col("trade_date").max().alias("dmax"))
         .collect().row(0))
    return r[0], r[1]


def _reconcile(client, db, table: str, src_rows: int | None) -> tuple[int, int, str]:
    ch = client.command(f"SELECT count() FROM {db}.{table}")
    if src_rows is None:
        if table == "trade_cal":
            # trade_cal 行数 = daily 源 distinct trade_date
            src_rows = (
                pl.scan_parquet(DAILY_SRC)
                .select(pl.col("trade_date").unique().count())
                .collect()
                .item()
            )
        elif table == "stock_basic":
            src_rows = (
                pl.scan_parquet(DAILY_SRC)
                .select(pl.col("code").unique().count())
                .collect()
                .item()
            )
        else:
            src_rows = pq.ParquetFile(DAILY_SRC).metadata.num_rows
    note = "一致" if ch == src_rows else f"不一致 (差 {ch - src_rows:+,})"
    print(f"  {table:12s} CH={ch:>16,} 源={src_rows:>16,}  {note}", flush=True)
    return ch, src_rows, note


def _check_daily_invariants(client, db) -> bool:
    """daily 派生列恒等式 + 日期范围（与 parquet 一致）。"""
    ok = True
    checks = [
        ("pre_close/change 恒等式",
         f"SELECT countIf(abs((close - pre_close) - change) > 1e-6) "
         f"FROM {db}.daily WHERE pre_close IS NOT NULL AND change IS NOT NULL"),
        ("pct_chg 恒等式",
         f"SELECT countIf(abs(pct_chg - (close / pre_close - 1) * 100) > 1e-4) "
         f"FROM {db}.daily WHERE pre_close IS NOT NULL AND pct_chg IS NOT NULL"),
        ("pre_close NULL 仅组内首行",
         f"SELECT count() FROM (SELECT pre_close, row_number() OVER "
         f"(PARTITION BY ts_code ORDER BY trade_date) AS rn FROM {db}.daily) "
         f"WHERE pre_close IS NULL AND rn > 1"),
    ]
    for name, sql in checks:
        bad = client.query(sql).result_rows[0][0]
        if bad:
            ok = False
        print(f"  不变量 {name:22s} 违规={bad:,}  {'OK' if not bad else 'FAIL'}",
              flush=True)
    dmin, dmax = _source_date_range()
    cmin, cmax = client.query(
        f"SELECT min(trade_date), max(trade_date) FROM {db}.daily").result_rows[0]
    same = (cmin == dmin and cmax == dmax)
    ok &= same
    print(f"  daily 日期范围 CH={cmin}..{cmax} 源={dmin}..{dmax}  "
          f"{'一致' if same else '不一致'}", flush=True)
    return ok


def _check_derived_tables(client, db, fact_rows: int) -> bool:
    """stk_limit / adj_detail / adj_event：行数 + 范围 + 不变量。"""
    from derive_stk_limit import expected_rows_sql
    ok = True

    # ---- stk_limit ----
    n, mn, mx, bad_band = client.query(
        f"SELECT count(), min(trade_date), max(trade_date), "
        f"countIf(up_limit <= 0 OR down_limit <= 0 OR down_limit > up_limit) "
        f"FROM {db}.stk_limit").result_rows[0]
    exp = client.query(expected_rows_sql(db)).result_rows[0][0]
    early = client.query(
        f"SELECT count() FROM {db}.stk_limit "
        f"WHERE trade_date < toDate('{MIN_LIMIT_DATE}')").result_rows[0][0]
    dangling = client.query(
        f"SELECT count() FROM {db}.stk_limit s WHERE NOT EXISTS ("
        f"  SELECT 1 FROM {db}.daily d WHERE d.ts_code = s.ts_code "
        f"  AND d.trade_date = s.trade_date AND d.pre_close IS NOT NULL)"
    ).result_rows[0][0]
    good = (n == exp and bad_band == 0 and early == 0 and dangling == 0)
    ok &= good
    print(f"  stk_limit    CH={n:>16,} 期望={exp:>16,}  日期 {mn}..{mx}  "
          f"band违例={bad_band} 早于{MIN_LIMIT_DATE}={early} 悬空={dangling}  "
          f"{'一致' if good else '不一致'}", flush=True)

    # ---- adj_detail ----
    n_d, u_d = client.query(
        f"SELECT count(), uniqExact((ts_code, trade_date)) FROM {db}.adj_detail"
    ).result_rows[0]
    dmn, dmx = client.query(
        f"SELECT min(trade_date), max(trade_date) FROM {db}.adj_detail").result_rows[0]
    dmin, dmax = _source_date_range()
    good_d = (n_d == fact_rows and u_d == n_d and dmn == dmin and dmx == dmax)
    ok &= good_d
    print(f"  adj_detail   CH={n_d:>16,} 源={fact_rows:>16,}  uniq={u_d:>16,}  "
          f"日期 {dmn}..{dmx}  {'一致' if good_d else '不一致'}", flush=True)

    # ---- adj_event ----
    exp_ev = _source_event_count()
    n_e, u_e = client.query(
        f"SELECT count(), uniqExact((ts_code, trade_date)) FROM {db}.adj_event"
    ).result_rows[0]
    good_e = (n_e == exp_ev and u_e == n_e)
    ok &= good_e
    print(f"  adj_event    CH={n_e:>16,} 源={exp_ev:>16,}  uniq={u_e:>16,}  "
          f"{'一致' if good_e else '不一致'}", flush=True)
    return ok


def main():
    from ingest_common import discover_tasks

    client = connect()
    db = load_config()["ch"]["database"]
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    ok = True
    fact_rows = None

    if which in ("all", "daily"):
        print("daily 层:", flush=True)
        for table, rows, note in DAILY_TABLES:
            ch, src, note = _reconcile(client, db, table, rows)
            if table == "daily":
                fact_rows = src
            ok &= ch == src
        print("daily 不变量:", flush=True)
        ok &= _check_daily_invariants(client, db)
        print("派生表:", flush=True)
        ok &= _check_derived_tables(client, db, fact_rows)

    if which in ("all", "bars"):
        print("bars_1m:", flush=True)
        from ingest_common import reconcile as rc
        ch, src = rc("bars_1m", discover_tasks("bars_1m"))
        ok &= ch == src

    if which in ("all", "tick"):
        for t in ("tick_trades", "tick_orders", "tick_snapshots"):
            print(f"{t}:", flush=True)
            from ingest_common import reconcile as rc
            ch, src = rc(t, discover_tasks(t))
            ok &= ch == src

    print("全库一致" if ok else "存在差异", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    os.environ.setdefault("PYARROW_JEMALLOC", "0")
    main()
