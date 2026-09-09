"""W3 锚定驱动 — 窗口化逐单重放 + 快照锚定 QA（采纳前对拍） + 差量分类桶

开盘模型（本文件冻结，W3 probe_open 实证）:
  - 竞价/撮合段 registry 残单 (价>0, rem>0) 在首张连续快照 ms 物化入簿
    (identity-preserving; engine.materialize_leftovers) → level_materialization 行。
    物化是必须而非可选项: 开盘后 4,456/9,781 残留单收到 fills/cancels (身份 refs 可解析),
    无身份采纳会毁守恒与 ref 解析。
  - 09:25-09:30 静默排队委托的加单消息在开盘后 ≤500ms 滞后回报 (δ-lag):
    首窗 anchor 缺档 → 差量归因桶 (吸收窗 (anchor, anchor+ABSORB] 同价 adds);
    窗口 2 引擎状态自愈 → M1 全匹配。

run_day(events, snaps, full_rows=False, absorb_ms=ABSORB_MS):
  events = 归一化事件 (排序任意; 驱动按 (ms, kind) 确定化); snaps = 快照行 dict 列表。
  全序列事件 ≤ 首锚 pre-replay → 物化 → 逐点 (QA anchors ∪ 分钟边界) 增量 ingest:
    - QA anchor (time_ms ≤ M1_END, 14:57:00 核心段止): pre-adoption 逐侧
      M1a 档位存现 / M1b rank 对齐价梯 / M2 价集缺档+量差+ghost (engine 域内独有档) / δ 归因
    - 分钟边界: 簿面检查点 (px, vol, n_queue) best-first (分钟 1 恒发; 其余窗有事件才发)
  尾段 flush → EOD。M3 由引擎逐 fill 分桶 (整日连续段); M4 引擎 vs qa.ledger 双实现
  逐 id 全等 + 桶对账映射; M6 (full_rows) 行流 fold == 分钟检查点逐字节。

返回 dict: rows(引擎行流, 含物化行) / sweeps / engine / qa{windows, open_materialized,
m3, m4, day, checkpoints, m6}。SNAP_DEPTH 之外引擎深档 (extras/域外) 是正常构造,
不算 ghost。锚定只 QA 不采纳: 引擎状态全程不剪枝不覆盖 (权威簿面)。
"""
from bisect import bisect_right

import config as C
from engine import Engine, _KIND_PRIO
from qa import ledger as L
from qa import metrics as mt


def _ev_key(e):
    return (e['ms'], _KIND_PRIO.get(e['kind'], 9))


def _ladder(engine, side):
    """引擎该侧活档 (px, vol) best-first (bid 降 / ask 升)"""
    items = [(px, lv['vol']) for px, lv in engine.books[side].items()]
    items.sort(key=lambda t: -t[0] if side == 'B' else t[0])
    return items


def _ckpt_dump(engine):
    """分钟簿面检查点: (px, vol, n_queue) best-first, 双侧"""
    out = {}
    for ev_side, key in (('B', 'bid'), ('S', 'ask')):
        items = [(px, lv['vol'], len(lv['queue']))
                 for px, lv in engine.books[ev_side].items()]
        items.sort(key=lambda t: -t[0] if ev_side == 'B' else t[0])
        out[key] = items
    return out


def _qa_window(engine, ms, row, evs, times, absorb_ms):
    """单锚 QA: M1a 存现 / M1b rank 对齐 / M2 价集缺档+量差+ghost / δ 缺档归因

    M1a n_present = 锚档价在引擎全深度档集存现数 (== n_anchor − n_missing);
    rank 对齐 n_match 受 best-edge δ 换位瞬态影响而 n_present 不受 (W3 校准: 门语义)"""
    w = {'ms': ms}
    lo = bisect_right(times, ms)
    hi = bisect_right(times, ms + absorb_ms)
    for side, ev_side in (('bid', 'B'), ('ask', 'S')):
        a_lad = mt.snap_row_to_ladders(row, side)
        e_lad = _ladder(engine, ev_side)
        m = mt.ladder_match(a_lad, e_lad)
        ghost = mt.ghost_levels(e_lad, a_lad)
        eng_px = {p for p, _ in e_lad}
        missing, missing_vol, att = [], 0, 0
        for p, av in a_lad:
            if p in eng_px:
                continue
            missing.append((p, av))
            missing_vol += av
            adds = sum(e['qty'] for e in evs[lo:hi]
                       if e['kind'] == 'add' and e['side'] == ev_side
                       and e['price'] == p)
            att += min(adds, av)          # δ 滞后: 吸收窗 adds 解释 anchor 侧缺档
        vd = 0
        for p, av in a_lad:
            if p in eng_px:
                vd += abs(av - engine.level_vol(ev_side, p))
        w[side] = dict(n_anchor=m['n_anchor'], n_match=m['n_match'],
                       n_present=mt.px_presence(a_lad, e_lad),
                       n_missing=len(missing), missing_vol=missing_vol,
                       attributed_vol=att,
                       unattributed_vol=missing_vol - att,
                       ghosts=list(ghost), vol_delta_sum=vd)
    return w


