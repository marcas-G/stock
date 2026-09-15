#!/usr/bin/env python3
"""通达信日K xlsx 导出 → daily_fact.parquet（事实层：原始价 + 复权因子）

源（<STOCK_ROOT>/data/raw/daily/，STOCK_ROOT 取 factorlab.core.factio.paths 单点）：
  - 19910101至上月底07月31日A股日k线.zip   全量（1991-01-01 .. 2026-07-31）
  - 2026-07-01至2026-08-21A股日k线.zip    增量（与全量尾部重叠，增量优先）
  - 退市股/*.xlsx                         退市股票（与全量重叠，退市目录优先）

复权语义不在此层固定：存原始价 + adj_factor（= 当日后复权价/收盘，等比累计因子）。
复权价由读取层派生：hf = raw×factor；pre = raw×factor/factor(anchor_date)。
退市股无复权因子（adj_factor=NaN，amount=NaN → V4 不会选入，尽力而为）。

R21 TOOLS-C2（2026-09-15）：退市文件**以 in-file `code` 列为真实代码**（形如
`sh.600811`/`sz.000003`/`bj.920305`），文件名 code6 仅作无 code 列/空文件时的
兜底。事实：`000018/000023/000024/000033/000038` 5 个文件的 code 列实为
`sh.600811`——旧实现按文件名贴标签造出 5 只假历史并让 600811 数据错位。
shard 命名与 merge 分组都按真实代码（多文件映射同 code 时按 trade_date 去重，
priority 升序 keep first）；同 code 多文件不共享 shard 路径（idx 唯一化）。
**信任序**：同名文件（in-file code == 文件名 code，原生导出，raw 价）优先于
错标文件（in-file code ≠ 文件名；实测 5 个错标文件装的是 600811 **前复权**序列）
——raw 是事实层契约，错标数据只在原生覆盖不到的日期补位（shard 前缀 2_ 原生、
3_ 错标）。每次非 `--merge-only` 运行会清空 tmp 分片，避免旧命名分片混入 merge。

R21 另产出退市 sidecar（`delisted_codes.parquet`：code/last_trade_date，原子写）：
退市目录**文件名/in-file code 集合即权威退市信号**（空文件也算），
`ingest_daily.py` 据此写 stock_basic.delist_date = last_trade_date + 1（平台
语义 is_listed = t < delist_date）。

R02-I6b：解析错误必须让进程退出码 ≠ 0（错误计数摘要落 stdout）；in-file code
列**非空但归一化失败**（含已 canonical 形态）→ ValueError 点名原始值，不再
静默回退文件名；code 列缺失/全空才是合法兜底。

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
import os
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


SIDE_CAR_NAME = 'delisted_codes.parquet'
_SRC_CODE_RE = re.compile(r'^(sh|sz|bj)\.(\d{6})$', re.IGNORECASE)


def market_of(code6: str) -> str:
    if code6[0] == '6':
        return '.SH'
    if code6[0] in ('0', '3'):
        return '.SZ'
    if code6.startswith('920'):
        return '.BJ'
    raise ValueError(f'unexpected code prefix: {code6}')


def normalize_src_code(raw) -> str | None:
    """退市文件 in-file code（'sh.600811'/'sz.000003'/'bj.920305'）→ canonical。

    非该形态（如已 canonical 的 '600811.SH'、空串、None）→ None。
    调用方须区分两类 None（R02-I6b）：空值可走文件名兜底；**非空却归一化失败**
    是数据可疑信号，必须显式报错，不得静默回退文件名。
    """
    if raw is None:
        return None
    m = _SRC_CODE_RE.fullmatch(str(raw).strip())
    if not m:
        return None
    return f'{m.group(2)}.{m.group(1).upper()}'


def _fallback_code(code6: str) -> str:
    """文件名 code6 → canonical；前缀无法判板块时退回 code6（worker 错误路径不炸）。"""
    try:
        return code6 + market_of(code6)
    except ValueError:
        return code6


def _parse_xlsx(payload: bytes) -> pd.DataFrame:
    wb = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    first = next(it, None)
    if first is None:
        # 空 sheet（无表头行）：数据可得性事实，不是解析错误 → 视同无数据跳过
        wb.close()
        return None
    header = [str(x) for x in first]
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
        # 退市股精简格式：8 列（None,date,code,open,high,low,close,volume），无 amount/复权/股本。
        # R21 TOOLS-C2：in-file `code` 是真实代码（文件名可能错标）——解析它并归一化；
        # 无 code 列/全空 → 文件名 code6 兜底。
        wb = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        it = wb.active.iter_rows(values_only=True)
        first = next(it, None)
        if first is None:
            # 空 sheet（无表头行）：3 个既有 stub（000047/920305/920680）即此形态；
            # 数据可得性事实而非解析错误 → 跳过，不改变退出码（仍进退市 sidecar）
            wb.close()
            return None
        header = [str(x) for x in first]
        need = ['date', 'open', 'high', 'low', 'close', 'volume']
        if any(c not in header for c in need):
            wb.close()
            raise ValueError(f'delisted xlsx columns unexpected: {header}')
        cols = need + (['code'] if 'code' in header else [])
        take = [i for i, h in enumerate(header) if h in cols]
        keep_names = [header[i] for i in take]        # 保持文件列序（code 在 date 后）
        rows = [[r[i] for i in take] for r in it]
        wb.close()
        df = pd.DataFrame(rows, columns=keep_names)
        real = None
        if 'code' in df.columns:
            raw = df['code'].dropna().astype(str).str.strip()
            raw = raw[raw != '']
            codes = {c for c in (normalize_src_code(v) for v in raw)
                     if c is not None}
            unparsed = sorted({v for v in raw if normalize_src_code(v) is None})
            if len(codes) > 1:
                raise ValueError(
                    f'delisted xlsx in-file code 冲突（文件名 {code6}）：'
                    f'{sorted(codes)}')
            if unparsed:
                # R02-I6b：非空 in-file code 归一化失败 = 数据可疑；此前静默回退
                # 文件名会让错标文件重演 R21 TOOLS-C2 的假历史。点名原始值 fail fast。
                raise ValueError(
                    f'delisted xlsx in-file code 无法归一化（文件名 {code6}）：'
                    f'{unparsed[:5]}（{len(unparsed)} 种）——期望 sh./sz./bj.+6 位数字；'
                    f'拒绝静默回退文件名')
            real = next(iter(codes)) if codes else None
            df = df.drop(columns=['code'])
        if real is None:
            real = _fallback_code(code6)
        for c in need[1:]:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=need)
        if df.empty:
            return None
        df['amount'] = np.nan
        df['float_shares'] = np.nan
        df['total_shares'] = np.nan
        df['code'] = real
        # 无复权因子：退市股历史仅原始价，amount 缺失使 V4 不会选入（尽力而为）
        df['adj_factor'] = np.nan
        for cn in EVENT_SRC.values():
            df[cn] = np.nan
        return df.rename(columns={'date': 'trade_date'})[FINAL_COLS]
    df = _parse_xlsx(payload)
    if df is None or df.empty:
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
    """解析单文件 → 落盘 tmp/{priority}_{idx:05d}_{real_code}.parquet。

    R21 TOOLS-C2：shard 名嵌入**真实代码**（退市文件 = in-file code）与唯一 idx
    （5 个错标文件同映射 600811.SH 也不能共享路径）；主进程按 `_shard_code`
    分组，同 code 多文件按 trade_date 去重。
    分片落盘替代"收集 5533 帧 + 全量 concat"（旧实现峰值内存 ≈ 数据双份 +
    pandas 帧开销；16GB 无页面文件机器上有爆内存风险）。

    返回 (err, code6, real_code)：空/坏文件也返回文件名兜底 real（退市目录
    本身是权威信号，空文件仍须进 sidecar）。
    """
    priority, kind, path, member, code6, idx = args
    fallback = _fallback_code(code6)
    try:
        if kind == 'zip':
            with zipfile.ZipFile(path) as z:
                payload = z.read(member)
        else:
            payload = Path(path).read_bytes()
        if not payload:
            # 空退市文件：文件存在本身是权威退市信号，进 sidecar（last=NULL）
            return None, code6, fallback
        df = _parse_one(code6, payload, delisted=(priority == 2))
        if df is None or df.empty:
            return None, code6, fallback
        real = str(df['code'].iat[0])
        # 与旧 main 同款的日期归一化，提到 worker 内完成 → 落盘即定型；
        # priority 不入 schema（OUT_SCHEMA 为最终列集）——来源优先级由 shard 名
        # {0|1|2}_ 前缀编码，merge 阶段按文件名顺序 concat + keep='first' 还原
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.normalize()
        df = df.sort_values('trade_date').reset_index(drop=True)
        # 信任序：错标退市文件（in-file code ≠ 文件名 code）排在同名原生之后
        prefix = 3 if (priority == 2 and real != fallback) else priority
        t = pa.Table.from_pandas(df, schema=OUT_SCHEMA, preserve_index=False)
        pq.write_table(t, _tmp_dir / f'{prefix}_{idx:05d}_{real}.parquet',
                       compression='zstd', compression_level=3,
                       use_dictionary=['code'])
        del df, t
        return None, code6, real
    except Exception as e:
        return f'{code6}: {type(e).__name__}: {e}', code6, fallback


def _shard_code(name: str) -> str:
    """分片文件名 → 真实代码（`{prefix}_{idx:05d}_{real}.parquet`）。

    prefix：0=full、1=incr、2=退市原生、3=退市错标（排序即信任序）。
    """
    stem = name[:-len('.parquet')] if name.endswith('.parquet') else name
    parts = stem.split('_', 2)
    if len(parts) != 3 or parts[0] not in ('0', '1', '2', '3'):
        raise ValueError(f'unrecognized shard name: {name!r}')
    return parts[2]


def _merge_code(files: list[Path]) -> pd.DataFrame:
    """同真实代码的 full/incr/delisted 分片：priority 升序 concat + 日期去重。"""
    df = pd.concat([pd.read_parquet(p) for p in sorted(files)], ignore_index=True)
    return df.drop_duplicates(subset=['trade_date'], keep='first')


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
    # idx = 分片路径唯一化（同 real code 的多个文件绝不共享 tmp 路径）
    return [(t[0], t[1], t[2], t[3], t[4], i) for i, t in enumerate(tasks)]


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
    delisted_reals: set[str] = set()
    if not a.merge_only:
        # shard 命名格式随 C2 变更：清掉旧分片，避免旧命名/过期数据混入 merge
        stale = list(tmp.glob('*.parquet'))
        for s in stale:
            s.unlink()
        if stale:
            print(f'  清理旧分片 {len(stale)} 个')
        done = 0
        with mp.Pool(a.workers) as pool:
            for done, (err, _code, real) in enumerate(
                    pool.imap(_worker, tasks), 1):
                if tasks[done - 1][0] == 2:
                    delisted_reals.add(real)    # 退市目录 = 权威退市信号（空文件也算）
                if err:
                    errors.append(err)
                if done % 500 == 0:
                    print(f'  parsed {done}/{len(tasks)}', flush=True)
        print(f'parsed {done}/{len(tasks)} files, errors: {len(errors)}')
        for e in errors[:10]:
            print('  ERROR', e)
    else:
        print(f'--merge-only：跳过解析（复用 {tmp} 现有分片）')
        delisted_reals = {_shard_code(f.name) for f in tmp.glob('*.parquet')
                          if f.name[:1] in ('2', '3')}

    # 逐 code 流式合并（同 code 至多 full(0)/incr(1)/delisted(2) 三份）：
    # 文件名 {0|1|2}_ 前缀升序 = 来源优先级；各文件内部已按 trade_date 升序 →
    # 顺序 concat 后 drop_duplicates(keep='first') 即"full 行优先，incr/delisted
    # 补充未覆盖日期"（与旧全局 concat 排序去重语义一致；旧注释"增量/退市优先"
    # 与实际相反——重叠行源自同源厂商值相同，无差异）。
    files = sorted(tmp.glob('*.parquet'))
    if not files:
        raise SystemExit('no data parsed')
    by_code: dict[str, list[Path]] = {}
    skipped = 0
    for f in files:
        try:
            real = _shard_code(f.name)
        except ValueError:
            skipped += 1
            continue
        by_code.setdefault(real, []).append(f)
    if skipped:
        print(f'  WARN 忽略无法识别的分片 {skipped} 个（旧命名/残留）')
    n_rows = 0
    n_bad = 0
    d_min = d_max = None
    codes_done = 0
    delisted_last: dict[str, object] = {}
    out.parent.mkdir(parents=True, exist_ok=True)
    with pq.ParquetWriter(out, OUT_SCHEMA, compression='zstd',
                          compression_level=3, use_dictionary=['code']) as w:
        for real in sorted(by_code):
            df = _merge_code(sorted(by_code[real]))
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
            if real in delisted_reals:
                last = d.max()
                delisted_last[real] = last.date() if hasattr(last, 'date') else last
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

    if delisted_reals:
        sidecar = out.parent / SIDE_CAR_NAME
        _write_delisted_sidecar(
            sidecar, [(c, delisted_last.get(c)) for c in sorted(delisted_reals)])
        print(f'sidecar: {sidecar} ({len(delisted_reals)} 退市代码，'
              f'{sum(1 for c in delisted_reals if c in delisted_last)} 个有交易日)')

    if errors:
        # R02-I6b：解析错误必须改变进程退出码（此前只打印，cron/CI 误报成功）；
        # 已解析分片仍合并落盘，便于用 --merge-only 复核后再修源重跑。
        print(f'解析错误 {len(errors)} 个：退出码 1（前 10 条见上；修复源文件后重跑，'
              f'或用 --merge-only 复核现有分片）', flush=True)
        raise SystemExit(1)


def _write_delisted_sidecar(path: Path, rows: list[tuple[str, object]]) -> None:
    """原子写退市 sidecar（code / last_trade_date；无数据的代码 last=NULL）。"""
    table = pa.table({
        'code': pa.array([r[0] for r in rows], type=pa.string()),
        'last_trade_date': pa.array([r[1] for r in rows], type=pa.date32()),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.tmp.{os.getpid()}')
    try:
        pq.write_table(table, tmp, compression='zstd')
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    main()
