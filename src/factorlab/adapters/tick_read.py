"""tick 事实库读适配器（P-1 的本地 parquet 实现）：单点读 (表, 日)。

收敛既有 ≥6 份"读 tick_fact 月表"实现（lob_fact 工具链内）：
路径派生与列契约取 core.factio（paths/schema/tick_month），本模块只做 I/O。
读序：浮出月目录全部 part → 过滤 trade_date（+ 可选 code）→ 投影到列契约 → collect。
行序不承诺（调用方按需排序）；内存纪律：投影裁剪在 scan 阶段完成。
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from factorlab.core.factio import paths
from factorlab.core.factio.schema import (TICK_ORDERS_COLS, TICK_SNAP_COLS,
                                          TICK_TRADES_COLS)
from factorlab.core.factio import paths as _paths

_TABLE_COLS = {
    "trades": TICK_TRADES_COLS,
    "orders": TICK_ORDERS_COLS,
    "snapshots": TICK_SNAP_COLS,
}


def tick_month_files(table_dir: str, day: str) -> list[Path]:
    """该 (表, 日) 所在月目录下全部 part 文件（排序确定；目录缺失 → 空表）。

    从 core/factio 下沉（文件系统枚举属 I/O；core 只保留路径派生与表名映射）。
    """
    d = _paths.tick_month_dir(table_dir, day)
    return sorted(d.glob("*.parquet")) if d.is_dir() else []


def read_tick_table(table: str, day: str, *, codes: Sequence[str] | None = None,
                    columns: Sequence[str] | None = None) -> pl.DataFrame:
    """读 tick_fact 的 (表, 日) 数据。

    table ∈ {trades, orders, snapshots}（目录名）；day = 'YYYYMMDD'。
    codes 给定时按 code 过滤（ts_code 带后缀，与事实库一致）。
    缺月目录/无 part → FileNotFoundError（不静默返回空表）。
    """
    if table not in _TABLE_COLS:
        raise ValueError(f"未知 tick 表: {table!r}（可用: {sorted(_TABLE_COLS)}）")
    files = tick_month_files(table, day)
    if not files:
        raise FileNotFoundError(
            f"无数据: tick_fact/{table} {day}（{paths.tick_month_dir(table, day)}）")
    date = dt.datetime.strptime(day, "%Y%m%d").date()
    proj = list(columns) if columns is not None else list(_TABLE_COLS[table])
    lf = pl.scan_parquet(files).filter(pl.col("trade_date") == date)
    if codes is not None:
        lf = lf.filter(pl.col("code").is_in(list(codes)))
    return lf.select(proj).collect()
