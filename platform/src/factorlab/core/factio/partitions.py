"""分区路径派生单点（纯，无 I/O）：事实库三种真实布局的**字符串规则**。

R4c 收敛依据（R0 基线实测）：研究侧 11 个文件、平台 2 处各自拼 `year=/month=`；
布局共三种，此前没有任何单点：
- **tick_fact**：`{root}/{table}/year=YYYY/month=MM/part-*.parquet`（part-000 + part-001）
- **bars_1m** ：`{root}/year=YYYY/month=MM/part-000.parquet`（无表级，单 part）
- **lob_fact**：`{root}/{table}/year=YYYY/month=MM/YYYYMMDD.parquet`（**日一文件**）

本模块只做路径派生（目录枚举属 I/O，在 `adapters/*`）；`year_month_label` 供 CH 分区名
（`DROP PARTITION 'YYYYMM'`）共用。**core 禁 I/O**：此处无 glob/exists。
"""
from __future__ import annotations

from datetime import date as _date
from pathlib import Path


def partition_dir(root: Path, *, table: str | None, year: int, month: int) -> Path:
    """`{root}[/{table}]/year=YYYY/month=MM`（月份零填充两位）。"""
    base = root if table is None else root / table
    return base / f"year={year}" / f"month={month:02d}"


def tick_parts_pattern(root: Path, table: str, year: int, month: int) -> str:
    """tick_fact 月 part 的 glob 模式（`part-*.parquet`，多 part）。"""
    return str(partition_dir(root, table=table, year=year, month=month) / "part-*.parquet")


def bars_month_part(root: Path, year: int, month: int) -> Path:
    """bars_1m 单月文件（`part-000.parquet`）。"""
    return partition_dir(root, table=None, year=year, month=month) / "part-000.parquet"


def lob_day_file(root: Path, table: str, day: str | _date) -> Path:
    """lob_fact 日文件路径。day 接受 `'YYYYMMDD'` 或 `datetime.date`（研究侧两种传参都有）。"""
    if isinstance(day, _date):
        d = day
    else:
        s = str(day)
        if len(s) != 8 or not s.isdigit():
            raise ValueError(f"day 必须是 'YYYYMMDD' 或 date（收到 {day!r}）")
        d = _date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return partition_dir(root, table=table, year=d.year, month=d.month) / f"{d:%Y%m%d}.parquet"


def year_month_label(year: int, month: int) -> str:
    """ClickHouse 分区名（`ch_ingest` 的 `DROP PARTITION 'YYYYMM'` 与 state 键共用）。"""
    return f"{year}{month:02d}"
