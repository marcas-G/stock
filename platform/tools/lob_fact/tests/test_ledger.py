"""W1 qa/ledger 逐单账本 TDD（红→绿）

账本 = 引擎消费记账的纯函数核心（M4 逐单守恒 + W1 撤单量语义/ref 解析率测量共用）:
  add(id, side, price, type, qty)      — 登记委托
  fill(id, qty)                        — 成交消费（未知 id/超剩余 → 违规计数+clamp）
  cancel(id, qty)                      — 撤单消费 min(qty, 剩余)（部分撤单语义；超量/未知 → 违规计数）
每单守恒恒等式: added == filled_actual + canceled_actual + rem（违规量单独计数不静默）。

断言逐值击穿存根（硬编码固定 rem/固定计数必 FAIL）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # lob_fact/
from core.qa import ledger as L


def test_add_fill_cancel_basic():
    """加单 1000 → 成交 400 → 撤 600: 全消费, rem=0, 守恒成立"""
    out = L.ledger([
        L.ev_add(1, 'B', 122800, '0', 1000, ms=34200000),
        L.ev_fill(1, 400, ms=34200100),
        L.ev_cancel(1, 600, ms=34200200),
    ])
    o = out['orders'][1]
    assert (o['added'], o['filled'], o['canceled'], o['rem']) == (1000, 400, 600, 0)
    assert out['counters'] == dict(unknown_fill=0, fill_excess=0, unknown_cancel=0,
                                   cancel_excess=0, dup_add=0)
    # 守恒恒等式
    assert o['added'] == o['filled'] + o['canceled'] + o['rem']


def test_partial_cancel_semantics():
    """部分撤单: 1000 已成交 200, 撤 300 (剩余 800) → rem 500; 撤单量<剩余是合法部分撤"""
    out = L.ledger([
        L.ev_add(7, 'S', 123000, '0', 1000, ms=34200000),
        L.ev_fill(7, 200, ms=34200100),
        L.ev_cancel(7, 300, ms=34200200),
    ])
    assert out['orders'][7]['rem'] == 500
    # cancel_log 记录撤单时剩余, 供 W1 撤单量语义分布
    assert out['cancel_log'][-1] == dict(id=7, qty=300, rem_before=800,
                                         type='0', side='S', ms=34200200)


def test_cancel_excess_clamped_and_counted():
    """撤单量 > 剩余: 消费 clamp 到剩余, cancel_excess=1 记录超量 (禁止静默)"""
    out = L.ledger([
        L.ev_add(7, 'B', 122800, '0', 500, ms=34200000),
        L.ev_cancel(7, 800, ms=34200100),
    ])
    o = out['orders'][7]
    assert o['rem'] == 0 and o['canceled'] == 500
    assert out['counters']['cancel_excess'] == 1
    assert o['added'] == o['filled'] + o['canceled'] + o['rem']


def test_cancel_unknown_id_counted():
    """撤未知单 (ref 解析失败) → unknown_cancel, 不崩不产订单"""
    out = L.ledger([L.ev_cancel(999, 100, ms=34200100)])
    assert out['counters']['unknown_cancel'] == 1
    assert 999 not in out['orders']


def test_fill_beyond_remaining():
    """成交消费超剩余 (账坏信号): clamp + fill_excess 计数"""
    out = L.ledger([
        L.ev_add(1, 'B', 122800, '0', 300, ms=34200000),
        L.ev_fill(1, 500, ms=34200100),
    ])
    assert out['orders'][1]['rem'] == 0
    assert out['counters']['fill_excess'] == 1


def test_fill_unknown_id_counted():
    """成交 ref 解析失败 (SH 先成交后报 / 竞价虚拟) → unknown_fill"""
    out = L.ledger([L.ev_fill(424242, 100, ms=34200100)])
    assert out['counters']['unknown_fill'] == 1


def test_deterministic_same_ms_tiebreak():
    """同 ms 的 add 与 cancel 同单: 确定性 add 先 (文档化 tie-break), 结果与排序无关"""
    a = L.ledger([L.ev_add(1, 'B', 122800, '0', 100, ms=34200100),
                  L.ev_cancel(1, 100, ms=34200100)])
    b = L.ledger([L.ev_cancel(1, 100, ms=34200100),
                  L.ev_add(1, 'B', 122800, '0', 100, ms=34200100)])
    assert a['orders'][1]['rem'] == b['orders'][1]['rem'] == 0
    assert a['counters'] == b['counters'] == dict(unknown_fill=0, fill_excess=0,
                                                  unknown_cancel=0, cancel_excess=0,
                                                  dup_add=0)


def test_dup_add_detected():
    """同 id 二次 add (交易所委托号跨日复用/文件拼接错) → dup_add"""
    out = L.ledger([
        L.ev_add(7, 'B', 122800, '0', 100, ms=34200000),
        L.ev_add(7, 'B', 122800, '0', 100, ms=34200500),
    ])
    assert out['counters']['dup_add'] == 1


def test_multi_order_independence():
    """多单交错互不污染: 2 单独立守恒"""
    out = L.ledger([
        L.ev_add(1, 'B', 122800, '0', 1000, ms=34200000),
        L.ev_add(2, 'S', 123000, '0', 500, ms=34200010),
        L.ev_fill(1, 400, ms=34200100),   # 只消费单 1
        L.ev_cancel(2, 300, ms=34200100), # 只撤单 2
        L.ev_cancel(1, 600, ms=34200200),
    ])
    o1, o2 = out['orders'][1], out['orders'][2]
    assert (o1['rem'], o2['rem']) == (0, 200)
    for o in (o1, o2):
        assert o['added'] == o['filled'] + o['canceled'] + o['rem']


def test_final_balances_conservation_summary():
    """全账守恒: Σadded == Σfilled + Σcanceled + Σrem (每单独立成立则聚合成立)"""
    out = L.ledger([
        L.ev_add(1, 'B', 122800, '0', 1000, ms=1), L.ev_fill(1, 300, ms=2),
        L.ev_add(2, 'S', 123000, '0', 800, ms=3), L.ev_cancel(2, 500, ms=4),
    ])
    sums = L.conservation_summary(out['orders'])
    assert sums == dict(added=1800, filled=300, canceled=500, rem=1000)
