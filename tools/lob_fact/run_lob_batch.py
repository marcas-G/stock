#!/usr/bin/env python
"""W4 批算 — 纯逻辑层 (映射/日表/日门/月门; TDD) + W4c 批算编排 (同文件下半)

纯逻辑层 (无 I/O, 直接可测):
  orders_to_events / trades_to_events / cancels_to_events / parquet_events
    tick_fact parquet 行 → 归一化事件 (W4a parity_probe 14/14 逐事件字节对等语义;
    SH A→add D→cancel S→skip; SZ 0/1/U 全收 add; qty<=0 跳过; trades 按正 ref 拆
    fill (bid 先发); cancels side 0=B 1=S)
  day_tables(code, day, res)  anchoring.run_day 返回 → lob_events/lob_sweep_meta/
    lob_checkpoints 三表 (polars DataFrame)。观测行 (registry 段 prev==new==0 或
    phase != continuous) 不入 events 表 — 表行 = 连续段簿面绝对量流契约
    (fold 语义; 状态全深度, 行带 band 抑制 — W2 冻结)。seq = 1..n 按引擎事件序。
  day_gate(res)  日门 (batch 版, 无 m6): presence = n_present/n_anchor 圆整 5 位
    ≥ GATE_PRES (n_anchor=0 → vacuous 1.0) 且 M4 conservation PASS & orders==0 &
    counters_equal; n_present>n_anchor 数据错拒。与 W3 measure_w3.verdict 同口径
    (少 m6: 生产 band 行 m6='SKIP' 不参与日门)。
  month_gate(day_rows)  月聚合: 锚定日 (n_anchor>0) 交 measure_w3.m1a_gate (SZ/SH
    池化 + 逐日, 单点), vacuous 日独立计数; ok = 池化门 ∧ 逐日门 ∧ 全部日行门。

批算编排 (W4c, 本文件下半 + __main__): date-major 读 + ProcessPool(spawn) +
flock 单实例 + 900s stall 看门狗 + state.json 断点 (month→date) + MemAvailable
节流 + RSS 30s 审计 + worker 直写 (table, date) part 文件 (唯一 tmp + os.replace
原子) + conversion_manifest 输入守卫 + 月 _SUCCESS。
"""
import polars as pl

from measure_w3 import GATE_PRES, m1a_gate

# ---------- 表 → 事件映射 (W4a 对等语义) ----------

def _ex_of(code):
    """code → 'SZ'/'SH' (裸码前缀或后缀; tick_fact code 列存后缀形 '000155.SZ')"""
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
    ex = _ex_of(code)
    evs = orders_to_events(rows_o, ex) + trades_to_events(rows_t)
    if ex == 'SZ':
        evs += cancels_to_events(rows_c)
    return evs


# ---------- 日表构建 (schema 契约; 列序 = 行序契约) ----------

COL_EVENTS = [('code', pl.Utf8), ('trade_date', pl.Date), ('time_ms', pl.Int32),
              ('seq', pl.Int64), ('kind', pl.Utf8), ('phase', pl.Utf8),
              ('side', pl.Utf8), ('price_x10000', pl.Int64),
              ('prev_vol', pl.Int64), ('new_vol', pl.Int64),
              ('qty', pl.Int64), ('id', pl.Int64), ('otype', pl.Utf8)]
COL_SWEEP = [('code', pl.Utf8), ('trade_date', pl.Date), ('time_ms', pl.Int32),
             ('seq', pl.Int64), ('phase', pl.Utf8), ('side', pl.Utf8),
             ('price_x10000', pl.Int64), ('vol_before', pl.Int64),
             ('tail_order', pl.Int64), ('tail_resid', pl.Int64)]
COL_CKPT = [('code', pl.Utf8), ('trade_date', pl.Date), ('time_ms', pl.Int32),
            ('seq', pl.Int64), ('side', pl.Utf8), ('price_x10000', pl.Int64),
            ('vol', pl.Int64), ('n_queue', pl.Int32)]

_ROW_KEY = {'kind': 'kind', 'phase': 'phase', 'side': 'side',
            'price_x10000': 'price', 'qty': 'qty', 'id': 'id'}


