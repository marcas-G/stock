#!/usr/bin/env python
"""W2 schema 冻结门实测 — 校准日引擎吞吐 / 事件行量 / 峰值 RSS（每 code-day 子进程）

用法:
  python measure_w2.py               # 父进程: 全 10 校准日逐日 spawn 子进程（串行, 内存隔离）
  python measure_w2.py 000021 20260706   # 子进程: 单日引擎实测 → 单行 JSON（stdout）

实测对象 = 生产事件链: raw zip → qa.streams 归一化 → Engine.ingest/eod。
输出: CALIB_OUT/w2_measure_{code}_{day}.json（子进程写）+ 父进程汇总表 + summary.json。
冻结门: 吞吐 <2s/code-day（引擎重放段）、行量/字节 vs 源行数（tier 决策）、峰值 RSS
（W4 worker 算术输入: 单 date 切片峰值）。

引擎内存最坏情况实测: 事件流全驻留 + registry + 簿面 + 事件行（W3 物化将流式化, 本数字为
上界）。RSS = 子进程 ru_maxrss（本日独占, 无跨日累积）。
"""
import json, os, resource, subprocess, sys, time, zipfile, io
from collections import Counter

import pandas as pd

import config as C
from qa import streams as S
from engine import Engine

# 行宽冻结估算（整数主导列 × 8B 均摊; 物化 schema W3 冻结时以 parquet 实测修正）
EV_ROW_BYTES = 96
SW_ROW_BYTES = 88


def load_events(code, day):
    """raw zip → 归一化事件（与 W1 校准同源; 双侧 ref 拆两条 fill, C 行 → cancel）"""
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


def run_day(code, day):
    """单 code-day: 读 → 归一化 → 引擎重放 → 行量/RSS/耗时 JSON"""
    t0 = time.time()
    evs, ga, gt = load_events(code, day)
    t_read = (time.time() - t0) * 1000
    kinds = Counter(ev['kind'] for ev in evs)
    t0 = time.time()
    e = Engine()
    e.ingest(evs)
    t_eng = (time.time() - t0) * 1000
    e.eod()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return dict(code=code, day=day,
                n_events=len(evs), n_add=kinds['add'], n_fill=kinds['fill'],
                n_cancel=kinds['cancel'],
                read_ms=round(t_read, 1), engine_ms=round(t_eng, 1),
                n_rows=len(e.events), n_sweeps=len(e.sweeps),
                est_bytes=len(e.events) * EV_ROW_BYTES + len(e.sweeps) * SW_ROW_BYTES,
                peak_rss_kb=rss_kb, counters=dict(e.counters),
                eod=dict(e.eod_summary), stats=dict(e.stats),
                guards={**ga, **gt}, best_B=e.best('B'), best_S=e.best('S'))


def main():
    if len(sys.argv) == 3:
        code, day = sys.argv[1], sys.argv[2]
        res = run_day(code, day)
        path = os.path.join(C.CALIB_OUT, f'w2_measure_{code}_{day}.json')
        with open(path, 'w') as f:
            json.dump(res, f, ensure_ascii=False)
        print(json.dumps(res, ensure_ascii=False))
        return
    # 父进程: 串行逐日 spawn（内存隔离 + 独立 RSS）
    print(f'{"code-day":<15}{"events":>9}{"eng_ms":>8}{"rows":>9}{"sweeps":>8}{"rssMB":>8}')
    summary = []
    for code, day in C.CALIB_DAYS:
        out = subprocess.run([sys.executable, os.path.abspath(__file__), code, day],
                             capture_output=True, text=True)
        res = json.loads(out.stdout.strip().splitlines()[-1])
        summary.append(res)
        print(f'{code}@{day:<8}{res["n_events"]:>9}{res["engine_ms"]:>8.1f}'
              f'{res["n_rows"]:>9}{res["n_sweeps"]:>8}{res["peak_rss_kb"]/1024:>8.1f}')
    with open(os.path.join(C.CALIB_OUT, 'w2_measure_summary.json'), 'w') as f:
        json.dump(summary, f, ensure_ascii=False)
    print('->', C.CALIB_OUT)


if __name__ == '__main__':
    main()
