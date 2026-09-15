"""tick 事实库 vs TDX 日线交叉验证（标准验证强度）。

manifest（每 code-day: Σvol/Σamt/首末价/笔数/零价行数）→ 与 daily_fact.parquet 比对:
  E_V = |Σvol_tick − vol_tdx| / vol_tdx;  E_A 同理
  first_price/10000 vs TDX open;  last_price/10000 vs TDX close
已知源口径:
  - SZ 零价行(集合竞价虚拟撮合)已过滤 → 有效首笔=开盘竞价真实成交 → 应≈TDX open
  - SH 首笔可能为集合竞价试撮合价(与 1m B_open_noise 同机制) → 允许差异, 归因
  - 末笔=收盘竞价最后成交 → 应≈TDX close (1m E_nonBJ_close 类尾部按分类处理)
输出: validation/tick_daily_crosscheck.json
"""
import argparse
import json
import sys

import numpy as np
import pandas as pd

import datapaths  # 工具内配置/路径单点（R19）

# tick 转换回执清单（**回执非事实表**；G-READ 登记见 scripts/check_dataiface.py）。
# 路径在模块常量处求值：读取表达式因此化简为常量名，可登记（R19）。
TICK_MANIFEST = datapaths.tick_manifest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default=None, help='工具配置（默认 <工具>/config.yaml）')
    a = p.parse_args()
    cfg = datapaths.load_config(a.config)

    man = pd.read_parquet(TICK_MANIFEST)
    man['trade_date'] = pd.to_datetime(man['trade_date'])
    daily = pd.read_parquet(datapaths.daily_fact())
    # R19 修复：原来只转 man 不转 daily → merge 抛
    # ValueError: You are trying to merge on datetime64[s] and object columns。
    # 本脚本因此**从未产出过** tick_daily_crosscheck.json（validation/ 里没有该文件）。
    daily['trade_date'] = pd.to_datetime(daily['trade_date'])
    daily = daily[['code', 'trade_date', 'open', 'high', 'low', 'close',
                   'amount', 'volume']]

    # tick 池内的 TDX 行
    codes = man['code'].unique()
    daily = daily[daily['code'].isin(codes)]
    print(f'tick code-days: {len(man)}, TDX rows in scope: {len(daily)}')

    m = man.merge(daily, on=['code', 'trade_date'], how='left',
                  suffixes=('_tick', '_tdx'))
    print(f'matched: {m["volume"].notna().sum()}, '
          f'tick-only (TDX 无): {m["volume"].isna().sum()}')

    mm = m[m['volume'].notna()].copy()
    eps = 1.0
    mm['E_V'] = (mm['sum_vol'] - mm['volume']).abs() / mm['volume'].clip(lower=eps)
    mm['E_A'] = (mm['sum_amt'] - mm['amount']).abs() / mm['amount'].clip(lower=eps)
    # 首末价: tick int×10000 → 元
    mm['first_px'] = mm['first_price'] / 10000.0
    mm['last_px'] = mm['last_price'] / 10000.0
    mm['E_open'] = (mm['first_px'] - mm['open']).abs() / mm['open'].clip(lower=0.01)
    mm['E_close'] = (mm['last_px'] - mm['close']).abs() / mm['close'].clip(lower=0.01)
    mm['year'] = mm['trade_date'].dt.year

    def dist(s):
        return {'median': float(s.median()), 'p95': float(s.quantile(0.95)),
                'p99': float(s.quantile(0.99)), 'max': float(s.max())}

    res = {
        'n_tick_code_days': int(len(man)),
        'n_matched': int(len(mm)),
        'n_tick_only': int(len(m) - len(mm)),
        'n_tdx_only': int(len(daily) - len(mm)),
        'E_vol': dist(mm['E_V']), 'E_amt': dist(mm['E_A']),
        'E_open': dist(mm['E_open']), 'E_close': dist(mm['E_close']),
        'by_year': {str(y): {'n': int(g.shape[0]),
                             'E_V_p99': float(g['E_V'].quantile(0.99)),
                             'E_A_p99': float(g['E_A'].quantile(0.99))}
                    for y, g in mm.groupby('year')},
    }

    # 大偏差分类 (量额 >1% 或 首末价 >1%)
    bad = mm[(mm['E_V'] > 0.01) | (mm['E_A'] > 0.01) | (mm['E_open'] > 0.01) |
             (mm['E_close'] > 0.01)].copy()
    print(f'large deviations: {len(bad)}')
    # 分类: 价差主导(量额对但价差) vs 量额差主导
    if len(bad):
        bad['kind'] = np.where(
            (bad['E_open'] > 0.01) & (bad['E_V'] < 0.001),
            'open_gap', np.where(
                (bad['E_close'] > 0.01) & (bad['E_V'] < 0.001),
                'close_gap', 'vol_amt_gap'))
        res['large_dev'] = {
            'n': int(len(bad)),
            'by_kind': {k: int(v) for k, v in bad['kind'].value_counts().items()},
            'by_year': {str(y): int(v) for y, v in
                        bad.groupby('year').size().items()},
            'samples': bad.assign(code=str, date=lambda x: x['trade_date'].dt.date)
            [['code', 'trade_date', 'sum_vol', 'volume', 'sum_amt', 'amount',
              'first_px', 'open', 'last_px', 'close', 'kind']].head(20).to_dict(
                orient='records'),
        }
        for row in res['large_dev']['samples']:
            row['trade_date'] = str(row['trade_date'])

    # tick-only 样本 (TDX 无对应日: 停牌? 上市后?)
    if len(m) > len(mm):
        to = m[m['volume'].isna()][['code', 'trade_date']]
        res['tick_only_samples'] = to.head(10).assign(
            d=lambda x: x['trade_date'].dt.strftime('%Y-%m-%d')).to_dict(
            orient='records')

    out_dir = datapaths.out_dir(cfg, 'validation')
    with open(out_dir / 'tick_daily_crosscheck.json', 'w') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
