"""lob_fact 读单点（R4a）：收敛研究侧 3 份 lob 读实现。

- 收敛前：`factor_panel._read_lob`、`compact_lob`（字节摘要路径）、`audit_w5`（state/manifest）各自拼路径。
- 布局：`{root}/{table}/year=Y/month=M/YYYYMMDD.parquet`（**日一文件**——与 tick 的月 part 关键差别：
  读某日**不得**混入同月其他日；有专门测试锁这一点）。
- 路径规则取 `core/factio/partitions`，列契约取 `core/factio/schema`；本模块只做 I/O。
- `columns=None` → **读全部列**（与收敛前行为一致，保证字节级等价）；给定时按声明保序投影。
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import polars as pl

from factorlab.core.factio import partitions, paths
from factorlab.core.factio.schema import (LOB_CHECKPOINTS_COLS, LOB_EVENTS_COLS,
                                          LOB_SWEEP_META_COLS)

_TABLE_COLS = {
    "lob_events": LOB_EVENTS_COLS,
    "lob_sweep_meta": LOB_SWEEP_META_COLS,
    "lob_checkpoints": LOB_CHECKPOINTS_COLS,
}


def lob_day_path(table: str, day: str, *, root: Path | None = None) -> Path:
    """该 (表, 日) 的文件路径（root 缺省 = factio.paths.lob_fact_root()）。"""
    if table not in _TABLE_COLS:
        raise ValueError(f"未知 lob 表: {table!r}（可用: {sorted(_TABLE_COLS)}）")
    return partitions.lob_day_file(root if root is not None else paths.lob_fact_root(),
                                  table, day)


def read_lob_table(table: str, day: str, *, codes: Sequence[str] | None = None,
                   columns: Sequence[str] | None = None,
                   root: Path | None = None) -> pl.DataFrame:
    """读 lob_fact 的 (表, 日)。

    缺文件 → FileNotFoundError（不静默返回空表，与 adapters/tick_read 同约定）。
    """
    f = lob_day_path(table, day, root=root)
    if not f.is_file():
        raise FileNotFoundError(f"无数据: {table} {day}（{f}）")
    lf = pl.scan_parquet(f)
    if codes is not None:
        lf = lf.filter(pl.col("code").is_in(list(codes)))
    if columns is not None:
        lf = lf.select(list(columns))
    return lf.collect()