# ---------- M4: 引擎 vs 账本双实现 ----------

def _m4(engine, evs):
    led = L.ledger(evs)
    mism, n_orders = [], 0
    eng_tot = dict(added=0, filled=0, canceled=0, rem=0)
    for oid, rec in engine._orders.items():
        o = led['orders'].get(oid)
        if o is None:
            mism.append((oid, 'engine_only', rec))
            n_orders += 1
            continue
        for f in ('side', 'price', 'added', 'filled', 'canceled', 'rem'):
            if rec[f] != o[f]:
                mism.append((oid, f, rec[f], o[f]))
                n_orders += 1
                if len(mism) >= 5:
                    break
        for k in eng_tot:
            eng_tot[k] += rec[k]
    led_tot = L.conservation_summary(led['orders'])
    cons = 'PASS' if n_orders == 0 and eng_tot == led_tot else 'FAIL'
    c = led['counters']
    same = (engine.counters['unknown_fill'] == c['unknown_fill'] and
            engine.counters['unknown_cancel'] == c['unknown_cancel'] and
            engine.counters['cancel_excess'] == c['cancel_excess'] and
            engine.counters['dup_add'] == c['dup_add'] and
            engine.counters['fill_excess'] + engine.counters['fill_over_rem']
            == c['fill_excess'])
    return dict(orders=n_orders, mismatch_examples=mism[:5],
                conservation=cons, counters_equal=same,
                bucket_check=dict(unknown_fill=c['unknown_fill'],
                                  fill=c['fill_excess'],
                                  unknown_cancel=c['unknown_cancel'],
                                  cancel_excess=c['cancel_excess'],
                                  dup_add=c['dup_add']))


# ---------- M6: 行流 fold == 分钟检查点 (full_rows 逐字节) ----------

def _merge(rows, sweeps):
    """rows/sweeps 各自 ms 单调 → 归并 (同 ms rows 在前 = 引擎处理序)"""
    r, s = iter(rows), iter(sweeps)
    nr = next(r, None)
    ns = next(s, None)
    while nr is not None or ns is not None:
        if ns is None or (nr is not None and nr['ms'] <= ns['ms']):
            yield nr
            nr = next(r, None)
        else:
            yield ns
            ns = next(s, None)


def _apply(state, x):
    k = x['kind']
    if k == 'sweep':
        state[x['side']].pop(x['price'], None)
        return
    if k in ('add', 'trade', 'cancel', 'level_materialization'):
        if x['prev_vol'] == 0 and x['new_vol'] == 0:
            return                    # registry 观测行: 簿面无效应
        state[x['side']][x['price']] = x['new_vol']
    # 其余行型 (phase/kind_seq 类观测) 簿面无效应


def _vol_eq(state, ck):
    for side, key in (('B', 'bid'), ('S', 'ask')):
        want = {p: v for p, v, _ in ck[key]}
        if state[side] != want:
            return False
    return True


def fold_m6(rows, sweeps, ckpts):
    """逐字节 fold → 'PASS' / 'FAIL' (行流完整重建每检查点簿面)"""
    if not ckpts:
        return 'PASS'
    state = {'B': {}, 'S': {}}
    i = 0
    n = len(ckpts)
    for x in _merge(rows, sweeps):
        while i < n and x['ms'] > ckpts[i]['ms']:
            if not _vol_eq(state, ckpts[i]):
                return 'FAIL'
            i += 1
        _apply(state, x)
    while i < n:                       # 流尽: 终态对照余下检查点
        if not _vol_eq(state, ckpts[i]):
            return 'FAIL'
        i += 1
    return 'PASS'


# ---------- driver ----------

