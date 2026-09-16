#!/usr/bin/env python3
"""A 股 1m fact store 转换器 v1.0.0 — 2026-08-22

模式:
  validation (Step 1): 单日 → bars_1m_validation/<day>.parquet (disposable, OHLC float64),
    配置读 validation/step0a_result.json + validation/step0b_result.json
  production  (Step 4+): 单月 → bars_1m/year=YYYY/month=MM/ (OHLC float32 冻结 SCHEMA),
    配置仅读 _dataset_metadata.json; _SUCCESS 为事务边界; 月级并行 Step 5 补齐

核心规则 (plan logical-wandering-peach.md):
  - 240 行/日固定网格: row0=09:25 竞价, 1..118=09:31..11:28, 119=11:29,
    120..238=13:00..14:58, 239=15:00 收盘竞价 (minute-end 标签)
  - 后缀式 3 天 (2025-12-01..03) 网格不兼容 → 整日隔离 grid_incompatible_suffix_format
  - 日期偏移日 (validation/date_shift_exceptions.csv, 文件日期=真实 T±1 数据) →
    整日隔离 date_shifted_daily (2025-12-22..2026-05-13 次新股, 证据链见 detect_date_shift.py)
  - 指数 (sz 399xxx) / B 股 (sz 200/201, sh 900) → 隔离, 事实库只收 A 股
  - 单位换算按 trade_date 查 source_unit_regimes (禁重叠/缺段); production gate:
    匹配 regime 必须 production_approved==true, 否则 MONTH_ERROR
  - 市场判定用 zip member 路径目录 (920xxx 是 BJ 不是 SH)
  - Parser 已冻结: pandas.read_csv (Step 2: 比 pyarrow 快 ~5 倍且无 ULP 舍入差异)
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import zipfile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# R4b：写侧单点（标记/锁/state/原子写）。lib 只需 tools/ 上 sys.path（不依赖平台）。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from _env import ensure_platform as _ensure_platform  # noqa: E402

_ensure_platform()
from lib import writekit as W  # noqa: E402
from pathlib import Path  # noqa: E402
from factorlab.core.factio import partitions, paths  # noqa: E402

# ---- 源布局（workspace 归并 2026-09-12：数据统一入 <stock>/data/{raw,fact,calib}） ----
STOCK_ROOT = str(paths.STOCK_ROOT)
DATA_ROOT = str(paths.DATA_ROOT)
SRC_DIR = str(paths.minutes_root())               # 原始分钟 zip
PROD_DIR = str(paths.bars_1m_root())              # 事实库 Hive parquet
VALIDATION_DIR = str(paths.CALIB_ROOT / 'bars_1m_validation')   # 校验模式输出（可再生成）
METADATA = f'{PROD_DIR}/_dataset_metadata.json'
SOURCE_COLUMNS = ['open', 'high', 'low', 'close', 'amount', 'volume']
COMPRESSION = 'zstd'
COMPRESSION_LEVEL = 3
CONVERTER_VERSION = '1.0.0'

FIELDS = [('datetime', pa.timestamp('ms')), ('trade_date', pa.date32()),
          ('code', pa.string()), ('minute_index', pa.int16()),
          ('session_type', pa.uint8())]


def build_minutes():
    m = ['09:25']
    hh, mm = 9, 31
    while not (hh == 11 and mm == 30):
        m.append(f'{hh:02d}:{mm:02d}')
        mm += 1
        if mm == 60:
            hh += 1
            mm = 0
    hh, mm = 13, 0
    while not (hh == 14 and mm == 59):
        m.append(f'{hh:02d}:{mm:02d}')
        mm += 1
        if mm == 60:
            hh += 1
            mm = 0
    m.append('15:00')
    assert len(m) == 240, len(m)
    return m


MINUTES = build_minutes()
SESSION_TYPE = np.zeros(240, dtype=np.uint8)
SESSION_TYPE[1:238] = 1
SESSION_TYPE[238:240] = 2


def schema(ohlc_type):
    return pa.schema(FIELDS + [(n, ohlc_type) for n in ('open', 'high', 'low', 'close')]
                     + [('amount', pa.float64()), ('volume', pa.float64())])


SCHEMA_VAL = schema(pa.float64())
SCHEMA_PROD = schema(pa.float32())


class RegimeTable:
    """按 trade_date 恰好匹配一个 regime; 禁重叠/缺段"""

    def __init__(self, regimes, name):
        self.name = name
        segs = []
        for r in regimes:
            start = pd.Timestamp(r['trade_date_start'])
            end = (pd.Timestamp('9999-12-31') if r.get('trade_date_end') is None
                   else pd.Timestamp(r['trade_date_end']))
            segs.append((start, end, r))
        segs.sort(key=lambda s: s[0])
        for (s1, e1, _), (s2, e2, _) in zip(segs, segs[1:]):
            if s2 <= e1:
                raise ValueError(f'{name}: overlapping regimes {s1}~{e1} and {s2}~{e2}')
        self.segs = segs

    def get(self, trade_date):
        t = pd.Timestamp(trade_date)
        hit = [r for s, e, r in self.segs if s <= t <= e]
        if len(hit) != 1:
            raise ValueError(f'{self.name}: {trade_date} matches {len(hit)} regimes '
                             f'(expected exactly 1)')
        return hit[0]


class ZipReader:
    def __init__(self, path):
        self.zf = zipfile.ZipFile(path)

    def read(self, member):
        return self.zf.read(member)

    def namelist(self):
        return self.zf.namelist()

    def close(self):
        self.zf.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class SevenZipReader:
    """7z 一次性解压到临时目录 (比逐 member 7z e -so 起进程快), close 时清理"""

    def __init__(self, path):
        import shutil
        import subprocess
        import tempfile
        self._shutil = shutil
        self.tmpdir = tempfile.mkdtemp(prefix='minutes7z_')
        subprocess.run(['7z', 'x', '-y', f'-o{self.tmpdir}', path],
                       check=True, capture_output=True)

    def read(self, member):
        with open(os.path.join(self.tmpdir, member), 'rb') as f:
            return f.read()

    def namelist(self):
        names = []
        for root, _, files in os.walk(self.tmpdir):
            for fn in files:
                names.append(os.path.relpath(os.path.join(root, fn), self.tmpdir))
        return names

    def close(self):
        self._shutil.rmtree(self.tmpdir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def open_reader(day8):
    """按 archive 类型打开: zip → 内存流; 7z → 临时解压; 返回 (reader, archive_path)"""
    base = f'{SRC_DIR}/{day8[:4]}/{day8[4:6]}/{day8}'
    for ext, cls in (('.zip', ZipReader), ('.7z', SevenZipReader)):
        p = f'{base}{ext}'
        if os.path.exists(p):
            return cls(p), p
    raise FileNotFoundError(base)


SUFFIX_MEMBER_RE = re.compile(r'^\d{8}/[a-z]{2}/\d{6}\.[a-z]{2}\.csv$')
DATE_SHIFT_EXC_PATH = f'{DATA_ROOT}/calib/validation/date_shift_exceptions.csv'


def date_shift_exceptions():
    """{(day8, code): (real_date, shift_dir, evidence)} — 供应商文件日期偏移日清单。

    证据链: 整日六字段 (close/high/low/amount/volume) 匹配 T±1 + 假竞价行
    (row0=真实 T 日收盘), 由 scripts/detect_date_shift.py 全库扫描生成。
    """
    global _DATE_SHIFT_EXC
    if _DATE_SHIFT_EXC is None:
        _DATE_SHIFT_EXC = {}
        if os.path.exists(DATE_SHIFT_EXC_PATH):
            df = pd.read_csv(DATE_SHIFT_EXC_PATH)
            for _, r in df.iterrows():
                _DATE_SHIFT_EXC[(str(r['trade_date']), r['code'])] = (
                    str(r['real_date']), r['shift_dir'], float(r['evidence_e_best']))
    return _DATE_SHIFT_EXC


_DATE_SHIFT_EXC = None


def market_of_member(member):
    return member.split('/')[-2]


def _convert_one(member, reader, day8, unit_regime, out, errors, minute_dt, stats, seen):
    """转换单个 CSV member; 违规 → errors; 正常 → out.append(pd.DataFrame 240 行)"""
    if not (member.endswith('.csv') and member.count('/') in (1, 2)):
        return
    mkt = market_of_member(member)
    if mkt not in ('sz', 'sh', 'bj'):
        return
    # 后缀式 (2025-12-01..03) 必须先于 bad_code 判定: member 名 `920000.bj.csv`
    # 无法通过 \d{6} stem 校验, 会被 bad_code 截胡 → 整日隔离记 grid_incompatible_suffix_format
    if SUFFIX_MEMBER_RE.match(member):
        df = pd.read_csv(io.BytesIO(reader.read(member)))
        stats['n_suffix'] += 1
        code6 = re.match(r'\d{6}', member.split('/')[-1]).group(0)
        time_col = df['time'] if 'time' in df.columns else None
        errors.append({'trade_date': day8, 'code': f'{code6}.{mkt.upper()}',
                       'source_member': member, 'rows': len(df),
                       'time_first': (str(time_col.iloc[0])
                                      if time_col is not None else ''),
                       'time_last': (str(time_col.iloc[-1])
                                     if time_col is not None else ''),
                       'error_type': 'grid_incompatible_suffix_format',
                       'detail': '240 行含 time 列: row0=[09:25,09:32) 三合一, '
                                 '无独立 09:31, 多 14:59 零行 → 无法无损映射事实网格'})
        return
    stem = member.split('/')[-1].removesuffix('.csv')
    if stem.startswith(mkt):
        stem = stem[len(mkt):]
    if not re.fullmatch(r'\d{6}', stem):
        errors.append({'trade_date': day8, 'code': stem, 'source_member': member,
                       'error_type': 'bad_code'})
        return
    code = f'{stem}.{mkt.upper()}'
    if mkt == 'sz' and stem.startswith('399'):
        stats['n_index'] += 1
        errors.append({'trade_date': day8, 'code': code, 'source_member': member,
                       'error_type': 'index_series_excluded',
                       'detail': '399xxx 深证指数 (点位/基数), 非 A 股股票'})
        return
    if (mkt == 'sz' and stem.startswith(('200', '201'))) or (mkt == 'sh' and stem.startswith('900')):
        stats['n_b_share'] += 1
        errors.append({'trade_date': day8, 'code': code, 'source_member': member,
                       'error_type': 'b_share_excluded',
                       'detail': 'B 股以港元/美元计价, 非 A 股 (元/股 regime 不适用)'})
        return
    if mkt == 'sh' and stem.startswith('899'):
        stats['n_index'] += 1
        errors.append({'trade_date': day8, 'code': code, 'source_member': member,
                       'error_type': 'index_series_excluded',
                       'detail': '899xxx 中证指数 (点位/基数), 非 A 股股票'})
        return
    if code in seen:
        stats['n_dup'] += 1
        errors.append({'trade_date': day8, 'code': code, 'source_member': member,
                       'error_type': 'duplicate_codes',
                       'detail': '同一 zip 内同代码多 member (20260617 裸式+前缀式双份), 保留首个'})
        return
    seen.add(code)
    exc = date_shift_exceptions().get((day8, code))
    if exc is not None:
        stats['n_date_shift'] += 1
        real_date, shift_dir, evidence = exc
        errors.append({'trade_date': day8, 'code': code, 'source_member': member,
                       'error_type': 'date_shifted_daily',
                       'real_date': real_date, 'shift_dir': shift_dir,
                       'evidence_e_best': evidence,
                       'detail': f'供应商文件日期 {day8} 实为 {real_date} 交易日数据'
                                 f'({shift_dir} 偏移, 整日 OHLCV 匹配) + 假竞价行 → 隔离, '
                                 f'原 ZIP 保留'})
        return
    raw = reader.read(member)
    df = pd.read_csv(io.BytesIO(raw))
    if 'time' in df.columns:
        stats['n_suffix'] += 1
        errors.append({'trade_date': day8, 'code': code, 'source_member': member,
                       'error_type': 'grid_incompatible_suffix_format',
                       'detail': 'time 列后缀式网格, 无法无损映射事实网格'})
        return
    if len(df) != 240:
        stats['n_bad_rows'] += 1
        errors.append({'trade_date': day8, 'code': code, 'source_member': member,
                       'error_type': 'rows_not_240', 'rows': len(df)})
        return
    if not set(SOURCE_COLUMNS) <= set(df.columns):
        stats['n_schema'] += 1
        errors.append({'trade_date': day8, 'code': code, 'source_member': member,
                       'error_type': 'schema_mismatch',
                       'detail': f'cols={list(df.columns)}'})
        return
    o, h, l, c = df['open'], df['high'], df['low'], df['close']
    a, v = df['amount'], df['volume']
    ohlc_ok = (np.all(h >= l) and np.all(h >= o) and np.all(h >= c)
               and np.all(l <= o) and np.all(l <= c)
               and np.all(np.isfinite(df[SOURCE_COLUMNS].values))
               and np.all(a >= 0) and np.all(v >= 0)
               and not np.any((v > 0) & (o <= 0)))
    if not ohlc_ok:
        stats['n_bad_ohlc'] += 1
        for i in range(len(df)):
            row = df.iloc[i]
            bad = (not (row['high'] >= row['low'] and row['high'] >= row['open']
                        and row['high'] >= row['close'] and row['low'] <= row['open']
                        and row['low'] <= row['close'])
                   or not (np.isfinite(row[SOURCE_COLUMNS]).all() and row['amount'] >= 0
                           and row['volume'] >= 0
                           and not (row['volume'] > 0 and row['open'] <= 0)))
            if bad:
                errors.append({'trade_date': day8, 'code': code, 'minute_index': i,
                               'error_type': 'ohlc_invalid',
                               'raw_open': row['open'], 'raw_high': row['high'],
                               'raw_low': row['low'], 'raw_close': row['close'],
                               'raw_amount': row['amount'], 'raw_volume': row['volume'],
                               'amount_multiplier': unit_regime['amount_multiplier'],
                               'volume_multiplier': unit_regime['volume_multiplier'],
                               'source_member': member})
        return

    out.append(pd.DataFrame({
        'datetime': minute_dt,
        'trade_date': np.datetime64(f'{day8[:4]}-{day8[4:6]}-{day8[6:]}'),
        'code': code,
        'minute_index': np.arange(240, dtype=np.int16),
        'session_type': SESSION_TYPE,
        'open': o.to_numpy(dtype=np.float64),
        'high': h.to_numpy(dtype=np.float64),
        'low': l.to_numpy(dtype=np.float64),
        'close': c.to_numpy(dtype=np.float64),
        'amount': a.to_numpy(dtype=np.float64) * unit_regime['amount_multiplier'],
        'volume': v.to_numpy(dtype=np.float64) * unit_regime['volume_multiplier'],
    }))
    stats['n_ok'] += 1
    stats['n_rows'] += 240


def convert_day(day8, unit_tbl, ntr_tbl, errors, prod_gate=False,
                ohlc_schema=None):
    """转换一个交易日 → 返回 (stats, Arrow Table | None)"""
    reader, zp = open_reader(day8)
    unit_regime = unit_tbl.get(day8)
    ntr_regime = ntr_tbl.get(day8)
    if ntr_regime['policy'] != 'keep_rows_flat_prev_close':
        errors.append({'trade_date': day8, 'error_type': 'unsupported_no_trade_policy',
                       'detail': ntr_regime['policy']})
        return None, None
    if prod_gate and not unit_regime.get('production_approved'):
        raise RuntimeError(f'MONTH_ERROR: {day8} regime '
                           f'{unit_regime["trade_date_start"]}~'
                           f'{unit_regime.get("trade_date_end")} not production_approved')

    stats = {'n_ok': 0, 'n_suffix': 0, 'n_bad_ohlc': 0, 'n_rows': 0,
             'n_index': 0, 'n_b_share': 0, 'n_bad_rows': 0, 'n_schema': 0,
             'n_dup': 0, 'n_date_shift': 0}
    frames = []
    seen = set()
    minute_dt = pd.to_datetime([f'{day8} {h}' for h in MINUTES],
                               format='%Y%m%d %H:%M')
    with reader:
        for member in sorted(reader.namelist()):
            _convert_one(member, reader, day8, unit_regime, frames, errors,
                         minute_dt, stats, seen)
    stats['day'] = day8
    stats['unit_regime'] = (f"{unit_regime['trade_date_start']}~"
                            f"{unit_regime.get('trade_date_end')}")
    if not frames:
        return stats, None
    out = pd.concat(frames, ignore_index=True)
    sc = ohlc_schema or SCHEMA_VAL
    table = pa.Table.from_pandas(out, preserve_index=False, schema=sc)
    return stats, table


# ============ validation mode (Step 1) ============

def run_validation(day, out_root):
    with open(f'{DATA_ROOT}/calib/validation/step0a_result.json') as f:
        step0a = json.load(f)
    with open(f'{DATA_ROOT}/calib/validation/step0b_result.json') as f:
        step0b = json.load(f)
    if step0a['time_mapping'] != 'PASS':
        sys.exit(f'step0a time_mapping={step0a["time_mapping"]}, 拒绝转换')
    unit_tbl = RegimeTable(step0b['source_unit_regimes'], 'source_unit_regimes')
    ntr_tbl = RegimeTable(step0b['no_trade_minute_regimes'], 'no_trade_minute_regimes')

    os.makedirs(out_root, exist_ok=True)
    errors = []
    stats, table = convert_day(day, unit_tbl, ntr_tbl, errors,
                               ohlc_schema=SCHEMA_VAL)
    if table is not None:
        pq.write_table(table, os.path.join(out_root, f'{day}.parquet'),
                       compression=COMPRESSION, compression_level=COMPRESSION_LEVEL,
                       use_dictionary=['code'])
    err_csv = os.path.join(out_root, f'_conversion_errors_{day}.csv')
    if errors:
        pd.DataFrame(errors).to_csv(err_csv, index=False)
    else:
        open(err_csv, 'w').close()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f'errors: {len(errors)} → {err_csv}')


# ============ production mode (Step 4+) ============

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


_PROD_META = None


def prod_tables():
    global _PROD_META
    if _PROD_META is None:
        with open(METADATA) as f:
            _PROD_META = json.load(f)
    return _PROD_META


def _cleanup_month(data_dir, state_dir):
    """删除一个月的全部产物 (数据 + _state), 事务失败后重转前调用"""
    for d in (data_dir, state_dir):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass


def _committed_ok(data_dir, state_dir):
    """_SUCCESS 存在时的轻量校验: schema + num_rows + row groups"""
    try:
        conv = W.load_state(state_dir, name='_conversion.json')   # R4b：state 单点（原子写/空文件容错）
        with pq.ParquetFile(os.path.join(data_dir, 'part-000.parquet')) as pf:
            return (pf.schema_arrow == SCHEMA_PROD
                    and pf.metadata.num_rows == conv['rows']
                    and pf.metadata.num_row_groups >= 1)
    except Exception:
        return False


def convert_month_worker(ym):
    """一个 worker 负责一个完整月; _SUCCESS 为事务边界, 已提交且校验通过则跳过"""
    year, month = int(ym[:4]), int(ym[4:6])
    data_dir = str(partitions.partition_dir(Path(PROD_DIR), table=None,
                                           year=year, month=month))
    state_dir = str(partitions.partition_dir(Path(PROD_DIR), table='_state',
                                             year=year, month=month))
    success = W.success_marker(data_dir)   # R8c：标记单点

    if W.has_success(data_dir):   # R8c：标记单点
        if _committed_ok(data_dir, state_dir):
            conv = W.load_state(state_dir, name='_conversion.json')   # R4b：state 单点
            err_csv = os.path.join(state_dir, '_conversion_errors.csv')
            n_err = 0
            if os.path.exists(err_csv) and os.path.getsize(err_csv) > 0:
                with open(err_csv) as f:
                    n_err = sum(1 for _ in f) - 1
            return {'ym': ym, 'skipped': True, 'days': conv['days'],
                    'codes': conv['codes'], 'rows': conv['rows'],
                    'n_errors': n_err, 'n_suffix': 0}
        print(f'  {ym}: _SUCCESS 存在但校验失败 -> 清理重转', flush=True)
    else:
        print(f'  {ym}: 转换中', flush=True)
    _cleanup_month(data_dir, state_dir)   # 未提交/损坏 → 整个月重来
    return _convert_month(ym, year, month, data_dir, state_dir)


def _convert_month(ym, year, month, data_dir, state_dir):
    meta = prod_tables()
    unit_tbl = RegimeTable(meta['source_unit_regimes'], 'source_unit_regimes')
    ntr_tbl = RegimeTable(meta['no_trade_minute_regimes'], 'no_trade_minute_regimes')

    src_dir = f'{SRC_DIR}/{year}/{month:02d}'
    zips = sorted(f for f in os.listdir(src_dir)
                  if f.endswith('.zip') or f.endswith('.7z'))
    if not zips:
        sys.exit(f'no archives in {src_dir}')

    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(state_dir, exist_ok=True)

    tmp_parquet = os.path.join(data_dir, f'part-000.parquet.tmp.{os.getpid()}')
    errors = []
    manifest_rows = []
    total_rows = 0
    n_codes = 0
    writer = pq.ParquetWriter(tmp_parquet, SCHEMA_PROD, compression=COMPRESSION,
                              compression_level=COMPRESSION_LEVEL,
                              use_dictionary=['code'])
    try:
        for zname in zips:
            day8 = zname[:8]
            zp = os.path.join(src_dir, zname)
            stats, table = convert_day(day8, unit_tbl, ntr_tbl, errors,
                                       prod_gate=True, ohlc_schema=SCHEMA_PROD)
            if table is not None:
                writer.write_table(table, row_group_size=262144)
                total_rows += table.num_rows
                n_codes += stats['n_ok']
            manifest_rows.append({
                'trade_date': np.datetime64(f'{day8[:4]}-{day8[4:6]}-{day8[6:]}'),
                'codes_ok': stats['n_ok'], 'rows': stats['n_rows'],
                'n_index_excluded': stats['n_index'],
                'n_b_share_excluded': stats['n_b_share'],
                'n_suffix_isolated': stats['n_suffix'],
                'n_date_shift_isolated': stats['n_date_shift'],
                'n_dup_codes': stats['n_dup'],
                'n_errors': sum(1 for e in errors if e['trade_date'] == day8),
                'source_zip': zname,
                'source_zip_size': os.path.getsize(zp),
                'source_zip_sha256': sha256_file(zp),
            })
            print(f'  {zname}: codes={stats["n_ok"]} rows={stats["n_rows"]} '
                  f'idx={stats["n_index"]} b={stats["n_b_share"]}', flush=True)
        writer.close()

        # 轻量提交校验
        pf = pq.ParquetFile(tmp_parquet)
        assert pf.schema_arrow == SCHEMA_PROD, 'schema mismatch'
        assert pf.metadata.num_rows == total_rows, (
            f'num_rows {pf.metadata.num_rows} != {total_rows}')
        assert pf.metadata.num_row_groups >= 1
        pf.close()

        # sidecars (各自目录内 os.replace 原子)
        out_parquet = os.path.join(data_dir, 'part-000.parquet')
        out_size = os.path.getsize(tmp_parquet)
        conv = {'year': year, 'month': month, 'days': len(zips),
                'codes': n_codes, 'rows': total_rows,
                'output_parquet_size': out_size,
                'output_parquet_sha256': sha256_file(tmp_parquet),
                'converter_version': CONVERTER_VERSION,
                'created_at': pd.Timestamp.now().isoformat(timespec='seconds')}
        err_csv = os.path.join(state_dir, '_conversion_errors.csv')
        pd.DataFrame(errors).to_csv(err_csv, index=False) if errors else open(err_csv, 'w').close()
        man = pd.DataFrame(manifest_rows)
        man.to_parquet(os.path.join(state_dir, '_daily_manifest.parquet'),
                       compression='zstd')
        W.save_state(state_dir, conv, name='_conversion.json')   # R4b：原子写
        os.replace(tmp_parquet, out_parquet)   # 数据分区最后提交
        W.mark_success(data_dir)  # 事务边界, 最后写（R8c：标记单点）
    except Exception:
        try:
            writer.close()
        except Exception:
            pass
        if os.path.exists(tmp_parquet):
            os.remove(tmp_parquet)
        raise
    return {'ym': ym, 'days': len(zips), 'codes': n_codes, 'rows': total_rows,
            'n_errors': len(errors), 'n_suffix': sum(
                1 for e in errors if e['error_type'] == 'grid_incompatible_suffix_format')}


def list_months(start_year, end_year, start_month, end_month):
    out = []
    for y in range(start_year, end_year + 1):
        sm = start_month if y == start_year else 1
        em = end_month if y == end_year else 12
        for m in range(sm, em + 1):
            d = f'{SRC_DIR}/{y}/{m:02d}'
            if os.path.isdir(d) and any(
                    f.endswith('.zip') or f.endswith('.7z') for f in os.listdir(d)):
                out.append(f'{y}{m:02d}')
    return out


def run_production(workers, months):
    from multiprocessing import Pool
    print(f'production: {len(months)} months, workers={workers}')
    t0 = pd.Timestamp.now()
    with Pool(workers) as p:
        results = p.map(convert_month_worker, months)
    elapsed = (pd.Timestamp.now() - t0).total_seconds()
    total = {'months': len(results), 'skipped': 0, 'days': 0, 'codes': 0,
             'rows': 0, 'errors': 0, 'suffix_isolated': 0}
    for r in results:
        if r.get('skipped'):
            total['skipped'] += 1
        total['days'] += r['days']
        total['codes'] += r['codes']
        total['rows'] += r['rows']
        total['errors'] += r['n_errors']
        total['suffix_isolated'] += r['n_suffix']
    total['elapsed_min'] = round(elapsed / 60, 1)
    print(json.dumps(total, indent=2, ensure_ascii=False))
    if total['errors']:
        print(f'WARNING: {total["errors"]} conversion errors across '
              f'{total["suffix_isolated"]} suffix-isolated stock-days '
              f'(see per-month _conversion_errors.csv)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['validation', 'production'], default='validation')
    ap.add_argument('--day', default='20260817')
    ap.add_argument('--out-root', default=VALIDATION_DIR)
    ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--start-year', type=int, default=2020)
    ap.add_argument('--end-year', type=int, default=2026)
    ap.add_argument('--start-month', type=int, default=1)
    ap.add_argument('--end-month', type=int, default=12)
    args = ap.parse_args()

    if args.mode == 'validation':
        run_validation(args.day, args.out_root)
    else:
        months = list_months(args.start_year, args.end_year,
                             args.start_month, args.end_month)
        run_production(args.workers, months)


if __name__ == '__main__':
    main()
