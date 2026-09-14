#!/usr/bin/env python
"""W1 校准集测量 — 逐 code-day 证据 JSON（校准备忘录数据来源）


import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # lob_fact/
覆盖（校准集 config.CALIB_DAYS = 10 code-day × 7 个月 × 双所 4 代码）:
  cancel_semantics  撤单量语义分布 (full/partial/excess) + 逐单守恒 + 违规计数
  ref_resolve       撤单/成交 ref 解析率（含 SH 先成交后报量化）
  type_boundary     SZ 委托类型 0/1/U 域 + 价0 + 撤/成交对 '1'/U 单引用
  open_queue        开盘排队簿（首张连续快照档级量, SZ@09:30:00.000 硬基线）
  delta             δ 滞后归因（add-only 档, 连续快照对, bins ≤500ms）
  coverage          覆盖清单: raw zip / tick_fact 3 表 / cancels 表 / manifest 对照
  diff_attr         2025-09 tick_fact vs raw 正常行差行归因（仅校准日）

输出: config.CALIB_OUT/w1_{item}_{code}_{day}.json + summary.json
用法: python calibrate_w1.py [--items cancel_semantics,delta] [--codes 000155] [--days 20260803]
"""
import argparse, bisect, datetime, glob, io, json, os, zipfile
import numpy as np
import pandas as pd
import polars as pl

from core import config as C
from core.qa import streams as S, ledger as L, delta as D, metrics as mt

_raw_cache = {}


def rd(code, day, fname, cols=None):
    """raw zip 内 CSV 读取（缓存; cols 为 None 读全列）"""
    key = (code, day, fname)
    if key not in _raw_cache:
        p = C.code_zip(code, day)
        with zipfile.ZipFile(p) as zf:
            _raw_cache[key] = pd.read_csv(io.BytesIO(zf.read(fname)),
                                          encoding='gbk', usecols=cols)
    return _raw_cache[key]


def tf_rows(code, day, tbl):
    """tick_fact 某表 code-day 行数（date 谓词剪枝单月文件）"""
    from factorlab.adapters.tick_read import read_tick_table
    try:
        df = read_tick_table(tbl, day, codes=[f'{code}.{"SZ" if code[0] in "03" else "SH"}'],
                             columns=['code'])
    except FileNotFoundError:
        return 0
    return df.height


def _ledger_run(code, day):
    """全账本: (ledger 结果, adds map(id→type), 守卫计数)"""
    o = rd(code, day, '逐笔委托.csv')
    t = rd(code, day, '逐笔成交.csv')
    if code[0] in '03':
        add_evs, g1 = S.sz_orders_events(o)
        tr_evs, g2 = S.sz_trades_events(t)
        guards = dict(**g1, **g2)
    else:
        add_evs, aux = S.sh_orders_events(o)
        tr_evs, g2 = S.sh_trades_events(t)
        guards = dict(**g2, S_rows=aux['S'], S_codes=aux['S_codes'])
    out = L.ledger(add_evs + tr_evs)
    otype = {}
    for e in add_evs:
        if e['id'] not in otype:
            otype[e['id']] = e['otype']
    for e in tr_evs:  # fill/cancel 引用到的单的类型补查（按需小表）
        pass
    return out, otype, guards, add_evs, tr_evs


def _dist_classes(rows, auct_ms=34_200_000):
    """cancel_log → 语义分布 dict"""
    d = dict(n=len(rows), full=0, partial=0, excess=0, by_type={},
             auction=0, continuous=0, by_side={'B': 0, 'S': 0})
    for r in rows:
        cls = 'full' if r['qty'] == r['rem_before'] else (
            'partial' if r['qty'] < r['rem_before'] else 'excess')
        d[cls] += 1
        d['by_type'][r['type']] = d['by_type'].get(r['type'], 0) + 1
        d['by_side'][r['side']] += 1
        if r['ms'] < auct_ms:
            d['auction'] += 1
        else:
            d['continuous'] += 1
    return d


# ---------- 测量项 ----------

