"""W6 盘口因子面板 + M7 同位门 TDD（红→绿）

断言源 = 规格 §3.3 M7（"盘口因子面板两路独立实现（lob_fact vs tick_fact 直放最小实现）
整数簿态全等、因子 max|Δ| ≤ 1e-6"）+ §2 物化 schema 契约（events 绝对量行 / sweep 档删除 /
checkpoints (vol, n_queue) 分钟态）+ 逐值手算数字。

两路：
  A = BandFold：lob_fact 行流折叠（生产表消费路径）
  B = ReplayB：从规格重写的最小簿重放（不调用 engine.py；表→事件映射复用 W4a 已验证机械转换）

纪律：所有期望值逐值手算/构造；任何"返回硬编码汇总"的存根必败（面板逐样本逐因子断言 +
闸门注入分歧必 FAIL 断言）。
"""
import os
import sys
from datetime import date

import polars as pl
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as C
import factor_panel as FP
import run_lob_batch as R

OPEN = C.OPEN
AUCTION = C.AUCTION_START
CK1 = OPEN + 60_000
CK2 = OPEN + 120_000
D = date(2026, 8, 3)
DSTR = '20260803'


def row(ms, kind, side, px, prev, new, qty=0, id=0, otype=None):
    return dict(time_ms=ms, kind=kind, side=side, price_x10000=px,
                prev_vol=prev, new_vol=new, qty=qty, id=id or None, otype=otype)


def swp(ms, side, px, vol_before, tail):
    return dict(time_ms=ms, side=side, price_x10000=px, vol_before=vol_before,
                tail_order=tail, tail_resid=0)


def ckp(ms, side, px, vol, nq):
    return dict(time_ms=ms, side=side, price_x10000=px, vol=vol, n_queue=nq)


def ev(kind, ms, id, side, px, qty, otype=None):
    return dict(kind=kind, ms=ms, id=id, side=side, price=px, qty=qty,
                otype=otype)


# ---------- 采样栅格 ----------

def test_sample_times_1s_1m_grid_lunch_skip_and_close_exclusion():
    s1 = FP.sample_times(FP.GRID_1S)
    s6 = FP.sample_times(FP.GRID_1M)
    assert s1[0] == OPEN + 1000 and s1[1] == OPEN + 2000
    assert s6[0] == OPEN + 60_000 and s6[1] == OPEN + 120_000
    assert OPEN not in s1                       # 栅格不落 OPEN 本身
    assert max(s1) == C.CLOSE - 1000 and max(s6) == C.CLOSE - 60_000
    # 午休 (11:30:00, 13:00:00] 剔除; 11:30:00 与 13:01:00 保留
    assert C.LUNCH_START in s1 and C.LUNCH_END + 60_000 in s1
    assert C.LUNCH_START + 60_000 not in s1 and C.LUNCH_END not in s1
    assert all(not (C.LUNCH_START < t <= C.LUNCH_END) for t in s1)
    n_1s = (C.CLOSE - C.OPEN) // 1000 - 1                     # 19,799 秒位
    n_1m = (C.CLOSE - C.OPEN) // 60_000 - 1                   # 329 分钟位
    skip = (C.LUNCH_END - C.LUNCH_START)                      # 5,400,000ms
    assert len(s1) == n_1s - skip // 1000 == 14_399
    assert len(s6) == n_1m - skip // 60_000 == 239
    s_all = FP.sample_times(FP.GRID_1S, skip_lunch=False)
    assert len(s_all) == n_1s == 19_799 and C.LUNCH_END in s_all


# ---------- Path A: BandFold（lob_fact 行流折叠） ----------

def test_band_fold_absolute_vol_trade_cancel_and_sweep_delete():
    """绝对量契约: 行 new_vol 直接置档量; sweep 删除档 (幂等); 未知档 sweep no-op"""
    rows = [row(OPEN + 1000, 'add', 'B', 100000, 0, 500, qty=500, id=1),
            row(OPEN + 2000, 'trade', 'B', 100000, 500, 300, qty=200, id=1),
            row(OPEN + 4000, 'add', 'S', 100500, 0, 700, qty=700, id=2),
            row(OPEN + 5000, 'cancel', 'S', 100500, 700, 200, qty=500, id=2)]
    sweeps = [swp(OPEN + 3000, 'B', 100000, 300, 1),      # 300 → 删档
              swp(OPEN + 3000, 'B', 99000, 100, 9)]       # 未知档: no-op
    f = FP.BandFold().run(rows, sweeps, [], [OPEN + 2000, OPEN + 3000,
                                             OPEN + 6000])
    assert f.states == [{'B': {100000: 300}, 'S': {}},
                        {'B': {}, 'S': {}},
                        {'B': {}, 'S': {100500: 200}}]


