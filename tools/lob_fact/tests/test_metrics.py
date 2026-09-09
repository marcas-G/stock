"""W1 qa/metrics 度量基元 TDD（红→绿，与引擎无关的纯函数先行）

断言来源 = 设计规格 §3.3（M1 价梯/M2 量/ghost/M3 打印合法性/基元 rint），
逐值断言击穿存根：硬编码返回固定匹配率/固定桶的存根必 FAIL。
"""
import datetime
import numpy as np
import pandas as pd
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qa import metrics as mt


# ---------- rint 基元（规格: 快照价 float64 已 ×10000, rint(p) 勿再乘） ----------

def test_rint_exact_and_noise():
    """精确值与浮点噪声都归整到 ×10000 刻度整数"""
    assert mt.rint(122800.0) == 122800
    assert mt.rint(122799.9999999999) == 122800
    assert mt.rint(122800.0000000001) == 122800
    assert mt.rint(0.0) == 0
    assert mt.rint(1.5) == 2  # 半值进位规则固定（round-half-even 对 x.5: 1.5→2, 2.5→2）


def test_rint_stub_defeat():
    """返回 int 且非截断: 100.9 → 101 而非 100 (存根 int() 会截断)"""
    assert mt.rint(100.9) == 101


# ---------- ladder 提取（快照 10 档 → 有序 (p,v), 空档剔除） ----------

def _snap_row(asks, bids):
    """构造 snapshots 风格 dict（key 与 tick_fact snapshots 列一致: ask_p1.. bid_v10）"""
    row = {}
    for i, (p, v) in enumerate(asks, 1):
        row[f'ask_p{i}'] = float(p); row[f'ask_v{i}'] = float(v)
    for i, (p, v) in enumerate(bids, 1):
        row[f'bid_p{i}'] = float(p); row[f'bid_v{i}'] = float(v)
    return row


def test_snap_row_to_ladders_order_and_filter():
    """买 1..10 降序、卖 1..10 升序；价或量非正的档剔除（空档/深档位 0）"""
    row = _snap_row([(122900, 100), (123000, 200)], [(122800, 300), (122700, 0), (122600, np.nan)])
    L = mt.snap_row_to_ladders(row, side='ask')
    assert L == [(122900, 100), (123000, 200)]
    Lb = mt.snap_row_to_ladders(row, side='bid')
    # 剔除量=0 与 NaN 档后
    assert Lb == [(122800, 300)]


def test_snap_row_nan_and_zero_prices_filtered():
    """价 NaN（快照无档填 NaN）不产出 (0,nan) 脏档"""
    row = {'ask_p1': np.nan, 'ask_v1': 5.0, 'bid_p1': 0.0, 'bid_v1': 0.0}
    assert mt.snap_row_to_ladders(row, side='ask') == []
    assert mt.snap_row_to_ladders(row, side='bid') == []


# ---------- M1 价梯（rank 对齐分类: match/missing/adjacent/deep + rate） ----------

def test_ladder_match_identical():
    res = mt.ladder_match([(100, 10), (200, 20)], [(100, 10), (200, 20)])
    assert res['n_match'] == 2 and res['n_anchor'] == 2 and res['rate'] == 1.0
    assert res['ranks'][0]['cls'] == 'match'
    assert res['ranks'][1]['vol_delta'] == 0


def test_ladder_match_engine_shorter_missing():
    """引擎缺档 → missing (rank 1), rate=1/2"""
    res = mt.ladder_match([(100, 10), (200, 20)], [(100, 10)])
    assert res['ranks'][1]['cls'] == 'missing'
    assert res['rate'] == 0.5


def test_ladder_match_adjacent_vs_deep():
    """差 1 tick(100 units) → adjacent; 差>1 tick → deep; extra = 引擎超出 anchor 档
    (真实刻度: 122700 vs 122800 = 0.01 元差)"""
    res = mt.ladder_match([(122800, 10), (122900, 20)], [(122700, 10), (123300, 9), (125000, 1)])
    assert res['ranks'][0]['cls'] == 'adjacent'
    assert res['ranks'][1]['cls'] == 'deep'
    assert res['extras'] == [(125000, 1)]
    assert res['n_match'] == 0


def test_ladder_match_rate_stub_defeat():
    """逐档分类而非数量: 存根恒返回 n_match==n_anchor 必被以下结构击穿"""
    res = mt.ladder_match([(122800, 10), (122900, 20), (123000, 30)], [(122800, 10), (123000, 30)])
    # rank1: 引擎无 122900 → 见 123000 差 1 tick → adjacent；rank2 引擎缺 → missing
    assert [r['cls'] for r in res['ranks']] == ['match', 'adjacent', 'missing']


# ---------- M2 量相等: 命中档量差与 ghost（引擎在 anchor 价域内的多余价档） ----------

def test_vol_delta_on_matched_ranks():
    res = mt.ladder_match([(100, 10)], [(100, 15)])
    assert res['ranks'][0]['vol_delta'] == 5  # 引擎−快照


def test_ghost_levels_within_anchor_price_span():
    """快照价域 [min,max] 内引擎有 anchor 未见的价 → ghost（M2: 采纳后应=0）"""
    anchor = [(100, 10), (90, 20), (80, 30)]
    engine = [(100, 10), (95, 5), (90, 20), (80, 30)]  # 95 在价域内 → ghost
    ghosts = mt.ghost_levels(engine, anchor)
    assert ghosts == [(95, 5)]


def test_deep_levels_not_ghost():
    """引擎深档(价域外 rank>R 正常构造)不算 ghost — 只查 anchor 价域内"""
    anchor = [(100, 10), (90, 20)]
    engine = [(100, 10), (90, 20), (60, 5), (110, 5)]
    assert mt.ghost_levels(engine, anchor) == []


# ---------- M3 打印合法性（成交价 ∈ [best_bid−ε, best_ask+ε]） ----------

def test_trade_price_legal_cases():
    assert mt.classify_trade_price(10000, 9900, 10100, eps=0) == 'in_spread'
    assert mt.classify_trade_price(9900, 9900, 10100, eps=0) == 'in_spread'  # 等于 best bid
    assert mt.classify_trade_price(10100, 9900, 10100, eps=0) == 'in_spread'
    assert mt.classify_trade_price(10101, 9900, 10100, eps=0) == 'above_ask'
    assert mt.classify_trade_price(9899, 9900, 10100, eps=0) == 'below_bid'


def test_trade_price_eps():
    """ε(单位=×10000 的 tick=100) 容差: 超 1 tick 内合法"""
    assert mt.classify_trade_price(10200, 9900, 10100, eps=100) == 'in_spread'
    assert mt.classify_trade_price(10201, 9900, 10100, eps=100) == 'above_ask'


def test_trade_price_no_quote():
    """单侧无报价 → no_quote（不硬 FAIL，桶计数）"""
    assert mt.classify_trade_price(10000, None, 10100, eps=0) == 'no_quote'
    assert mt.classify_trade_price(10000, 9900, None, eps=0) == 'no_quote'


# ---------- 汇总统计（双侧合并 rate / 计数） ----------

def test_match_summary_merges_sides():
    res = {
        'B': mt.ladder_match([(100, 10)], [(100, 10)]),
        'S': mt.ladder_match([(100, 10), (200, 10)], [(100, 10)]),
    }
    s = mt.match_summary(res)
    assert s['n_match'] == 2 and s['n_anchor'] == 3
    assert s['rate'] == pytest.approx(2 / 3)
