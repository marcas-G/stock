#!/usr/bin/env python
"""W3 anchoring QA 校准集对拍 — M1/M2/M3/M4/M6 全门逐 code-day（anchoring.run_day 单趟 full_rows）


import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # lob_fact/
用法:
  python measure_w3.py                 # 父进程: 全 10 校准日逐日 spawn（串行, 内存隔离; 调者负责 nice）
  python measure_w3.py 000155 20260803 # 子进程: 单日 → CALIB_OUT/w3_measure_{code}_{day}.json

与 W2 差异: 事件链 raw zip → qa.streams → **anchoring.run_day(evs, snaps, full_rows=True)**。
full_rows 单趟 = 引擎状态与 band 模式逐位相同（band 只抑行不抑状态）→ M1-M4 QA 数值同源,
M6 fold 需要行流全量才非 SKIP → 一趟同时出 M1-M4 + M6。快照 = tick_fact snapshots parquet
(code+trade_date 谓词过滤, time_ms 升序); QA 锚 = 全部 ≥OPEN ≤ M1_END(14:57:00) 快照 ∪ 分钟边界。

输出 JSON: day 聚合 (M1a 存现 / M1b 价梯率/量差/ghost/δ 归因) + 问题窗示例 + m3/m4 +
m6 + 开盘物化 + EOD。
门 (W3 校准, 规格 §3.3 M1 双口径; 校准集 10 日实测见 notes/w3_anchoring_memo.md §4):
- M1a 档位存现率 (n_present/n_anchor: 锚档价在引擎全深度档集存现): 逐日 ≥ 0.97
  且 SZ/SH 池化 ≥ 0.97 → 门。rank 对齐 n_match 受 fast 日 best-edge δ 换位瞬态拖累
  (0.80-0.94) 而存现不受 (≥0.986) → 门语义 = 存现率, 非 rank 对齐 (W3 校准修正
  W1 单平静日 rank 基线 97.6% 的适用范围: 平静日 rank ≥0.987 ≥ W1 语义, M1b 保留报告)
- M1b rank 对齐价梯率 (W1 口径 ladder_match): 逐日/池化报告不设门
- M4 conservation PASS & 逐单 mismatch = 0 & counters_equal; M6 逐日 PASS;
  每窗差量全分类 (unattributed_vol 记录归因, 不静默); 引擎不崩。
"""
import glob, io, json, os, resource, subprocess, sys, time, zipfile
from collections import Counter

import polars as pl
import pandas as pd

from core import config as C
from core.qa import streams as S
from core import anchoring as A

GATE_PRES = 0.97     # M1a 档位存现门: W3 校准 10 日实测 0.9862-0.9999 (最差 fast SZ
                     # 000021@20260706), 门 0.97 留重建故障余量 (真丢档/丢段 presence 崩 <0.9)


def load_events(code, day):
    """raw zip → 归一化事件（与 W2 测量同链同源）"""
    p = C.code_zip(code, day)
    with zipfile.ZipFile(p) as zf:
        o = pd.read_csv(io.BytesIO(zf.read('逐笔委托.csv')), encoding='gbk')
        t = pd.read_csv(io.BytesIO(zf.read('逐笔成交.csv')), encoding='gbk')
    if code[0] in '03':
        evs_a, ga = S.sz_orders_events(o)
        evs_t, gt = S.sz_trades_events(t)
    else:
        evs_a, ga = S.sh_orders_events(o)
        evs_t, gt = S.sh_trades_events(t)
    return evs_a + evs_t, ga, gt


def load_snaps(code, day):
    """tick_fact snapshots 月文件 → 该 (code,date) 全行 dicts（time_ms 升序; 锚定列裁剪）"""
    ex = 'SZ' if code.startswith(('0', '3')) else 'SH'
    from factorlab.adapters.tick_read import read_tick_table
    cols = ['time_ms']
    for side in ('bid', 'ask'):
        cols += [f'{side}_p{i}' for i in range(1, 11)]
        cols += [f'{side}_v{i}' for i in range(1, 11)]
    df = read_tick_table('snapshots', day, codes=[f'{code}.{ex}'],
                         columns=cols).sort('time_ms')
    return df.to_dicts()