def test_band_fold_same_ms_chain_rule_sweep_position():
    """同 (ms, side, px) 链式规则 (W6 实测定标): sweep 落在首个 prev_vol==0 行之前;
    无 prev==0 行则落在本 ms 全部行之后。四种组合逐值断言。"""
    S = OPEN + 1000
    # (a) 行在先扫单在后: 500→200 后档清空
    rows = [row(S, 'add', 'B', 100000, 0, 500, qty=500, id=1),
            row(S, 'trade', 'B', 100000, 500, 200, qty=300, id=1)]
    f = FP.BandFold().run(rows, [swp(S, 'B', 100000, 200, 1)], [], [S])
    assert f.states[0]['B'] == {}
    # (b) 有行但链末(700) != vol_before(500): 折叠缺行形 → sweep 仍按终结采纳
    #     (W6 实测定标; 详见 test_band_fold_terminal_sweep_wins_on_unseen_consumption)
    rows = [row(S, 'add', 'B', 100000, 0, 700, qty=700, id=2)]
    f = FP.BandFold().run(rows, [swp(S, 'B', 100000, 500, 9)], [], [S])
    assert f.states[0]['B'] == {}
    # (c) 行—扫单—再重加 混合
    rows = [row(S, 'trade', 'B', 100000, 500, 200, qty=300, id=1),
            row(S, 'add', 'B', 100000, 0, 900, qty=900, id=3)]
    f = FP.BandFold().run(rows, [swp(S, 'B', 100000, 200, 1)], [], [S])
    assert f.states[0]['B'] == {100000: 900}
    # (d) 其他档同 ms 行不受影响 (独立键)
    rows = [row(S, 'add', 'S', 100500, 0, 400, qty=400, id=4)]
    f = FP.BandFold().run(rows, [swp(S, 'B', 100000, 200, 1)], [], [S])
    assert f.states[0] == {'B': {}, 'S': {100500: 400}}


def test_band_fold_sweep_terminates_drifted_level_when_no_rows_same_ms():
    """无行同 ms 扫单 = 该档终结 (即使 vol_before ≠ 折叠现量): 必删。

    实盘根因 (2026-08-03): 档出带后真实量变化, 折叠现量停在上次在带值; 该档被扫时
    vol_before 与折叠现量不等 → 旧规则"保守保留"留下幽灵档 (A 有 B 无), M7 phantom
    门抓出 (000155 1s 5 例 / 600036 1s 5902 例)。真簿语义: sweep 行只在该档清空时发
    → 终结 (无行时无重建可言; 有行时折叠缺行不可作反证 — 见
    test_band_fold_terminal_sweep_wins_on_unseen_consumption)。"""
    S1, S2 = OPEN + 1000, OPEN + 2000
    rows = [row(S1, 'add', 'B', 100000, 0, 500, qty=500, id=1)]
    # 无同 ms 行 + vol_before(900) ≠ 折叠现量(500) → 删除 (禁: 留 500 幽灵档)
    f = FP.BandFold().run(rows, [swp(S2, 'B', 100000, 900, 1)], [], [S1, S2])
    assert f.states[0]['B'] == {100000: 500}
    assert f.states[1]['B'] == {}
    assert f.n_same_ms_ambiguous == 0
    # 同 ms 有重建行 (prev==0) 且链末 (700) != vol_before (900): sweep 仍按终结采纳
    # (引擎只在档清空时发 sweep; 折叠缺行时链末不可作反证) → 计 1 例歧义 (不静默)
    rows2 = rows + [row(S2, 'add', 'B', 100000, 0, 700, qty=700, id=2)]
    f2 = FP.BandFold().run(rows2, [swp(S2, 'B', 100000, 900, 1)], [], [S1, S2])
    assert f2.states[1]['B'] == {}
    assert f2.n_same_ms_ambiguous == 1


def test_band_fold_same_ms_sweep_position_by_vol_before_not_tail_row_id():
    """扫单位置 = vol_before 命中的链位; tail_order 只证"该单在簿过", 不证位置。

    实盘实测 (000155.SZ@20260803, S@121700, ms=34257180): 段内 add 收至 6100
    (id=2173022 为尾单, 其 add 行在段中) 后连续成交 on 2172979/2172984/2172987/2173020
    收至 3200 (链末), 随后尾单自身被全量成交 → 档清空: sweep(vol_before=3200,
    tail_order=2173022)。真实位置 = 该 ms 行流之末 (链末量 == vol_before), 而非
    tail_order 行之后; 按 tail 行 id 就地删档 → 其后成交行把档"复活"为 3200
    (A=3200 / B 无 → M7 phantom 5,798 level-samples, 000155/600036 全 FAIL)。"""
    S, S2 = OPEN + 1000, OPEN + 2000
    rows = [row(S, 'add', 'S', 121700, 0, 600, qty=600, id=2172979),
            row(S, 'add', 'S', 121700, 600, 6100, qty=5500, id=2173022),
            row(S, 'trade', 'S', 121700, 6100, 5500, qty=600, id=2172979),
            row(S, 'trade', 'S', 121700, 5500, 5400, qty=100, id=2172984),
            row(S, 'trade', 'S', 121700, 5400, 5200, qty=200, id=2172987),
            row(S, 'trade', 'S', 121700, 5200, 3200, qty=300, id=2173020),
            row(S2, 'add', 'S', 121700, 0, 100, qty=100, id=2234436)]
    f = FP.BandFold().run(rows, [swp(S, 'S', 121700, 3200, 2173022)], [], [S, S2])
    assert f.states[0]['S'] == {}                 # 禁: 幽灵档 3200 (尾单行 id 误绑)
    assert f.states[1]['S'] == {121700: 100}      # 下 ms prev=0 重建 → 100
    assert f.n_same_ms_ambiguous == 0


def test_band_fold_terminal_sweep_wins_on_unseen_consumption():
    """链末 != vol_before 的 sweep 仍终结该档 (折叠缺行是常态, 非反证)。

    实盘实测 (000021.SZ@20260803, B@369100, ms=34209180): 行流仅见 add(0→2500,
    id=1198385), 其后 400 被成交消耗 (该键已出带 → 消耗行未落表), 尾单 2100 全量成交
    清空该档: sweep(vol_before=2100, tail_order=1198385)。按"链末不等则保留"会留下
    2500 幽灵档 (A=2500 / B 无 → 2,048 level-samples / 因子错 14,226 / dmax 3.35e5);
    8 code-day 两向误差对照 (保留 vs 终结) = (2048 phantom, 0 state_mm) vs (0, 0)
    → 终结采纳。"""
    S = OPEN + 1000
    rows = [row(S, 'add', 'B', 369100, 0, 2500, qty=2500, id=1198385)]
    f = FP.BandFold().run(rows, [swp(S, 'B', 369100, 2100, 1198385)], [], [S])
    assert f.states[0]['B'] == {}                 # 禁: 幽灵档 2500
    assert f.n_same_ms_ambiguous == 1             # 折叠缺行形 → 计数不静默


