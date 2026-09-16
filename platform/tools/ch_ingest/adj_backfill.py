#!/usr/bin/env python3
"""daily_fact.parquet → ClickHouse 除权相关表（adj_detail + adj_event）全量重建

背景（2026-09-07）：早期 CH 装载环节的白名单只灌了价量列（daily/adj_factor/
daily_basic，18,162,795 行全量），K 包 xlsx 的复权因子/扣减值/红利/送/转/配
从未进库 → 平台 CA Gate（factorlab 执行层）所需 adj_event 无源。
（历史：本脚本原属 ashare_alpha3 项目的 12 号脚本；CH 装载侧源码在 2026-09-03 仓库
重组时被遗留、2026-09-08 取回。R19 收编时按职责归位到 ch_ingest：`ddl.sql` 无
adj_event/adj_detail 二表，daily 层灌入由 `ingest_daily.py` 负责，而本脚本幂等管理
这两张**派生表**，与 ingest_daily.py **各管各的表、互不触碰**。）

语义：
  - 源 = daily_fact.parquet（ashare_ingest/import_daily.py 的产物，2026-09-07 起含 7 列
    除权字段；事件列平时 NaN、除权日非 0）
  - adj_detail(ts_code, trade_date, fq_factor, fq_deduct, div_cash, div_bonus,
    div_transfer, rights_num, rights_price)：全量明细（18M 行级）
  - adj_event(ts_code, trade_date)：除权事件行 = 任意
    div_cash/div_bonus/div_transfer/rights_num ≠ 0 —— 平台 CA Gate 事件源
    （contract：factorlab.adapters.read.market_open._adj_rows_ch——ts_code String + trade_date Date）

幂等性：两张表纯派生自 parquet（源真相），脚本每次 = DROP + CREATE + 流式全量
INSERT（分块 40 万行，pyarrow iter_batches 单批驻留，内存 < ~200MB；不并发、
不长时间占用 CPU）。重跑安全。既有 daily/adj_factor/daily_basic 表零改动。

2026-09-07 执行记录（一次通过，75s，exit 0）：
  - adj_detail 18,162,795 行 = parquet 全量；adj_event 57,173 行，
    uniq(ts_code, trade_date) = 57,173（无重复）
  - 验证：全 7 列 NULL 数 vs parquet NaN 逐列精确相等（1,289,000 ×2 密集列 +
    18,105,621 ×5 稀疏列）；600519.SH 事件行值抽对（2025-12-19 div 239.57 /
    fq 5.63 / deduct 1640.8）；平台读路径 load_adj_event_window 真 CH 冒烟过
  - 已知角落：920029.BJ 2025-03-28 五行全 0 的填充行（来源 xlsx 事件版式）——
    按 ≠0 事件语义不进 adj_event，两库一致保留，非缺陷

用法（单解释器 = platform venv；任选 cwd）：
  platform/.venv/bin/python platform/tools/ch_ingest/adj_backfill.py [--src …] [--batch 400000]
"""
from __future__ import annotations

import argparse
import gc
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from factorlab.core.factio import paths  # R19：路径单点（原为写死绝对路径）

from common import connect  # noqa: E402  ch_ingest 同族写路径（R19：原 import
# factorlab.data.ch_source 是**死路径**——factorlab.data 早已不存在）

DAILY_SRC = str(paths.daily_fact_path())   # A5 权威位（data-map）

ADJ_DETAIL_COLS = ['ts_code', 'trade_date', 'fq_factor', 'fq_deduct', 'div_cash',
                   'div_bonus', 'div_transfer', 'rights_num', 'rights_price']
EVENT_COLS = ['div_cash', 'div_bonus', 'div_transfer', 'rights_num']

_DDL = {
    'adj_detail': """
        CREATE TABLE IF NOT EXISTS {db}.adj_detail (
            ts_code String,
            trade_date Date,
            fq_factor Nullable(Float64),
            fq_deduct Nullable(Float64),
            div_cash Nullable(Float64),
            div_bonus Nullable(Float64),
            div_transfer Nullable(Float64),
            rights_num Nullable(Float64),
            rights_price Nullable(Float64)
        ) ENGINE = MergeTree ORDER BY (ts_code, trade_date)
    """,
    'adj_event': """
        CREATE TABLE IF NOT EXISTS {db}.adj_event (
            ts_code String,
            trade_date Date
        ) ENGINE = MergeTree ORDER BY (ts_code, trade_date)
    """,
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--src', default=DAILY_SRC, help='源 parquet（默认 A5 权威位）')
    p.add_argument('--batch', type=int, default=400_000)
    p.add_argument('--db', default='factorlab')
    a = p.parse_args()
    src = Path(a.src)
    if not src.exists():
        raise SystemExit(f'源缺失: {src}')

    client = connect()
    t0 = time.time()

    # 幂等：派生表全量重建（DROP + CREATE）
    for table, ddl in _DDL.items():
        client.command(f'DROP TABLE IF EXISTS {a.db}.{table}')
        client.command(ddl.format(db=a.db))
        print(f'[{table}] table ready ({time.time()-t0:.0f}s)')

    n_detail = n_event = 0
    t_last = t0
    pf = pq.ParquetFile(src)
    total_batches = (pf.metadata.num_rows + a.batch - 1) // a.batch
    for i, batch in enumerate(pf.iter_batches(
            batch_size=a.batch,
            columns=['code', 'trade_date', *ADJ_DETAIL_COLS[2:]]), 1):
        df = batch.to_pandas().rename(columns={'code': 'ts_code'})
        # NaN → NULL（Nullable 列；clickhouse-connect 对 float NaN 按 NULL 写）
        detail = df[ADJ_DETAIL_COLS]
        client.insert_df('adj_detail', detail)
        n_detail += len(detail)

        ev = df[['ts_code', 'trade_date', *EVENT_COLS]].fillna(0.0)
        ev = ev[(ev[EVENT_COLS] != 0).any(axis=1)][['ts_code', 'trade_date']]
        if len(ev):
            client.insert_df('adj_event', ev)
            n_event += len(ev)

        del df, detail, ev, batch
        if i % 20 == 0 or i == total_batches:
            print(f'  batch {i}/{total_batches} | detail {n_detail:,} | '
                  f'event {n_event:,} | {time.time()-t_last:.0f}s since last',
                  flush=True)
            t_last = time.time()
        if i % 40 == 0:
            gc.collect()

    # 收尾核对
    for table in ('adj_detail', 'adj_event'):
        n = client.query(f'SELECT count() FROM {a.db}.{table}').result_rows[0][0]
        print(f'[{table}] rows in CH: {n:,}')
    ev_days = client.query(
        f"SELECT uniqExact((ts_code, trade_date)) FROM {a.db}.adj_event"
    ).result_rows[0][0]
    print(f'[adj_event] uniq (ts_code, trade_date): {ev_days:,}')
    print(f'DONE in {time.time()-t0:.0f}s (parquet rows: {pf.metadata.num_rows:,})')


if __name__ == '__main__':
    main()
