"""W1 qa/delta δ 滞后归因纯函数 TDD（红→绿）

口径（规格 §2 δ 滞后定律 + 校准备忘录方法）:
连续快照对 (S1@t1, S2@t2) 间, 对无撤单/成交活动的干净档位 (add-only level):
  Δsnap = v2−v1 应 == 窗内加单总量 A(t1,t2]; 若有差, 差量=δ 滞后在 S2 渲染时
  已生效的 t2 之后加单前缀 → 归因 lag = (首个使前缀总量==Δ 的加单时刻 − t2)。
桶: zero_lag(Δ==A≤t2) / over(Δ<A≤t2, S1 自身 δ 载入) / no_exact(前缀未达恰值)
   / lag_ms(命中) / lag_gt_window(500ms 内未达)。
存根击穿: 恒返回 fixed lag/恒 zero 的存根被逐样例 cls 断言击穿。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # lob_fact/
from core.qa import delta as D


def test_zero_lag_exact():
    """窗内加单与快照差精确一致 → zero_lag"""
    adds = [(34200100, 100), (34200200, 50)]   # (ms, vol), 窗 (34200000, 34200300]
    assert D.attrib_lag(adds, 34200000, 34200300, 150, window=500)['cls'] == 'zero_lag'


def test_partial_window_plus_lag_exact():
    """Δ=窗内 100 + 滞后 200@t2+130ms → lag=130ms"""
    adds = [(34200100, 100), (34201130, 200), (34201220, 50)]
    r = D.attrib_lag(adds, 34200000, 34201000, 300, window=500)
    assert r['cls'] == 'lag'
    # base = Σ≤t2 = 100; 前缀 @+130ms 达 300 == Δ ✓
    assert r['lag_ms'] == 130


def test_lag_exact_on_second_add():
    """t2 后第一笔量不够, 第二笔才恰达 → lag 取第二笔时刻 (t2=34201000)"""
    adds = [(34200100, 100), (34201100, 50), (34201300, 150)]
    r = D.attrib_lag(adds, 34200000, 34201000, 300, window=500)
    # base=100; @+100ms →150 不够; @+300ms →300 恰达 ✓
    assert r['cls'] == 'lag' and r['lag_ms'] == 300


def test_over_bucket():
    """Δ < 窗内加单 (S1 已载 δ 后加单) → over 桶（结构自解, 非错误）"""
    adds = [(34200100, 500)]
    r = D.attrib_lag(adds, 34200000, 34200300, 300, window=500)
    assert r['cls'] == 'over'


def test_no_exact_when_prefix_crosses():
    """前缀总量跨过 Δ（无恰等）→ no_exact"""
    adds = [(34200310, 200)]          # ≤t2 0; 200@+10 但 Δ=150 → 无恰等
    r = D.attrib_lag(adds, 34200000, 34200300, 150, window=500)
    assert r['cls'] == 'no_exact'


def test_lag_gt_window():
    """500ms 内加单总量不足 → lag_gt_window（δ>窗 只统计）"""
    adds = [(34200900, 300)]
    r = D.attrib_lag(adds, 34200000, 34200300, 300, window=500)
    assert r['cls'] == 'lag_gt_window'


def test_zero_delta_zero_adds():
    """Δ=0 且窗内无加单 → zero_lag（空闲档, 主体样本）"""
    r = D.attrib_lag([], 34200000, 34200300, 0, window=500)
    assert r['cls'] == 'zero_lag'


def test_boundary_add_at_t2_included():
    """t2 时刻加单属窗内 (半开 (t1,t2]) → 计入 base"""
    adds = [(34200300, 100)]
    r = D.attrib_lag(adds, 34200000, 34200300, 100, window=500)
    assert r['cls'] == 'zero_lag'