def test_band_fold_same_ms_recreate_row_does_not_consume_sweep():
    """rebuild 行 (prev=0) 不消耗同 ms 扫单: 扫单归位靠 vol_before 链位匹配。

    实盘根因 (000021.SZ@20260803 1s phantom 19,539 / 2025-08-12 40 例): 折叠现量因
    出带漂移停在旧值 (100), 本 ms 新单 100 到达并被自身立即全部成交 —— 行 prev=0/
    new=100 与扫单 (vol_before=100, tail_order=该行 id) 同 ms。旧链式规则见 prev(0)≠
    现量(100) 即判"扫单在先 + 重建", 在行前消耗扫单 → 行把档复活成 100 幽灵档。
    正确归位: 行按绝对量采纳 (终值 100), 段末链末量 == vol_before → 档终结。"""
    S = OPEN + 1000
    rows = [row(OPEN + 500, 'add', 'B', 100000, 0, 100, qty=100, id=1),
            row(S, 'add', 'B', 100000, 0, 100, qty=100, id=7)]
    f = FP.BandFold().run(rows, [swp(S, 'B', 100000, 100, 7)], [], [OPEN + 500, S])
    assert f.states[0]['B'] == {100000: 100}
    assert f.states[1]['B'] == {}                 # 禁: 幽灵档 100 残留
    assert f.n_same_ms_ambiguous == 0             # tail 直证 → 非歧义


def test_band_fold_rejects_unknown_kind_and_observation_rows():
    """错误路径: 未知 kind → ValueError（不静默吞）; 观测行 (prev==new==0) 不入簿"""
    with pytest.raises(ValueError):
        FP.BandFold().run([row(OPEN + 1000, 'phase', 'B', 0, 0, 0)], [], [],
                          [OPEN + 2000])
    f = FP.BandFold().run([row(OPEN + 1000, 'add', 'B', 0, 0, 0, qty=100, id=7)],
                          [], [], [OPEN + 2000])
    assert f.states[0] == {'B': {}, 'S': {}}


def test_band_fold_nq_checkpoint_cadence_not_row_derived():
    """n_queue 检查点口径: 样本间不变; 检查点覆盖后更新; 未见于任何检查点的档 → 未知
    (不猜; 行流无逐单剩余量, 见 module 契约)"""
    rows = [row(OPEN + 1000, 'add', 'B', 100000, 0, 500, qty=500, id=1),
            row(OPEN + 2000, 'add', 'B', 99900, 0, 300, qty=300, id=2)]
    ck = [ckp(CK1, 'B', 100000, 500, 2), ckp(CK1, 'B', 99900, 300, 1)]
    f = FP.BandFold().run(rows, [], ck, [CK1, CK1 + 1000])
    assert f.nqs == [{'B': {100000: 2, 99900: 1}, 'S': {}},
                     {'B': {100000: 2, 99900: 1}, 'S': {}}]
    # 无检查点: nq 未知 (档不在 dict, 非 0)
    f2 = FP.BandFold().run(rows, [], [], [CK1])
    assert f2.nqs == [{'B': {}, 'S': {}}]
    assert f2.states[0] == {'B': {100000: 500, 99900: 300}, 'S': {}}


def test_band_fold_nq_refresh_no_stale_carry_across_checkpoints():
    """检查点刷新口径: 新检查点未含的档 → 未知 (旧值不得残留)。

    实盘根因 (2026-08-03 000155.SZ 尾盘): 收盘集合竞价期档位出带, 检查点不再包含
    该档, 而折叠 nq 字典累积旧值 → 面板以数分钟前的 n_queue 冒充当刻挂单笔数
    (M7 nq 门抓出 A=18 vs B=未知)。刷新后未知 → -1 哨兵。"""
    rows = [row(OPEN + 1000, 'add', 'B', 100000, 0, 500, qty=500, id=1),
            row(OPEN + 2000, 'add', 'B', 99900, 0, 300, qty=300, id=2)]
    ck = [ckp(CK1, 'B', 100000, 500, 2), ckp(CK1, 'B', 99900, 300, 1),
          ckp(CK1 + 60_000, 'B', 99900, 300, 4)]     # 100000 已出带 → 新检查点无此档
    f = FP.BandFold().run(rows, [], ck, [CK1, CK1 + 60_000])
    assert f.nqs[0] == {'B': {100000: 2, 99900: 1}, 'S': {}}
    # 新检查点未含 → 未知 (不回带 2); 档面本身仍在 (行流口径, 与 nq 口径无关)
    assert f.nqs[1] == {'B': {99900: 4}, 'S': {}}
    assert 100000 not in f.nqs[1]['B']
    assert f.states[1] == {'B': {100000: 500, 99900: 300}, 'S': {}}
    # 面板: 未知 → -1 哨兵 (禁: 回带旧值 2)
    fl = dict(n_add=0, n_cancel=0, n_trade=0, n_sweep=0, add_vol=0,
              cancel_vol=0, trade_vol=0, sweep_vol_sum=0, sweep_vol_max=0)
    r = FP.factors_row(f.states[1], f.nqs[1], fl, f.states[0])
    assert (r['bid_p1'], r['bid_nq1'], r['bid_nq5']) == (100000, -1, -1)


