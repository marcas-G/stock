"""daily 层 5 表灌入：daily_fact.parquet → factorlab.{daily, adj_factor, daily_basic, trade_cal, stock_basic}。

派生（polars 侧，R21 修复后口径）：
- pre_close = 除权参考价：无事件日 = 上一交易日 close；除权除息日 =
  round((prev_close - div_cash/10 + rights_price×rights_num/10)
        / (1 + div_bonus/10 + div_transfer/10), 2)（half-up；单位：元或股/10股）。
  组内首行 NULL。change = close - pre_close；pct_chg = (close/pre_close-1)*100。
  ——R01-TOOLS-DATA-C2：raw 前收与 platform/docs/catalog.md「除权参考价」承诺不符；
  300842.SZ 2024-04-10 验算 (70.9-0.8)/1.4=50.07。
- total_mv = close × total_shares（源 total_shares 单位=万股 → 万元 tushare 口径）。
  ——R01-TOOLS-C1：旧版多除 1e4。
- turnover_rate = vol / (float_shares × 1e4) × 100（%）。旧版漏 ×1e4。
- adj_factor/amount：NaN（退市股无源）→ NULL；adj_factor <= 0（vendor 后复权价异常）
  也归 NULL——CH 列 Nullable，qfq 基准 argMax 跳过 NULL（R01-TOOLS-I1）。
- stock_basic.delist_date：sidecar（退市目录权威信号，last<max → last+1）+ 断流
  兜底（不在 sidecar 且 gap>250 交易日 → last+1）；平台语义 is_listed = t < delist_date。
- trade_cal = distinct trade_date（is_open=1）；stock_basic.list_date = 最早交易日代理。

单进程即可（18M 行一次物化 ~3GB）。幂等：每表先 TRUNCATE 再灌。
用法：python ingest_daily.py
"""
from __future__ import annotations

import os
import datetime
from pathlib import Path

import polars as pl

from factorlab.core.factio import paths  # R8：路径单点
from factorlab.core.factio.boards import zh_market_expr  # R4c 单点

from common import connect, load_config

DAILY_SRC = str(paths.daily_fact_path())   # R8：取 factio.paths（原硬编码绝对路径）
# 退市 sidecar：与 fact 同目录（import_daily.py 原子写；缺失 → 仅断流兜底）
DELISTED_SRC = str(Path(DAILY_SRC).with_name("delisted_codes.parquet"))
BATCH = 2_000_000  # insert_arrow 每批行数
STALE_TRADING_DAYS = 250   # 断流兜底：>250 交易日无数据且不在 sidecar → 判退市


def derive_daily_fields(df: pl.DataFrame) -> pl.DataFrame:
    """加 pre_close（除权参考价）/change/pct_chg/total_mv/turnover_rate。

    输入需含 code/trade_date/close/volume/total_shares/float_shares +
    5 个除权事件列（div_cash/div_bonus/div_transfer/rights_num/rights_price，
    平时 NULL）。全部逐值断言见 tests/test_ingest_daily_derive.py。
    """
    prev = pl.col("close").shift(1).over("code", order_by="trade_date")
    div_cash = pl.col("div_cash").fill_null(0.0)
    div_bonus = pl.col("div_bonus").fill_null(0.0)
    div_transfer = pl.col("div_transfer").fill_null(0.0)
    rights_num = pl.col("rights_num").fill_null(0.0)
    rights_price = pl.col("rights_price").fill_null(0.0)
    event = (div_cash != 0) | (div_bonus != 0) | (div_transfer != 0) | (rights_num != 0)
    den = 1.0 + div_bonus / 10.0 + div_transfer / 10.0
    num = prev - div_cash / 10.0 + rights_price * rights_num / 10.0
    # 分 half-up（交易所口径；Float64 上避免银行家舍入）
    pre_adj = ((num / den * 100.0 + 0.5).floor()) / 100.0
    turn = (pl.col("volume") / (pl.col("float_shares") * 1e4) * 100.0)
    return df.with_columns(
        pl.when(event & (den > 0)).then(pre_adj).otherwise(prev).alias("pre_close"),
    ).with_columns(
        (pl.col("close") - pl.col("pre_close")).alias("change"),
        ((pl.col("close") / pl.col("pre_close") - 1.0) * 100.0).alias("pct_chg"),
        (pl.col("close") * pl.col("total_shares")).fill_nan(None).alias("total_mv"),
        pl.when(pl.col("float_shares").is_not_nan()
                & (pl.col("float_shares") > 0))
        .then(turn).otherwise(None).alias("turnover_rate"),
    )


