"""T7：load_daily 资金流列（`_MONEYFLOW_MAP` → LEFT JOIN `moneyflow`，缺行 → null）。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-7-brief.md
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §2
（读路径按 (trade_date, ts_code) join，缺行传播 null）。
双腿参数化：duckdb 平台文件 | CH 临时库（tests/conftest.py env fixture）。
"""
import datetime

import polars as pl
import pytest

from factorlab.adapters.read.source import (
    _MONEYFLOW_MAP,
    load_daily,
    load_daily_fill_state,
)

MONEY_COLS = ("main_net_inflow", "auction", "super_in", "super_out", "super_net",
              "super_net_pct", "big_in", "big_out", "big_net", "big_net_pct",
              "mid_in", "mid_out", "mid_net", "mid_net_pct",
              "small_in", "small_out", "small_net", "small_net_pct")


def _moneyflow_table(with_all_cols: bool):
    """moneyflow 数据描述：000001 两天 + 600519 缺席（缺行 → null 断言用）。"""
    if with_all_cols:
        cols = [(c, "f64?") for c in MONEY_COLS]
        rows = [("000001.SZ", "20240102", *[float(i + 1) for i in range(len(MONEY_COLS))]),
                ("000001.SZ", "20240103", *[float(100 + i + 1) for i in range(len(MONEY_COLS))])]
    else:
        cols = [("main_net_inflow", "f64?"), ("super_net_pct", "f64?")]
        rows = [("000001.SZ", "20240102", 1.23e8, 27.01),
                ("000001.SZ", "20240103", -4.5e7, -3.3)]
    return ([("ts_code", "str"), ("trade_date", "date"), *cols], rows)


_BASE = {
    "daily": ([("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
               ("high", "f64"), ("low", "f64"), ("close", "f64"),
               ("pre_close", "f64"), ("change", "f64"), ("pct_chg", "f64"),
               ("vol", "f64"), ("amount", "f64")],
              [("000001.SZ", "20240102", 10.0, 11.0, 9.5, 10.5, 10.2, 0.3, 0.03, 1000.0, 1e6),
               ("000001.SZ", "20240103", 10.5, 11.5, 10.0, 11.0, 10.5, 0.5, 0.05, 1100.0, 1.1e6),
               ("600519.SH", "20240102", 20.0, 21.0, 19.0, 20.5, 19.8, 0.7, 0.04, 2000.0, 2e6)]),
    "adj_factor": ([("ts_code", "str"), ("trade_date", "date"), ("adj_factor", "f64")],
                   [("000001.SZ", "20240102", 1.0), ("000001.SZ", "20240103", 1.0),
                    ("600519.SH", "20240102", 1.0)]),
    # ch 编译器两层 IN 依赖 stock_basic（duckdb 腿多余表无副作用）
    "stock_basic": ([("symbol", "str"), ("ts_code", "str")],
                    [("000001", "000001.SZ"), ("600519", "600519.SH")]),
}


def _seed(env, with_all_cols=False):
    env.seed({**_BASE, "moneyflow": _moneyflow_table(with_all_cols)})


def test_moneyflow_map_covers_brief_columns_identity():
    assert set(_MONEYFLOW_MAP) == set(MONEY_COLS)
    assert all(_MONEYFLOW_MAP[c] == c for c in MONEY_COLS)


def test_load_daily_joins_moneyflow_values_and_nulls(env):
    _seed(env)
    df = load_daily(env.rd, ["000001", "600519"],
                    cols=["close", "main_net_inflow", "super_net_pct"],
                    float32=False).collect()
    assert df.columns == ["date", "code", "close", "main_net_inflow", "super_net_pct"]
    assert df["main_net_inflow"].to_list() == [1.23e8, -4.5e7, None]   # 600519 缺行 → null
    assert df["super_net_pct"].to_list() == [27.01, -3.3, None]
    assert df["date"][2] == datetime.date(2024, 1, 2)


@pytest.mark.parametrize("col", MONEY_COLS)
def test_each_moneyflow_column_maps_through(env, col):
    """18 列逐列：seed 为列内互异值——任一映射缺失/错位都会被本测试抓住。"""
    _seed(env, with_all_cols=True)
    idx = MONEY_COLS.index(col)
    df = load_daily(env.rd, ["000001"], cols=["close", col], float32=False).collect()
    assert df.columns == ["date", "code", "close", col]
    assert df[col].to_list() == [float(idx + 1), float(100 + idx + 1)]


def test_moneyflow_not_joined_by_default(env):
    _seed(env)
    df = load_daily(env.rd, ["000001"]).collect()
    assert "main_net_inflow" not in df.columns
    assert df.columns == ["date", "code", "open", "high", "low", "close",
                          "pre_close", "change", "pct_chg", "volume", "amount"]


def test_moneyflow_absent_rows_survive_left_join(env):
    """LEFT JOIN 不展开/不丢 daily 行：600519 的 daily 行仍在（money 列为 null）。"""
    _seed(env)
    df = load_daily(env.rd, ["600519"], cols=["close", "main_net_inflow"],
                    float32=False).collect()
    assert df.height == 1
    assert df["main_net_inflow"].null_count() == 1


def test_fill_state_moneyflow_latest_non_null(env):
    _seed(env)
    fs = load_daily_fill_state(env.rd, ["000001", "600519"], before="2024-01-03",
                               cols=["close", "main_net_inflow"], float32=False)
    got = {r["code"]: r["main_net_inflow"] for r in fs.iter_rows(named=True)}
    assert got == {"000001": 1.23e8, "600519": None}


def test_daily_without_moneyflow_table_still_loads_when_not_requested(env):
    """moneyflow 缺表（库尚未灌资金流）不影响既有 daily 读取。"""
    env.seed(_BASE)
    df = load_daily(env.rd, ["000001"], cols=["close"]).collect()
    assert df.schema["close"] == pl.Float32
