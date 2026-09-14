"""逐单账本（W1；M4 逐单守恒 + W1 撤单量语义/ref 解析率测量共用）

以订单 id（SZ 交易所委托号 / SH 交易所委托号）为键的纯函数记账：
  add    — 登记委托（同 id 二次 add → dup_add 违规）
  fill   — 成交消费（未知 id → unknown_fill；超剩余 → clamp + fill_excess）
  cancel — 撤单消费 min(qty, 剩余)（部分撤单语义；未知 → unknown_cancel；超量 → clamp + cancel_excess）
每单守恒恒等式 added == filled_actual + canceled_actual + rem（违规超量部分单独计数，不静默）。

确定性: 事件先按 ms 升序，同 ms 内 kind 序 add < fill < cancel（文档化 tie-break，
同 ms 同单的歧义事件由锚定吸收桶兜底）。调用方须先按此规则排好 events（ledger 内不再排序，
由 stream 构造器保证——构造器单测覆盖）。设计意图: 账本不依赖簿面价档，纯 id 算术,
ref 侧别（叫买=bid 侧 / 叫卖=ask 侧）由调用方映射。
"""
from dataclasses import dataclass, field

ZERO = dict(unknown_fill=0, fill_excess=0, unknown_cancel=0,
            cancel_excess=0, dup_add=0)


def ev_add(oid, side, price, otype, qty, ms):
    return dict(kind='add', id=oid, side=side, price=price, type=otype,
                qty=qty, ms=ms)


def ev_fill(oid, qty, ms):
    return dict(kind='fill', id=oid, qty=qty, ms=ms)


def ev_cancel(oid, qty, ms):
    return dict(kind='cancel', id=oid, qty=qty, ms=ms)


def _order(side, price, otype):
    return dict(side=side, price=price, type=otype,
                added=0, filled=0, canceled=0, rem=0)


def ledger(events):
    """事件流 → (orders, counters, cancel_log)；内部按 (ms, kind 序 add<fill<cancel) 稳定排序

    同 ms 同单的歧义事件由此确定性化（文档化 tie-break, 锚定吸收桶兜底）。

    orders:   {id: {side, price, type, added, filled, canceled, rem}}
    counters: ZERO 结构违规计数
    cancel_log: 每条 cancel 的 (id, qty, rem_before, type, side) — W1 撤单量语义分布
    """
    KIND_PRI = {'add': 0, 'fill': 1, 'cancel': 2}
    events = sorted(events, key=lambda e: (e['ms'], KIND_PRI[e['kind']]))
    orders, ctr, clog = {}, dict(ZERO), []
    for ev in events:
        k, oid, qty = ev['kind'], ev['id'], ev['qty']
        o = orders.get(oid)
        if k == 'add':
            if o is not None:
                ctr['dup_add'] += 1
                continue
            orders[oid] = _order(ev['side'], ev['price'],
                                 ev.get('type', ev.get('otype')))
            o = orders[oid]
            o['added'] += qty
            o['rem'] += qty
        elif k == 'fill':
            if o is None:
                ctr['unknown_fill'] += 1
            else:
                _consume(o, qty, ctr, 'filled', 'fill_excess')
        elif k == 'cancel':
            if o is None:
                ctr['unknown_cancel'] += 1
            else:
                rem_before = o['rem']
                _consume(o, qty, ctr, 'canceled', 'cancel_excess')
                clog.append(dict(id=oid, qty=qty, rem_before=rem_before,
                                 type=o['type'], side=o['side'], ms=ev['ms']))
        else:
            raise ValueError(f'unknown kind {k}')
    return dict(orders=orders, counters=ctr, cancel_log=clog)


def _consume(o, qty, ctr, field, ctr_key):
    """消费 min(qty, rem) 到 field(filled/canceled)；超剩余计入 ctr[ctr_key]"""
    actual = min(qty, o['rem'])
    if qty > o['rem']:
        ctr[ctr_key] += 1
    o[field] += actual
    o['rem'] -= actual
    return actual


def conservation_summary(orders):
    """聚合守恒: Σadded == Σfilled + Σcanceled + Σrem（违规单独计，此处恒成立为前置断言）"""
    s = dict(added=0, filled=0, canceled=0, rem=0)
    for o in orders.values():
        for k in s:
            s[k] += o[k]
    return s
