#!/usr/bin/env python3
"""通达信日K xlsx 导出 → daily_fact.parquet（事实层：原始价 + 复权因子）

源（<STOCK_ROOT>/data/raw/daily/，STOCK_ROOT 取 factorlab.core.factio.paths 单点）：
  - 19910101至上月底07月31日A股日k线.zip   全量（1991-01-01 .. 2026-07-31）
  - 2026-07-01至2026-08-21A股日k线.zip    增量（与全量尾部重叠，增量优先）
  - 退市股/*.xlsx                         退市股票（与全量重叠，退市目录优先）

复权语义不在此层固定：存原始价 + adj_factor（= 当日后复权价/收盘，等比累计因子）。
复权价由读取层派生：hf = raw×factor；pre = raw×factor/factor(anchor_date)。
退市股无复权因子（adj_factor=NaN，amount=NaN → V4 不会选入，尽力而为）。

排除：200xxx（深B）、900xxx（沪B）。920xxx 为北交所新代码段，保留。

2026-09-07 增补（早期白名单丢字段修复——xlsx 47 列规格只取了 10 列）：
同时直存 7 个除权相关列（平时空，除权日非 0/非空，命名映射见 EVENT_SRC）：
  fq_factor    复权因子  = 前复权因子(起始=1)，逐事件累加，除权日才变
  fq_deduct    扣减值（与红利/送转/配的现金流累计相关，除权日才变）
  div_cash     红利（元/10股）     div_bonus   送股数（每10股）
  div_transfer 转增股（每10股）     rights_num  配股数（每10股）
  rights_price 配股价（元/股）
事件列 = CA Gate（平台 factorlab 执行层 adj_event 表）派生源：任意
div_cash/div_bonus/div_transfer/rights_num ≠ 0 → 除权事件行。
语义勘误（勿再混淆）：adj_factor ≠ fq_factor——adj_factor = 当日后复权价/收盘
≈ fq_factor + fq_deduct/close（扣减值项使其**逐日漂移**，8437 日实测 8212 日
变化），仅作 qfq/hf 价派生链用，**不可作除权日检测源**。
"""
from __future__ import annotations
import argparse
import io
import re
import zipfile
from pathlib import Path
import multiprocessing as mp

import datapaths  # 工具内配置/路径单点（R19）

import numpy as np
import pandas as pd
import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq

# 全量快照文件名标记（`_build_tasks` 用它把全量/增量 zip 分开）
FULL_SNAPSHOT_MARK = '07月31日'

COLS = ['date', 'open', 'high', 'low', 'close', 'amount', 'volume',
        'float_shares', 'total_shares', 'hf_close']
# xlsx 列名 → 事实列名（除权事件组，2026-09-07 增补；顺序即 FINAL_COLS 追加序）
EVENT_SRC = {'复权因子': 'fq_factor', '扣减值': 'fq_deduct', '红利': 'div_cash',
             '送股数': 'div_bonus', '转增股': 'div_transfer', '配股数': 'rights_num',
             '配股价': 'rights_price'}
NUMERIC_COLS = ['open', 'high', 'low', 'close', 'amount', 'volume',
                '流通股本', '总股本', '后复权价'] + list(EVENT_SRC)
SRC_COLS = ['date'] + NUMERIC_COLS
MUST_COLS = {'date', 'open', 'high', 'low', 'close', 'amount', 'volume'}

# 输出列序（worker 落盘 / 主进程合并 / CH 补数均按此，新增列统一追加尾部）
FINAL_COLS = ['trade_date', 'code', 'open', 'high', 'low', 'close', 'adj_factor',
              'amount', 'volume', 'float_shares', 'total_shares'] + list(EVENT_SRC.values())