def test_band_fold_flow_window_counters_and_sweep_stats():
    """窗口流水 (prev, t]: 行计数/量 + sweep 计数/量; 窗口外不计; 首窗自 OPEN;
    档清空 (全撤/全成) 走 sweep 行 → 不计 cancel/trade 计数 (行流口径, 见备忘)"""
    rows = [row(OPEN, 'level_materialization', 'B', 100000, 0, 500, qty=500),  # OPEN 位: 不入首窗
            row(OPEN + 1000, 'add', 'B', 100000, 500, 800, qty=300, id=1),
            row(OPEN + 2000, 'trade', 'B', 100000, 800, 500, qty=300, id=1),
            row(OPEN + 3000, 'cancel', 'S', 100500, 400, 100, qty=300, id=2),
            row(OPEN + 61_000, 'add', 'B', 100000, 500, 900, qty=400, id=3)]
    sweeps = [swp(OPEN + 2000, 'B', 99000, 700, 5),
              swp(OPEN + 62_000, 'S', 100500, 100, 2)]
    f = FP.BandFold().run(rows, sweeps, [], [CK1, CK2])
    w1, w2 = f.flows
    assert (w1['n_add'], w1['add_vol']) == (1, 300)
    assert (w1['n_trade'], w1['trade_vol']) == (1, 300)
    assert (w1['n_cancel'], w1['cancel_vol']) == (1, 300)
    assert (w1['n_sweep'], w1['sweep_vol_sum'], w1['sweep_vol_max']) == (1, 700, 700)
    assert (w2['n_add'], w2['add_vol'], w2['n_trade']) == (1, 400, 0)
    assert (w2['n_sweep'], w2['sweep_vol_sum'], w2['sweep_vol_max']) == (1, 100, 100)
    assert w2['n_cancel'] == 0


# ---------- Path B: ReplayB（规格最小重放, 不调用 engine.py） ----------

def _b_run(events, samples, first_anchor=None, gate=None):
    b = FP.ReplayB(samples)
    b.run(events, first_anchor_ms=first_anchor, gate=gate)
    return b


def test_replay_b_add_fill_cancel_sweep_and_shadow():
    """簿语义 + shadow (发射行绝对量视图): 全消 → sweep 行 + 档删除;
    档少时全簿 == shadow; nq = 活单数"""
    evs = [ev('add', OPEN + 1000, 1, 'B', 100000, 500, '0'),
           ev('add', OPEN + 2000, 2, 'S', 102000, 400, '0'),
           ev('add', OPEN + 3000, 3, 'B', 100000, 100, '0'),
           ev('fill', OPEN + 4000, 3, 'B', 100000, 100),
           ev('fill', OPEN + 5000, 2, 'S', 102000, 400),
           ev('add', OPEN + 6000, 4, 'B', 99000, 200, '0')]
    b = _b_run(evs, [OPEN + 7000])
    got = [(r['ms'], r['kind'], r['side'], r['price'], r['prev_vol'],
            r['new_vol'], r['qty']) for r in b.rows]
    assert got == [(OPEN + 1000, 'add', 'B', 100000, 0, 500, 500),
                   (OPEN + 2000, 'add', 'S', 102000, 0, 400, 400),
                   (OPEN + 3000, 'add', 'B', 100000, 500, 600, 100),
                   (OPEN + 4000, 'trade', 'B', 100000, 600, 500, 100),
                   (OPEN + 6000, 'add', 'B', 99000, 0, 200, 200)]
    assert [(s['ms'], s['side'], s['price'], s['vol_before'], s['tail_order'])
            for s in b.sweeps] == [(OPEN + 5000, 'S', 102000, 400, 2)]
    assert b.shadow == [{'B': {100000: 500, 99000: 200}, 'S': {}}]
    assert b.books == [{'B': {100000: 500, 99000: 200}, 'S': {}}]
    assert b.nqs[0] == {'B': {100000: 1, 99000: 1}, 'S': {}}


def test_replay_b_phase_price_zero_dup_add_and_unknown_refs():
    """阶段/边界: 竞价期 add 只观测 (prev==new==0 行) 不入簿; price=0 连续段不进簿
    不发行; dup_add 计数无行; 未知 fill/cancel ref 仅计数 (簿面不动)"""
    evs = [ev('add', AUCTION + 1000, 1, 'B', 100000, 300, '0'),   # 竞价 → 观测行
           ev('add', OPEN + 1000, 1, 'B', 100000, 300, '0'),       # 重复 id
           ev('add', OPEN + 2000, 2, 'S', 0, 500, '1'),            # 市价: 不进簿
           ev('fill', OPEN + 3000, 99, 'B', 100000, 50),           # 未登记
           ev('cancel', OPEN + 4000, 98, 'S', 100500, 50)]         # 未登记
    b = _b_run(evs, [OPEN + 5000])
    assert [(r['kind'], r['ms'], r['prev_vol'], r['new_vol'])
            for r in b.rows] == [('add', AUCTION + 1000, 0, 0)]
    assert b.shadow == [{'B': {}, 'S': {}}]
    assert b.counters['dup_add'] == 1
    assert b.counters['unknown_fill'] == 1
    assert b.counters['unknown_cancel'] == 1


