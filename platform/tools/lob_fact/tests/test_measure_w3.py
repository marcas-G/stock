"""measure_w3 M1a 存现门语义 TDD（W3 校准: 门 = 档位存现率, 非 rank 对齐率）

断言来源 = W3 校准集 10 日实测（notes/w3_anchoring_memo.md §4）: rank 对齐率在
fast 日 0.80-0.94 = δ 边界 best-edge 换位瞬态（M4 守恒 0 违例 + presence 0.986-0.999
佐证无引擎缺档）; 平静日 ≥0.987 ≥ W1 97.6% 语义。→ M1 拆双口径: M1a 存现率
(逐日 + SZ/SH 池化 ≥ GATE_PRES) 是门; M1b rank 对齐率逐日报告不设门。

存根击穿: 真缺档日 (presence 崩) 必须 FAIL; fast 日 rank 0.80 不影响门 (用实测数值)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # lob_fact/
from diag import measure_w3 as M3


def _res(code, n_anchor, n_match, n_present):
    """run_day JSON 的 m1 相关形状（m1a_gate 只读 m1.n_present/n_anchor + code）"""
    return dict(code=code, m1=dict(n_anchor=n_anchor, n_match=n_match,
                                   n_present=n_present))


def test_m1a_gate_passes_fast_day_on_presence_not_rank():
    """W3 实测形状: 000021@20260706 rank 0.8030 (76145/94822) 但 presence 0.9862
    (93511/94822); 000155@20260803 presence 0.9993。旧 rank 门 (池化 0.8947) 会 FAIL
    → 门语义 = 存现率: 逐日 PASS + SZ 池化 188043/189424=0.9927 ≥ 0.97 → ok"""
    fast = _res('000021', 94822, 76145, 93511)
    calm = _res('000155', 94602, 93347, 94532)
    g = M3.m1a_gate([fast, calm])
    assert g['per_day'] == [True, True]
    assert g['ok'] is True
    assert g['sz'] >= 0.97
    assert abs(g['sz'] - round(188043 / 189424, 5)) < 1e-9   # 逐值验算, 非宽限掩蔽


def test_m1a_gate_fails_on_true_absence():
    """引擎真缺档日 (presence 0.8985 < 门) → FAIL: 丢档/丢消息的引擎不被存现门放过"""
    broken = _res('000155', 94602, 90000, 85000)
    g = M3.m1a_gate([broken])
    assert g['per_day'] == [False]
    assert g['ok'] is False


def test_m1a_gate_both_exchanges_pooled():
    """SZ/SH 分池; 单池空 (无该所日) 时该池不 FAIL (全 10 日跑批两池恒非空)"""
    sh = _res('600036', 94820, 81415, 94075)           # 600036@20251215 presence 0.9921
    g = M3.m1a_gate([sh])
    assert g['per_day'] == [True]
    assert g['sh'] >= 0.97 and g['ok'] is True


def test_m1a_gate_verdict_false_if_any_day_fails_pooled_ok():
    """单日崩 (presence 0.95) 即使池化 ≥0.97 → 逐日门 FAIL (ok=False): 池化不掩日"""
    a = _res('000155', 94602, 90000, 94532)
    b = _res('000021', 94822, 76145, 90000)            # 0.9492 < 门
    g = M3.m1a_gate([a, b])
    assert g['per_day'] == [True, False]
    assert g['ok'] is False