OUT_SCHEMA = pa.schema([
    pa.field('trade_date', pa.date32()),
    pa.field('code', pa.string()),
    pa.field('open', pa.float64()),
    pa.field('high', pa.float64()),
    pa.field('low', pa.float64()),
    pa.field('close', pa.float64()),
    pa.field('adj_factor', pa.float64()),
    pa.field('amount', pa.float64()),
    pa.field('volume', pa.float64()),
    pa.field('float_shares', pa.float64()),
    pa.field('total_shares', pa.float64()),
    pa.field('fq_factor', pa.float64()),
    pa.field('fq_deduct', pa.float64()),
    pa.field('div_cash', pa.float64()),
    pa.field('div_bonus', pa.float64()),
    pa.field('div_transfer', pa.float64()),
    pa.field('rights_num', pa.float64()),
    pa.field('rights_price', pa.float64()),
])

_tmp_dir: Path | None = None   # main() 启动后赋值，供 mp worker（fork）继承


def market_of(code6: str) -> str:
    if code6[0] == '6':
        return '.SH'
    if code6[0] in ('0', '3'):
        return '.SZ'
    if code6.startswith('920'):
        return '.BJ'
    raise ValueError(f'unexpected code prefix: {code6}')


def _parse_xlsx(payload: bytes) -> pd.DataFrame:
    wb = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    header = [str(x) for x in next(it)]
    missing = MUST_COLS - set(header)
    if missing:
        raise ValueError(f'missing xlsx columns: {sorted(missing)}')
    take = [i for i, h in enumerate(header) if h in SRC_COLS]
    keep_names = [SRC_COLS[SRC_COLS.index(header[i])] for i in take]
    rows = [[r[i] for i in take] for r in it]
    wb.close()
    df = pd.DataFrame(rows, columns=keep_names)
    for cn in NUMERIC_COLS:
        # 某版本文件缺列（如早期导出无事件列）→ 补 NaN 而非 KeyError
        if cn not in df.columns:
            df[cn] = np.nan
        df[cn] = pd.to_numeric(df[cn], errors='coerce')
    df = df.dropna(subset=['date', 'open', 'high', 'low', 'close', 'amount', 'volume'])
    return df


def _parse_one(code6: str, payload: bytes, delisted: bool = False):
    if delisted:
        # 退市股精简格式：8 列（None,date,code,open,high,low,close,volume），无 amount/复权/股本
        wb = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        it = wb.active.iter_rows(values_only=True)
        header = [str(x) for x in next(it)]
        cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        if any(c not in header for c in cols):
            wb.close()
            raise ValueError(f'delisted xlsx columns unexpected: {header}')
        take = [i for i, h in enumerate(header) if h in cols]
        rows = [[r[i] for i in take] for r in it]
        wb.close()
        df = pd.DataFrame(rows, columns=cols)
        for c in cols[1:]:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['date', 'open', 'high', 'low', 'close', 'volume'])
        if df.empty:
            return None
        df['amount'] = np.nan
        df['float_shares'] = np.nan
        df['total_shares'] = np.nan
        df['code'] = code6 + market_of(code6)
        # 无复权因子：退市股历史仅原始价，amount 缺失使 V4 不会选入（尽力而为）
        df['adj_factor'] = np.nan
        for cn in EVENT_SRC.values():
            df[cn] = np.nan
        return df.rename(columns={'date': 'trade_date'})[FINAL_COLS]
    df = _parse_xlsx(payload)
    if df.empty:
        return None
    df['code'] = code6 + market_of(code6)
    df = df.rename(columns={'流通股本': 'float_shares', '总股本': 'total_shares',
                            '后复权价': 'hf_close', **EVENT_SRC})
    df = df[COLS + list(EVENT_SRC.values()) + ['code']].sort_values('date').reset_index(drop=True)
    # adj_factor = 当日后复权价/原始收盘（≈ fq_factor + fq_deduct/close——扣减值
    # 项使其逐日漂移，仅作 qfq/hf 价派生链，非除权检测源，见模块 docstring）
    df['adj_factor'] = df['hf_close'] / df['close']
    df = df.drop(columns=['hf_close'])
    return df.rename(columns={'date': 'trade_date'})[FINAL_COLS]


