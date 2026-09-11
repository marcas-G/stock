"""W4a 事件对等探针: tick_fact parquet 读链 vs raw zip streams 链 逐事件等价 + 读耗时校准

生产批算（run_lob_batch）的事件源 = tick_fact parquet（orders/trades/cancels/snapshots，
非 raw zip）。前提：parquet 行 → 归一化事件的映射必须与 qa.streams(raw) 逐事件字节等价
（同 kind/ms/id/side/price/qty/otype 序列，含同 (ms,kind) 组内文件行序），否则批算引擎
状态/QA 数值与 W1-W3 校准链不同源。

本探针逐 code-day：
  1) raw 链: zip 逐笔委托/成交 CSV → qa.streams 事件（measure_w3.load_events 同链）
  2) parquet 链: tick_fact orders/trades/cancels 行 → 新映射 (run_lob_batch 同款纯函数)
  3) 断言两列事件表逐项全等（保序）; 报告 4 表读耗时/行数/映射耗时（W4 worker 算术输入）
代码-day 直接传参: python parity_probe.py 000155 20260803 [code day ...]

2026-09-10 校准抽样: 双所 × 平静/fast × 13 月跨度 × 科创板 688/301 × 市价单重日。
"""
import sys, os, time, json, glob, zipfile, io
sys.path.insert(0, '/data/students/gaolei/stock/projects/quant-platform-research/tools/lob_fact')
import config as C
from qa import streams as S
import pandas as pd, polars as pl

def orders_to_events(code_ex: str, day: str):
    """tick orders 行 → (add/cancel events, aux)。SH: A→add D→cancel S→aux; SZ: 全行 add。
    bs→side, exch_order_no→id, order_type→otype, qty<=0 跳过 (streams n_bad_row 同语义)"""
    fs = sorted(glob.glob(f'{C.tick_month("orders", day)}part-*.parquet'))
    df = (pl.scan_parquet(fs)
          .filter((pl.col('code') == code_ex)
                  & (pl.col('trade_date') == pl.lit(__import__('datetime').date(
                      int(day[:4]), int(day[4:6]), int(day[6:8])))))
          .collect())
    evs = []
    ex = code_ex.split('.')[1]
    for r in df.iter_rows(named=True):
        t = r['order_type']
        qty = r['volume']
        if t == 'S':                        # SH 杂项: 不进簿不进流
            continue
        if qty <= 0:
            continue                        # streams n_bad_row 语义 (哨兵/坏行)
        side = r['bs']
        if ex == 'SH':
            if t == 'A':
                evs.append(dict(kind='add', ms=int(r['time_ms']), id=r['exch_order_no'],
                                side=side, price=int(r['price_x10000']), qty=int(qty),
                                otype='A'))
            elif t == 'D':
                evs.append(dict(kind='cancel', ms=int(r['time_ms']), id=r['exch_order_no'],
                                qty=int(qty), side=side))
        else:                               # SZ: 类型 0/1/U 全收 (引擎按价裁决)
            evs.append(dict(kind='add', ms=int(r['time_ms']), id=r['exch_order_no'],
                            side=side, price=int(r['price_x10000']), qty=int(qty),
                            otype=t))
    return evs


def trades_to_events(code_ex: str, day: str):
    """tick trades 行 → fill 事件 (raw 正常行已剔除 C/哨兵; 双侧 ref>0 各发一条)"""
    fs = sorted(glob.glob(f'{C.tick_month("trades", day)}part-*.parquet'))
    df = (pl.scan_parquet(fs)
          .filter((pl.col('code') == code_ex)
                  & (pl.col('trade_date') == pl.lit(__import__('datetime').date(
                      int(day[:4]), int(day[4:6]), int(day[6:8])))))
          .collect())
    evs = []
    for r in df.iter_rows(named=True):
        bid, ask, qty = r['bid_seq'], r['ask_seq'], r['volume']
        if bid == 0 and ask == 0:
            continue
        px = int(r['price_x10000'])
        if bid > 0:
            evs.append(dict(kind='fill', ms=int(r['time_ms']), id=bid, qty=int(qty),
                            side='B', price=px))
        if ask > 0:
            evs.append(dict(kind='fill', ms=int(r['time_ms']), id=ask, qty=int(qty),
                            side='S', price=px))
    return evs


def cancels_to_events(code_ex: str, day: str):
    """tick cancels 行 (SZ-only) → cancel 事件: side 0=B 1=S, id=order_ref"""
    fs = sorted(glob.glob(f'{C.tick_month("cancels", day)}part-*.parquet'))
    if not fs:
        return []
    df = (pl.scan_parquet(fs)
          .filter((pl.col('code') == code_ex)
                  & (pl.col('trade_date') == pl.lit(__import__('datetime').date(
                      int(day[:4]), int(day[4:6]), int(day[6:8])))))
          .collect())
    return [dict(kind='cancel', ms=int(r['time_ms']), id=r['order_ref'],
                 qty=int(r['volume']), side='B' if r['side'] == 0 else 'S')
            for r in df.iter_rows(named=True)]


