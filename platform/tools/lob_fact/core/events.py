"""表 → 事件映射（W4a 对等语义）：纯函数，无 I/O。

R11 从 `pipeline/run_lob_batch.py` 移入 `core/`——原先 `core/factor_panel.py`（独立重放）
要复用这几个函数，只能反向 import 驱动脚本（库依赖可执行文件）。移入 core 后两侧都从
叶子引用，方向正确；`run_lob_batch` 仍 re-export 同名函数，历史调用点（含测试的 `R.xxx`）不变。
"""

from __future__ import annotations


def ex_of(code):
    """code → 'SZ'/'SH'（裸码前缀或后缀；tick_fact code 列存后缀形 '000155.SZ'）。"""
    return 'SZ' if code.lstrip('0123456789') == '.SZ' or code.startswith(('0', '3')) \
        else 'SH'


def orders_to_events(rows, ex):
    """orders 行 → (add/cancel, aux) 事件。SH: A→add D→cancel S→skip;
    SZ: 全行 add (0/1/U, 引擎按价裁决)。bs→side, exch_order_no→id,
    order_type→otype (add), qty<=0 跳过 (streams n_bad_row 语义, 坏行不产事件)。"""
    evs = []
    for r in rows:
        t = r['order_type']
        qty = r['volume']
        if t == 'S':                        # SH 杂项: 不进簿不进流
            continue
        if qty <= 0:
            continue                        # streams n_bad_row 语义 (哨兵/坏行)
        side = r['bs']
        if ex == 'SH':
            if t == 'A':
                evs.append(dict(kind='add', ms=int(r['time_ms']),
                                id=r['exch_order_no'], side=side,
                                price=int(r['price_x10000']), qty=int(qty),
                                otype='A'))
            elif t == 'D':
                evs.append(dict(kind='cancel', ms=int(r['time_ms']),
                                id=r['exch_order_no'], qty=int(qty), side=side))
        else:                               # SZ: 类型 0/1/U 全收 (引擎按价裁决)
            evs.append(dict(kind='add', ms=int(r['time_ms']),
                            id=r['exch_order_no'], side=side,
                            price=int(r['price_x10000']), qty=int(qty),
                            otype=t))
    return evs


def trades_to_events(rows):
    """trades 行 → fill 事件: 双侧正 ref 各发一条 (bid 先发); 双零行跳过
    (SH 先成交后报空引用类 — 正确忽略, 簿面由消息重建不受影响)"""
    evs = []
    for r in rows:
        bid, ask, qty = r['bid_seq'], r['ask_seq'], r['volume']
        if bid == 0 and ask == 0:
            continue
        px = int(r['price_x10000'])
        if bid > 0:
            evs.append(dict(kind='fill', ms=int(r['time_ms']), id=bid,
                            qty=int(qty), side='B', price=px))
        if ask > 0:
            evs.append(dict(kind='fill', ms=int(r['time_ms']), id=ask,
                            qty=int(qty), side='S', price=px))
    return evs


def cancels_to_events(rows):
    """cancels 行 (SZ-only) → cancel 事件: side 0=B 1=S, id=order_ref"""
    return [dict(kind='cancel', ms=int(r['time_ms']), id=r['order_ref'],
                 qty=int(r['volume']), side='B' if r['side'] == 0 else 'S')
            for r in rows]


def parquet_events(code, day, rows_o, rows_t, rows_c):
    """code-day 全事件: orders+trades (+SZ cancels) 块序拼接; day 仅信息参数
    (行已含 trade_date; 引擎 ingest 内部按 (ms, kind) 确定性排序兜底)"""
    ex = ex_of(code)
    evs = orders_to_events(rows_o, ex) + trades_to_events(rows_t)
    if ex == 'SZ':
        evs += cancels_to_events(rows_c)
    return evs
