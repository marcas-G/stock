#!/usr/bin/env python
"""W3 物化必要性实证 — registry 残留单物化后仍被后续 fills/cancels 消费的比例

开盘模型的关键前提: 09:25-09:30 (竞价/撮合段) 加单的残留单 (registry, 价>0, rem>0)
在首张连续快照处物化入簿是**必须**而非可选 —— 开盘后仍有大量成交/撤单事件按 id 引用
这些订单; 不物化则身份丢失 → unknown_fill/unknown_cancel 桶暴增 + 簿面量缺失 + 守恒破坏。

本探针: 逐校准日 run_day(full_rows) → 从引擎逐单账统计竞价/撮合段注册单中,
物化后被 fill/cancel 消费的比例与量 (不物化会丢多少事件)。

用法: python probe_mat_followup.py [code day ...]   (缺省全 10 校准日; 每日子进程隔离)
"""
import io, json, os, resource, subprocess, sys, time, zipfile

import pandas as pd

import config as C
from qa import streams as S
import anchoring as A
from measure_w3 import load_events, load_snaps


def run_day(code, day):
    evs, ga, gt = load_events(code, day)
    snaps = load_snaps(code, day)
    out = A.run_day(evs, snaps, full_rows=True)
    eng = out['engine']
    n_reg = n_followup = n_fill = n_cancel = n_keep = 0
    vol_reg = vol_followup = 0
    for rec in eng._orders.values():
        if rec['phase'] not in ('auction', 'match') or rec['price'] <= 0:
            continue
        n_reg += 1
        vol_reg += rec['added']
        if rec['booked'] and (rec['filled'] > 0 or rec['canceled'] > 0):
            n_followup += 1
            vol_followup += rec['added']
            if rec['filled'] > 0:
                n_fill += 1
            if rec['canceled'] > 0:
                n_cancel += 1
        elif rec['booked'] and rec['rem'] > 0:
            n_keep += 1
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return dict(code=code, day=day,
                n_reg_orders=n_reg, reg_vol=vol_reg,
                n_followup=n_followup, n_filled=n_fill, n_canceled=n_cancel,
                n_keep_rest=n_keep,
                followup_rate=round(n_followup / n_reg, 4) if n_reg else None,
                vol_followup_rate=round(vol_followup / vol_reg, 4)
                if vol_reg else None,
                unknown_fill=eng.counters['unknown_fill'],
                unknown_cancel=eng.counters['unknown_cancel'],
                peak_rss_kb=rss)


def main():
    days = sys.argv[1:] or [f'{c}_{d}' for c, d in C.CALIB_DAYS]
    rows = []
    for arg in days:
        code, day = arg.split('_')
        out = subprocess.run([sys.executable, os.path.abspath(__file__), code,
                              day], capture_output=True, text=True)
        res = json.loads(out.stdout.strip().splitlines()[-1])
        rows.append(res)
        print(f'{code}@{day:<8} reg {res["n_reg_orders"]:>6}  '
              f'followup {res["n_followup"]:>6} ({res["followup_rate"]:.1%})  '
              f'filled {res["n_filled"]:>6}  canceled {res["n_canceled"]:>6}  '
              f'kept {res["n_keep_rest"]:>6}  unk {res["unknown_fill"]}/{res["unknown_cancel"]}')
    with open(os.path.join(C.CALIB_OUT, 'w3_mat_followup_summary.json'), 'w') as f:
        json.dump(rows, f, ensure_ascii=False)
    tot_reg = sum(r['n_reg_orders'] for r in rows)
    tot_fu = sum(r['n_followup'] for r in rows)
    print(f'\nΣ 残留单 {tot_reg} | 物化后被消费 {tot_fu} ({tot_fu / tot_reg:.1%})'
          ' —— 不物化则这些 fills/cancels 全部身份丢失')
    print('->', os.path.join(C.CALIB_OUT, 'w3_mat_followup_summary.json'))


if __name__ == '__main__':
    if len(sys.argv) == 3:
        code, day = sys.argv[1], sys.argv[2]
        sys.stdout.write(json.dumps(run_day(code, day), ensure_ascii=False))
    else:
        main()