def test_replay_b_band_predicate_out_of_band_suppressed_and_rank_semantics():
    """band = δ(对侧 best) ∪ 全簿 rank ≤ 50: 61 档簿中第 61 档 (对侧无 best) 出带 →
    不发事件行 (状态保留, books 有/shadow 无); 最低档 rank=1 → 带内"""
    evs = []
    oid = 100
    for i in range(61):                       # B 侧 61 档: 99000 起 每 tick 一档
        oid += 1
        evs.append(ev('add', OPEN + 1000 + i, oid, 'B', 99000 + 100 * i, 100, '0'))
    oid += 1
    evs.append(ev('add', OPEN + 2000, oid, 'B', 90000, 100, '0'))   # 第 62 档: 更低
    b = _b_run(evs, [OPEN + 70_000])          # 采样在全部事件之后 (61+1 档全在簿)
    emitted = {(r['side'], r['price']) for r in b.rows}
    assert ('B', 90000) in emitted            # 最低档 rank=1: 带内
    assert ('B', 99000) in emitted            # 次低档 rank=2: 带内
    assert ('B', 105000) not in emitted       # 最高档 rank=62>50 且对侧无 best → 出带
    assert b.books[0]['B'][105000] == 100     # 状态保留 (全簿有)
    assert 105000 not in b.shadow[0]['B']     # 未物化 (行带无)
    assert b.shadow[0]['B'][90000] == 100


def test_replay_b_materialize_with_cross_gate_identity_preserved():
    """开盘物化: 竞价残留 (价>0) 逐档聚合行; 越对侧 best 的交叉残留不入簿但保留身份
    (后续 cancel 按 id 消费, 不落 unknown 桶)"""
    evs = [ev('add', AUCTION + 1000, 1, 'B', 100000, 300, 'A'),
           ev('add', AUCTION + 2000, 2, 'B', 106000, 200, 'A'),   # 越过 ask best 106000
           ev('cancel', OPEN + 2000, 2, 'B', 0, 200)]              # 物化后按 id 撤
    b = FP.ReplayB([OPEN + 3000])
    b.run(evs, first_anchor_ms=OPEN, gate={'B': 106000, 'S': 95000})
    mat = [r for r in b.rows if r['kind'] == 'level_materialization']
    assert [(r['ms'], r['side'], r['price'], r['prev_vol'], r['new_vol'],
             r['qty']) for r in mat] == [(OPEN, 'B', 100000, 0, 300, 300)]
    assert b.counters['unknown_cancel'] == 0   # 交叉残留身份保留 → 消费成功
    assert b.shadow[0]['B'] == {100000: 300}


# ---------- 因子面板（逐值手算; 存根必败） ----------

def test_factors_hand_computed_all_columns_and_sentinels():
    """因子逐值: obi1/obi5/深度比/价差/挂单笔数 + 窗口流水 (撤单率/耗尽冲击);
    空侧哨兵 (-1 / 0.0) 明确"""
    st = {'B': {100000: 300, 99900: 700}, 'S': {100100: 500}}
    nq = {'B': {100000: 2, 99900: 3}, 'S': {100100: 1}}
    fl = dict(n_add=4, n_cancel=2, n_trade=1, n_sweep=1,
              add_vol=900, cancel_vol=300, trade_vol=150,
              sweep_vol_sum=400, sweep_vol_max=400)
    prev = {'B': {100000: 1000, 99900: 500}, 'S': {100100: 500}}
    r = FP.factors_row(st, nq, fl, prev)
    assert (r['bid_p1'], r['ask_p1'], r['spread']) == (100000, 100100, 100)
    assert (r['bid_v1'], r['ask_v1'], r['bid_v5'], r['ask_v5']) == (300, 500,
                                                                   1000, 500)
    assert r['obi1'] == pytest.approx((300 - 500) / 800)
    assert r['obi5'] == pytest.approx((1000 - 500) / 1500)
    assert r['depth_ratio5'] == pytest.approx(2.0)
    assert (r['bid_nq1'], r['ask_nq1'], r['bid_nq5'], r['ask_nq5']) == (2, 1, 5, 1)
    assert r['cancel_rate'] == pytest.approx(300 / 900)
    assert r['depletion_impact'] == pytest.approx(400 / 2000)   # prev 总 5 档深度
    # 空侧哨兵
    r2 = FP.factors_row({'B': {100000: 300}, 'S': {}}, {}, fl, prev)
    assert (r2['ask_p1'], r2['spread'], r2['ask_v5']) == (0, -1, 0)
    assert r2['obi1'] == pytest.approx(1.0)
    assert r2['depth_ratio5'] == -1.0          # 分母空 → 未定义哨兵
    assert (r2['ask_nq1'], r2['ask_nq5']) == (-1, -1)   # nq 未知哨兵
    # 全空 → obi 0.0 / 深度比哨兵 / prev 深度 0 → 耗尽冲击 0.0
    r3 = FP.factors_row({'B': {}, 'S': {}}, {}, fl, {})
    assert r3['obi1'] == 0.0 and r3['depth_ratio5'] == -1.0
    assert r3['depletion_impact'] == 0.0
    assert r3['obi1'] != r['obi1']             # 逐样本不同 → 常量存根必败


