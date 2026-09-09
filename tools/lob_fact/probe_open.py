#!/usr/bin/env python
"""W3 开盘模型探针 — 竞价/撮合 registry 残单 vs 首张连续快照档级量（决定开盘簿注入模型）

问题（设计 §1/W1 memo §4）: SZ/SH 开盘簿的可见量 5.3万-51.6万股/日来源为何？
  A) 竞价 registry 携入: 09:15-09:25 价>0 adds 经 09:25 撮合打印与竞价撤单消费后的剩余
     在开盘（OPEN / 首张连续快照 ms）转入连续簿 —— 身份保留，后续 fills/cancels 自然解析
  B) 快照硬基线聚合注入: 快照档量含 registry 之外的无源排队委托 → 聚合注入口径

本探针: 对每校准日取 registry（ms < snap_ms 的事件子集过账本）+ 首张连续快照 ladder，
按 (side, price) 逐档对账 → carryover 价级与快照 10 档的价格一致率 / 量差分布。
结论冻结 anchoring 开盘注入模型与开盘窗 M1 桶定义。

输出: CALIB_OUT/w3_open_{code}_{day}.json + 汇总表。用法: python probe_open.py [code day]
"""
import glob, json, os, sys, zipfile, io, datetime
import pandas as pd
import polars as pl

import config as C
from qa import streams as S, ledger as L, metrics as mt


def first_snap(code, day):
    """首张连续快照（time_ms ≥ OPEN）10 档 ladder bid/ask（best-first）+ snap_ms
    —— 读 tick_fact snapshots（time_ms 已 ms-of-day；与 W1 open_queue 同源同口径）"""
    fs = sorted(glob.glob(f'{C.tick_month("snapshots", day)}part-*.parquet'))
    if not fs:
        return None, None, None
    y, m, d = int(day[:4]), int(day[4:6]), int(day[6:])
    df = pl.scan_parquet(fs).filter(
        (pl.col('code') == f'{code}.{"SZ" if code[0] in "03" else "SH"}') &
        (pl.col('trade_date') == datetime.date(y, m, d))).collect().sort('time_ms')
    cont = df.filter(pl.col('time_ms') >= C.OPEN)
    if cont.is_empty():
        return None, None, None
    row = cont.head(1).to_dicts()[0]
    return int(row['time_ms']), mt.snap_row_to_ladders(row, 'bid'), \
        mt.snap_row_to_ladders(row, 'ask')


def carryover(code, day, snap_ms):
    """registry 在 snap_ms 前成立、价>0 且剩余>0 的单 → per (side, price) Σrem（best-first 序）"""
    p = C.code_zip(code, day)
    with zipfile.ZipFile(p) as zf:
        o = pd.read_csv(io.BytesIO(zf.read('逐笔委托.csv')), encoding='gbk')
        t = pd.read_csv(io.BytesIO(zf.read('逐笔成交.csv')), encoding='gbk')
    if code[0] in '03':
        add_evs, _ = S.sz_orders_events(o)
        tr_evs, _ = S.sz_trades_events(t)
    else:
        add_evs, _ = S.sh_orders_events(o)
        tr_evs, _ = S.sh_trades_events(t)
    evs = [e for e in add_evs + tr_evs if e['ms'] < snap_ms]
    out = L.ledger(evs)
    agg = {'B': {}, 'S': {}}
    n_ord = 0
    for r in out['orders'].values():
        if r['price'] > 0 and r['rem'] > 0:
            agg[r['side']].setdefault(r['price'], 0)
            agg[r['side']][r['price']] += r['rem']
            n_ord += 1
    def ladder(side):
        return sorted(agg[side].items(), reverse=(side == 'B'))
    return n_ord, ladder('B'), ladder('S')


def probe(code, day):
    snap_ms, ab, aa = first_snap(code, day)
    if snap_ms is None:
        return dict(code=code, day=day, error='no snap >= OPEN')
    n_ord, cb, ca = carryover(code, day, snap_ms)
    res = dict(code=code, day=day, snap_ms=snap_ms, n_carryover_orders=n_ord)
    for side, anchor, carry in (('bid', ab, cb), ('ask', aa, ca)):
        m = mt.ladder_match(anchor, carry)
        res[f'{side}_match'] = dict(n_anchor=m['n_anchor'], n_match=m['n_match'],
                                    rate=m['rate'], extras_px=[p for p, _ in m['extras']],
                                    extra_vol=sum(v for _, v in m['extras']))
        # 共同价档量差（引擎方向: 快照 − carryover）
        avol = {p: v for p, v in anchor}
        cvol = {p: v for p, v in carry}
        d = [dict(price=p, snap_v=avol[p], carry_v=cvol.get(p, 0),
                  diff=avol[p] - cvol.get(p, 0)) for p in avol]
        res[f'{side}_vol'] = dict(common=len([x for x in d if x['price'] in cvol]),
                                  n_diff0=sum(1 for x in d if x['diff'] == 0),
                                  n_carry_gt_snap=sum(1 for x in d if x['diff'] < 0),
                                  carry_covered=round(
                                      sum(cvol.get(p, 0) for p in avol) /
                                      sum(avol.values()), 6) if avol else 1.0,
                                  diffs=d[:12])
    return res


def main():
    if len(sys.argv) == 3:
        r = probe(sys.argv[1], sys.argv[2])
        with open(os.path.join(C.CALIB_OUT, f'w3_open_{r["code"]}_{r["day"]}.json'), 'w') as f:
            json.dump(r, f, ensure_ascii=False)
        print(json.dumps(r, ensure_ascii=False))
        return
    print(f'{"code-day":<15}{"snap_ms":>9}{"carry":>7}{"bid_rate":>9}{"bid_cov":>9}{"ask_rate":>9}{"ask_cov":>9}')
    for code, day in C.CALIB_DAYS:
        r = probe(code, day)
        with open(os.path.join(C.CALIB_OUT, f'w3_open_{code}_{day}.json'), 'w') as f:
            json.dump(r, f, ensure_ascii=False)
        b, a = r.get('bid_match', {}), r.get('ask_match', {})
        print(f'{code}@{day:<8}{r.get("snap_ms", "-"):>9}{r.get("n_carryover_orders", 0):>7}'
              f'{b.get("rate", 0):>9.4f}{r.get("bid_vol", {}).get("carry_covered", 0):>9.4f}'
              f'{a.get("rate", 0):>9.4f}{r.get("ask_vol", {}).get("carry_covered", 0):>9.4f}')
    print('->', C.CALIB_OUT)


if __name__ == '__main__':
    main()
