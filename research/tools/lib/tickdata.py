"""tick / lob 事实读取薄封装（R4a）：投影声明 + 缺数据语义 + 平台单点转发。

收敛前：研究侧 ≥5 份各自实现（`run_lob_batch._read_date`、`factor_panel._read_tick/_read_lob`、
`diag/calibrate_w1`、`diag/verify_cancels_sample`、`diag/audit_w5`）+ 1 个 stale import。
收敛后：本模块是研究侧唯一入口，实现全部在平台（`adapters/tick_read`、`adapters/lob_read`）。

**投影**是研究侧声明（LOB 批算只需这些列，投影裁剪是内存纪律）；本模块 import 期校验
每个投影必须是对平台契约列的**子集**——契约增删列时这里会立刻报错，而不是静默漂移。
"""
from __future__ import annotations

import datetime as _dt
import os as _os
import sys as _sys

# 允许脚本直接 `from lib import tickdata`：把 tools/ 放上 sys.path 后经 _env 注入平台
# （平台路径注入**只经 _env**，此处只是让 tools/ 自身可导入）。
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from _env import ensure_platform as _ensure_platform  # noqa: E402

_ensure_platform()
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from factorlab.adapters.lob_read import read_lob_table
from factorlab.adapters.tick_read import read_tick_table
from factorlab.core.factio.schema import (CANCELS_COLS, TICK_ORDERS_COLS,
                                          TICK_SNAP_COLS, TICK_TRADES_COLS)

# 研究侧投影（顺序沿用收敛前的历史声明，保证批算逐字节等价）
PROJECTIONS: dict[str, list[str]] = {
    "orders": ["code", "time_ms", "order_type", "bs", "price_x10000",
               "volume", "exch_order_no"],
    "trades": ["code", "time_ms", "price_x10000", "volume", "ask_seq", "bid_seq"],
    "cancels": ["code", "time_ms", "side", "order_ref", "volume"],
    "snapshots": ["code", "time_ms"] + [f"{s}_{k}{i}" for s in ("bid", "ask")
                                        for k in ("p", "v") for i in range(1, 11)],
}
TICK_TABLES: tuple[str, ...] = ("orders", "trades", "snapshots", "cancels")
LOB_TABLES: tuple[str, ...] = ("lob_events", "lob_sweep_meta", "lob_checkpoints")

_CONTRACT = {"orders": TICK_ORDERS_COLS, "trades": TICK_TRADES_COLS,
             "cancels": CANCELS_COLS, "snapshots": TICK_SNAP_COLS}

# import 期门：投影必须是契约子集（契约改名/删列 → 立刻失败，不静默）
for _tbl, _cols in PROJECTIONS.items():
    _unknown = [c for c in _cols if c not in _CONTRACT[_tbl]]
    if _unknown:
        raise RuntimeError(
            f"tick 投影与平台契约不符（{_tbl}）：{_unknown} 不在 "
            f"core/factio/schema.{_tbl.upper()} 契约中")


def _day_str(day: str | _dt.date) -> str:
    return f"{day:%Y%m%d}" if isinstance(day, _dt.date) else str(day)


def read_tick(table: str, day: str | _dt.date, *, codes: Sequence[str] | None = None,
              columns: Sequence[str] | None = None, root: str | Path | None = None,
              missing_ok: bool = False) -> pl.DataFrame | None:
    """读 tick_fact (表, 日)。columns 缺省 = 该表的研究侧投影。

    missing_ok=True → 缺月目录/无 part 返回 None（批算按日跳过）；False → 抛 FileNotFoundError。
    """
    if table not in TICK_TABLES:
        raise ValueError(f"未知 tick 表: {table!r}（可用: {sorted(TICK_TABLES)}）")
    cols = list(PROJECTIONS[table]) if columns is None else list(columns)
    try:
        return read_tick_table(table, _day_str(day), codes=codes, columns=cols,
                               root=Path(root) if root is not None else None)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise


def read_lob(table: str, day: str | _dt.date, *, codes: Sequence[str] | None = None,
             columns: Sequence[str] | None = None, root: str | Path | None = None,
             missing_ok: bool = False) -> pl.DataFrame | None:
    """读 lob_fact (表, 日)。columns 缺省 = 全列（与收敛前一致）。"""
    if table not in LOB_TABLES:
        raise ValueError(f"未知 lob 表: {table!r}（可用: {sorted(LOB_TABLES)}）")
    try:
        return read_lob_table(table, _day_str(day), codes=codes, columns=columns,
                              root=Path(root) if root is not None else None)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