def test_panel_rows_from_fold_and_replay_are_same_columns():
    """面板行 = 因子 + (code, trade_date, grid, time_ms); A/B 两路共用列契约 → M7 可逐列比"""
    rows = [row(OPEN + 1000, 'add', 'B', 100000, 0, 500, qty=500, id=1)]
    f = FP.BandFold().run(rows, [], [ckp(CK1, 'B', 100000, 500, 1)], [CK1])
    pa = FP.panel_rows(f.states, f.nqs, f.flows, [CK1], '000155.SZ', D, '1s')
    b = _b_run([ev('add', OPEN + 1000, 1, 'B', 100000, 500, '0')], [CK1])
    pb = FP.panel_rows(b.shadow, b.nqs, b.flows, [CK1], '000155.SZ', D, '1s')
    assert list(pa[0]) == list(pb[0])
    assert {'code', 'trade_date', 'grid', 'time_ms'} <= set(pa[0])
    assert pa[0]['code'] == '000155.SZ' and pa[0]['grid'] == '1s'
    assert pa[0]['time_ms'] == CK1
    assert pa[0]['bid_v1'] == 500 and pa[0]['bid_nq1'] == 1
    assert pb[0]['bid_v1'] == 500 and pb[0]['bid_nq1'] == 1


# ---------- M7 闸门（注入分歧必 FAIL = 存根必败） ----------

def _mk_pair():
    # otype 两路一致 (表行 otype == 事件 otype, 生产链契约) → 行流元组才可比
    rows = [row(OPEN + 1000, 'add', 'B', 100000, 0, 500, qty=500, id=1, otype='0'),
            row(OPEN + 2000, 'add', 'S', 100500, 0, 300, qty=300, id=2, otype='0')]
    f = FP.BandFold().run(rows, [], [ckp(CK1, 'B', 100000, 500, 1),
                                     ckp(CK1, 'S', 100500, 300, 1)], [CK1])
    evs = [ev('add', OPEN + 1000, 1, 'B', 100000, 500, '0'),
           ev('add', OPEN + 2000, 2, 'S', 100500, 300, '0')]
    b = _b_run(evs, [CK1])
    return f, b


def _gate(f, b, samples, aligned=None):
    return FP.m7_gate(dict(rows=f.flat_rows, sweeps=f.flat_sweeps,
                           states=f.states, nqs=f.nqs,
                           panel=FP.panel_rows(f.states, f.nqs, f.flows, samples,
                                               '000155.SZ', D, '1m')),
                      dict(rows=b.rows, sweeps=b.sweeps, states=b.shadow,
                           nqs=b.nqs,
                           panel=FP.panel_rows(b.shadow, b.nqs, b.flows, samples,
                                               '000155.SZ', D, '1m')),
                      samples, aligned if aligned is not None else set(samples))


def test_m7_gate_passes_on_identical_pair_and_reports_checked_counts():
    f, b = _mk_pair()
    g = _gate(f, b, [CK1])
    assert g['ok'] is True
    assert g['row_mismatch'] == [] and g['state_mismatch'] == []
    assert g['n_state_levels'] == 2 and g['n_rows'] == 2
    assert g['factor_delta_max'] == 0.0


def test_m7_gate_detects_row_and_state_divergence_not_a_stub():
    """禁止行为: 注入行分歧/簿态分歧/幻影档 → 闸门必须 FAIL (全部为真检测)"""
    f, b = _mk_pair()
    b.rows[1]['new_vol'] = 301                 # 行分歧
    g = _gate(f, b, [CK1])
    assert g['ok'] is False and g['row_mismatch']
    f, b = _mk_pair()
    b.shadow[0]['S'][100500] = 999             # 簿态分歧
    g = _gate(f, b, [CK1])
    assert g['ok'] is False and g['state_mismatch']
    f, b = _mk_pair()
    f.states[0]['S'].pop(100500)               # A 缺档 (B 有 A 无)
    g = _gate(f, b, [CK1])
    assert g['ok'] is False and g['state_mismatch']
    f, b = _mk_pair()
    f.states[0]['B'][123456] = 1               # 幻影档 (A 有 B 无)
    g = _gate(f, b, [CK1])
    assert g['ok'] is False and g['phantom']


def test_m7_gate_factor_tolerance_and_nq_alignment_rule():
    """因子门 1e-6: 1e-7 过 / 1e-5 拒 (obi1 浮点列); nq 列仅在检查点对齐样本比较
    (1s 非对齐样本 A 侧 nq 为检查点口径, 不与 B 逐秒活单数比较)"""
    f, b = _mk_pair()
    # A 侧 nq 未知 (折叠未获检查点) → bid_nq1 = -1; B 侧真值 1 (活单数)
    pa_unk = FP.panel_rows(f.states, [{'B': {}, 'S': {}}], f.flows, [CK1],
                           '000155.SZ', D, '1m')
    pa = FP.panel_rows(f.states, f.nqs, f.flows, [CK1], '000155.SZ', D, '1m')
    pb = FP.panel_rows(b.shadow, b.nqs, b.flows, [CK1], '000155.SZ', D, '1m')
    assert (pa_unk[0]['bid_nq1'], pb[0]['bid_nq1']) == (-1, 1)   # 面板确有两路差
    # 非对齐: nq 列不比较 → 差值不触发门
    g = FP.m7_gate(dict(rows=f.flat_rows, sweeps=f.flat_sweeps, states=f.states,
                        nqs=[{'B': {}, 'S': {}}], panel=pa_unk),
                   dict(rows=b.rows, sweeps=b.sweeps, states=b.shadow,
                        nqs=b.nqs, panel=pb),
                   [CK1], aligned=set())
    assert g['ok'] is True and g['nq_compared'] == 0
    # 对齐: nq 列比较 → A(清空态) vs B(真值) 分歧 FAIL
    pa_five = FP.panel_rows(f.states, [{'B': {100000: 5}, 'S': {}}], f.flows, [CK1],
                            '000155.SZ', D, '1m')
    g2 = FP.m7_gate(dict(rows=f.flat_rows, sweeps=f.flat_sweeps, states=f.states,
                         nqs=[{'B': {100000: 5}, 'S': {}}], panel=pa_five),
                    dict(rows=b.rows, sweeps=b.sweeps, states=b.shadow,
                         nqs=b.nqs, panel=pb),
                    [CK1], aligned={CK1})
    assert g2['nq_compared'] == 1 and g2['ok'] is False and g2['nq_mismatch']
    # 因子浮点门: 1e-7 过
    pa_small = [dict(pa[0], obi1=pa[0]['obi1'] + 1e-7)]
    g3 = FP.m7_gate(dict(rows=f.flat_rows, sweeps=f.flat_sweeps, states=f.states,
                         nqs=f.nqs, panel=pa_small),
                    dict(rows=b.rows, sweeps=b.sweeps, states=b.shadow,
                         nqs=b.nqs, panel=pb), [CK1], aligned={CK1})
    assert g3['ok'] is True and g3['factor_delta_max'] == pytest.approx(1e-7)
    # 1e-5 拒 (超 1e-6 门)
    pa_big = [dict(pa[0], obi1=pa[0]['obi1'] + 1e-5)]
    g4 = FP.m7_gate(dict(rows=f.flat_rows, sweeps=f.flat_sweeps, states=f.states,
                         nqs=f.nqs, panel=pa_big),
                    dict(rows=b.rows, sweeps=b.sweeps, states=b.shadow,
                         nqs=b.nqs, panel=pb), [CK1], aligned={CK1})
    assert g4['ok'] is False and g4['factor_mismatch']


