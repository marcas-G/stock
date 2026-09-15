#!/usr/bin/env python3
"""中证500 (000905.SH) 指数日线 → benchmark_daily_pre.parquet

数据源：腾讯 kline 接口（web.ifzq.gtimg.cn），按 end 回退 count 条翻页。
指数点位无复权问题，直接取收盘价作为 pre_close。
"""
from __future__ import annotations
import argparse
import datetime
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

import datapaths  # 工具内配置/路径单点（R19）

START = '2018-01-01'
SYMBOL = 'sh000905'


def default_end() -> str:
    """翻页起点默认 = **今天**。

    R19 修复：原实现写死 `end = '2026-12-31'`（未来日期）。实测上游对未到来的日期返回
    `{"code": 11, "msg": "mysql connect failed...", "data": ""}` —— `data` 是**字符串**，
    紧接着的 `.get(SYMBOL)` 抛 AttributeError：**脚本在 2026-12-31 之前根本跑不起来**。
    """
    return datetime.date.today().isoformat()


def fetch_page(end: str, count: int = 800) -> list:
    url = (f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
           f'?param={SYMBOL},day,{START},{end},{count},qfq')
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    payload = r.json()
    data = payload.get('data')
    if not isinstance(data, dict):      # R19：异常响应显式报错，不再以 AttributeError 面目出现
        raise RuntimeError(
            f'上游返回异常（end={end}）：code={payload.get("code")} '
            f'msg={payload.get("msg")!r} data={data!r}')
    d = data.get(SYMBOL, {})
    bars = d.get('qfqday') or d.get('day') or []
    return [b[:5] for b in bars]  # [date, open, close, high, low]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default=None,
                   help='输出 parquet（默认 A10 权威位：<STOCK_ROOT>/data/ref/000905.SH.parquet）')
    p.add_argument('--start', default=START)
    p.add_argument('--end', default=None,
                   help='翻页起点日期（默认今天；见 default_end() 的 R19 修复说明）')
    a = p.parse_args()

    all_bars = []
    end = a.end or default_end()
    while True:
        bars = fetch_page(end)
        if not bars:
            break
        all_bars = bars + all_bars
        oldest = bars[0][0]
        print(f'  page end={end} -> oldest={oldest} (n={len(all_bars)})', flush=True)
        if oldest <= a.start:
            break
        end = str(pd.Timestamp(oldest) - pd.Timedelta(days=1))[:10]
        time.sleep(0.5)

    df = pd.DataFrame(all_bars, columns=['date', 'open', 'close', 'high', 'low'])
    df['trade_date'] = pd.to_datetime(df['date']).dt.normalize()
    df['pre_close'] = pd.to_numeric(df['close'], errors='coerce')
    df = df[df['pre_close'].notna()].sort_values('trade_date')
    df = df.drop_duplicates(subset=['trade_date'], keep='last')
    print(f'bars: {len(df)}  {df["trade_date"].min().date()} .. {df["trade_date"].max().date()}')

    table = pa.Table.from_pandas(
        df[['trade_date', 'pre_close']].reset_index(drop=True),
        schema=pa.schema([
            pa.field('trade_date', pa.date32()),
            pa.field('pre_close', pa.float64()),
        ]), preserve_index=False)
    out = Path(a.out) if a.out else datapaths.index_daily()
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out, compression='zstd', compression_level=3)
    print(f'wrote {out} ({out.stat().st_size/1e6:.2f} MB)')


if __name__ == '__main__':
    main()