def run_day(code, day):
    """单 code-day: 事件 + 快照 → run_day 全量 → QA 门指标紧凑 JSON"""
    t0 = time.time()
    evs, ga, gt = load_events(code, day)
    snaps = load_snaps(code, day)
    t_read = (time.time() - t0) * 1000
    kinds = Counter(ev['kind'] for ev in evs)
    t0 = time.time()
    out = A.run_day(evs, snaps, full_rows=True)
    t_eng = (time.time() - t0) * 1000
    qa = out['qa']
    eng = out['engine']
    rows, sweeps = out['rows'], out['sweeps']
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # 每窗双侧逐级分类聚合 → 日级 M1/M2 门指标
    d = qa['day']
    vd_sum = sum(w[s_]['vol_delta_sum']
                 for w in qa['windows'] for s_ in ('bid', 'ask'))
    n_win_ghost = sum(1 for w in qa['windows']
                      if w['bid']['ghosts'] or w['ask']['ghosts'])
    n_win_unatt = sum(1 for w in qa['windows']
                      if w['bid']['unattributed_vol'] or w['ask']['unattributed_vol'])
    n_matched = sum(w[s_]['n_match'] for w in qa['windows'] for s_ in ('bid', 'ask'))
    n_zero_vd = sum(1 for w in qa['windows'] for s_ in ('bid', 'ask')
                    if w[s_]['vol_delta_sum'] == 0 and w[s_]['n_match'] > 0)
    win_ex = []
    for w in qa['windows']:
        if w['bid']['n_missing'] or w['ask']['n_missing'] or \
           w['bid']['ghosts'] or w['ask']['ghosts']:
            win_ex.append(dict(ms=w['ms'], bid=w['bid'], ask=w['ask']))
        if len(win_ex) >= 8:
            break
    mat_rows = [x for x in rows if x['kind'] == 'level_materialization']
    om = qa['open_materialized']
    return dict(code=code, day=day,
                n_events=len(evs), n_add=kinds['add'], n_fill=kinds['fill'],
                n_cancel=kinds['cancel'], n_snaps=len(snaps),
                n_windows=len(qa['windows']), n_ckpts=len(qa['checkpoints']),
                read_ms=round(t_read, 1), engine_ms=round(t_eng, 1),
                n_rows=len(rows), n_sweeps=len(sweeps),
                n_mat_rows=len(mat_rows),
                peak_rss_kb=rss_kb,
                m1=dict(n_anchor=d['n_anchor'], n_match=d['n_match'],
                        rate=round(d['n_match'] / d['n_anchor'], 5)
                        if d['n_anchor'] else 1.0,
                        n_present=d['n_present'],
                        presence=round(d['n_present'] / d['n_anchor'], 5)
                        if d['n_anchor'] else 1.0,
                        n_missing=d['n_anchor'] - d['n_present'],
                        missing_vol=d['missing_vol'],
                        attributed_vol=d['attributed_vol'],
                        unattributed_vol=d['unattributed_vol'],
                        delta_attribution=d['delta_attribution']),
                m2=dict(n_matched_levels=n_matched,
                        vol_delta_abs_sum=vd_sum,
                        n_zero_vd_windows=n_zero_vd,
                        ghost_vol=d['ghost_vol'],
                        n_windows_with_ghost=n_win_ghost,
                        n_windows_with_unattributed=n_win_unatt),
                m3=dict(qa['m3']), m4=qa['m4'], m6=qa['m6'],
                open_materialized=dict(bid=dict(n=len(om['B']),
                                                vol=sum(v for _, v in om['B'])),
                                       ask=dict(n=len(om['S']),
                                                vol=sum(v for _, v in om['S']))),
                eod=dict(eng.eod_summary), stats=dict(eng.stats),
                guards={**ga, **gt}, best_B=eng.best('B'), best_S=eng.best('S'),
                issue_windows=win_ex)


def verdict(res):
    """单日硬门判定（M1a 逐日存现门并入）: M4 守恒 0 mismatch & counters_equal;
    M6 PASS; presence ≥ GATE_PRES。返回 (ok, M1b rank rate)"""
    ok = res['m4']['conservation'] == 'PASS' and res['m4']['orders'] == 0 \
        and res['m4']['counters_equal'] and res['m6'] == 'PASS' \
        and res['m1']['presence'] >= GATE_PRES
    return ok, res['m1']['rate']