# ---------- 端到端（合成 tick_fact 树 → 真表 → 两路面板 → M7） ----------

def _write_part_df(dirpath, df):
    dirpath.mkdir(parents=True, exist_ok=True)
    df.write_parquet(dirpath / 'part-000.parquet')


def _mk_panel_tick(root):
    """合成 tick_fact 迷你树 (20260803; SZ+SH 双所, 跨 2 个分钟检查点):
    SZ 000155: 竞价 add (物化) + 连续段 add/trade/全撤(C 行 → sweep) + 远档 add;
    SH 600036: A add + D 全撤 (sweep) + 第二窗 A add。行数/量逐值手算。"""
    mk = lambda t, rows: _write_part_df(
        root / t / 'year=2026' / 'month=08',
        pl.DataFrame(rows).with_columns(pl.lit(D).alias('trade_date').cast(pl.Date)))
    mk('orders', [
        dict(code='000155.SZ', time_ms=AUCTION + 1000, order_no=1,
             exch_order_no=2001, order_type='0', bs='B', price_x10000=100000,
             volume=400),                                        # 竞价 → 物化
        dict(code='000155.SZ', time_ms=OPEN + 1000, order_no=2,
             exch_order_no=2002, order_type='0', bs='B', price_x10000=100000,
             volume=300),                                        # 簿上加单
        dict(code='000155.SZ', time_ms=OPEN + 2000, order_no=3,
             exch_order_no=2003, order_type='0', bs='S', price_x10000=100500,
             volume=500),                                        # 卖档 (整撤清空)
        dict(code='000155.SZ', time_ms=CK1 + 1000, order_no=4,
             exch_order_no=2004, order_type='0', bs='B', price_x10000=99000,
             volume=200),                                        # 第二窗远档
        dict(code='600036.SH', time_ms=OPEN + 1500, order_no=5,
             exch_order_no=1001, order_type='A', bs='B', price_x10000=371000,
             volume=600),
        dict(code='600036.SH', time_ms=OPEN + 2500, order_no=6,
             exch_order_no=1001, order_type='D', bs='B', price_x10000=371000,
             volume=600),                                        # 全撤 → sweep
        dict(code='600036.SH', time_ms=CK1 + 2000, order_no=7,
             exch_order_no=1002, order_type='A', bs='S', price_x10000=371500,
             volume=800)])
    mk('trades', [
        dict(code='000155.SZ', time_ms=OPEN + 3000, trade_no=11, bs=1,
             price_x10000=100000, volume=100, ask_seq=0, bid_seq=2002),
    ])
    mk('cancels', [
        dict(code='000155.SZ', time_ms=OPEN + 4000, trade_no=21, side=0,
             order_ref=2003, volume=500),                        # 全撤 → sweep
    ])
    # 快照 = 引擎簿面真值 (日门 M1a/M2/M4 对拍: 物化 400 → +300 add −100 trade = 600;
    # 卖档 OPEN+2000 建/OPEN+4000 清 均在首锚之后 → 两锚点 ask 侧为空)
    snaps = []
    for k, (bp1, bv1, ap1, av1) in enumerate([(100000, 400, None, None),
                                              (100000, 600, None, None)]):
        r = dict(code='000155.SZ', time_ms=OPEN + 60_000 * k)
        for i in range(1, 11):
            r[f'bid_p{i}'] = bp1 if i == 1 else None
            r[f'bid_v{i}'] = bv1 if i == 1 else None
            r[f'ask_p{i}'] = ap1 if i == 1 else None
            r[f'ask_v{i}'] = av1 if i == 1 else None
        snaps.append(r)
    mk('snapshots', snaps)
    mdir = root / '_manifest'
    mdir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame([
        dict(code='000155.SZ', trade_date=D, n_orders=4, n_trades=1, n_snap=2),
        dict(code='600036.SH', trade_date=D, n_orders=3, n_trades=0, n_snap=0),
    ]).write_parquet(mdir / 'conversion_manifest.parquet')
    pl.DataFrame([dict(code='000155.SZ', trade_date=D, n_cancels=1)]) \
        .write_parquet(mdir / 'cancels_manifest.parquet')


