"""W3 锚定驱动 TDD（红→绿；合成日驱动金样 + 差量分类桶）

anchoring.run_day 语义（规格 §3.1/3.3 + W3 开盘模型冻结）:
  1. 事件 ≤ 首锚 ms 先重放（竞价/撮合段 registry 消费）
  2. 开盘物化: registry 竞价残留 (价>0, rem>0) 逐单入簿 (身份保留) + level_materialization 行
  3. 逐窗 (prev_anchor, anchor] 重放 → pre-adoption QA (M1 价梯/M2 量差/ghost)
     → 差量归因: 吸收窗 (anchor, anchor+ABSORB] 内同价 adds 解释 anchor 侧缺档 (δ 自愈)
  4. 分钟检查点 (整分钟, 窗内行非空才发): 引擎簿面 (px, vol, n_queue) best-first
  5. 行流 = 引擎事件行 + 物化行; M6 (full_rows) = 行重放 == 检查点逐字节 (vol 全等)

M3/M4: M3 打印合法桶由引擎在 ingest 内逐 fill 分桶 (metrics 同口径, 仅连续段);
M4 = 引擎 recs vs qa.ledger 双实现逐 id 全等 + 桶对账映射
  (fill_excess_eng + fill_over_rem_eng == ledger fill_excess)。

存根击穿: 每断言从合成日真实数字推导; 任何硬编码 QA 汇总必败。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import Engine
import anchoring as A
import config as C

OPEN = C.OPEN
AM = C.AUCTION_MATCH
OPEN_DAY = [
    dict(kind='add', id=1, ms=33_500_000, side='B', price=122800, qty=1100, otype='0'),
    dict(kind='add', id=2, ms=33_500_100, side='S', price=123100, qty=600, otype='0'),
    dict(kind='add', id=3, ms=33_500_200, side='B', price=122900, qty=300, otype='0'),
    dict(kind='fill', id=1, ms=AM + 1, qty=400, price=122800),   # 9:25 撮合
    dict(kind='cancel', id=2, ms=AM + 2, qty=100),               # 9:25 后撤 (撮合段)
]


def snap(ms, bid, ask):
    """快照行 (tick_fact snapshots 风格: bid_p1.. / ask_p1..)"""
    row = dict(time_ms=ms)
    for i, (p, v) in enumerate(bid, 1):
        row[f'bid_p{i}'], row[f'bid_v{i}'] = p, v
    for i, (p, v) in enumerate(ask, 1):
        row[f'ask_p{i}'], row[f'ask_v{i}'] = p, v
    return row


def add(oid, ms, side, px, qty, otype='0'):
    return dict(kind='add', id=oid, ms=ms, side=side, price=px, qty=qty,
                otype=otype)


def fill(oid, ms, qty, px):
    return dict(kind='fill', id=oid, ms=ms, qty=qty, refs=[oid], price=px)


def cancel(oid, ms, qty):
    return dict(kind='cancel', id=oid, ms=ms, qty=qty)


# ---------- 场景 1: 开盘物化 + 首窗对拍 ----------

def test_open_materialization_matches_snapshot_window1():
    """残留 B700@122800/B300@122900/S500@123100 物化入簿 (B700=1100−撮合400,
    S500=600−撤100); 首窗对拍 M1 全 match M2 δ=0; 行流含 3 条 level_materialization;
    open 桶干净; 身份保留"""
    snaps = [snap(OPEN, [(122900, 300), (122800, 700)], [(123100, 500)])]
    r = A.run_day(OPEN_DAY, snaps)
    q = r['qa']
    w = q['windows'][0]
    for side in ('bid', 'ask'):
        assert w[side]['n_missing'] == 0 and w[side]['ghosts'] == []
        assert w[side]['vol_delta_sum'] == 0, (side, w[side])
    assert w['bid']['n_anchor'] == 2 and w['bid']['n_match'] == 2
    assert w['ask']['n_anchor'] == 1 and w['ask']['n_match'] == 1
    assert q['open_materialized']['B'] == [(122900, 300), (122800, 700)]
    assert q['open_materialized']['S'] == [(123100, 500)]
    ml = [x for x in r['rows'] if x['kind'] == 'level_materialization']
    assert {(x['side'], x['price'], x['new_vol']) for x in ml} == \
        {('B', 122900, 300), ('B', 122800, 700), ('S', 123100, 500)}
    assert r['engine'].order(1)['booked'] and r['engine'].order(2)['booked']
    assert r['engine'].level_vol('B', 122800) == 700
    assert r['engine'].level_vol('S', 123100) == 500


def test_window2_replay_advances_and_matches():
    """窗口 2 (含窗口 1 后 add/fill/cancel) → 引擎状态推进, 对拍匹配"""
    evs = OPEN_DAY + [
        add(4, OPEN + 1000, 'B', 122900, 200),      # 同价加厚
        fill(2, OPEN + 2000, 150, 123100),          # 吃 ask 残留单 id2 (500→350)
        add(6, OPEN + 2500, 'S', 123200, 400),
    ]
    snaps = [snap(OPEN, [(122900, 300), (122800, 700)], [(123100, 500)]),
             snap(OPEN + 3000, [(122900, 500), (122800, 700)],
                  [(123100, 350), (123200, 400)])]
    r = A.run_day(evs, snaps)
    w2 = r['qa']['windows'][1]
    assert w2['bid']['n_match'] == 2 and w2['ask']['n_match'] == 2
    assert w2['bid']['n_missing'] == 0
    assert r['engine'].level_vol('S', 123100) == 350
    assert r['engine'].level_vol('S', 123200) == 400


# ---------- 场景 2: δ 滞后 (开盘排队消息滞后) ----------

def test_delta_lag_queue_self_heals_and_attributes():
    """快照含 09:30:00.000 生效的排队单 (消息 34200050 才报): 窗 1 缺档归因 δ;
    窗 2 自愈后全匹配; 归因率 1.0"""
    evs = OPEN_DAY + [add(7, OPEN + 50, 'B', 123000, 300)]
    snaps = [snap(OPEN, [(123000, 300), (122900, 300), (122800, 700)],
                  [(123100, 500)]),
             snap(OPEN + 3000, [(123000, 300), (122900, 300), (122800, 700)],
                  [(123100, 500)])]
    r = A.run_day(evs, snaps)
    q = r['qa']
    w1, w2 = q['windows'][0], q['windows'][1]
    assert w1['bid']['n_anchor'] == 3 and w1['bid']['n_missing'] == 1
    assert w1['bid']['missing_vol'] == 300
    assert w1['bid']['attributed_vol'] == 300      # 吸收窗内 add300 解释
    assert w1['bid']['unattributed_vol'] == 0
    assert w2['bid']['n_match'] == 3 and w2['bid']['n_missing'] == 0
    assert q['day']['delta_attribution'] == 1.0


# ---------- 场景 3: ghost (引擎在 anchor 价域内独有档, 无消息解释) ----------

def test_ghost_level_bucketed_not_silent():
    """引擎档 122880 (域内) 快照无且无撤消消息: ghost 桶记录量; 归因未动;
    ghost_vol 在日汇总显式计数 (非静默, M2 分类桶)"""
    evs = OPEN_DAY + [
        add(8, OPEN + 100, 'B', 122880, 120),      # 消息生效
        fill(8, OPEN + 150, 60, 122880),           # 部分成交仍余 60
    ]
    # 快照 122880 不存在且后续无撤消消息 → 引擎独有 60 股 (anchor 价域内)
    snaps = [snap(OPEN, [(122900, 300), (122800, 700)], [(123100, 500)]),
             snap(OPEN + 3000, [(122900, 300), (122800, 700)],
                  [(123100, 500)])]
    r = A.run_day(evs, snaps)
    q = r['qa']
    assert (122880, 60) in q['windows'][1]['bid']['ghosts']
    assert q['day']['ghost_vol'] == 60
    assert q['day']['unattributed_vol'] == 0       # ghost 独立于缺档归因桶
    # M1 不受 ghost 拖累: 122900/122800 仍 match (价梯率 2/2 首窗)
    assert q['windows'][0]['bid']['n_match'] == 2


# ---------- 场景 4: M3 打印合法性 (引擎逐 fill 分桶) ----------

def test_m3_buckets_exact_counts_full_day():
    """连续段打印价桶精确计数 (in_spread 8 / below 2 / above 1 / no_quote 1),
    撮合段打印不入 m3"""
    evs = OPEN_DAY + [
        add(10, OPEN + 1000, 'B', 122800, 100),
        add(11, OPEN + 1400, 'S', 123100, 400),
        fill(2, OPEN + 1500, 100, 123100),          # in_spread (吃残留)
        fill(11, OPEN + 1600, 400, 123100),         # in_spread (同价清排队单)
        fill(130, OPEN + 1700, 60, 122700),         # below_bid (未知单仍分桶)
        fill(140, OPEN + 1800, 700, 123500),        # above_ask
        fill(3, OPEN + 1900, 60, 122900),           # in_spread (吃 bid 残留)
        add(16, OPEN + 1950, 'S', 123300, 500),
        fill(2, OPEN + 2000, 400, 123100),          # in_spread → 123100 扫空 sweep
        fill(6, OPEN + 2100, 50, 123200),           # in_spread (未知, 中价带)
        fill(20, OPEN + 2200, 30, 122750),          # below_bid (未知, 出 ε=1tick 带)
        fill(3, OPEN + 2400, 240, 122900),          # in_spread → 122900 扫空
        fill(10, OPEN + 2500, 100, 122800),         # in_spread (bestB 消费)
        fill(1, OPEN + 2800, 700, 122800),          # in_spread → B 侧扫空
        fill(28, OPEN + 2900, 5, 122800),           # no_quote (B 空, 未知)
    ]
    snaps = [snap(OPEN, [(122900, 300), (122800, 700)], [(123100, 500)])]
    r = A.run_day(evs, snaps)
    assert r['qa']['m3'] == dict(print_in_spread=8, print_below_bid=2,
                                 print_above_ask=1, print_no_quote=1)
    assert r['engine'].counters['unknown_fill'] == 5   # 130/140/6/20/28
    assert r['engine'].level_vol('B', 122800) == 0     # sweep 生效


# ---------- 场景 5: M4 逐单守恒 (引擎 vs 账本双实现) ----------

def test_m4_conservation_identity_engine_vs_ledger():
    """双实现逐 id 全等 (side/price/added/filled/canceled/rem); 桶对账映射:
    unknown_fill/unknown_cancel/dup_add/cancel_excess 直接相等,
    fill 桶 = fill_excess_eng + fill_over_rem_eng == ledger fill_excess"""
    evs = OPEN_DAY + [
        add(4, OPEN + 1000, 'B', 122900, 200),
        add(5, OPEN + 1500, 'S', 123100, 300),
        fill(5, OPEN + 2000, 300, 123100),          # 扫空 id5 → 死单
        add(6, OPEN + 2500, 'S', 123200, 400),
        fill(6, OPEN + 3000, 400, 123200),          # 扫空 → sweep
        cancel(6, OPEN + 3500, 100),                # 死单撤 → cancel_excess
        fill(5, OPEN + 4000, 10, 123100),           # 死单成交 → fill_excess
        fill(99, OPEN + 4500, 10, 123200),          # unknown fill
        cancel(777, OPEN + 5000, 10),               # unknown cancel
        add(2, OPEN + 5500, 'B', 122800, 100),      # dup add (保留首 add)
        add(8, OPEN + 6000, 'S', 123300, 100),
        fill(8, OPEN + 6500, 250, 123300),          # 活单超量 → fill_over_rem
    ]
    snaps = [snap(OPEN, [(122900, 300), (122800, 700)], [(123100, 500)])]
    r = A.run_day(evs, snaps)
    m4 = r['qa']['m4']
    assert m4['orders'] == 0 and m4['mismatch_examples'] == []
    assert m4['conservation'] == 'PASS'
    assert m4['counters_equal'] is True
    assert m4['bucket_check'] == dict(unknown_fill=1, fill=2, unknown_cancel=1,
                                      cancel_excess=1, dup_add=1)
    assert r['engine'].counters['fill_excess'] == 1
    assert r['engine'].counters['fill_over_rem'] == 1
    assert r['engine'].order(2)['price'] == 123100  # dup add 保留首 add 身份


# ---------- 场景 6: 分钟检查点 + M6 行重放 (full_rows 逐字节) ----------

def _full_day_events():
    """开盘物化 + 连续段两分钟窗事件 (含同价加厚/档耗尽 sweep/新深档)"""
    return OPEN_DAY + [
        add(20, OPEN + 1000, 'B', 122900, 500),    # 122900 加厚 → 800
        fill(20, OPEN + 61_000, 300, 122900),      # 09:31:01 消费 300 → 500
        add(22, OPEN + 61_500, 'S', 123100, 200),  # ask 加厚 → 700
        cancel(22, OPEN + 62_000, 200),            # 排队单撤净 → vol 500
        add(23, OPEN + 120_000, 'B', 123300, 90),  # 09:32:00.000 整点 → 新档
        fill(23, OPEN + 121_000, 50, 123300),      # → 40
    ]


def test_checkpoints_minute_boundaries_and_m6_replay():
    """检查点落在有行分钟的整点 (09:31:00/09:32:00/09:33:00), 簿面 (px, vol, n_queue);
    full_rows 行重放 == 检查点逐字节"""
    snaps = [snap(OPEN, [(122900, 300), (122800, 700)], [(123100, 500)])]
    r = A.run_day(_full_day_events(), snaps, full_rows=True)
    q = r['qa']
    ms_list = [c['ms'] for c in q['checkpoints']]
    assert ms_list == [OPEN + 60_000, OPEN + 120_000, OPEN + 180_000]
    c1, c2, c3 = q['checkpoints']
    b1 = {p: v for p, v, _ in c1['bid']}
    a1 = {p: v for p, v, _ in c1['ask']}
    assert b1 == {122900: 800, 122800: 700}        # 加厚 300+500
    assert a1 == {123100: 500}
    assert {p: n for p, _, n in c1['bid']}[122900] == 2   # id3 + id20
    b2 = {p: v for p, v, _ in c2['bid']}
    assert b2 == {123300: 90, 122900: 500, 122800: 700}
    assert {p: v for p, v, _ in c2['ask']} == {123100: 500}
    # 09:32:01 的 fill23 属分钟 (09:32, 09:33] → ckpt3 含消费后 40
    b3 = {p: v for p, v, _ in c3['bid']}
    assert b3 == {123300: 40, 122900: 500, 122800: 700}
    assert q['m6'] == 'PASS'                       # 行重放逐字节 == 检查点
    # 终态 (最后检查点后事件) 与引擎一致
    assert r['engine'].level_vol('B', 123300) == 40


def test_checkpoints_skip_empty_minutes():
    """无行的整分钟不发检查点; 分钟 1 因物化行 (ms=OPEN) 恒发"""
    evs = OPEN_DAY + [add(30, OPEN + 65_000, 'B', 122800, 50)]
    snaps = [snap(OPEN, [(122900, 300), (122800, 700)], [(123100, 500)])]
    r = A.run_day(evs, snaps, full_rows=True)
    ms_list = [c['ms'] for c in r['qa']['checkpoints']]
    assert ms_list == [OPEN + 60_000, OPEN + 120_000]  # 物化行占分钟1; +65s 事件占分钟2
    assert {p: v for p, v, _ in r['qa']['checkpoints'][1]['bid']} == \
        {122900: 300, 122800: 750}


def test_full_day_auction_and_post_ignored_m1_no_crash():
    """撮合段加单/收盘 post 段事件日跑通: 首锚前重放 → 物化 → 尾段 ingest 到 EOD 不崩;
    eod 分桶守恒; post 段打印不入 m3; post 加单 registry-only (W2 语义 book 仅连续段)"""
    evs = OPEN_DAY + [
        add(40, AM + 5000, 'B', 122900, 100),      # 撮合段加单 → 开盘物化
        add(50, 54_000_000, 'S', 123100, 80),      # 15:00:00.000 post 加单 (registry)
        fill(50, 54_000_500, 80, 123100),          # post 打印 → registry 消费
    ]
    snaps = [snap(OPEN, [(122900, 300), (122800, 700)], [(123100, 500)]),
             snap(OPEN + 3000, [(122900, 300), (122800, 700)], [(123100, 500)])]
    r = A.run_day(evs, snaps)
    q = r['qa']
    assert q['m6'] == 'SKIP'                       # 默认 band 行不全 → M6 不适用
    e = r['engine']
    assert e.eod_summary == dict(auction_rem=0, unbooked_rem=0,
                                 continuous_rem=300 + 700 + 500 + 100)
    assert e.order(50)['booked'] is False          # post 加单 registry-only
    assert e.order(40)['booked'] is True           # 撮合段残单开盘物化入簿
    assert e.order(50)['rem'] == 0                 # post 打印消费成功
    assert sum(e.m3.values()) == 0                 # ≥CLOSE 打印不入 m3


# ---------- M1a 档位存现 (窗级 + 日级聚合; W3 校准门口径) ----------

def test_window_and_day_px_presence():
    """窗 2: 引擎 best-edge extra 123000 (快照价域外 → ghost 不算的悖论窗结构):
    rank 对齐 0/2 (adjacent 换位) 但锚档 122900/122800 全部存现 → n_present=2;
    同窗 ask 123200 真缺 (无消息) → n_present=1 (== n_anchor − n_missing);
    day 聚合 = 逐窗双侧和"""
    evs = OPEN_DAY + [add(9, OPEN + 100, 'B', 123000, 500)]   # extra best 永存 (δ 瞬态类)
    snaps = [snap(OPEN, [(122900, 300), (122800, 700)], [(123100, 500)]),
             snap(OPEN + 3000, [(122900, 300), (122800, 700)],
                  [(123100, 500), (123200, 400)])]           # 123200 无 add 消息 → 引擎缺
    r = A.run_day(evs, snaps)
    w1, w2 = r['qa']['windows'][0], r['qa']['windows'][1]
    assert w1['bid']['n_present'] == 2 and w1['bid']['n_match'] == 2   # 窗1 无 extra
    assert w2['bid']['n_present'] == 2 and w2['bid']['n_match'] == 0   # 悖论窗: 存现全但 rank 0
    assert w2['bid']['ghosts'] == []                                   # 123000 在锚价域外
    assert w2['ask']['n_present'] == 1 and w2['ask']['n_missing'] == 1 # 真缺档减存现
    d = r['qa']['day']
    assert d['n_present'] == 6 and d['n_anchor'] == 7          # 2+1 + 2+1 双侧逐窗和
    assert d['n_present'] == d['n_anchor'] - sum(
        w[s]['n_missing'] for w in r['qa']['windows'] for s in ('bid', 'ask'))


# ---------- 场景 3: 物化交叉闸门 (首锚对侧 best) ----------

def test_cross_gate_keeps_first_window_clean_and_identity():
    """首锚 ask best 41.75 / bid best 41.72 → 物化闸门拦下 B425700×600 (竞价
    撮合 400 后余) 与 S375700×600; 首窗 M1 全 match 无 ghost 无 missing;
    被拦残留后续 fill 按 id 消费且全程不建档"""
    evs = [
        dict(kind='add', id=1, ms=33_500_000, side='B', price=425700, qty=1000,
             otype='0'),                      # 竞价残留: 撮合 400 → 余 600 (越过锚 ask)
        dict(kind='add', id=2, ms=33_500_100, side='S', price=375700, qty=600,
             otype='0'),                      # 低于锚 bid best → 拦
        dict(kind='add', id=3, ms=33_500_200, side='B', price=417200, qty=800,
             otype='0'),
        dict(kind='add', id=4, ms=33_500_300, side='S', price=417500, qty=700,
             otype='0'),
        dict(kind='fill', id=1, ms=AM + 1, qty=400, price=417400, refs=[1]),
    ]
    snaps = [snap(OPEN, [(417200, 800)], [(417500, 700)]),
             snap(OPEN + 3_000, [(417200, 800)], [(417500, 700)])]
    r = A.run_day(evs, snaps)
    q = r['qa']
    for w in q['windows']:
        assert w['bid']['n_match'] == 1 and w['ask']['n_match'] == 1
        assert w['bid']['ghosts'] == [] and w['ask']['ghosts'] == []
        assert w['bid']['n_missing'] == 0 and w['ask']['n_missing'] == 0
    om = q['open_materialized']
    assert om['B'] == [(417200, 800)] and om['S'] == [(417500, 700)]
    eng = r['engine']
    assert eng.books['B'].get(425700) is None and eng.books['S'].get(375700) is None
    assert eng.order(1)['rem'] == 600 and eng.order(1)['booked'] is False
    ml = [x for x in r['rows'] if x['kind'] == 'level_materialization']
    assert {(x['side'], x['price']) for x in ml} == {('B', 417200), ('S', 417500)}
    # 身份保留: 盘中 fill 消费被拦残留, 不建档
    eng.ingest([dict(kind='fill', id=1, ms=OPEN + 1_000, qty=600, price=417500,
                     refs=[1])])
    assert eng.order(1)['filled'] == 1000 and eng.order(1)['rem'] == 0
    assert eng.books['B'].get(425700) is None
    assert eng.counters['unknown_fill'] == 0
