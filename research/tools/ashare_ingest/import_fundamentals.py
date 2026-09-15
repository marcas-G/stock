#!/usr/bin/env python3
"""PIT 基本面 → fundamentals_pti.parquet

数据源：
  - daily_fact.parquet（通达信日K）：market_cap（原始 close×总股本，万股→股）、list_date、名称源无
  - TDX 财务 parquet（Windows 导出的 {date}_financial.parquet，见 20260515/财务数据和个股财务_parquet.py）：
    code, 报告期, 更新日期, 主营收入(万), 净利润(万), 总资产(万), 总负债(万), 名称 → ST 判断

PIT 语义：available_date = 财务更新日期（TDX gpcw 每交易日更新已披露财报）；
market_cap 用 factor_date 当天的总股本×close（daily 的股本列逐日更新）。
pe_ratio = market_cap / 年化净利润（报告期累计净利×(4/报告季度数)，近似 TTM 偏保守）。
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import datapaths  # 工具内配置/路径单点（R19）

OUT_SCHEMA = pa.schema([
    pa.field('available_date', pa.date32()),
    pa.field('code', pa.string()),
    pa.field('market_cap', pa.float64()),
    pa.field('pe_ratio', pa.float64()),
    pa.field('operating_revenue', pa.float64()),
    pa.field('total_assets', pa.float64()),
    pa.field('total_liability', pa.float64()),
    pa.field('list_date', pa.date32()),
    pa.field('is_st', pa.bool_()),
])

# TDX 财务 parquet 的列名候选（jxry.tdx Financial 输出，按实际导出调整）
CANDIDATES = {
    'code': ['code', 'code_0'],
    'report_date': ['报告期'],
    'update_date': ['更新日期'],
    'revenue': ['主营收入', '主营收入(万)'],
    'net_profit': ['净利润', '净利润(万)'],
    'total_assets': ['总资产', '总资产(万)'],
    'total_liability': ['总负债', '总负债(万)'],
    'name': ['名称'],
}


def _pick(df: pd.DataFrame, names) -> str | None:
    for n in names:
        if n in df.columns:
            return n
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--daily', default=None,
                   help='daily_fact（默认 A5 权威位：factio.paths.daily_fact_path()）')
    p.add_argument('--fin-parquet', default=None,
                   help='Windows 导出的 {date}_financial.parquet（TDX 财务）')
    p.add_argument('--out', default=None,
                   help='输出 parquet（默认 <STOCK_ROOT>/data/fact/fundamentals/fundamentals_pti.parquet）')
    a = p.parse_args()
    daily_path = Path(a.daily) if a.daily else datapaths.daily_fact()
    out_path = Path(a.out) if a.out else datapaths.fundamentals()

    daily = pd.read_parquet(daily_path)
    daily['trade_date'] = pd.to_datetime(daily['trade_date']).dt.normalize()
    daily['code'] = daily['code'].astype(str)

    if a.fin_parquet is None:
        raise SystemExit(
            '未提供 --fin-parquet（Windows TDX 财务导出 {date}_financial.parquet）。\n'
            '先在 Windows 运行 20260515/财务数据和个股财务_parquet.py 并拷贝产物到本机。')

    fin = pd.read_parquet(a.fin_parquet)
    col = {k: _pick(fin, v) for k, v in CANDIDATES.items()}
    missing = {k for k, v in col.items() if v is None}
    if missing:
        raise SystemExit(f'财务 parquet 缺列 {sorted(missing)}；实际列: {fin.columns.tolist()[:40]}')

    fin = fin.rename(columns={col['code']: 'code',
                              col['report_date']: 'report_date',
                              col['update_date']: 'update_date',
                              col['revenue']: 'revenue',
                              col['net_profit']: 'net_profit',
                              col['total_assets']: 'total_assets',
                              col['total_liability']: 'total_liability'})
    fin = fin.drop_duplicates(subset=['code', 'report_date'], keep='last')
    fin['report_date'] = pd.to_datetime(fin['report_date']).dt.normalize()
    fin['update_date'] = pd.to_datetime(fin['update_date']).dt.normalize()
    for c in ['revenue', 'net_profit', 'total_assets', 'total_liability']:
        fin[c] = pd.to_numeric(fin[c], errors='coerce') * 10000.0  # 万 → 元
    fin['quarter'] = fin['report_date'].dt.month.map({3: 1, 6: 2, 9: 3, 12: 4})
    fin['annualized_net_profit'] = fin['net_profit'] * 4.0 / fin['quarter']
    if col['name'] is not None:
        fin['is_st'] = fin['name'].fillna('').astype(str).str.contains('ST', regex=False)
    else:
        fin['is_st'] = False

    # PIT：财务披露时刻（update_date）之后才可见
    fin_pti = fin.sort_values(['code', 'update_date', 'report_date']) \
        .drop_duplicates(subset=['code', 'update_date'], keep='last') \
        .sort_values(['code', 'update_date']).reset_index(drop=True)

    # market_cap：factor 日期的市值 = 当日总股本 × 当日原始收盘价（PIT；复权价会
    # 被因子序列缩放，不能用 pre_* 算市值）
    daily_cap = daily[daily['total_shares'].notna() & (daily['total_shares'] > 0)].copy()
    daily_cap['market_cap'] = daily_cap['close'] * daily_cap['total_shares'] * 10000.0
    cap = daily_cap[['trade_date', 'code', 'market_cap']].sort_values(
        ['code', 'trade_date']).drop_duplicates(subset=['code', 'trade_date'], keep='last')

    # 每股代码的 list_date
    list_date = daily.groupby('code')['trade_date'].min().rename('list_date')

    # 合并：对每个 (code, update_date) 找当日可见市值
    merged = fin_pti.merge(list_date, on='code', how='left')
    merged = merged.merge(cap, left_on=['code', 'update_date'], right_on=['code', 'trade_date'],
                          how='left')
    merged = merged.drop(columns=['trade_date'])
    merged['pe_ratio'] = np.where(
        merged['annualized_net_profit'].notna() & (merged['annualized_net_profit'] > 0),
        merged['market_cap'] / merged['annualized_net_profit'], np.nan)
    merged['available_date'] = merged['update_date']
    merged['operating_revenue'] = merged['revenue']

    out = merged[['available_date', 'code', 'market_cap', 'pe_ratio',
                  'operating_revenue', 'total_assets', 'total_liability',
                  'list_date', 'is_st']].sort_values(
        ['code', 'available_date']).reset_index(drop=True)
    out = out[out['market_cap'].notna() & out['operating_revenue'].notna()]

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(out, schema=OUT_SCHEMA, preserve_index=False)
    pq.write_table(table, out_path, compression='zstd', compression_level=3,
                   use_dictionary=['code'])
    print(f'wrote {out_path} ({out_path.stat().st_size/1e6:.1f} MB)')
    print(f'rows: {len(out)}, codes: {out["code"].nunique()}, '
          f'available_date range: {out["available_date"].min().date()} .. {out["available_date"].max().date()}')


if __name__ == '__main__':
    main()