def item_cancel_semantics(code, day):
    """撤单量语义 + 账本违规计数 + 守恒"""
    out, otype, guards, add_evs, tr_evs = _ledger_run(code, day)
    dist = _dist_classes(out['cancel_log'])
    fills = [e for e in tr_evs if e['kind'] == 'fill']
    canc = [e for e in tr_evs if e['kind'] == 'cancel']
    cons = L.conservation_summary(out['orders'])
    return dict(code=code, day=day,
                n_add_events=len(add_evs), n_cancel_rows=len(canc),
                n_fill_refs=len(fills),
                cancel=dist,
                unknown_cancel=out['counters']['unknown_cancel'],
                unknown_fill=out['counters']['unknown_fill'],
                fill_ref_resolve_rate=round(
                    1 - out['counters']['unknown_fill'] / len(fills), 6) if fills else 1.0,
                dup_add=out['counters']['dup_add'],
                fill_excess=out['counters']['fill_excess'],
                cancel_excess=out['counters']['cancel_excess'],
                zero_ref_cancel_rows=guards.get('n_zero_ref', 0),
                conservation=cons,
                excess_ok=(cons['added'] == cons['filled'] + cons['canceled'] + cons['rem']),
                )


def _ref_types(tr_evs, otype):
    """撤单/成交 ref 的类型分布（解析到的单; SH D/A 同通道: A 型）"""
    crt, frt, unk = {}, {}, 0
    for e in tr_evs:
        d = crt if e['kind'] == 'cancel' else frt
        t = otype.get(e['id'], '?')
        if t == '?':
            unk += 1
        else:
            d[t] = d.get(t, 0) + 1
    return crt, frt, unk


def item_ref_resolve(code, day):
    """ref 解析率: 撤单行 ref / 成交行双侧 ref 各自解析率（含逐单消费后未知率）"""
    out, otype, guards, add_evs, tr_evs = _ledger_run(code, day)
    bid_f, ask_f, u_bid, u_ask = 0, 0, 0, 0
    for e in tr_evs:
        if e['kind'] == 'fill':
            if e['side'] == 'B':
                bid_f += 1
                if e['id'] not in otype:
                    u_bid += 1
            else:
                ask_f += 1
                if e['id'] not in otype:
                    u_ask += 1
    crt, frt, _ = _ref_types(tr_evs, otype)
    return dict(code=code, day=day,
                fill_refs={'B': bid_f, 'S': ask_f},
                fill_unresolved={'B': u_bid, 'S': u_ask},
                cancel_unknown=out['counters']['unknown_cancel'],
                # 撤单/成交引用类型分布 (引到 0/1/U/A 单; '?'=未解析)
                cancel_ref_type=crt,
                fill_ref_type=frt,
                )


def item_type_boundary(code, day):
    """SZ 委托类型域/价0 + 各类型簿面参与（入簿规则证据）; SH A/D/S 域"""
    o = rd(code, day, '逐笔委托.csv')
    t = rd(code, day, '逐笔成交.csv')
    res = dict(code=code, day=day)
    if code[0] in '03':
        add_evs, _ = S.sz_orders_events(o)
        t_evs, _ = S.sz_trades_events(t)
        types, p0 = {}, {}
        for e in add_evs:
            types[e['otype']] = types.get(e['otype'], 0) + 1
            if e['price'] == 0:
                p0[e['otype']] = p0.get(e['otype'], 0) + 1
        otype = {e['id']: e['otype'] for e in add_evs}
        ref1u = {'fill': 0, 'cancel': 0}
        for e in t_evs:
            if otype.get(e['id']) in ('1', 'U'):
                ref1u[e['kind']] += 1
        # U 价>0（进簿类）计数
        u_pos = sum(1 for e in add_evs if e['otype'] == 'U' and e['price'] > 0)
        # 各类型簿面参与: 账本逐单聚合（n / 带价 n / 消费去向）
        out = L.ledger(add_evs + t_evs)
        orders_by_type = {}
        for o in out['orders'].values():
            a = orders_by_type.setdefault(o['type'], dict(
                n=0, n_price_pos=0, filled=0, canceled=0, rem=0))
            a['n'] += 1
            if o['price'] > 0:
                a['n_price_pos'] += 1
            a['filled'] += o['filled']
            a['canceled'] += o['canceled']
            a['rem'] += o['rem']
        res.update(types=types, price0_by_type=p0, u_price_pos=u_pos,
                   refs_to_1u=ref1u, orders_by_type=orders_by_type)
    else:
        evs, aux = S.sh_orders_events(o)
        res.update(n_A=sum(1 for e in evs if e.get('otype') == 'A'),
                   n_D=sum(1 for e in evs if e['kind'] == 'cancel'),
                   S_aux=aux['S'], S_codes=aux['S_codes'])
    return res