def _worker(args):
    """解析单文件 → 落盘 tmp/{priority}_{code6}.parquet（同一 code 至多
    full/incr/delisted 三份，主进程按 code 合并去重）。

    分片落盘替代"收集 5533 帧 + 全量 concat"（旧实现峰值内存 ≈ 数据双份 +
    pandas 帧开销；16GB 无页面文件机器上有爆内存风险）。"""
    priority, kind, path, member, code6 = args
    try:
        if kind == 'zip':
            with zipfile.ZipFile(path) as z:
                payload = z.read(member)
        else:
            payload = Path(path).read_bytes()
        df = _parse_one(code6, payload, delisted=(priority == 2))
        if df is None or df.empty:
            return None, code6
        # 与旧 main 同款的日期归一化，提到 worker 内完成 → 落盘即定型；
        # priority 不入 schema（OUT_SCHEMA 为最终列集）——来源优先级由文件名
        # {0|1|2}_ 前缀编码，merge 阶段按文件名顺序 concat + keep='first' 还原
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.normalize()
        df = df.sort_values('trade_date').reset_index(drop=True)
        t = pa.Table.from_pandas(df, schema=OUT_SCHEMA, preserve_index=False)
        pq.write_table(t, _tmp_dir / f'{priority}_{code6}.parquet',
                       compression='zstd', compression_level=3,
                       use_dictionary=['code'])
        del df, t
        return None, code6
    except Exception as e:
        return f'{code6}: {type(e).__name__}: {e}', code6


def _build_tasks(src_dir: Path):
    full_zip = [p for p in src_dir.glob('*.zip') if FULL_SNAPSHOT_MARK in p.name]
    incr_zip = [p for p in src_dir.glob('*.zip') if p not in full_zip]
    if not full_zip:
        raise SystemExit('no full daily kline zip found')
    tasks = []
    zf = zipfile.ZipFile(full_zip[0])
    for n in zf.namelist():
        if not n.endswith('.xlsx'):
            continue
        code6 = n.split('/')[-1].split('.')[0]
        if not re.fullmatch(r'\d{6}', code6):
            continue
        tasks.append((0, 'zip', str(full_zip[0]), n, code6))
    zf.close()
    if incr_zip:
        zi = zipfile.ZipFile(incr_zip[0])
        for n in zi.namelist():
            if not n.endswith('.xlsx'):
                continue
            code6 = n.split('/')[-1].split('.')[0]
            if not re.fullmatch(r'\d{6}', code6):
                continue
            tasks.append((1, 'zip', str(incr_zip[0]), n, code6))
        zi.close()
    for f in src_dir.glob('退市股/*.xlsx'):
        code6 = f.name[:6]
        if re.fullmatch(r'\d{6}', code6) and not (code6.startswith('200') or code6.startswith('900')):
            tasks.append((2, 'dir', str(f), None, code6))
    return tasks