def parquet_events(code, day):
    ex = 'SZ' if code[0] in '03' else 'SH'
    ce = f'{code}.{ex}'
    return (orders_to_events(ce, day) + trades_to_events(ce, day)
            + (cancels_to_events(ce, day) if ex == 'SZ' else []))


# ---- raw 链 (measure_w3.load_events 同款) ----

def raw_events(code, day):
    p = C.code_zip(code, day)
    with zipfile.ZipFile(p) as zf:
        o = pd.read_csv(io.BytesIO(zf.read('逐笔委托.csv')), encoding='gbk')
        t = pd.read_csv(io.BytesIO(zf.read('逐笔成交.csv')), encoding='gbk')
    if code[0] in '03':
        evs_a, _ = S.sz_orders_events(o)
        evs_t, _ = S.sz_trades_events(t)
    else:
        evs_a, _ = S.sh_orders_events(o)
        evs_t, _ = S.sh_trades_events(t)
    return evs_a + evs_t


def norm(evs):
    # 输入等价判据 = run_day/engine 确定性: (ms, kind prio) 稳定排序后的逐事件键
    # (raw 链 adds++fills/cancels 行序 vs parquet 链 adds++fills++cancels 块序:
    #  同 (ms,kind) 组内序两侧均 = raw 文件行序; 组间序由 (ms,kind) 决定 → 稳定排序后全等
    #  才与引擎输入等价。cancel/fill 事件无 price/otype 键 → get 归一)
    prio = {'add': 0, 'fill': 1, 'cancel': 2}
    evs = sorted(evs, key=lambda e: (e['ms'], prio.get(e['kind'], 9)))
    out = []
    for e in evs:
        out.append((e['kind'], e['ms'], e['id'], e['side'], e.get('price'),
                    e['qty'], e.get('otype')))
    return out


def load_parquet_rows(code_ex, day, tbl):
    """4 表逐 code-day 读耗时测量 (全列, 与事件映射同 scan)"""
    fs = sorted(glob.glob(f'{C.tick_month(tbl, day)}part-*.parquet'))
    if not fs:
        return 0
    t0 = time.time()
    df = (pl.scan_parquet(fs)
          .filter((pl.col('code') == code_ex)
                  & (pl.col('trade_date') == pl.lit(__import__('datetime').date(
                      int(day[:4]), int(day[4:6]), int(day[6:8])))))
          .collect())
    return time.time() - t0, len(df)


def main():
    days = [tuple(sys.argv[i:i + 2]) for i in range(1, len(sys.argv), 2)]
    if not days:
        days = [('000155', '20250822'), ('000155', '20260803'),
                ('000021', '20260706'), ('000021', '20260803'),
                ('000333', '20260513'), ('000333', '20251215'),
                ('600036', '20251215'), ('600036', '20260513'),
                ('600184', '20260803'), ('600184', '20250915'),
                ('688708', '20260821'), ('301632', '20260821'),
                ('000810', '20250915'), ('000887', '20260210')]
    # 缺 zip 的样本剔除 (池按月轮换, 部分 code-day 无源)
    days = [(c, d) for c, d in days
            if os.path.exists(C.code_zip(c, d))]
    rows = []
    t_read_pq, t_map_pq, t_raw = 0.0, 0.0, 0.0
    n_eq = 0
    for code, day in days:
        ex = 'SZ' if code[0] in '03' else 'SH'
        ce = f'{code}.{ex}'
        # parquet 读耗时 (4 表联测, 每表独立 scan — 批算 worker 同款)
        t0 = time.time()
        read_ms = {}
        for tbl in ('orders', 'trades', 'snapshots') if ex == 'SH' else \
                  ('orders', 'trades', 'cancels', 'snapshots'):
            r = load_parquet_rows(ce, day, tbl)
            if isinstance(r, tuple):
                read_ms[tbl] = round(r[0] * 1000, 1)
        t_read_pq += time.time() - t0
        t0 = time.time()
        pe = parquet_events(code, day)
        map_ms = (time.time() - t0) * 1000
        t_map_pq += map_ms / 1000
        t0 = time.time()
        re = raw_events(code, day)
        t_raw += time.time() - t0
        npe, nre = len(pe), len(re)
        ok = norm(pe) == norm(re)
        n_eq += ok
        # 同 (ms,kind) 组内序由保序全列比较覆盖 (== 即含序)
        rows.append(dict(code=code, day=day, n_parquet=npe, n_raw=nre,
                         equal=ok, read_ms=read_ms, map_ms=round(map_ms, 1)))
        print(f'{code}@{day}: raw={nre:,} parquet={npe:,} '
              f'equal={"YES" if ok else "NO"} map={map_ms:.0f}ms '
              f'read={ {k: v for k, v in read_ms.items()} }', flush=True)
    tot = dict(n_days=len(days), n_equal=n_eq,
               read_s=round(t_read_pq, 1), map_s=round(t_map_pq, 1),
               raw_s=round(t_raw, 1))
    print(json.dumps(tot, ensure_ascii=False))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'parity_probe.json'), 'w') as f:
        json.dump(dict(days=rows, total=tot), f, ensure_ascii=False, indent=1)
    if n_eq != len(days):
        sys.exit(f'对等失败 {len(days) - n_eq}/{len(days)} 天')


if __name__ == '__main__':
    main()
