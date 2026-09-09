"""W2 订单簿引擎 TDD（红→绿；金样场景驱动 + 合成边界）

引擎 = 逐单事件级重建（规格 §1）:
  add(id, side, price, qty, otype)   — 价>0 进簿(全深度 price-keyed + 档内 FIFO 队列);
                                       价=0('1'/U 永不进簿) 只登账本
  fill(ms, qty, refs=[...])          — 逐 ref 消费 min(qty, 订单剩余): 未登记=unknown_fill;
                                       超剩余=clamp+fill_excess (SH 合并打印语义实测桶)
  cancel(id, qty)                    — 双所同构撤单通道: 全撤剩余量语义(W1 校准 SZ 100%/SH D
                                       主导), 消费 min(qty, 剩余); 未知/已死单 → 桶计数
  阶段机: 由 ms 自动切换 (config 阈值), 竞价段只观测(registry 不建簿),
          09:25 撮合打印消费竞价单; 连续段簿面全深度重建
不变量: 每价档 vol == Σ 档内订单剩余 (逐事件断言); FIFO 队序=同价加单序;
EOD 结算: auction 残单/continuous 残单/unbooked 残单分桶守恒。

存根击穿: 每个场景断言都从金样 CSV 的真实数字推导, 换任何硬编码实现必 FAIL。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'fixtures'))
from fixtures import loader as F
from engine import Engine
import config as C


def scenario_events(name):
    """scenario CSV → 引擎归一化事件 (PHASE 行丢弃=自动阶段机)"""
    evs = []
    for r in F.load_scenario(name):
        k = r['kind']
        if k == 'PHASE':
            continue
        if k == 'ADD':
            evs.append(dict(kind='add', ms=r['ms'], side=r['side'],
                            price=r['price'], qty=r['qty'], id=r['id'],
                            otype=r['otype'] or '0'))
        elif k == 'CANCEL':
            evs.append(dict(kind='cancel', ms=r['ms'], id=r['id'],
                            qty=r['qty'], side=r['side']))
        elif k == 'FILL':
            refs = [r['id']] + ([r['ref2']] if r['ref2'] else [])
            evs.append(dict(kind='fill', ms=r['ms'], qty=r['qty'],
                            refs=refs, price=r['price']))
    return evs


def run(name, **kw):
    e = Engine(**kw)
    e.ingest(scenario_events(name))
    e.eod()
    return e


def assert_book_consistent(e):
    """不变量: 每价档 vol == Σ 订单剩余; 档内 id 都在 registry; FIFO=加单序"""
    for side in ('B', 'S'):
        for px, st in e.side_books(side).items():
            q = st['vol']
            oids = st['orders']
            assert sum(e.order(i)['rem'] for i in oids) == q, (side, px)
            for i in oids:
                assert e.order(i)['side'] == side and e.order(i)['price'] == px
    # 队列单调: 档内 ids 序列 == registry 中该价的加单序 (partial 消费不换序)
    for side in ('B', 'S'):
        for px, st in e.side_books(side).items():
            seq = [oid for oid, r in e.registry_seq(side, px)]
            assert seq == st['orders'], (side, px, seq, st['orders'])


# ---------- 场景 1: 阶段机 (竞价只观测, 撮合消费竞价单, 连续簿干净) ----------

def test_phases_auction_observe_only():
    e = run('phases')
    # 竞价加单登 registry 但不进簿 (竞价段只观测不重建)
    assert e.order(1)['rem'] == 700      # 1000 - 竞价撮合 300
    assert e.order(2)['rem'] == 200      # 500 - 300
    assert e.level_vol('B', 122000) == 0  # 竞价价档永不进连续簿
    assert e.level_vol('S', 123000) == 0
    # 连续段加单进簿
    assert e.level_vol('B', 122600) == 200
    assert e.level_vol('B', 122500) == 100
    # 午休/复盘残留携入
    assert e.best('B') == 122600
    # EOD 结算分桶
    s = e.eod_summary
    assert s['auction_rem'] == 900        # 竞价残单 (700+200) 不携入连续簿
    assert s['continuous_rem'] == 300     # id3 200 + id4 100
    assert_book_consistent(e)


def test_phases_phase_flags_on_events():
    """事件行带 phase 标记; 竞价撮合打印在 AUCTION_MATCH 相位"""
    e = Engine()
    evs = scenario_events('phases')
    e.ingest(evs)
    ph = {r['phase'] for r in e.events}
    assert 'auction' in ph and 'continuous' in ph
    assert e.phase_at(33_900_000) == 'match'      # 09:25 撮合
    assert e.phase_at(46_800_000) == 'continuous'  # 13:00 复盘后


# ---------- 场景 2: 远近档加单 (全深度簿 + band 判定) ----------

def test_near_far_adds_full_depth_book():
    e = run('near_far_adds')
    # 全深度: 228 tick 深的档也在簿 (价>0 全收)
    assert e.level_vol('B', 100000) == 400     # id6 深档
    assert e.level_vol('S', 130000) == 200     # id7
    assert e.best('B') == 122800 and e.best('S') == 123000
    for oid in range(1, 8):
        assert e.order(oid)['rem'] == e.order(oid)['added']
    assert_book_consistent(e)


def test_adds_emit_level_rows_with_prev_new_vol():
    """加单事件行: 每 (事件, 触碰价档) 一行, prev/new vol 绝对值正确"""
    e = Engine()
    e.ingest(scenario_events('near_far_adds'))
    adds = [r for r in e.events if r['kind'] == 'add']
    assert len(adds) == 7
    a6 = next(r for r in adds if r['id'] == 6)
    assert (a6['side'], a6['price']) == ('B', 100000)
    assert a6['prev_vol'] == 0 and a6['new_vol'] == 400
    # 档位触碰只发一行; 同价二单合并 vol
    a1 = next(r for r in adds if r['id'] == 1)
    assert a1['new_vol'] == 1000


# ---------- 场景 3: 扫单 (逐打印消费档位 → 耗尽/留尾) ----------

def test_market_sweep_levels_depleted():
    e = run('market_sweep')
    # ask3(123200)/ask4(123300) 被吃光 → 档删除
    assert e.level_vol('S', 123200) == 0
    assert e.level_vol('S', 123300) == 0
    # 剩余 ask 档与 best
    assert e.level_vol('S', 123000) == 200
    assert e.level_vol('S', 123100) == 300
    assert e.best('S') == 123000
    # bid5 被卖压吃光 → bid 无档
    assert e.best('B') is None
    assert_book_consistent(e)


def test_sweep_depletion_rows_sparse():
    """耗尽才发 sweep_meta: 3 个 level 归零 → 3 行, 带队尾 id 与残余 0"""
    e = Engine()
    e.ingest(scenario_events('market_sweep'))
    sweeps = [r for r in e.sweeps]
    assert len(sweeps) == 3
    by_price = {(r['side'], r['price']) for r in sweeps}
    assert by_price == {('S', 123200), ('S', 123300), ('B', 122800)}
    s3 = next(r for r in sweeps if r['price'] == 123200)
    assert s3['vol_before'] == 500 and s3['tail_order'] == 3 and s3['tail_resid'] == 0


# ---------- 场景 4: FIFO 队序 ----------

def test_fifo_queue_consumption():
    e = run('fifo_queue')
    # 同价档消费按加单序: q1 全消, q2 两笔吃光, q3 余 50
    assert e.order(1)['rem'] == 0
    assert e.order(2)['rem'] == 0
    assert e.order(3)['rem'] == 50
    st = e.level_state('B', 122800)
    assert st['vol'] == 50
    assert st['orders'] == [3]           # 队头只余 q3
    # 打印4 半量消费: 档内 id 序保留 (FIFO 位置不变)
    assert_book_consistent(e)


def test_fifo_late_join_goes_tail():
    """同价部分消费后新加单 → 队尾; 撤队内剩余单后队序级联"""
    evs = scenario_events('fifo_queue') + [
        dict(kind='add', ms=34_200_140, side='B', price=122800, qty=500,
             id=4, otype='0')]
    evs.append(dict(kind='cancel', ms=34_200_150, id=3, qty=50, side='B'))
    e = Engine()
    e.ingest(evs)
    st = e.level_state('B', 122800)
    # 队尾加入 (id4 在 q3 后); 撤 q3 全剩余 → 队内清除, 只余 id4
    assert st['orders'] == [4]
    assert st['vol'] == 500
    assert e.order(3)['rem'] == 0
    assert_book_consistent(e)


# ---------- 场景 5: 撤单量语义 (防御路径 + 校准主路径) ----------

def test_partial_cancel_defensive_clamp():
    """撤单量≠剩余 的防御路径: 部分撤(min) / 超量撤(clamp) / 撤后成交(ref 死单)"""
    e = run('partial_cancel')
    assert e.order(1)['rem'] == 0        # 1000 -300 -200 -500
    assert e.order(2)['rem'] == 0        # 800 → 超量撤 900 clamp 800
    assert e.counters['cancel_excess'] == 1
    assert e.counters['fill_excess'] == 1   # 撤后成交 ref → clamp 0
    assert e.best('B') is None and e.best('S') is None
    assert_book_consistent(e)


def test_cancel_full_remaining_sz_and_sh_channel():
    """校准主路径: SZ C / SH D 都按全撤剩余量 (dual_flow), 结果同构"""
    e = run('dual_flow')
    assert e.order(1)['rem'] == 0        # D 全撤 400
    assert e.order(2)['rem'] == 0        # C 全撤 400
    assert e.order(3)['rem'] == 0        # D 全撤 500
    assert e.counters['unknown_cancel'] == 0
    assert e.counters['cancel_excess'] == 0
    assert e.best('B') is None and e.best('S') is None
    assert_book_consistent(e)


def test_cancel_same_channel_regardless_type():
    """同通道: 同样事件把 D/C 标签互换 → 簿与计数完全一致 (无类型分支)"""
    evs = scenario_events('dual_flow')
    evs_c = [dict(ev) for ev in evs]
    for ev in evs_c:
        if ev['kind'] == 'cancel':
            ev['otype'] = 'C'
    a, b = Engine(), Engine()
    a.ingest(evs); a.eod()
    b.ingest(evs_c); b.eod()
    assert a.counters == b.counters
    for side in ('B', 'S'):
        assert a.side_books(side) == b.side_books(side)
    for oid in (1, 2, 3):
        assert a.order(oid) == b.order(oid)


def test_cancel_of_consumed_order_noop_bucket():
    """撤已全消单(校准: 生产中不发生; 防御) = no-op + excess 桶; 撤未知单 = unknown 桶"""
    e = run('cancel_consumed')
    assert e.order(1)['rem'] == 0
    assert e.level_vol('S', 123000) == 300     # id2 还在簿
    assert e.order(2)['rem'] == 300
    assert e.counters['cancel_excess'] == 1    # id1 已全消 100 再撤 100
    assert e.counters['unknown_cancel'] == 1   # id99 从未加单
    assert_book_consistent(e)


# ---------- 场景 6: SZ 市价/U 语义 (价=0 永不进簿) ----------

def test_sz_market_u_price0_never_books():
    e = run('sz_market_u')
    # '1'/U 价=0: 不占簿; 其成交 ref 吃空方 = 只消 registry
    assert e.order(10)['rem'] == 0       # 撤 200
    assert e.order(11)['rem'] == 100     # U 价0 从未被触达 → EOD unbooked
    assert e.order(12)['rem'] == 0       # '1' 带价: 进簿后被吃光
    assert e.level_vol('B', 122800) == 0
    assert e.level_vol('S', 123000) == 400    # id13 存活
    assert e.best('S') == 123000 and e.best('B') is None
    assert e.counters['unknown_cancel'] == 0  # 撤价0单 registry 可解析
    assert e.counters['unknown_fill'] == 0    # '1'/U ref 都在 registry
    assert e.eod_summary['unbooked_rem'] == 100   # id11
    assert_book_consistent(e)


# ---------- 确定性 / 不变量 ----------

def test_fill_single_ref_streams_shape_equivalent():
    """streams 生产形态 (fill 单 ref 事件, id=bid/ask 各一条, 无 refs 键) 与
    金样 refs 列表形态 → 同簿同计数（生产路径不得静默空转）"""
    evs_list = scenario_events('fifo_queue')
    evs_single = []
    for ev in evs_list:
        d = dict(ev)
        if d['kind'] == 'fill':
            refs = d.pop('refs')
            d['id'] = refs[0] if refs else 0
        evs_single.append(d)
    a, b = Engine(), Engine()
    a.ingest(evs_list)
    b.ingest(evs_single)
    for side in ('B', 'S'):
        assert a.side_books(side) == b.side_books(side)
    assert a.counters == b.counters


def test_unsorted_events_deterministic():
    """输入乱序 → 引擎按 (ms, kind 序 add<fill<cancel) 确定性重排"""
    evs = scenario_events('fifo_queue')
    import random
    random.seed(7)
    sh = evs[:]
    random.shuffle(sh)
    a, b = Engine(), Engine()
    a.ingest(evs); b.ingest(sh)
    for side in ('B', 'S'):
        assert a.side_books(side) == b.side_books(side)
    assert a.counters == b.counters


def test_invariant_all_scenarios():
    """全部金样场景跑完簿面一致 (vol==Σrem 不变量)"""
    for name in ('phases', 'near_far_adds', 'market_sweep', 'fifo_queue',
                 'partial_cancel', 'cancel_consumed', 'dual_flow', 'sz_market_u'):
        e = run(name)
        assert_book_consistent(e)


def test_band_gating_deep_level_emission():
    """band gating: 超 R(rank) 且出 δ 窗的深档不产事件行但保留簿面
    (rank_limit=2, δ=1%: rank3 且出窗 → 状态有、事件无)"""
    evs = []
    # 制造 rank 序列: best 122800 之上 6 个 ask 档递增 (每档一单)
    for i, tick in enumerate(range(1, 7)):
        evs.append(dict(kind='add', ms=34_200_000 + i, side='S',
                        price=122800 + tick * 100, qty=100, id=100 + i,
                        otype='0'))
    e = Engine(rank_limit=2, delta_pct=0.01)
    e.ingest(evs)
    # 无对侧 best → δ 窗规则不适用, 纯 rank 规则 → 只发 rank ≤2 (122900/123000) 两行
    add_rows = [r for r in e.events if r['kind'] == 'add']
    assert len(add_rows) == 2
    # 状态全深度保留: rank3+(123100...) 档在簿
    for i in range(3, 7):
        assert e.level_vol('S', 122800 + i * 100) == 100
    assert e.best('S') == 122900


def test_band_delta_window_with_opposite_best():
    """band δ 窗 (对侧 best ±1%): 出窗但 rank 小 → 在 band (rank 规则优先并入)"""
    evs = [
        dict(kind='add', ms=34_200_000, side='B', price=122800, qty=100,
             id=1, otype='0'),
        dict(kind='add', ms=34_200_010, side='S', price=123000, qty=100,
             id=2, otype='0'),
        dict(kind='add', ms=34_200_020, side='S', price=125000, qty=100,
             id=3, otype='0'),   # 距对侧 best 122800: 2200 > 1%×122800=1228 → 出 δ 窗
    ]
    e = Engine(rank_limit=2, delta_pct=0.01)
    e.ingest(evs)
    rows = [r for r in e.events if r['kind'] == 'add']
    # rank3 出 δ 窗 → 不发; rank2(123000) 在窗(200<1228) → 发
    assert {r['id'] for r in rows} == {1, 2}