def item_open_queue(code, day):
    """开盘排队簿: 首张连续快照（SZ@09:30:00.000 硬基线; SH 首张连续）档级分布"""
    fs = sorted(glob.glob(f'{C.tick_month("snapshots", day)}part-*.parquet'))
    if not fs:
        return dict(code=code, day=day, error='no month file')
    y, m, d = int(day[:4]), int(day[4:6]), int(day[6:])
    df = pl.scan_parquet(fs).filter(
        (pl.col('code') == f'{code}.{"SZ" if code[0] in "03" else "SH"}') &
        (pl.col('trade_date') == datetime.date(y, m, d))).collect().sort('time_ms')
    cont = df.filter(pl.col('time_ms') >= 34_200_000)
    if cont.is_empty():
        return dict(code=code, day=day, error='no continuous snapshot')
    row = cont.head(1).to_dicts()[0]
    bid = mt.snap_row_to_ladders(row, 'bid')
    ask = mt.snap_row_to_ladders(row, 'ask')
    return dict(code=code, day=day, snap_ms=int(row['time_ms']),
                n_frames_cont=len(cont),
                bid=[{'p': p, 'v': v} for p, v in bid],
                ask=[{'p': p, 'v': v} for p, v in ask],
                bid_sum=int(sum(v for _, v in bid)),
                ask_sum=int(sum(v for _, v in ask)))