@pytest.fixture()
def mini_trees(tmp_path):
    tick, lob = tmp_path / 'tick', tmp_path / 'lob'
    _mk_panel_tick(tick)
    R._init_worker(dict(tick_root=str(tick), lob_root=str(lob),
                        conv_manifest=str(tick / '_manifest'
                                          / 'conversion_manifest.parquet'),
                        cancels_manifest=str(tick / '_manifest'
                                             / 'cancels_manifest.parquet')))
    p = R.process_date(DSTR)
    assert p['ok'] and p['n_fail'] == 0
    return tick, lob


def test_e2e_panel_two_paths_m7_pass_and_values(mini_trees):
    """端到端: 合成树 → 生产三表 → A 折叠 / B 重放 → M7 过门 + 因子逐值。
    手算 (000155.SZ): 物化 B@100000=400; +300 add; -100 trade → B@100000=600;
    C 行全撤 500 → 档清空 sweep (无 cancel 行); 第二窗 +200 @99000。
    CK1: bid_v1=600 / ask 空 (spread=-1); CK2: +200 → bid_v1=600, 窗口 sweep 0。"""
    tick, lob = mini_trees
    rep = FP.run_code_day('000155.SZ', D, str(tick), str(lob), grids=('1m',))
    m7 = rep['m7']['1m']
    assert m7['ok'] is True
    assert m7['row_mismatch'] == [] and m7['state_mismatch'] == []
    assert m7['n_rows'] == 5                      # 物化 1 + add 2 + trade 1 + add 1
    rows = rep['panel']['1m']
    assert len(rows) == 239 and rows[0]['time_ms'] == CK1   # 全栅格 (首检查点起)
    by_ms = {r['time_ms']: r for r in rows}
    assert {CK1, CK2} <= set(by_ms)
    r1, r2 = by_ms[CK1], by_ms[CK2]
    assert (r1['bid_v1'], r1['bid_p1'], r1['ask_v1'], r1['spread']) == \
        (600, 100000, 0, -1)
    assert r1['obi1'] == pytest.approx(1.0)
    assert r1['depth_ratio5'] == -1.0
    assert (r1['bid_nq1'], r1['bid_nq5']) == (2, 2)     # 物化单 + 连续段 add
    assert (r1['n_add'], r1['add_vol']) == (2, 800)     # 连续段 add 300 + 卖档 500
    assert (r1['n_trade'], r1['trade_vol']) == (1, 100)
    assert (r1['n_cancel'], r1['cancel_vol']) == (0, 0)  # 全撤 → sweep 行, 非 cancel
    assert (r1['n_sweep'], r1['sweep_vol_sum']) == (1, 500)
    assert (r2['bid_v1'], r2['bid_p1']) == (600, 100000)
    assert (r2['n_add'], r2['add_vol']) == (1, 200)
    assert r2['depletion_impact'] == 0.0
    # 末事件后的采样点: 簿态冻结 (同值重复, 非缺行; 窗口流水归零)
    tail = rows[-1]
    assert tail['time_ms'] == C.CLOSE - 60_000
    assert (tail['bid_v1'], tail['bid_p1'], tail['n_add']) == (600, 100000, 0)


def test_e2e_panel_detects_table_tamper(mini_trees):
    """禁止行为: 篡改 lob_events 一行 new_vol → M7 必须 FAIL (证明闸门测的是表,
    不是两路自恰)"""
    tick, lob = mini_trees
    p = lob / 'lob_events' / 'year=2026' / 'month=08' / '20260803.parquet'
    df = pl.read_parquet(p)
    df = df.with_columns(
        pl.when((pl.col('code') == '000155.SZ') & (pl.col('kind') == 'trade'))
        .then(pl.col('new_vol') + 7).otherwise(pl.col('new_vol')).alias('new_vol'))
    df.write_parquet(p)
    rep = FP.run_code_day('000155.SZ', D, str(tick), str(lob), grids=('1m',))
    assert rep['m7']['1m']['ok'] is False
    assert rep['m7']['1m']['row_mismatch'] or rep['m7']['1m']['state_mismatch']


def test_e2e_panel_write_atomic_schema_and_idempotent(mini_trees, tmp_path):
    """面板落盘: 表名/列/schema 契约; 原子 (无 .tmp 残留); 重写幂等 (字节全等)"""
    tick, lob = mini_trees
    rep = FP.run_code_day('000155.SZ', D, str(tick), str(lob), grids=('1s', '1m'))
    out = tmp_path / 'out'
    paths = FP.write_panels(out, rep['panel'], '000155.SZ', D)
    assert set(paths) == {'1s', '1m'}
    for g, p in paths.items():
        assert p.name == '20260803.parquet'
        assert p.parent.name == 'month=08' and p.parent.parent.name == 'year=2026'
        df = pl.read_parquet(p)
        assert list(df.columns) == FP.PANEL_COLS
        assert df.height == len(rep['panel'][g])
        assert not list(p.parent.glob('*.tmp*'))
    h1 = {g: p.read_bytes() for g, p in paths.items()}
    paths2 = FP.write_panels(out, rep['panel'], '000155.SZ', D)
    assert {g: p.read_bytes() for g, p in paths2.items()} == h1


def test_e2e_sh_code_panel_two_paths_agree(mini_trees):
    """SH 路径同样过门 (A/D 撤单通道 + D 全撤 sweep)"""
    tick, lob = mini_trees
    rep = FP.run_code_day('600036.SH', D, str(tick), str(lob), grids=('1m',))
    assert rep['m7']['1m']['ok'] is True
    assert any(r['n_sweep'] == 1 for r in rep['panel']['1m'])
