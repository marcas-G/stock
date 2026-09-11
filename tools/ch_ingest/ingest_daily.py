"""daily 层 5 表灌入：daily_fact.parquet → factorlab.{daily, adj_factor, daily_basic, trade_cal, stock_basic}。

派生（raw 口径，polars 侧）：
- pre_close = close per-code shift（组内首行 NULL）；change = close - pre_close；pct_chg = (close/pre_close-1)*100
- total_mv = close × total_shares / 1e4（万元）；turnover_rate = vol / float_shares × 100（%）
- trade_cal = distinct trade_date（is_open=1）；stock_basic = distinct code（list_date = 最早交易日代理）

单进程即可（18M 行一次物化 ~3GB）。幂等：每表先 TRUNCATE 再灌。
用法：python ingest_daily.py
"""
from __future__ import annotations

import os

import polars as pl

from common import connect, load_config

DAILY_SRC = "/data/students/gaolei/stock/data/fact/daily_fact/daily_fact.parquet"
BATCH = 2_000_000  # insert_arrow 每批行数


def _insert_table(client, table: str, df: pl.DataFrame):
    total = 0
    for i in range(0, df.height, BATCH):
        batch = df.slice(i, BATCH).to_arrow()
        client.insert_arrow(table, batch, database=load_config()["ch"]["database"])
        total += batch.num_rows
    print(f"  {table}: {total:,} rows", flush=True)


def main():
    client = connect()
    db = load_config()["ch"]["database"]
    print("读取 daily_fact.parquet ...", flush=True)
    df = (
        pl.scan_parquet(DAILY_SRC)
        .sort(["code", "trade_date"])
        .collect()
    )
    print(f"  {df.height:,} rows / {df['code'].n_unique():,} codes / "
          f"{df['trade_date'].n_unique():,} trade dates", flush=True)

    # --- 派生列（组内 shift，显式 order_by 保证跨 polars 版本一致）---
    df = df.with_columns(
        pl.col("close").shift(1).over("code", order_by="trade_date").alias("_pre"),
    )
    df = df.with_columns(
        pl.col("_pre").alias("pre_close"),
        (pl.col("close") - pl.col("_pre")).alias("change"),
        ((pl.col("close") / pl.col("_pre") - 1.0) * 100.0).alias("pct_chg"),
        (pl.col("close") * pl.col("total_shares") / 1e4).alias("total_mv"),
        (pl.col("volume") / pl.col("float_shares") * 100.0).alias("turnover_rate"),
    ).drop("_pre")

    # --- daily ---
    print("TRUNCATE + 灌 daily", flush=True)
    client.command(f"TRUNCATE TABLE {db}.daily")
    daily = df.select([
        pl.col("code").alias("ts_code"), "trade_date",
        "open", "high", "low", "close", "pre_close", "change", "pct_chg",
        pl.col("volume").alias("vol"), "amount",
    ])
    _insert_table(client, "daily", daily)

    # --- adj_factor（拆列）---
    print("TRUNCATE + 灌 adj_factor", flush=True)
    client.command(f"TRUNCATE TABLE {db}.adj_factor")
    _insert_table(client, "adj_factor", df.select([
        pl.col("code").alias("ts_code"), "trade_date", "adj_factor",
    ]))

    # --- daily_basic（含 5 个占位空列）---
    print("TRUNCATE + 灌 daily_basic", flush=True)
    client.command(f"TRUNCATE TABLE {db}.daily_basic")
    basic = df.select([
        pl.col("code").alias("ts_code"), "trade_date", "total_mv", "turnover_rate",
    ]).with_columns([
        pl.lit(None, dtype=pl.Float64).alias(c) for c in
        ("circ_mv", "pe_ttm", "pb", "dv_ratio", "volume_ratio")
    ])
    _insert_table(client, "daily_basic", basic)

    # --- trade_cal ---
    print("TRUNCATE + 灌 trade_cal", flush=True)
    client.command(f"TRUNCATE TABLE {db}.trade_cal")
    cal = (
        df.select(pl.col("trade_date").unique())
        .sort("trade_date")
        .rename({"trade_date": "cal_date"})
        .with_columns(pl.lit(1, dtype=pl.UInt8).alias("is_open"))
    )
    _insert_table(client, "trade_cal", cal)

    # --- stock_basic ---
    # market = 板块名规范值（平台 execution rules 消费：rules.py 显式映射
    # (market, suffix) → 申报数量规则；取值 '主板'/'创业板'/'科创板'/'北交所'，
    # 段规则同 derive_stk_limit.py——2026-09-08 ch_prod 真实段实测暴露缺列）
    print("TRUNCATE + 灌 stock_basic", flush=True)
    client.command(f"TRUNCATE TABLE {db}.stock_basic")
    code = pl.col("code")
    market = (
        pl.when(code.str.ends_with(".BJ")).then(pl.lit("北交所"))
        .when(code.str.slice(0, 3).is_in(["688", "689"])).then(pl.lit("科创板"))
        .when(code.str.slice(0, 3).is_in(["300", "301", "302"])).then(pl.lit("创业板"))
        .otherwise(pl.lit("主板"))
        .alias("market")
    )
    basic_stocks = (
        df.group_by("code").agg(pl.col("trade_date").min().alias("list_date"))
        .with_columns(
            pl.col("code").str.slice(0, 6).alias("symbol"),
            market,
            pl.lit(None, dtype=pl.String).alias("industry"),
        )
    )
    sb = basic_stocks.select(
        ["symbol", pl.col("code").alias("ts_code"), "list_date", "market", "industry"])
    _insert_table(client, "stock_basic", sb)

    print("daily 层灌入完成", flush=True)


if __name__ == "__main__":
    os.environ.setdefault("PYARROW_JEMALLOC", "0")
    main()