def item_delta(code, day):
    """δ 滞后归因（add-only 档 × 连续快照对; SZ 双侧 best1-2）"""
    if code[0] not in '03':  # SH 同构测量 W3 引擎后展开（D 全撤消息语义先行）
        return dict(code=code, day=day, skipped='SH W3')
    o = rd(code, day, '逐笔委托.csv')
    t = rd(code, day, '逐笔成交.csv')
    add_evs, _ = S.sz_orders_events(o)
    oprice = {e['id']: (e['side'], e['price']) for e in add_evs}
    # 每 (side, price>0) 加单序列
    per_level = {}
    for e in add_evs:
        if e['price'] > 0:
            per_level.setdefault((e['side'], e['price']), []).append((e['ms'], e['qty']))
    for k in per_level:
        per_level[k].sort()
    # 触达事件（撤/成交消费面）: (side, price) → ms 列表（撤按单价格; 成交双侧: 可解析单按
    # 自身价, 不可解析按打印价双侧保守标记）
    touch = {}
    for r in t.itertuples(index=False):
        codec = str(r.成交代码).strip()
        bid, ask, qty, ms = int(r.叫买序号), int(r.叫卖序号), int(r.成交数量), \
            S.hms_to_ms(int(r.时间))
        px = int(r.成交价格)
        if codec == 'C':  # 撤单: 引用单价格
            for ref, side in ((bid, 'B'), (ask, 'S')):
                sp = oprice.get(ref)
                if ref > 0 and sp is not None and sp[1] > 0:
                    touch.setdefault((sp[0], sp[1]), []).append(ms)
        else:            # 成交
            for ref, side in ((bid, 'B'), (ask, 'S')):
                sp = oprice.get(ref) if ref > 0 else None
                if sp is not None and sp[1] > 0:
                    touch.setdefault((sp[0], sp[1]), []).append(ms)
                elif ref > 0 and px > 0:  # 不可解析 → 双侧 px 保守
                    touch.setdefault(('B', px), []).append(ms)
                    touch.setdefault(('S', px), []).append(ms)
    for k in touch:
        touch[k].sort()
    # 快照流
    y, m, d = int(day[:4]), int(day[4:6]), int(day[6:])
    fs = sorted(glob.glob(f'{C.tick_month("snapshots", day)}part-*.parquet'))
    if not fs:
        return dict(code=code, day=day, error='no month file')
    sn = pl.scan_parquet(fs).filter(
        (pl.col('code') == f'{code}.SZ') &
        (pl.col('trade_date') == datetime.date(y, m, d))).collect().sort('time_ms')
    sn = sn.filter(pl.col('time_ms') >= 34_200_000)
    rows = sn.to_dicts()
    # 逐对窗口归因（可见档 rank 1-5 双侧）; 空闲档(v2==v1 且窗内无加单)/level_gone(v2==0)
    # 单独成桶, zero_lag 只计有加单活动的档窗
    tally = dict(windows={'B%d' % r: 0 for r in range(1, 6)} |
                 {'S%d' % r: 0 for r in range(1, 6)},
                 active=0, idle=0, level_gone=0, touched_skip=0, rank_shift=0,
                 zero_lag=0, over=0, no_exact=0, lag_gt_window=0, lag=0,
                 lag_bins={})
    for i in range(len(rows) - 1):
        r1, r2 = rows[i], rows[i + 1]
        t1, t2 = int(r1['time_ms']), int(r2['time_ms'])
        if t2 - t1 > 30_000:
            continue
        for rk in range(1, 6):
            for side, sxy in (('bid', 'B'), ('ask', 'S')):
                key = f'{sxy}{rk}'
                p = r1.get(f'{side}_p{rk}')
                v1 = r1.get(f'{side}_v{rk}')
                if p is None or v1 is None or not (isinstance(p, float) and p > 0):
                    continue
                p = mt.rint(p)
                v1 = mt.rint(v1)
                if v1 <= 0:
                    continue
                p2 = r2.get(f'{side}_p{rk}')
                v2r = r2.get(f'{side}_v{rk}')
                if p2 is None or v2r is None or mt.rint(p2) != p:
                    tally['rank_shift'] += 1          # 档位移出 rank → 非同一档, 跳过
                    continue
                v2 = mt.rint(v2r)
                if v2 <= 0:
                    tally['level_gone'] += 1          # 整档消失(撤空/被吃光) → 快照侧差分桶
                    continue
                tally['windows'][key] += 1
                lv = per_level.get((sxy, p))
                # 触达排除
                tl = touch.get((sxy, p))
                if tl:
                    j = bisect.bisect_left(tl, t1)
                    if j < len(tl) and tl[j] <= t2 + D.WINDOW_MS:
                        tally['touched_skip'] += 1
                        continue
                j0 = bisect.bisect_right(lv, (t1, 10 ** 9))
                j1 = bisect.bisect_right(lv, (t2 + D.WINDOW_MS, 10 ** 9))
                adds = lv[j0:j1]
                if not adds and v2 == v1:
                    tally['idle'] += 1
                    continue
                tally['active'] += 1
                r = D.attrib_lag(adds, t1, t2, v2 - v1, window=D.WINDOW_MS)
                if r['cls'] == 'lag':
                    tally['lag'] += 1
                    b = min(r['lag_ms'] // 50, 9)
                    tally['lag_bins'][b] = tally['lag_bins'].get(b, 0) + 1
                else:
                    tally[r['cls']] += 1
    return dict(code=code, day=day, tally=tally, window_ms=D.WINDOW_MS)


def item_coverage(code, day):
    """覆盖清单: raw zip 大小/行数 × tick_fact 3 表 × cancels 表 × manifest 对照"""
    o = rd(code, day, '逐笔委托.csv')
    t = rd(code, day, '逐笔成交.csv')
    h = rd(code, day, '行情.csv')
    canc = 0
    if code[0] in '03':
        evs, _ = S.sz_trades_events(t)
        canc = sum(1 for e in evs if e['kind'] == 'cancel')
    return dict(code=code, day=day,
                zip_bytes=os.path.getsize(C.code_zip(code, day)),
                raw_rows=dict(orders=len(o), trades=len(t), snaps=len(h)),
                raw_cancel_rows=canc,
                tick_orders=tf_rows(code, day, 'orders'),
                tick_trades=tf_rows(code, day, 'trades'),
                tick_snaps=tf_rows(code, day, 'snapshots'),
                tick_cancels=tf_rows(code, day, 'cancels'),
                )


def item_diff_attr(code, day):
    """2025-09 差行归因: tick_fact trades vs raw 正常行（row key 全等对照）"""
    t = rd(code, day, '逐笔成交.csv')
    if code[0] in '03':
        norm = t[t['成交代码'].astype(str).str.strip().ne('C')]
    else:
        norm = t
    keys = lambda bid, ask: (int(0 if pd.isna(bid) else bid),
                             int(0 if pd.isna(ask) else ask))
    raw = {(S.hms_to_ms(int(r.时间)), int(r.成交编号), int(r.成交价格),
            int(r.成交数量), *keys(r.叫买序号, r.叫卖序号))
           for r in norm.itertuples(index=False)}
    fs = sorted(glob.glob(f'{C.tick_month("trades", day)}part-*.parquet'))
    if not fs:
        return dict(code=code, day=day, error='no month file')
    y, m, d = int(day[:4]), int(day[4:6]), int(day[6:])
    df = pl.scan_parquet(fs).filter(
        (pl.col('code') == f'{code}.{"SZ" if code[0] in "03" else "SH"}') &
        (pl.col('trade_date') == datetime.date(y, m, d))).collect()
    tick = {(int(r['time_ms']), int(r['trade_no']), int(r['price_x10000']),
             int(r['volume']), int(r['bid_seq']), int(r['ask_seq']))
            for r in df.to_dicts()}
    raw_only = raw - tick
    tick_only = tick - raw
    samples = []
    for key in sorted(raw_only)[:5]:
        ms, no, px, qty, bid, ask = key
        samples.append(dict(time_ms=ms, trade_no=no, price=px, qty=qty,
                            bid_ref=bid, ask_ref=ask))
    return dict(code=code, day=day, n_raw=len(raw), n_tick=len(tick),
                raw_only=len(raw_only), tick_only=len(tick_only),
                samples=samples,
                # 归类: 零价行(px==0) = 转换器零价过滤路径 (C 行同桶)
                px0_raw_only=sum(1 for s in samples if s['price'] == 0))


ITEMS = dict(cancel_semantics=item_cancel_semantics, ref_resolve=item_ref_resolve,
             type_boundary=item_type_boundary, open_queue=item_open_queue,
             delta=item_delta, coverage=item_coverage, diff_attr=item_diff_attr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--items', default=','.join(ITEMS))
    ap.add_argument('--codes', default='')
    ap.add_argument('--days', default='')
    ap.add_argument('--diff-days', default='20250915')
    args = ap.parse_args()
    os.makedirs(C.CALIB_OUT, exist_ok=True)
    codes = set(args.codes.split(',')) if args.codes else None
    days = set(args.days.split(',')) if args.days else None
    items = args.items.split(',')
    all_res = {}
    diff_days = {d for d in args.diff_days.split(',') if d}
    for code, day in C.CALIB_DAYS:
        if codes and code not in codes:
            continue
        if days and day not in days:
            continue
        for it in items:
            if it == 'diff_attr' and day not in diff_days:
                continue
            fn = ITEMS[it]
            res = fn(code, day)
            path = os.path.join(C.CALIB_OUT, f'w1_{it}_{code}_{day}.json')
            with open(path, 'w') as f:
                json.dump(res, f, ensure_ascii=False, sort_keys=True)
            all_res[f'{it}/{code}/{day}'] = res
            small = {k: v for k, v in res.items() if not isinstance(v, (dict, list))}
            print(f'  {it} {code}@{day}: {small}')
    with open(os.path.join(C.CALIB_OUT, 'summary.json'), 'w') as f:
        json.dump(all_res, f, ensure_ascii=False)
    print('done ->', C.CALIB_OUT)


if __name__ == '__main__':
    main()
