"""分区路径派生单点（core/factio/partitions.py）——纯函数，无 I/O。

R4c 收敛目标：研究侧 11 个文件、平台 2 处各自拼 `year=/month=` 字符串 → 收一份。
本测试锁三种**真实布局**（R0 基线实测）：
- tick_fact：`{root}/{table}/year=YYYY/month=MM/part-*.parquet`（part-000 + part-001）
- bars_1m ：`{root}/year=YYYY/month=MM/part-000.parquet`（无表级）
- lob_fact：`{root}/{table}/year=YYYY/month=MM/YYYYMMDD.parquet`（日一文件）
"""
from __future__ import annotations

from pathlib import Path

from factorlab.core.factio import partitions


def test_tick_month_glob_matches_baseline_layout():
    got = partitions.tick_parts_pattern(Path("/r"), "orders", 2026, 6)
    assert got == "/r/orders/year=2026/month=06/part-*.parquet", got
    # 月份零填充（单数字月）
    assert partitions.tick_parts_pattern(Path("/r"), "trades", 2025, 11) == \
        "/r/trades/year=2025/month=11/part-*.parquet"


def test_bars_month_part_matches_baseline_layout():
    assert partitions.bars_month_part(Path("/r"), 2026, 6) == \
        Path("/r/year=2026/month=06/part-000.parquet")


def test_lob_day_file_matches_baseline_layout():
    assert partitions.lob_day_file(Path("/r"), "lob_events", "20260610") == \
        Path("/r/lob_events/year=2026/month=06/20260610.parquet")
    # date 对象同样接受（研究侧两种传参都有）
    import datetime as dt
    assert partitions.lob_day_file(Path("/r"), "lob_events", dt.date(2026, 6, 10)) == \
        Path("/r/lob_events/year=2026/month=06/20260610.parquet")


def test_partition_dir_is_the_single_rule_both_layouts_use():
    assert partitions.partition_dir(Path("/r"), table="orders", year=2026, month=6) == \
        Path("/r/orders/year=2026/month=06")
    assert partitions.partition_dir(Path("/r"), table=None, year=2026, month=6) == \
        Path("/r/year=2026/month=06")


def test_year_month_label_matches_clickhouse_partition():
    """CH 分区名 = 'YYYYMM'（ch_ingest 的 DROP PARTITION 用它）。"""
    assert partitions.year_month_label(2026, 6) == "202606"


def test_invalid_day_is_rejected():
    import pytest
    for bad in ("2026-06-10", "2026061", "2026061x"):
        with pytest.raises(ValueError, match="day"):
            partitions.lob_day_file(Path("/r"), "lob_events", bad)


def test_year_month_prefixes_are_single_source_for_enumeration():
    """枚举年/月目录（glob/listdir）时的前缀也不得各处自拼。

    研究侧两处此前各写 `"year="` / `"month="` 字面量：`ch_ingest.discover_tasks`
    与 `1m_features._iter_months`（后者还用 `entry[5:]` 这类**位置切片**，前缀一改
    就静默错位）→ 前缀与切片偏移都必须来自本单点。
    """
    assert partitions.YEAR_PREFIX == "year="
    assert partitions.MONTH_PREFIX == "month="
    # partition_dir 自身也必须由这两个前缀构成（否则"单点"是假的）
    assert str(partitions.partition_dir(Path("/r"), table="orders", year=2026, month=6)) == \
        f"/r/orders/{partitions.YEAR_PREFIX}2026/{partitions.MONTH_PREFIX}06"