def run_day(events, snaps, full_rows=False, absorb_ms=C.ABSORB_MS):
    """见模块 docstring。返回 rows/sweeps/engine/qa。"""
    evs = sorted(events, key=_ev_key)
    times = [e['ms'] for e in evs]
    n_ev = len(evs)
    engine = Engine(rank_limit=10 ** 9, delta_pct=1e18) if full_rows else Engine()

    # 锚点: time_ms ≥ OPEN, 升序去重 (QA 止于 M1_END: SZ 收盘集合竞价窗不测)
    anchors = []
    for row in snaps:
        ms = int(row['time_ms'])
        if ms >= C.OPEN and (not anchors or anchors[-1][0] != ms):
            anchors.append((ms, row))
    anchors.sort()
    qa_anchors = [(ms, row) for ms, row in anchors if ms <= C.M1_END]
    row_by_ms = dict(anchors)
    first_a = anchors[0][0] if anchors else None

    cursor = bisect_right(times, first_a) if first_a else 0   # 事件 ≤ 首锚
    engine.ingest(evs[:cursor])
    # 物化交叉闸门: 首锚对侧 best (真实开盘簿可证: 越过对侧 best 的残留从不携带)
    gate = None
    fr = row_by_ms.get(first_a) if first_a else None
    if fr is not None:
        b1, a1 = fr.get('bid_p1'), fr.get('ask_p1')
        b1 = int(round(b1)) if b1 else None
        a1 = int(round(a1)) if a1 else None
        if b1 is not None or a1 is not None:
            gate = {'B': a1, 'S': b1}
    mat_rows = engine.materialize_leftovers(first_a, cross_gate=gate) if first_a else []

    # 分钟边界: 到 ≥ max(末事件, 末锚) 的整点 (k=1..; 窗内无行不发, 分钟 1 恒发)
    cap = max((times[-1] if times else 0),
              (anchors[-1][0] if anchors else 0))
    n_min = max(0, (cap - C.OPEN + C.MINUTE_MS - 1) // C.MINUTE_MS)
    minutes = {C.OPEN + C.MINUTE_MS * k for k in range(1, n_min + 1)}
    points = sorted(set(minutes) | {ms for ms, _ in anchors})

    windows, ckpts, day = [], [], dict(n_anchor=0, n_match=0, n_present=0,
                                       missing_vol=0, attributed_vol=0,
                                       unattributed_vol=0, ghost_vol=0)
    qa_ms = {ms for ms, _ in qa_anchors}       # O(1) 成员测试 (points×anchors 线性扫 = 数秒/日)
    prev_cursor = cursor
    for p in points:
        if p < first_a and p not in minutes:
            continue                       # 首锚前的锚已 pre-replay (防重入)
        idx = bisect_right(times, p)
        if idx > cursor:
            engine.ingest(evs[cursor:idx])
        cursor = idx
        if p in minutes:
            if p == C.OPEN + C.MINUTE_MS or idx > prev_cursor:
                d = _ckpt_dump(engine)
                d['ms'] = p
                ckpts.append(d)
        if p <= C.M1_END and p in qa_ms:
            row = row_by_ms[p]
            w = _qa_window(engine, p, row, evs, times, absorb_ms)
            windows.append(w)
            for side in ('bid', 'ask'):
                s = w[side]
                day['n_anchor'] += s['n_anchor']
                day['n_match'] += s['n_match']
                day['n_present'] += s['n_present']
                day['missing_vol'] += s['missing_vol']
                day['attributed_vol'] += s['attributed_vol']
                day['unattributed_vol'] += s['unattributed_vol']
                day['ghost_vol'] += sum(v for _, v in s['ghosts'])
        prev_cursor = idx
    if cursor < n_ev:                      # 尾段 flush
        engine.ingest(evs[cursor:])
    engine.eod()

    open_mat = {'B': [], 'S': []}
    for x in mat_rows:
        open_mat[x['side']].append((x['price'], x['new_vol']))
    open_mat['B'].sort(key=lambda t: -t[0])
    open_mat['S'].sort(key=lambda t: t[0])

    missing = day['missing_vol']
    qa = dict(windows=windows, open_materialized=open_mat,
              m3=dict(engine.m3), m4=_m4(engine, evs), day=day,
              checkpoints=ckpts,
              m6=fold_m6(engine.events, engine.sweeps, ckpts)
              if full_rows else 'SKIP')
    day['delta_attribution'] = (day['attributed_vol'] / missing
                                if missing else 1.0)
    return dict(rows=engine.events, sweeps=engine.sweeps, engine=engine, qa=qa)
