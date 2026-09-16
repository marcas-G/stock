"""bars_1m 本地事实读单点（R4a）。

布局：`{root}/year=YYYY/month=MM/part-*.parquet`（当前每个月一个 part-000；
用 glob 而非写死 part-000——救援合并会追加 part-001，见 ch_ingest 的同款约定）。
路径规则取 `core/factio/partitions`；本模块只做 I/O。
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import polars as pl

from factorlab.core.factio import partitions, paths


def bars_month_files(root: Path | None = None, *, year: int, month: int) -> list[Path]:
    """该月全部 part（排序确定；目录缺失 → 空列表）。"""
    base = paths.bars_1m_root() if root is None else Path(root)
    month_dir = partitions.partition_dir(base, table=None, year=year, month=month)
    return sorted(month_dir.glob("part-*.parquet")) if month_dir.is_dir() else []


def read_bars_month(root: Path | None = None, *, year: int, month: int,
                    columns: Sequence[str] | None = None) -> pl.DataFrame:
    """读 bars_1m 单月。缺目录/无 part → FileNotFoundError（不静默返回空表）。"""
    files = bars_month_files(root, year=year, month=month)
    if not files:
        base = paths.bars_1m_root() if root is None else Path(root)
        raise FileNotFoundError(
            f"无数据: bars_1m {year}-{month:02d}（{partitions.partition_dir(base, table=None, year=year, month=month)}）")
    lf = pl.scan_parquet(files)
    if columns is not None:
        lf = lf.select(list(columns))
    return lf.collect()
