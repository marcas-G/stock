"""R03-I6：分钟覆盖只读聚合（load_bars_1m_coverage——静态池生成/审计辅助）。

断言来源：finding R03-I6（分钟源幸存者偏差）与 interface.md 分钟覆盖口径节：
- 每 code 聚合 covered_days/first_date/last_date/min·max_rows_per_day，code 归一 6 位
- codes 过滤（6 位经 stock_basic 解析、ts_code 原样、未知 6 位 fail fast）
- 闭区间必填、空 codes 拒绝、duckdb 腿显式 ValueError
- 真 CH 小样本（ch_prod 集成，限流 2 条查询）：240 网格 code 当日 = covered_days 1
  且 min/max_rows_per_day 均 240——辅助口径与 bars_1m 事实契约一致。
"""
import datetime as dt

import polars as pl
import pytest

import dualbridge
from factorlab.adapters.intraday import load_bars_1m_coverage

_D1 = dt.date(2024, 1, 2)
_D2 = dt.date(2024, 1, 3)


def _seed(ch_db):
    client, db = ch_db
    bars = []
    for ts_code, days in {"000001.SZ": {_D1: 240, _D2: 239},
                          "600519.SH": {_D1: 240}}.items():
        for d, n in days.items():
            for mi in range(n):
                bars.append((d.strftime("%Y%m%d"), ts_code, mi, 1, 10.0, 10.0,
                             10.0, 10.0, 1.0, 1.0,
                             dt.datetime(d.year, d.month, d.day, 9, 25)))
    tables = {
        "stock_basic": ([("symbol", "str"), ("ts_code", "str"),
                         ("exchange", "str"), ("list_date", "date"),
                         ("industry", "str?")],
                        [("000001", "000001.SZ", "SZSE", "19910101", "银行"),
                         ("600519", "600519.SH", "SSE", "20010827", "白酒")]),
        "bars_1m": ([("trade_date", "date"), ("code", "str"),
                     ("minute_index", "u16"), ("session_type", "u8"),
                     ("open", "f32"), ("high", "f32"), ("low", "f32"),
                     ("close", "f32"), ("amount", "f64"), ("volume", "f64"),
                     ("datetime", "datetime")], bars),
    }
    dualbridge.seed_ch(client, db, tables)


def _open():
    from factorlab.app.bootstrap import open_read
    return open_read(data_backend="ch")


def test_coverage_aggregates_per_code(ch_db):
    """每 code 一行聚合（covered_days/first/last/行数极值）+ code 6 位归一
    + (code, trade_date) 分组计数真查 CH（不是硬编码）。"""
    _seed(ch_db)
    rd = _open()
    try:
        cov = load_bars_1m_coverage(rd, date_start="2024-01-02",
                                    date_end="2024-01-03")
    finally:
        rd.close()
    assert cov.columns == ["code", "covered_days", "first_date", "last_date",
                           "min_rows_per_day", "max_rows_per_day"]
    assert cov["code"].to_list() == ["000001", "600519"]
    r1 = cov.filter(pl.col("code") == "000001").row(0, named=True)
    assert r1["covered_days"] == 2
    assert r1["first_date"] == _D1 and r1["last_date"] == _D2
    assert r1["min_rows_per_day"] == 239 and r1["max_rows_per_day"] == 240
    r2 = cov.filter(pl.col("code") == "600519").row(0, named=True)
    assert r2["covered_days"] == 1
    assert r2["first_date"] == r2["last_date"] == _D1
    assert r2["min_rows_per_day"] == r2["max_rows_per_day"] == 240


def test_coverage_codes_filter_and_unknown_code(ch_db):
    """codes 过滤：ts_code 原样 / 6 位经 stock_basic 解析；未知 6 位与空 list
    fail fast（不静默丢 code）。"""
    _seed(ch_db)
    rd = _open()
    try:
        cov = load_bars_1m_coverage(rd, date_start="2024-01-02",
                                    date_end="2024-01-03",
                                    codes=["600519.SH"])
        assert cov["code"].to_list() == ["600519"]
        cov6 = load_bars_1m_coverage(rd, date_start="2024-01-02",
                                     date_end="2024-01-03", codes=["000001"])
        assert cov6["code"].to_list() == ["000001"]
        with pytest.raises(ValueError, match="未知 code"):
            load_bars_1m_coverage(rd, date_start="2024-01-02",
                                  date_end="2024-01-03", codes=["999999"])
        with pytest.raises(ValueError, match="codes"):
            load_bars_1m_coverage(rd, date_start="2024-01-02",
                                  date_end="2024-01-03", codes=[])
    finally:
        rd.close()


def test_coverage_guards():
    """闭区间必填、duckdb 腿显式拒绝（dispatch 前/后各自 fail fast）。"""
    class _NoRd:
        backend = "duckdb"

    with pytest.raises(ValueError, match="必须指定 date_start"):
        load_bars_1m_coverage(_NoRd(), date_start="2024-01-02", date_end=None)
    with pytest.raises(ValueError, match="必须指定 date_start"):
        load_bars_1m_coverage(_NoRd(), date_start=None, date_end="2024-01-02")
    with pytest.raises(ValueError, match="ClickHouse"):
        load_bars_1m_coverage(_NoRd(), date_start="2024-01-02",
                              date_end="2024-01-02")


@pytest.mark.integration
def test_coverage_prod_small_sample(ch_prod):
    """真 CH 小样本（限流：1 条定窗查询 + 1 条聚合）：两活跃 code 的最近共同
    交易日覆盖 = 1 日 × 240 行（bar 级网格事实与聚合辅助口径对齐）。"""
    client = ch_prod
    db = client.database
    day = client.query(
        f"SELECT trade_date FROM {db}.bars_1m "
        f"WHERE code IN ('000001.SZ', '600519.SH') "
        f"GROUP BY trade_date HAVING uniqExact(code) = 2 "
        f"ORDER BY trade_date DESC LIMIT 1").result_rows[0][0]
    assert day is not None and not str(day).startswith("1970")
    rd = _open()
    try:
        cov = load_bars_1m_coverage(rd, date_start=day.isoformat(),
                                    date_end=day.isoformat(),
                                    codes=["000001.SZ", "600519.SH"])
    finally:
        rd.close()
    assert cov["code"].to_list() == ["000001", "600519"]
    assert cov["covered_days"].to_list() == [1, 1]
    assert cov["min_rows_per_day"].to_list() == [240, 240]
    assert cov["max_rows_per_day"].to_list() == [240, 240]
    assert cov["first_date"].to_list() == [day, day]
