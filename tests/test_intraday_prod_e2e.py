"""M3 e2e：intraday 读接口 × 生产 ClickHouse 真数据（18.5 亿 bars_1m + ~14B tick）。

- 每表动态取"最近有数据的 code×trade_date"，loader 行数 == 直连 CH 同条件 count
- datetime 解码 = 源 wall 时钟（naive ms）：bars 首根落在 09:3x（上午段），
  全天单调；非跨日脏数据
- 环境缺 CH/库空时 ch_prod fixture 自动 skip（集成标记，CI 无 CH 不失败）
"""
import datetime as dt

import polars as pl
import pytest

from factorlab.adapters import intraday
from factorlab.app.bootstrap import open_read


@pytest.mark.integration
def test_bars_1m_prod_row_parity_and_wall_clock(ch_prod):
    client = ch_prod
    db = client.database
    # code 主键定位最近有数据的交易日（避免全表 max 扫描）
    day, code, n = _pick_day(client, "bars_1m", ("600519.SH", "000001.SZ"))
    assert n > 100, f"bars_1m {code} {day} 行数异常: {n}"
    df = intraday.load_bars_1m(open_read(data_backend="ch"), code, day=day)
    assert df.height == n, f"loader {df.height} != CH count {n}"
    t = df["datetime"]
    assert t.dtype == pl.Datetime("ms") and t.dtype.time_zone is None
    assert t.to_list() == sorted(t.to_list()) and t.n_unique() == t.len()  # 单调严格递增
    # 源 wall 时钟语义：首根在上午竞价/连续段（09:30 前后），非 UTC 偏移后的 17:3x
    first = t[0]
    assert first.date() == dt.date.fromisoformat(day)
    assert 9 <= first.hour <= 11 or 12 <= first.hour <= 15, \
        f"datetime 墙钟失真: {first}（应为交易时段内）"
    assert df["code"].n_unique() == 1 and df["code"][0] == code.split(".")[0]
    assert (df["minute_index"].to_list() == sorted(df["minute_index"].to_list()))
    print(f"bars_1m {code} {day}: {df.height} 行  首根 {first}  末根 {t[-1]}")


def _pick_day(client, table, codes):
    """该表最近有数据的 (trade_date, code, n)。"""
    for code in codes:
        d = client.query(
            f"SELECT max(trade_date) FROM {client.database}.{table} "
            f"WHERE code = '{code}'").result_rows[0][0]
        if d is None or str(d).startswith("1970"):
            continue
        n = client.query(
            f"SELECT count() FROM {client.database}.{table} "
            f"WHERE code = '{code}' AND trade_date = '{d}'").result_rows[0][0]
        if n:
            return str(d), code, n
    pytest.skip(f"{table} 生产库无样本 code 数据")


@pytest.mark.integration
@pytest.mark.parametrize("fn,table", [
    (intraday.load_tick_trades, "tick_trades"),
    (intraday.load_tick_orders, "tick_orders"),
    (intraday.load_tick_snapshots, "tick_snapshots"),
])
def test_tick_prod_row_parity(ch_prod, fn, table):
    """tick 三表：行数 == 直连 CH count；time_ms 升序；当日不跨天。"""
    client = ch_prod
    day, code, n = _pick_day(client, table, ("600519.SH", "000001.SZ", "300415.SZ"))
    assert n > 0
    df = fn(open_read(data_backend="ch"), code, day=day)
    assert df.height == n, f"loader {df.height} != CH count {n}"
    ms = df["time_ms"]
    assert ms.to_list() == sorted(ms.to_list()), "time_ms 未升序"
    # time_ms = 当日毫秒（00:00 起）：任何行都应落在 09:15~15:05 交易带
    assert (ms >= 9 * 3600_000 + 14 * 60_000).all() and (ms <= 15 * 3600_000 + 5 * 60_000).all(), \
        f"time_ms 跨出交易带: {ms.min()}..{ms.max()}"
    print(f"{table} {code} {day}: {df.height} 行  ms {ms.min()}..{ms.max()}")
