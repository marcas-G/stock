"""全库对账：CH 行数 vs 源（daily_fact.parquet / bars / tick parquet metadata）。

用法：python reconcile.py        # 8 张表全量对账
      python reconcile.py daily  # 只对账 daily 层 5 表
      python reconcile.py bars   # bars_1m
      python reconcile.py tick   # tick 3 表
退出码 0=全一致，1=有差异。
"""
from __future__ import annotations

import os
import sys

import pyarrow.parquet as pq

from factorlab.core.factio import paths  # R8：路径单点

from common import connect, load_config

DAILY_SRC = str(paths.daily_fact_path())   # R8：取 factio.paths（原硬编码绝对路径）

# (table, 源行数或 None=用 SQL 求, 说明)
DAILY_TABLES = [
    ("daily", None, "18M 全量"),
    ("adj_factor", None, "拆列"),
    ("daily_basic", None, "total_mv/turnover_rate + 占位"),
    ("trade_cal", None, "distinct 交易日"),
    ("stock_basic", None, "distinct 代码"),
]


def _reconcile(client, db, table: str, src_rows: int | None) -> tuple[int, int, str]:
    ch = client.command(f"SELECT count() FROM {db}.{table}")
    if src_rows is None:
        if table == "trade_cal":
            # trade_cal 行数 = daily 源 distinct trade_date
            import polars as pl
            src_rows = (
                pl.scan_parquet(DAILY_SRC)
                .select(pl.col("trade_date").unique().count())
                .collect()
                .item()
            )
        elif table == "stock_basic":
            import polars as pl
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


def main():
    from ingest_common import discover_tasks

    client = connect()
    db = load_config()["ch"]["database"]
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    ok = True

    if which in ("all", "daily"):
        print("daily 层:", flush=True)
        for table, rows, note in DAILY_TABLES:
            ch, src, note = _reconcile(client, db, table, rows)
            ok &= ch == src

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
