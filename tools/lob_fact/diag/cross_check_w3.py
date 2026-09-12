#!/usr/bin/env python
"""W4d 独立交叉核对 — 批算 day rows vs W3 measure_w3 单日 JSON (重叠校准 code-day)


import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # lob_fact/
两链同构语义 (state 全深度, band 只抑行不抑状态) → 以下字段必须逐字节相等:
  m1.n_anchor / n_present / missing_vol / unattributed_vol / presence(圆整5位)
  m4.conservation / orders / counters_equal
  n_snaps / n_sweeps (sweeps 永不因 band 抑制)
不等字段 (rows/n_events 由 band 差异, 合法不比对): n_rows 等事件计数。

用法: python cross_check_w3.py <month> [code-day ...]  (缺省 = 配置 CALIB_DAYS 交集)
  day rows 源: batch 运行产出 _batch/runs/*/day_rows.jsonl (按 code-day 最新去重)
  W3 源: C.CALIB_OUT/w3_measure_<code>_<day>.json
返回: 全等 → exit 0; 任一不等 → 逐字段打印 + exit 1
"""
import glob, json, os, sys

from core import config as C

_FIELDS = (('m1', 'n_anchor'), ('m1', 'n_present'), ('m1', 'missing_vol'),
           ('m1', 'unattributed_vol'), ('m4', 'conservation'),
           ('m4', 'orders'), ('m4', 'counters_equal'), ('n_snaps',))
_SPECIAL = {('m1', 'presence'): lambda rec, w3: (rec['gate']['presence'],
                                                w3['m1']['presence']),
            # batch recs 字段名 'sweeps'; w3 顶层 'n_sweeps' — 同量异名
            ('sweeps', 'n_sweeps'): lambda rec, w3: (rec['sweeps'],
                                                     w3['n_sweeps'])}


def batch_rows(month):
    """跨 run 聚合 batch code-day 行 (最新 run 覆盖; 只取 ok 行)"""
    out = {}
    runs = sorted(glob.glob(os.path.join(C.LOB_FACT_ROOT, '_batch', 'runs', '*',
                                         'day_rows.jsonl')))
    for rp in runs:
        with open(rp) as f:
            for ln in f:
                p = json.loads(ln)
                if p.get('day', '')[:6] != month:
                    continue
                for rec in p.get('recs', []):
                    if rec.get('ok'):
                        out[(rec['code'], rec['day'])] = rec
    return out


def _full(code):
    """CALIB_DAYS 裸码 → batch 键后缀形 (0/3 前缀 = SZ, 其余 SH)"""
    return code if '.' in code else code + ('.SZ' if code[0] in '03' else '.SH')


def w3_json(code, day):
    p = os.path.join(C.CALIB_OUT,
                     f'w3_measure_{code.split(".")[0]}_{day}.json')
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def main(argv=None):
    ap = __import__('argparse').ArgumentParser()
    ap.add_argument('month', help='YYYYMM')
    ap.add_argument('code_days', nargs='*',
                    help='code@YYYYMMDD; 缺省 = CALIB_DAYS 中落在该月的')
    args = ap.parse_args(argv)
    rows = batch_rows(args.month)
    pairs = [(_full(c), d) for cd in args.code_days
             for c, d in [tuple(cd.split('@'))]] or \
        [(_full(c), d) for c, d in C.CALIB_DAYS if d[:6] == args.month]
    if not pairs:
        print('无可比对 code-day (该月无校准交集)')
        return 0
    bad = 0
    for code, day in sorted(pairs):
        rec = rows.get((code, day))
        w3 = w3_json(code, day)
        if rec is None or w3 is None:
            print(f'{code}@{day}: MISSING batch={rec is not None} w3={w3 is not None}')
            bad += 1
            continue
        diffs = []
        for path in _FIELDS:
            b, w = rec, w3
            for k in path:
                b, w = b[k], w[k]
            if b != w:
                diffs.append((path, b, w))
        for path, get in _SPECIAL.items():
            b, w = get(rec, w3)
            if b != w:
                diffs.append((path, b, w))
        if diffs:
            bad += 1
            print(f'{code}@{day}: MISMATCH '
                  + '; '.join(f'{"/".join(p)} {b} != {w}' for p, b, w in diffs))
        else:
            print(f'{code}@{day}: OK  (m4 {rec["m4"]["conservation"]}, '
                  f'presence {rec["gate"]["presence"]})')
    print('FAIL' if bad else 'PASS')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
