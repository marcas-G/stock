"""lob_fact 读单点（adapters/lob_read.py）。

R4a：研究侧 3 份 lob 读实现（factor_panel._read_lob、compact_lob、audit_w5）收敛到此。
两种测试：
- **hermetic**：tmp 目录造日文件，锁路径/过滤/投影/缺数据语义（不依赖真实数据）；
- **integration**：真实 lob_fact 日文件 → 列契约与 `factio.schema` 声明一致（数据缺失则 skip）。
"""
from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from factorlab.core.factio import paths
from factorlab.core.factio.schema import (LOB_CHECKPOINTS_COLS, LOB_EVENTS_COLS,
                                          LOB_SWEEP_META_COLS)


def _write_lob(root, table, day: str, rows: list[dict]) -> None:
    d = dt.datetime.strptime(day, "%Y%m%d").date()
    p = root / table / f"year={d.year}" / f"month={d.month:02d}"
    p.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(p / f"{day}.parquet")


_ROWS = [
    {"code": "000001.SZ", "trade_date": dt.date(2026, 6, 10), "time_ms": 34200000, "seq": 1},
    {"code": "000001.SZ", "trade_date": dt.date(2026, 6, 10), "time_ms": 34200100, "seq": 2},
    {"code": "600519.SH", "trade_date": dt.date(2026, 6, 10), "time_ms": 34200200, "seq": 1},
]


def test_reads_day_file_and_filters_code(tmp_path):
    from factorlab.adapters.lob_read import read_lob_table
    _write_lob(tmp_path, "lob_events", "20260610", _ROWS)
    got = read_lob_table("lob_events", "20260610", root=tmp_path)
    assert got.height == 3
    one = read_lob_table("lob_events", "20260610", codes=["000001.SZ"], root=tmp_path)
    assert one.height == 2 and one["seq"].to_list() == [1, 2]
    # 列投影（保序）
    proj = read_lob_table("lob_events", "20260610", columns=["seq", "code"], root=tmp_path)
    assert proj.columns == ["seq", "code"]


def test_missing_day_raises(tmp_path):
    from factorlab.adapters.lob_read import read_lob_table
    with pytest.raises(FileNotFoundError, match="lob_events 20260611"):
        read_lob_table("lob_events", "20260611", root=tmp_path)


def test_unknown_table_rejected(tmp_path):
    from factorlab.adapters.lob_read import read_lob_table
    with pytest.raises(ValueError, match="未知 lob 表"):
        read_lob_table("lob_bogus", "20260610", root=tmp_path)


def test_does_not_leak_other_days(tmp_path):
    """日文件布局：读某日不得混入同日历月的其他日（与 tick 月 part 布局的关键差别）。"""
    from factorlab.adapters.lob_read import read_lob_table
    _write_lob(tmp_path, "lob_events", "20260610", _ROWS)
    _write_lob(tmp_path, "lob_events", "20260611", _ROWS)
    got = read_lob_table("lob_events", "20260610", root=tmp_path)
    assert got.height == 3


@pytest.mark.integration
@pytest.mark.parametrize("table,declared", [
    ("lob_events", LOB_EVENTS_COLS),
    ("lob_sweep_meta", LOB_SWEEP_META_COLS),
    ("lob_checkpoints", LOB_CHECKPOINTS_COLS),
])
def test_real_files_match_declared_column_contract(table, declared):
    """真实数据：声明的列契约必须是实际列的**子集**（契约声明与实际不漂移）。"""
    day = "20260610"
    f = paths.lob_fact_root() / table / "year=2026" / "month=06" / f"{day}.parquet"
    if not f.is_file():
        pytest.skip(f"真实数据缺失: {f}")
    actual = list(pl.scan_parquet(f).collect_schema().names())
    missing = [c for c in declared if c not in actual]
    assert not missing, f"{table} 契约列不在实际文件中: {missing}（实际 {actual}）"


# ---- R4a 第二批：bars 读单点 + tick 月行数（对账用）----

def test_bars_month_files_and_missing(tmp_path):
    import polars as pl
    from factorlab.adapters.bars_read import bars_month_files, read_bars_month
    d = tmp_path / "year=2026" / "month=06"
    d.mkdir(parents=True)
    pl.DataFrame({"trade_date": [__import__("datetime").date(2026, 6, 10)],
                  "code": ["000001.SZ"], "minute_index": [0], "close": [1.0],
                  "amount": [1.0], "volume": [1.0]}).write_parquet(d / "part-000.parquet")
    assert [p.name for p in bars_month_files(tmp_path, year=2026, month=6)] == ["part-000.parquet"]
    got = read_bars_month(tmp_path, year=2026, month=6, columns=["code", "close"])
    assert got.columns == ["code", "close"] and got.height == 1
    with pytest.raises(FileNotFoundError, match="无数据: bars_1m 2026-07"):
        read_bars_month(tmp_path, year=2026, month=7)


def test_count_tick_month(tmp_path):
    import datetime as _dt
    import polars as pl
    from factorlab.adapters.tick_read import count_tick_month
    d = tmp_path / "trades" / "year=2026" / "month=06"
    d.mkdir(parents=True)
    rows = [{"trade_date": _dt.date(2026, 6, 10), "code": "000001.SZ",
             "time_ms": i, "trade_no": i, "bs": 1, "price_x10000": 100,
             "volume": 1, "ask_seq": 0, "bid_seq": 0} for i in range(5)]
    pl.DataFrame(rows).write_parquet(d / "part-000.parquet")
    pl.DataFrame(rows[:3]).write_parquet(d / "part-001.parquet")   # 多 part 求和
    assert count_tick_month("trades", 2026, 6, root=tmp_path) == 8
    with pytest.raises(FileNotFoundError):
        count_tick_month("trades", 2026, 7, root=tmp_path)