def main():
    cfg = datapaths.load_config()
    p = argparse.ArgumentParser()
    p.add_argument('--src-dir', default=None,
                   help='源目录（默认 A8：<STOCK_ROOT>/data/raw/daily）')
    p.add_argument('--workers', type=int, default=cfg.get('ingest', {}).get('workers', 4),
                   help='xlsx 解析并发（内存/CPU 保守值；机器紧张时再降）')
    p.add_argument('--out', default=None,
                   help='输出 parquet（默认 A5 权威位：factio.paths.daily_fact_path()；'
                        'R19 前默认落 <src_dir>，即写进 raw/ 分类——按公约 raw/ 只读）')
    p.add_argument('--tmp', default=None,
                   help='分片临时目录（默认 <工具>/_staging/daily_tmp，出 data/）')
    p.add_argument('--merge-only', action='store_true',
                   help='跳过 xlsx 解析，复用 --tmp 现有分片只做合并（解析崩溃后快速续跑）')
    a = p.parse_args()
    src_dir = Path(a.src_dir) if a.src_dir else datapaths.raw_daily_dir()
    out = Path(a.out) if a.out else datapaths.daily_fact()
    tmp = Path(a.tmp) if a.tmp else datapaths.out_dir(cfg, 'staging') / 'daily_tmp'

    tasks = _build_tasks(src_dir)
    print(f'tasks: {len(tasks)} (full/incr/delisted: '
          f'{sum(t[0]==0 for t in tasks)}/{sum(t[0]==1 for t in tasks)}/{sum(t[0]==2 for t in tasks)})')

    global _tmp_dir
    tmp.mkdir(parents=True, exist_ok=True)
    _tmp_dir = tmp
    errors = []
    done = 0
    if not a.merge_only:
        with mp.Pool(a.workers) as pool:
            for (err, _code) in pool.imap_unordered(_worker, tasks):
                done += 1
                if err:
                    errors.append(err)
                if done % 500 == 0:
                    print(f'  parsed {done}/{len(tasks)}', flush=True)
        print(f'parsed {done}/{len(tasks)} files, errors: {len(errors)}')
        for e in errors[:10]:
            print('  ERROR', e)
    else:
        print(f'--merge-only：跳过解析（复用 {tmp} 现有分片）')

    # 逐 code 流式合并（同 code 至多 full(0)/incr(1)/delisted(2) 三份）：
    # 文件名 {0|1|2}_ 前缀升序 = 来源优先级；各文件内部已按 trade_date 升序 →
    # 顺序 concat 后 drop_duplicates(keep='first') 即"full 行优先，incr/delisted
    # 补充未覆盖日期"（与旧全局 concat 排序去重语义一致；旧注释"增量/退市优先"
    # 与实际相反——重叠行源自同源厂商值相同，无差异）。
    files = sorted(tmp.glob('*.parquet'))
    if not files:
        raise SystemExit('no data parsed')
    by_code: dict[str, list[Path]] = {}
    for f in files:
        by_code.setdefault(f.name.split('_', 1)[1][:-8], []).append(f)
    n_rows = 0
    n_bad = 0
    d_min = d_max = None
    codes_done = 0
    with pq.ParquetWriter(out, OUT_SCHEMA, compression='zstd',
                          compression_level=3, use_dictionary=['code']) as w:
        for code6 in sorted(by_code):
            df = pd.concat([pd.read_parquet(p) for p in sorted(by_code[code6])],
                           ignore_index=True)
            df = df.drop_duplicates(subset=['trade_date'], keep='first')
            # 基础校验（原始价）
            bad = df[(df['high'] < df['low']) | (df['high'] < df['open']) |
                     (df['high'] < df['close']) | (df['low'] > df['open']) |
                     (df['low'] > df['close']) | (df['amount'] < 0) |
                     (df['volume'] < 0)]
            n_bad += len(bad)
            if len(bad):
                df = df.drop(bad.index)
            if df.empty:
                continue
            df = df.sort_values('trade_date').reset_index(drop=True)
            n_rows += len(df)
            d = df['trade_date']
            d_min = d.min() if d_min is None else min(d_min, d.min())
            d_max = d.max() if d_max is None else max(d_max, d.max())
            w.write_table(pa.Table.from_pandas(df, schema=OUT_SCHEMA,
                                               preserve_index=False))
            codes_done += 1
            if codes_done % 1000 == 0:
                print(f'  merged {codes_done}/{len(by_code)} codes', flush=True)
            del df
    print(f'unique rows: {n_rows}, codes: {codes_done}')
    print(f'OHLC invalid rows: {n_bad}')
    print(f'wrote {out} ({out.stat().st_size/1e6:.1f} MB)')
    print(f'date range: {str(d_min)[:10]} .. {str(d_max)[:10]}')


if __name__ == '__main__':
    main()