def day_tables(code, day, res):
    """anchoring.run_day 返回 → {lob_events, lob_sweep_meta, lob_checkpoints}。

    events: res['rows'] 中连续段行 (观测行 prev==new==0/非 continuous phase 不入表;
    含 level_materialization — 开盘簿面物化 = 表内绝对量锚); seq = 引擎事件序 1..n。
    sweep_meta: res['sweeps'] 原序 (ms 单调); ckpts: qa.checkpoints 逐分钟逐侧
    best-first (bid 先行) long 行, seq 全程连续。三表 schema/列序 = 上方 COL_*。
    """
    rows = [r for r in res['rows']
            if r.get('phase') == 'continuous'
            and not (r['prev_vol'] == 0 and r['new_vol'] == 0)]
    ev = _fill(rows, code, day, COL_EVENTS)
    sw = _fill(res['sweeps'], code, day, COL_SWEEP)
    ck_rows = []
    for ck in res['qa']['checkpoints']:
        for side in ('bid', 'ask'):
            for px, vol, nq in ck[side]:
                ck_rows.append(dict(ms=ck['ms'], side='B' if side == 'bid' else 'S',
                                    price=px, vol=vol, n_queue=nq))
    ck = _fill(ck_rows, code, day, COL_CKPT)
    return {'lob_events': ev, 'lob_sweep_meta': sw, 'lob_checkpoints': ck}


def _fill(rows, code, day, schema):
    """schema 列序 + 逐列取值 (seq 独立; 空表保持 schema dtype 非退化)"""
    out = {}
    for name, dt in schema:
        if name == 'time_ms':
            v = [int(r['ms']) for r in rows]
        elif name == 'trade_date':
            v = [day] * len(rows)
        elif name == 'seq':
            v = list(range(1, len(rows) + 1))
        elif name == 'code':
            v = [code] * len(rows)
        else:
            v = [r.get(_ROW_KEY.get(name, name)) for r in rows]
        out[name] = pl.Series(name, v, dtype=dt, strict=False)
    return pl.DataFrame(out, schema=[n for n, _ in schema])


# ---------- 日门 / 月门 (W3 门语义; batch 版日门无 m6) ----------

def day_gate(res):
    """日门 (生产 batch 版): M1a 存现 ≥ GATE_PRES (n_anchor=0 → vacuous 1.0) +
    M4 conservation PASS & 逐单 0 mismatch & counters_equal。presence 圆整 5 位
    (W3 measure_w3.verdict 同口径)。返回 dict(ok, presence, vacuous, reasons)"""
    d = res['qa']['day']
    n_p, n_a = d['n_present'], d['n_anchor']
    reasons = []
    if n_p > n_a:
        reasons.append('presence_invalid')
    vacuous = n_a == 0
    presence = 1.0 if vacuous else round(n_p / n_a, 5)
    if presence < GATE_PRES:
        reasons.append('m1a_presence')
    m4 = res['qa']['m4']
    if m4['conservation'] != 'PASS':
        reasons.append('m4_conservation')
    if m4['orders'] != 0:
        reasons.append('m4_orders')
    if not m4['counters_equal']:
        reasons.append('m4_counters')
    return dict(ok=not reasons, presence=presence, vacuous=vacuous,
                reasons=reasons)


def month_gate(day_rows):
    """月门聚合: 日行 (code/day/m1/gate dicts) → m1a_gate 池化 (锚定日; vacuous
    日独立计数, 不入池 — 无锚日不给池贡献分母) + 逐日行门全 PASS。空月 ok。"""
    anchored = [r for r in day_rows
                if not r['gate'].get('vacuous') and r['m1']['n_anchor'] > 0]
    vac = [r for r in day_rows if r['gate'].get('vacuous')]
    gate = m1a_gate(anchored)
    ok = gate['ok'] and all(r['gate'].get('ok', True) for r in day_rows)
    return dict(n_days=len(day_rows), n_anchored=len(anchored),
                n_vacuous=len(vac), gate=gate, ok=ok)


# ---------- W4c 编排层 (batch worker/main; 见模块 docstring) ----------

if __name__ == '__main__':
    raise SystemExit('run_lob_batch: 编排层在 W4c 实现 (worker/main)')
