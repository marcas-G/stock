"""factio 契约测试（WS3e）：路径单点 / 时间解析单点 / 板块分类单点 / tick 读单点。

断言源 = 既有实现的语义（lob_fact qa/streams.hms_to_ms 的示例值、
ch_ingest 的板块映射、tick_fact 的实际目录形态），**逐值手算**而非从实现反推。
数据不在本机时 skip（不假通过）。
"""
from __future__ import annotations

import datetime

import polars as pl
import pytest

from factorlab.core.factio.boards import board_of
from factorlab.core.factio.paths import (DATA_ROOT, STOCK_ROOT, daily_fact_path,
                                         tick_fact_root, tick_month_dir)
from factorlab.adapters.tick_read import tick_month_files
from factorlab.core.factio.tick_month import TICK_TABLE_DIRS
from factorlab.core.factio.timeparse import hms_to_ms_of_day, parse_ms_series


# ---------------------------------------------------------------- paths

def test_paths_derivation_and_layout():
    assert STOCK_ROOT.name == "stock"
    assert DATA_ROOT == STOCK_ROOT / "data"
    # tick 月目录形态（Hive 分区）：<fact>/tick_fact/<table>/year=/month=
    d = tick_month_dir("orders", "20260803")
    assert d == tick_fact_root() / "orders" / "year=2026" / "month=08"
    assert daily_fact_path().name == "daily_fact.parquet"


# data_on_disk（fast 排除 / deep 跑）：断言 tick_fact 目录与 daily_fact 文件在盘。
# skip 守卫只看 STOCK_ROOT 存在性——CI checkout 里 STOCK_ROOT=checkout（存在）但
# data/fact 大数据不入库 → 必红；属"宿主在盘数据"假设，非实现缺陷。
@pytest.mark.data_on_disk
def test_paths_roots_exist_or_skip():
    if not STOCK_ROOT.is_dir():
        pytest.skip(f"工作区数据不在本机: {STOCK_ROOT}")
    assert tick_fact_root().is_dir()
    assert daily_fact_path().is_file()


# ---------------------------------------------------------------- timeparse

def test_hms_scalar_matches_known_values():
    # lob_fact qa/streams.hms_to_ms 的 docstring 示例（设计锚点）
    assert hms_to_ms_of_day(91500020) == 33300020      # 09:15:00.020
    assert hms_to_ms_of_day(93000000) == 34200000      # 09:30:00.000
    assert hms_to_ms_of_day(145959500) == 53999500     # 14:59:59.500
    assert hms_to_ms_of_day(0) == 0


def test_hms_series_matches_scalar_and_dtype():
    values = [91500020, 93000000, 113000000, 130000000, 145959500]
    out = parse_ms_series(pl.Series(values, dtype=pl.Int64))
    assert out.dtype == pl.Int32
    assert out.to_list() == [hms_to_ms_of_day(v) for v in values]


def test_hms_rejects_out_of_range():
    with pytest.raises(ValueError, match="时间|范围|非法"):
        hms_to_ms_of_day(999999999)      # 99:99:99.999 非法


# ---------------------------------------------------------------- boards

@pytest.mark.parametrize("code,expected", [
    ("688001.SH", "STAR"), ("689009.SH", "STAR"),
    ("300750.SZ", "CHINEXT"), ("301029.SZ", "CHINEXT"), ("302132.SZ", "CHINEXT"),
    ("920641.BJ", "BJ"), ("430047.BJ", "BJ"),
    ("600519.SH", "MAIN"), ("000001.SZ", "MAIN"),
])
def test_board_of(code, expected):
    assert board_of(code) == expected


def test_board_of_rejects_unknown():
    with pytest.raises(ValueError, match="未知|非法|后缀"):
        board_of("600519.XX")


# ---------------------------------------------------------------- tick 读单点

def test_tick_table_dirs_mapping():
    # 逻辑表名 → 目录名（三表 + cancels；cancels 由 extract_sz_cancels 单独抽取）
    assert TICK_TABLE_DIRS["tick_trades"] == "trades"
    assert TICK_TABLE_DIRS["tick_orders"] == "orders"
    assert TICK_TABLE_DIRS["tick_snapshots"] == "snapshots"
    assert TICK_TABLE_DIRS["cancels"] == "cancels"


def test_tick_month_files_layout():
    # 实际形态：part-000.parquet + part-001.parquet（多 part 是常态，如 2026-08）
    fs = tick_month_files("trades", "20260803")
    assert all(f.parent == tick_month_dir("trades", "20260803") for f in fs)
    assert all(f.name.startswith("part-") for f in fs)


def test_read_tick_table_real_data():
    """单点读：真实 tick_fact 一天的 trades（缺数据 skip，不假通过）。"""
    from factorlab.adapters.tick_read import read_tick_table
    if not STOCK_ROOT.is_dir():
        pytest.skip(f"工作区数据不在本机: {STOCK_ROOT}")
    day = "20260803"
    d = tick_month_dir("trades", day)
    if not d.is_dir():
        pytest.skip(f"无该月数据: {d}")
    df = read_tick_table("trades", day, codes=["000021.SZ"])   # code 带后缀（实况）
    if df.height == 0:
        pytest.skip("该日无该 code 数据（样本日选择问题）")
    # 列契约（core/factio/schema 投影）+ 日期过滤 + code 过滤
    assert set(df.columns) <= set(__import__(
        "factorlab.core.factio.schema", fromlist=["x"]).TICK_TRADES_COLS)
    assert df["trade_date"].unique().to_list() == [datetime.date(2026, 8, 3)]
    assert set(df["code"].to_list()) == {"000021.SZ"}


def test_read_tick_table_missing_data_raises():
    """缺数据 → FileNotFoundError 点名（不静默空表；错误路径断言）。"""
    from factorlab.adapters.tick_read import read_tick_table
    with pytest.raises(FileNotFoundError, match="无数据|不存在"):
        read_tick_table("trades", "19900101")
    with pytest.raises(ValueError, match="未知 tick 表"):
        read_tick_table("nosuch", "20260803")