def m1a_gate(summaries):
    """M1a 档位存现门: 逐日 presence ≥ GATE_PRES 且 SZ/SH 池化 presence ≥ GATE_PRES。

    summaries = run_day JSON 形状列表 (code + m1.n_present/n_anchor/presence)。
    W3 校准语义: rank 对齐率是 M1b 诊断 (fast 日 δ 换位瞬态 0.80-0.94, 不设门);
    存现率才是门 (真缺档日 presence 崩)。单池空 (无该所日) 时该池不 FAIL —
    全校准日跑批两池恒非空。
    """
    sz = [s for s in summaries if s['code'][0] in '03']
    sh = [s for s in summaries if s['code'][0] not in '03']

    def pool(ss):
        n_p = sum(s['m1']['n_present'] for s in ss)
        n_a = sum(s['m1']['n_anchor'] for s in ss)
        return round(n_p / n_a, 5) if n_a else 1.0

    p_sz, p_sh = pool(sz), pool(sh)
    # per-day 由原始计数推导 (不读 JSON 圆整字段) — 门只信 n_present/n_anchor
    per_day = [(s['m1']['n_present'] / s['m1']['n_anchor']) >= GATE_PRES
               for s in summaries]
    return dict(sz=p_sz, sh=p_sh, per_day=per_day,
                ok=all(per_day) and p_sz >= GATE_PRES and p_sh >= GATE_PRES)


def main():
    if len(sys.argv) == 3:
        code, day = sys.argv[1], sys.argv[2]
        res = run_day(code, day)
        path = os.path.join(C.CALIB_OUT, f'w3_measure_{code}_{day}.json')
        with open(path, 'w') as f:
            json.dump(res, f, ensure_ascii=False)
        print(json.dumps(res, ensure_ascii=False))
        return
    hdr = (f'{"code-day":<15}{"events":>9}{"snaps":>7}{"win":>6}{"rank":>8}'
           f'{"pres":>8}{"unattr_v":>9}{"ghost_v":>8}{"M4":>5}{"M6":>5}'
           f'{"eng_ms":>8}')
    print(hdr)
    summary = []
    n_anch, n_match, n_pres, n_unattr = 0, 0, 0, 0
    for code, day in C.CALIB_DAYS:
        out = subprocess.run([sys.executable, os.path.abspath(__file__), code, day],
                             capture_output=True, text=True)
        res = json.loads(out.stdout.strip().splitlines()[-1])
        summary.append(res)
        rate = res['m1']['rate']
        pres = res['m1']['presence']
        m4 = 'PASS' if res['m4']['conservation'] == 'PASS' \
            and res['m4']['orders'] == 0 and res['m4']['counters_equal'] else 'FAIL'
        print(f'{code}@{day:<8}{res["n_events"]:>9}{res["n_snaps"]:>7}'
              f'{res["n_windows"]:>6}{rate:>8.4f}{pres:>8.4f}'
              f'{res["m1"]["unattributed_vol"]:>9}{res["m2"]["ghost_vol"]:>8}'
              f'{m4:>5}{res["m6"]:>5}{res["engine_ms"]:>8.1f}')
        n_anch += res['m1']['n_anchor']
        n_match += res['m1']['n_match']
        n_pres += res['m1']['n_present']
        n_unattr += res['m1']['unattributed_vol']
    aggr = round(n_match / n_anch, 5) if n_anch else 1.0
    sz = [r for r in summary if r['code'][0] in '03']
    sh = [r for r in summary if r['code'][0] not in '03']
    g_sz = round(sum(r['m1']['n_match'] for r in sz)
                 / sum(r['m1']['n_anchor'] for r in sz), 5)
    g_sh = round(sum(r['m1']['n_match'] for r in sh)
                 / sum(r['m1']['n_anchor'] for r in sh), 5)
    p_all = round(n_pres / n_anch, 5) if n_anch else 1.0
    m1a = m1a_gate(summary)
    print(f'\nM1b rank 价梯 (诊断不设门): 全 {aggr} | SZ {g_sz} | SH {g_sh}')
    print(f'M1a 存现门 ≥{GATE_PRES}: 全 {p_all} | SZ {m1a["sz"]} | SH {m1a["sh"]}'
          f' | 逐日 ' + ' '.join('P' if x else 'F' for x in m1a['per_day'])
          + f" → {'PASS' if m1a['ok'] else 'FAIL'}")
    print('M4/M6 硬门逐日: ' + ' '.join('PASS' if verdict(r)[0] else 'FAIL'
                                        for r in summary))
    print(f'unattributed_vol 全日 Σ {n_unattr}'
          f'  (δ 归因外差量, 逐窗记录于 issue_windows)')
    with open(os.path.join(C.CALIB_OUT, 'w3_measure_summary.json'), 'w') as f:
        json.dump(dict(days=summary,
                       aggregate=dict(all=aggr, sz=g_sz, sh=g_sh,
                                      presence_all=p_all, presence=dict(
                                          sz=m1a['sz'], sh=m1a['sh']),
                                      m1a=m1a,
                                      gates=dict(sz=GATE_PRES, sh=GATE_PRES),
                                      total_unattributed_vol=n_unattr)),
                  f, ensure_ascii=False)
    print('->', C.CALIB_OUT)


if __name__ == '__main__':
    main()