def nullify_invalid(df: pl.DataFrame) -> pl.DataFrame:
    """adj_factor（NaN/<=0）与 amount（NaN）→ NULL；amount=0 是真实值不动。"""
    return df.with_columns(
        pl.when(pl.col("adj_factor").is_nan() | (pl.col("adj_factor") <= 0))
        .then(None).otherwise(pl.col("adj_factor")).alias("adj_factor"),
        pl.col("amount").fill_nan(None).alias("amount"),
    )


def compute_delist_dates(df: pl.DataFrame, sidecar: pl.DataFrame | None = None,
                         *, stale_trading_days: int = STALE_TRADING_DAYS) -> pl.DataFrame:
    """每 code → delist_date（NULL = 仍上市）。

    - sidecar 命中的 code：最后交易日 < 数据集最大日 → last + 1 天；
    - 不在 sidecar：断流（最大日序 - last 日序）> stale_trading_days → last + 1；
    - 其余 NULL。输出列 code/delist_date（date），供 stock_basic 直接 join。
    """
    days = (df.select(pl.col("trade_date").unique().sort())
              .with_row_index("_idx"))
    max_idx = days.height - 1
    max_d = days["trade_date"][-1] if days.height else None
    last = (df.group_by("code")
              .agg(pl.col("trade_date").max().alias("last_d"))
              .join(days.rename({"trade_date": "last_d"}), on="last_d", how="left")
              .with_columns((max_idx - pl.col("_idx")).alias("_gap")))
    side: dict = {}
    if sidecar is not None and sidecar.height:
        side = {r["code"]: r["last_trade_date"]
                for r in sidecar.iter_rows(named=True)}
    out = []
    for r in last.iter_rows(named=True):
        code, last_d, gap = r["code"], r["last_d"], r["_gap"]
        delist = None
        if code in side:
            if last_d < max_d:
                delist = last_d + datetime.timedelta(days=1)
        elif gap is not None and gap > stale_trading_days:
            delist = last_d + datetime.timedelta(days=1)
        out.append({"code": code, "delist_date": delist})
    return pl.DataFrame(out, schema={"code": pl.String, "delist_date": pl.Date})


def load_delisted_sidecar(path: str = DELISTED_SRC) -> pl.DataFrame | None:
    p = Path(path)
    if not p.is_file():
        print(f"  delist sidecar 缺失（{p}）→ delist_date 仅靠断流兜底", flush=True)
        return None
    sc = pl.read_parquet(p).select("code", "last_trade_date")
    print(f"  delist sidecar: {sc.height:,} codes", flush=True)
    return sc


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

    df = nullify_invalid(derive_daily_fields(df))
    delist = compute_delist_dates(df, load_delisted_sidecar())
    n_delist = delist.filter(pl.col("delist_date").is_not_null()).height
    print(f"  delist_date: {n_delist:,} codes（其中 sidecar 命中另计）", flush=True)

    # --- daily ---
    print("TRUNCATE + 灌 daily", flush=True)
    client.command(f"TRUNCATE TABLE {db}.daily")
    daily = df.select([
        pl.col("code").alias("ts_code"), "trade_date",
        "open", "high", "low", "close", "pre_close", "change", "pct_chg",
        pl.col("volume").alias("vol"), "amount",
    ])
    _insert_table(client, "daily", daily)

    # --- adj_factor（拆列；Nullable：NaN/<=0 归 NULL）---
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
    # delist_date：R21（DATA-C1 生产侧）——sidecar + 断流兜底，见 compute_delist_dates
    print("TRUNCATE + 灌 stock_basic", flush=True)
    client.command(f"TRUNCATE TABLE {db}.stock_basic")
    code = pl.col("code")
    # R4c：板块分类收敛到 core.factio.boards（标量/列式同规则，前缀集合单点）
    market = zh_market_expr(code).alias("market")
    basic_stocks = (
        df.group_by("code").agg(pl.col("trade_date").min().alias("list_date"))
        .with_columns(
            pl.col("code").str.slice(0, 6).alias("symbol"),
            market,
            pl.lit(None, dtype=pl.String).alias("industry"),
        )
        .join(delist, on="code", how="left")
    )
    sb = basic_stocks.select(
        ["symbol", pl.col("code").alias("ts_code"), "list_date", "market",
         "industry", "delist_date"])
    _insert_table(client, "stock_basic", sb)

    print("daily 层灌入完成", flush=True)


if __name__ == "__main__":
    os.environ.setdefault("PYARROW_JEMALLOC", "0")
    main()
