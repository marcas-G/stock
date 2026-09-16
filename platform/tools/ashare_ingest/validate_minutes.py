#!/usr/bin/env python3
"""bars_1m 事实库 vs TDX 日线交叉验证（绝对单位跨年锚定，Step 0B P0 双重验证）。

1m 按 (code, trade_date) 聚合 → 与 daily_fact.parquet（原始日线）对比：
  - day_open   = 首个成交分钟 (volume>0 OR amount>0) 的 open (min_by)
  - day_close  = 最后成交分钟的 close (max_by)
  - day_high/low = 成交分钟 max(high)/min(low)（FILTER）
  - sum_amount/sum_volume = 全天求和（含零成交行, 贡献 0）
零成交 flat bar 的 OHLC=陈旧前值会污染日级聚合 (如无竞价日 row0=前收盘),
只统计成交分钟; TDX 日线以实际成交为准。注意: 旧 window first_act 实现会把
首个成交分钟后紧邻的零成交 bar 误标 first_act (累计和=1), 导致 day_open 偏低,
已改为 min_by/max_by (只取成交分钟, 经 2026/08 全月比对 + TDX open 仲裁)。
内存: 全库 window 物化曾到 112GB, min_by/max_by 为标准 hash 聚合, 无 window 排序。
相对误差：E_X = |agg − daily| / max(|daily|, ε)，按年输出分布。
已知隔离：2025-12-01..03 后缀式网格不入库（conversion_errors），TDX 侧有行
而 bars_1m 无行，join 自动落空，不判错。
"""
import argparse
import json
import os
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from factorlab.core.factio import partitions

import datapaths  # 工具内配置/路径单点（R19）

EPS = 1.0


def connect_duckdb(cfg: dict):
    # 全库 1m 聚合 (window 函数物化 1.85B 行) 曾撑到 ~112GB RSS: 限内存, 超限
    # 落盘。机器 125GB 总内存, 默认 32GB 上限 (DUCKDB_MEM_LIMIT 可调)。
    # R19：temp_directory 不再写死绝对路径，落在工具 staging 区（DUCKDB_TMP_DIR 可覆盖）。
    con = duckdb.connect()
    con.execute("SET memory_limit='" + os.environ.get('DUCKDB_MEM_LIMIT', '32GB') + "'")
    tmp = os.environ.get('DUCKDB_TMP_DIR') or str(datapaths.out_dir(cfg, 'staging') / 'duckdb')
    con.execute("SET temp_directory='" + tmp + "'")
    return con


def _month_parts(root: Path, months: list[str] | None) -> list[str]:
    """月份 → bars_1m 月 part 文件清单（前缀/路径规则取 core.factio.partitions 单点）。

    months 形如 ['2026/07']；None = 枚举 root 下全部月份（以 part 文件存在为准）。
    """
    if months:
        return [str(partitions.bars_month_part(root, *map(int, m.split('/'))))
                for m in months]
    yp, mp = partitions.YEAR_PREFIX, partitions.MONTH_PREFIX
    out: list[str] = []
    for entry in sorted(os.listdir(root)):
        if not entry.startswith(yp):
            continue
        for m in sorted(os.listdir(root / entry)):
            if not m.startswith(mp):
                continue
            part = partitions.bars_month_part(root, int(entry[len(yp):]), int(m[len(mp):]))
            if part.exists():
                out.append(str(part))
    return out


def aggregate(con, files):
    # day_open/high/low/close 只用成交分钟 (volume>0 OR amount>0): 零成交 flat bar
    # 的 OHLC=陈旧前值 (如无竞价日的 row0=前收盘), 会污染日级聚合; TDX 日线以
    # 实际成交为准。sum_amount/volume 全分钟求和 (零行贡献 0)。
    files_sql = '[' + ','.join(f"'{f}'" for f in files) + ']'
    sql = f"""
    SELECT code, trade_date,
           min_by(CASE WHEN volume > 0 OR amount > 0 THEN open END,
                  CASE WHEN volume > 0 OR amount > 0 THEN minute_index END) AS day_open,
           max(high) FILTER (WHERE volume > 0 OR amount > 0) AS day_high,
           min(low)  FILTER (WHERE volume > 0 OR amount > 0) AS day_low,
           max_by(CASE WHEN volume > 0 OR amount > 0 THEN close END,
                  CASE WHEN volume > 0 OR amount > 0 THEN minute_index END) AS day_close,
           sum(amount) AS sum_amount,
           sum(volume) AS sum_volume,
           count(*) AS n_minutes
    FROM read_parquet({files_sql})
    GROUP BY code, trade_date
    """
    return con.execute(sql).df()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default=None, help='工具配置（默认 <工具>/config.yaml）')
    p.add_argument('--months', default=None,
                   help='逗号分隔 YYYY/MM（默认枚举全部月份）')
    a = p.parse_args()
    cfg = datapaths.load_config(a.config)
    months = a.months.split(',') if a.months else None
    month_pat = '|'.join(months) if months else '\\d{4}/\\d{2}'
    con = connect_duckdb(cfg)
    agg = aggregate(con, _month_parts(datapaths.bars_1m_root(), months))
    daily = con.execute(
        "SELECT trade_date, code, open, high, low, close, amount, volume "
        "FROM read_parquet('" + str(datapaths.daily_fact()) + "')").df()
    con.close()
    daily['ym'] = daily['trade_date'].astype(str).str[:7].str.replace('-', '/')

    m = agg.merge(daily, on=['code', 'trade_date'], suffixes=('_agg', '_day'))
    print(f'aggregated code-days: {len(agg)}, matched vs TDX daily: {len(m)}')
    # n_miss 只统计所选月份范围内、bars_1m 无行的日线行
    in_scope = daily['ym'].str.match(month_pat) if months else pd.Series(True, index=daily.index)
    n_miss = int(len(daily[in_scope]) - len(m))
    if n_miss:
        print(f'daily rows with no 1m match: {n_miss} (停牌/隔离日)')

    cols = [('day_open', 'open'), ('day_high', 'high'), ('day_low', 'low'),
            ('day_close', 'close'), ('sum_amount', 'amount'), ('sum_volume', 'volume')]
    m['year'] = m['trade_date'].astype(str).str[:4]
    out = {'matched_code_days': len(m), 'no_1m_match_daily_rows': int(n_miss)}
    for agg_c, day_c in cols:
        e = (m[agg_c] - m[day_c]).abs() / np.maximum(m[day_c].abs(), EPS)
        e = e.dropna()
        out[agg_c] = {
            'median': float(e.median()), 'p95': float(e.quantile(0.95)),
            'p99': float(e.quantile(0.99)), 'max': float(e.max()),
            'max_code_date': str(m.loc[e.idxmax(), 'code']) + '@' + str(m.loc[e.idxmax(), 'trade_date'])[:10],
        }
        print(f'{agg_c:10s} vs daily {day_c:6s}: median={e.median():.2e} '
              f'p95={e.quantile(0.95):.2e} p99={e.quantile(0.99):.2e} max={e.max():.2e}')
        # 按年
        by_year = {}
        for y, g in e.groupby(m.loc[e.index, 'year']):
            by_year[str(y)] = {'n': int(g.size), 'median': float(g.median()),
                               'p99': float(g.quantile(0.99))}
        out[agg_c + '_by_year'] = by_year

    out_path = datapaths.out_dir(cfg, 'validation') / 'minute_daily_crosscheck.json'
    out_path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f'wrote {out_path}')


if __name__ == '__main__':
    main()
