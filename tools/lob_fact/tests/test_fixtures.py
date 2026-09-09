"""W1 金样 fixtures 确定性测试（红→绿）

断言来源 = fixtures/README.md 金样纪律:
  1. 全部金样文件 sha256 与 pins.sha256 逐一对账（编辑金样 → 红, 防静默改语义）
  2. 场景 CSV schema/语义合法性（kind 域、价格整数刻度、量>0、确定性加载）
  3. 真实切片: 时间窗/快照首帧/@9:30 锚点存在性
存根击穿: 空读/错读的 loader 会在"行数>0 + 逐值类型/内容"断言下失败。
"""
import os
import pandas as pd
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fixtures'))
import loader as F

FIXDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fixtures')
KINDS = {'PHASE', 'ADD', 'CANCEL', 'FILL'}
SCENARIOS = ['near_far_adds', 'market_sweep', 'fifo_queue', 'partial_cancel',
             'cancel_consumed', 'phases', 'dual_flow', 'sz_market_u']
REAL = [
    'real_sz_000155_20260803_orders_t3420.csv',
    'real_sz_000155_20260803_trades_t3420.csv',
    'real_sz_000155_20260803_snap_open.csv',
    'real_sh_600184_20260803_orders_t3420.csv',
    'real_sh_600184_20260803_trades_t3420.csv',
    'real_sh_600184_20260803_snap_open.csv',
]


def test_pins_all_golden_files_match():
    """金样纪律: sha256 全对账 → 空失配。任何 CSV 编辑必须同更 pin"""
    assert F.check_pins() == []


def test_scenario_semantics_valid():
    """每个场景: 合法 kind 域; ADD 量>0 价≥0; FILL 价>0 量>0; 确定性两次读一致"""
    for name in SCENARIOS:
        rows = F.load_scenario(name)
        assert rows, name
        for r in rows:
            assert r['kind'] in KINDS, (name, r)
            if r['kind'] == 'ADD':
                assert r['qty'] > 0 and r['id'] > 0, (name, r)
                assert r['side'] in ('B', 'S'), (name, r)
                # 价=0 合法仅限市价'1'/本方最优'U'（永不进簿语义场景）
                if r['price'] == 0:
                    assert r['otype'] in ('1', 'U'), (name, r)
            elif r['kind'] == 'FILL':
                assert r['qty'] > 0 and r['price'] > 0, (name, r)
            elif r['kind'] == 'CANCEL':
                assert r['qty'] > 0 and r['id'] > 0, (name, r)
            elif r['kind'] == 'PHASE':
                assert r['ms'] > 0, (name, r)
        # 确定性: 两次加载逐行相等
        assert F.load_scenario(name) == rows


def test_scenario_ms_nondecreasing_and_unique_ids_in_auction():
    """ms 非降（引擎时序前提）; 场景无重复 order id"""
    for name in SCENARIOS:
        rows = F.load_scenario(name)
        ms = [r['ms'] for r in rows]
        assert ms == sorted(ms), name
        ids = [r['id'] for r in rows if r['kind'] == 'ADD']
        assert len(ids) == len(set(ids)), name


def test_partial_cancel_excess_row_value():
    """partial_cancel 金样语义值: 超量撤单行 900 > 800 → 触发 excess 语义（W2 断言锚）"""
    rows = F.load_scenario('partial_cancel')
    excess = [r for r in rows if r['kind'] == 'CANCEL' and r['id'] == 2]
    assert excess[-1]['qty'] == 900  # 数值锚: 900>800 才构成超量 clamp 场景


def test_real_slices_window_and_anchor():
    """真实切片: 委托/成交窗 = 09:30:00.000-09:31:00.000 (HHMMSSmmm 93000000..93060000);
    快照首帧 = SZ 93000000 / SH 93002000（连续段首张, 硬基线锚点）; 非空"""
    for f in REAL:
        assert os.path.exists(os.path.join(FIXDIR, 'real', f)), f
    for suf, code in (('sz', '000155'), ('sh', '600184')):
        o = pd.read_csv(os.path.join(FIXDIR, 'real', f'real_{suf}_{code}_20260803_orders_t3420.csv'))
        t = pd.read_csv(os.path.join(FIXDIR, 'real', f'real_{suf}_{code}_20260803_trades_t3420.csv'))
        assert len(o) > 500 and len(t) > 300
        assert o['时间'].between(93000000, 93060000).all()
        assert t['时间'].between(93000000, 93060000).all()
        s = pd.read_csv(os.path.join(FIXDIR, 'real', f'real_{suf}_{code}_20260803_snap_open.csv'))
        assert len(s) == 1
        assert int(s['时间'].iloc[0]) == (93000000 if suf == 'sz' else 93002000)
        # 快照首帧有非零档（开盘排队簿非空）
        pcols = [c for c in s.columns if c.endswith('价1')]
        assert any((s[c] > 0).any() for c in pcols)
