#!/usr/bin/env python
"""W0 QA: cancels 表 × raw 源 独立交叉复验 (与抽取器完全不同的读路径)


import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # lob_fact/
逐 code-day: raw 逐笔成交 → 空BS∩C 行计数/侧别/量 对照 tick_fact/cancels parquet。
覆盖: 跨月样本 (2025-09 差 1 行月 / 2026-02 / 2026-08) + 各月整月对账 (manifest vs 表)。

用法: python verify_cancels_sample.py            # 默认样本集
      python verify_cancels_sample.py --full     # 整月 manifest vs 表行数对账
"""
import zipfile, io, os, glob, argparse
import pandas as pd, polars as pl
import datetime

from core import config as C

ROOT = C.QUARK_ROOT
CANC = f'{C.TICK_FACT_ROOT}cancels/'
SAMPLE = [  # (day, code) — 跨月 + 覆盖 B 格式(撤单行 BS 真空)日 (2026-09-10 修复)
    ('20250812', '000155'), ('20250915', '000155'), ('20250822', '000021'),  # B 格式
    ('20260210', '000155'), ('20260803', '000155'), ('20260706', '000155'),  # B 格式
]
COLS = ['自然日', '成交代码', 'BS标志', '成交数量', '叫卖序号', '叫买序号']


def raw_cancel_counts(day, code):
    p = f'{ROOT}/{day}/{code}/{code}.SZ.zip'
    if not os.path.exists(p):
        return None
    with zipfile.ZipFile(p) as zf:
        df = pd.read_csv(io.BytesIO(zf.read('逐笔成交.csv')), encoding='gbk',
                         usecols=COLS, dtype={'自然日': 'str', '成交代码': 'str',
                                              'BS标志': 'str', '成交数量': 'float64',
                                              '叫卖序号': 'float64', '叫买序号': 'float64'})
    df = df[df['自然日'] != '0']
    # fillna: B 格式下载批次撤单行 BS 真空 → NaN (2026-09-10, 与抽取器同根)
    bs = df['BS标志'].fillna('').astype(str).str.strip()
    cc = df['成交代码'].astype(str).str.strip()
    sel = bs.eq('') & cc.eq('C')
    bid = df.loc[sel, '叫买序号'].to_numpy(dtype='float64')
    ask = df.loc[sel, '叫卖序号'].to_numpy(dtype='float64')
    return dict(n=int(sel.sum()),
                n_b=int((bid > 0).sum()), n_s=int((ask > 0).sum()),
                vol=int(df.loc[sel, '成交数量'].sum()))


def tbl_counts(code, day):
    from factorlab.adapters.tick_read import read_tick_table
    try:
        return read_tick_table('cancels', day, codes=[f'{code}.SZ'])
    except FileNotFoundError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--full', action='store_true')
    args = ap.parse_args()
    bad = 0
    for day, code in SAMPLE:
        r = raw_cancel_counts(day, code)
        t = tbl_counts(code, day)
        if r is None or t is None:
            print(f'  {code}@{day}: 缺失 (raw={r is not None} tbl={t is not None})')
            bad += 1
            continue
        tb = int(t.filter(pl.col('side') == 0).height)
        ts = int(t.filter(pl.col('side') == 1).height)
        ok = (r['n'] == t.height and r['n_b'] == tb and r['n_s'] == ts
              and r['vol'] == t['volume'].sum())
        print(f'  {code}@{day}: raw n={r["n"]} B={r["n_b"]} S={r["n_s"]} vol={r["vol"]:,} '
              f'| tbl n={t.height} B={tb} S={ts} vol={t["volume"].sum():,} -> '
              f'{"OK" if ok else "MISMATCH"}')
        bad += 0 if ok else 1
    if args.full:
        # manifest vs 表逐月行数对账 (整月)
        m = pl.read_parquet(f'{C.TICK_FACT_ROOT}_manifest/cancels_manifest.parquet')
        m = m.with_columns(pl.col('trade_date').dt.strftime('%Y%m%d'))
        print('== 整月 manifest vs 表行数')
        for ym in sorted({d[:6] for d in m['trade_date'].unique()}):
            y, mo = ym[:4], ym[4:]
            fs = sorted(glob.glob(f'{CANC}/year={y}/month={mo}/part-*.parquet'))
            if not fs:
                print(f'  {ym}: 表缺失'); bad += 1; continue
            rows = pl.scan_parquet(fs).collect().height
            msum = int(m.filter(m['trade_date'].str.starts_with(ym))['n_cancels'].sum())
            print(f'  {ym}: manifest={msum:,} 表={rows:,} -> '
                  f'{"OK" if msum == rows else "MISMATCH"}')
            bad += 0 if msum == rows else 1
    print(f'RESULT: {"ALL OK" if bad == 0 else f"{bad} MISMATCH"}')


if __name__ == '__main__':
    main()
