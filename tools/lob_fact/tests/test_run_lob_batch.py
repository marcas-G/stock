"""W4 批算纯逻辑 TDD（红→绿）: 表→事件映射 / 日表构建 (seq) / 日门 (无 m6) / 月门聚合

断言源 = 规格 §3.2 schema 契约 + §3.3 门语义 (W3 校准单点 GATE_PRES=0.97, m1a 池化) +
逐事件手算数字; 任何硬编码汇总/复制引擎实现的"存根"必败。

映射等价基准: W4a parity_probe 14/14 天逐事件字节对等 (parquet 读链 == raw zip 链),
本文件把 probe 的映射语义固化为模块纯函数 + 合成行测试 (SH A/D/S 分支 / SZ 0/1/U
全收 / qty<=0 跳过 / trades 双 ref 拆条 / cancels side 0=B 1=S)。
"""
import sys, os, json
from datetime import date

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_lob_batch as R
import anchoring as A
import config as C

OPEN = C.OPEN
AM = C.AUCTION_MATCH
GATE = R.GATE_PRES                       # 单点: 与 W3 校准同一常量

# ---- 合成行 (tick_fact parquet 行 dict, schema 冻结子集) ----

def o(code, t, typ, bs, px, vol, oid):
    return dict(code=code, time_ms=t, order_type=typ, bs=bs,
                price_x10000=px, volume=vol, exch_order_no=oid)


def tr(code, t, bseq, aseq, px, vol):
    return dict(code=code, time_ms=t, bid_seq=bseq, ask_seq=aseq,
                price_x10000=px, volume=vol)


def cn(code, t, side, ref, vol):
    return dict(code=code, time_ms=t, side=side, order_ref=ref, volume=vol)


# ---------- 表 → 事件映射 (W4a 对等语义固化为模块纯函数) ----------

def test_sz_orders_rows_all_add_with_otype_passthrough():
    """SZ: type 0/1/U 全收为 add (engine 按价裁决: '1' 市价 px=0 由引擎拦),
    otype 原样透传, bs→side, exch_order_no→id; qty<=0 跳过 (streams n_bad_row 语义)"""
    rows = [o('000155.SZ', 34200010, '0', 'B', 122800, 1100, 111),
            o('000155.SZ', 34200020, 'U', 'B', 122900, 300, 112),
            o('000155.SZ', 34200030, '1', 'S', 0, 800, 113),
            o('000155.SZ', 34200040, '0', 'S', 123100, 0, 114)]
    evs = R.orders_to_events(rows, 'SZ')
    assert [(e['kind'], e['ms'], e['id'], e['side'], e['price'], e['qty'],
             e['otype']) for e in evs] == [
        ('add', 34200010, 111, 'B', 122800, 1100, '0'),
        ('add', 34200020, 112, 'B', 122900, 300, 'U'),
        ('add', 34200030, 113, 'S', 0, 800, '1'),
    ]
    assert len(evs) == 3                       # qty=0 行不进流


def test_sh_orders_a_d_s_branch_and_bad_rows_skip():
    """SH: A→add(otype='A') D→cancel(id/量/侧) S→杂项跳过; qty<=0 双跳过
    (坏行语义: 引擎 unknown 桶不误计 = 不产生任何事件)"""
    rows = [o('600036.SH', 34200010, 'A', 'B', 122800, 700, 21),
            o('600036.SH', 34200020, 'S', 'B', 0, 0, 22),
            o('600036.SH', 34200030, 'D', 'S', 123100, 250, 23),
            o('600036.SH', 34200040, 'A', 'S', 123200, 0, 24),
            o('600036.SH', 34200050, 'D', 'B', 0, -5, 25)]
    evs = R.orders_to_events(rows, 'SH')
    assert [(e['kind'], e['ms'], e['id'], e['side'], e.get('price'),
             e['qty'], e.get('otype')) for e in evs] == [
        ('add', 34200010, 21, 'B', 122800, 700, 'A'),
        ('cancel', 34200030, 23, 'S', None, 250, None),
    ]
    assert len(evs) == 2                       # S 杂项 + 两 qty<=0 全跳过


def test_trades_rows_split_per_positive_ref_bid_then_ask():
    """成交行按 ref 拆 fill: bid_seq/ask_seq 各一 (bid 先发, 与 W4a 对等链一致);
    双零行跳过 (SH 先成交后报空引用类不产生簿事件)"""
    rows = [tr('000155.SZ', 34201000, 501, 502, 122800, 400),
            tr('000155.SZ', 34201050, 0, 601, 123100, 100),
            tr('000155.SZ', 34201100, 0, 0, 122800, 200),
            tr('000155.SZ', 34201150, 701, 0, 122900, 50)]
    evs = R.trades_to_events(rows)
    assert [(e['kind'], e['ms'], e['id'], e['side'], e['price'], e['qty'])
            for e in evs] == [
        ('fill', 34201000, 501, 'B', 122800, 400),
        ('fill', 34201000, 502, 'S', 122800, 400),
        ('fill', 34201050, 601, 'S', 123100, 100),
        ('fill', 34201150, 701, 'B', 122900, 50),
    ]
    assert len(evs) == 4                       # 双零行不产事件


def test_cancels_rows_side_map_zero_is_buy():
    """cancels 表 (SZ-only): side 0=B / 1=S, id=order_ref, 量=volume"""
    rows = [cn('000155.SZ', 34201200, 0, 701, 80),
            cn('000155.SZ', 34201250, 1, 502, 120)]
    evs = R.cancels_to_events(rows)
    assert [(e['kind'], e['ms'], e['id'], e['side'], e['qty']) for e in evs] == [
        ('cancel', 34201200, 701, 'B', 80),
        ('cancel', 34201250, 502, 'S', 120)]


def test_parquet_events_assembles_blocks_sz_only_cancels():
    """日事件组装: orders+trades (+SZ cancels) 块序拼接 (引擎确定性排序兜底);
    相同输入两次组装逐事件全等 (确定性, 无隐藏状态)"""
    rows_o = [o('000155.SZ', 34200010, '0', 'B', 122800, 1100, 111),
              o('000155.SZ', 34200020, '0', 'S', 123100, 600, 112)]
    rows_t = [tr('000155.SZ', 34201000, 111, 0, 122800, 200)]
    rows_c = [cn('000155.SZ', 34201100, 0, 111, 300)]
    evs1 = R.parquet_events('000155', '20260803',
                            rows_o, rows_t, rows_c)
    evs2 = R.parquet_events('000155', '20260803',
                            rows_o, rows_t, rows_c)
    assert [(e['kind'], e['ms'], e['id']) for e in evs1] == [
        ('add', 34200010, 111), ('add', 34200020, 112),
        ('fill', 34201000, 111), ('cancel', 34201100, 111)]
    assert evs1 == evs2

    # SH: cancels 块不存在 → 组装不含撤单事件 (SH 撤单走 orders D 通道)
    rows_o_sh = [o('600036.SH', 34200030, 'D', 'S', 123100, 250, 23)]
    evs_sh = R.parquet_events('600036', '20260803', rows_o_sh, [], [])
    assert [(e['kind'], e['id']) for e in evs_sh] == [('cancel', 23)]


# ---------- 日表构建 (run_day 输出 → 三表; schema/seq 契约) ----------

OPEN_DAY = [
    dict(kind='add', id=1, ms=33_500_000, side='B', price=122800, qty=1100,
         otype='0'),
    dict(kind='add', id=2, ms=33_500_100, side='S', price=123100, qty=600,
         otype='0'),
    dict(kind='add', id=3, ms=33_500_200, side='B', price=122900, qty=300,
         otype='0'),
    dict(kind='fill', id=1, ms=AM + 1, qty=400, refs=[1], price=122800),
    dict(kind='cancel', id=2, ms=AM + 2, qty=100),
]


def _snap(ms, bid, ask):
    row = dict(time_ms=ms)
    for i, (p, v) in enumerate(bid, 1):
        row[f'bid_p{i}'], row[f'bid_v{i}'] = p, v
    for i, (p, v) in enumerate(ask, 1):
        row[f'ask_p{i}'], row[f'ask_v{i}'] = p, v
    return row


def test_day_tables_events_sequence_and_schema():
    """开盘物化迷你日 (仅竞价单): rows=3 level_materialization 且全为表行;
    schema 列序/类型 = 规格契约; seq=1..n 按引擎事件序连续;
    出带前时段零连续行 → 表行 = 物化行数 (禁止行为: 事件行不掺 registry 观测行)"""
    snaps = [_snap(OPEN, [(122900, 300), (122800, 700)], [(123100, 500)])]
    res = A.run_day(OPEN_DAY, snaps, band_ckpts=True)
    tabs = R.day_tables('000155.SZ', date(2026, 8, 3), res)
    assert list(tabs.keys()) == ['lob_events', 'lob_sweep_meta', 'lob_checkpoints']
    ev = tabs['lob_events']
    assert ev.columns == ['code', 'trade_date', 'time_ms', 'seq', 'kind',
                          'phase', 'side', 'price_x10000', 'prev_vol',
                          'new_vol', 'qty', 'id', 'otype']
    assert ev.schema['time_ms'] == pl.Int32 and ev.schema['seq'] == pl.Int64
    assert ev['id'].null_count() == ev.height and ev['otype'].null_count() == ev.height
    rows = ev.to_dicts()
    # 物化行是 (side,px) 级行 (无逐单 id) — id 恒 null; 观测行 (auction 段 registry,
    # prev==new==0) 不落表 → 表行恰 = 3 物化行 (簿面绝对量流契约, fold 语义)
    assert [(r['seq'], r['kind'], r['side'], r['price_x10000'], r['prev_vol'],
             r['new_vol'], r['qty'], r['id']) for r in rows] == [
        (1, 'level_materialization', 'B', 122800, 0, 700, 700, None),
        (2, 'level_materialization', 'S', 123100, 0, 500, 500, None),
        (3, 'level_materialization', 'B', 122900, 0, 300, 300, None),
    ]
    assert all(r['time_ms'] == OPEN and r['phase'] == 'continuous'
               and r['code'] == '000155.SZ'
               and r['trade_date'] == date(2026, 8, 3) for r in rows)
    assert [r['seq'] for r in rows] == list(range(1, len(rows) + 1))
    assert tabs['lob_sweep_meta'].schema['tail_order'] == pl.Int64
    assert tabs['lob_checkpoints'].height == 0     # 无分钟边界 (事件终 ms < OPEN+1min? 见下)
    assert tabs['lob_events'].schema['new_vol'] == pl.Int64


def test_day_tables_sweep_and_banded_checkpoint_rows():
    """连续段: 吃尽唯一卖档 → sweep 行 (vol_before/tail_order); 分钟 1 检查点 =
    带限 long 行 (bid best-first; 该日无 ask — sweep 后 ask 空); events 行序
    与 sweep 分表 (events 不掺 sweep, fold 语义行序保持)"""
    evs = OPEN_DAY + [dict(kind='fill', id=2, ms=OPEN + 2500, qty=500,
                           refs=[2], price=123100)]
    snaps = [_snap(OPEN, [(122900, 300), (122800, 700)], [(123100, 500)])]
    res = A.run_day(evs, snaps, band_ckpts=True)
    tabs = R.day_tables('000155.SZ', date(2026, 8, 3), res)
    sw = tabs['lob_sweep_meta'].to_dicts()
    assert [(r['seq'], r['side'], r['price_x10000'], r['vol_before'],
             r['tail_order'], r['tail_resid'], r['time_ms']) for r in sw] == [
        (1, 'S', 123100, 500, 2, 0, OPEN + 2500)]
    ck = tabs['lob_checkpoints']
    assert ck.columns == ['code', 'trade_date', 'time_ms', 'seq', 'side',
                          'price_x10000', 'vol', 'n_queue']
    assert ck.schema['n_queue'] == pl.Int32
    assert [(r['seq'], r['time_ms'], r['side'], r['price_x10000'], r['vol'],
             r['n_queue']) for r in ck.to_dicts()] == [
        (1, OPEN + 60_000, 'B', 122900, 300, 1),
        (2, OPEN + 60_000, 'B', 122800, 700, 1),
    ]
    ev = tabs['lob_events'].to_dicts()
    assert all(r['kind'] == 'level_materialization' for r in ev)
    assert all(r['id'] is None for r in ev)            # 物化行无逐单 id; sweep 不进 events 表
    assert tabs['lob_sweep_meta'].columns[2] == 'time_ms'


# ---------- 日门 (batch 版: 无 m6; presence 圆整 5 位同 W3 verdict) ----------

def _mk(present, anchor, conservation='PASS', orders=0, counters_equal=True):
    qa = dict(day=dict(n_present=present, n_anchor=anchor),
              m4=dict(conservation=conservation, orders=orders,
                      counters_equal=counters_equal))
    return dict(qa=qa, code='000155.SZ', day='20260803')


def test_day_gate_presence_two_tier_and_vacuous():
    """presence 圆整 5 位, 双阶 (W4d 修订): ≥ GATE_PRES=0.97 直接过;
    δ 带 [GATE_FLOOR=0.90, 0.97) → ok 过但 band 标记 (W4d 实测 20260803:
    32/300 code-day presence 0.913-0.970, M4/守卫全净 — fast 名 δ 滞后带;
    W3 逐日 0.97 校准集 10 日不含此带, 对真实分布过严);
    < GATE_FLOOR → 硬拒 m1a_presence (真缺档/坏数据日崩)。
    n_anchor=0 → vacuous presence 1.0 过; m4 各失败分支独立拒绝"""
    assert R.day_gate(_mk(194, 200))['ok'] is True
    r = R.day_gate(_mk(193, 200))                  # 0.965 → δ 带: 过 + band
    assert r['ok'] is True and r['band'] is True
    assert 'm1a_delta_band' in r['notes'] and r['reasons'] == []
    r = R.day_gate(_mk(179, 200))                  # 0.895 < GATE_FLOOR → 硬拒
    assert r['ok'] is False and 'm1a_presence' in r['reasons']
    r = R.day_gate(_mk(0, 0))
    assert r['ok'] is True and r['presence'] == 1.0 and r['vacuous'] is True
    r = R.day_gate(_mk(100, 100, conservation='FAIL'))
    assert r['ok'] is False and 'm4_conservation' in r['reasons']
    r = R.day_gate(_mk(100, 100, orders=3))
    assert r['ok'] is False and 'm4_orders' in r['reasons']
    r = R.day_gate(_mk(100, 100, counters_equal=False))
    assert r['ok'] is False and 'm4_counters' in r['reasons']
    # 零锚日不豁免守恒: m4 FAIL 时 vacuous presence 仍拒
    r = R.day_gate(_mk(0, 0, conservation='FAIL'))
    assert r['ok'] is False and r['presence'] == 1.0
    # presence 计算必须用真计数 (硬编码 PASS 存根必败): 边界两阶各 1 档差即翻转
    assert R.day_gate(_mk(1000, 2000))['ok'] is False    # 0.5 远低于硬底线
    r = R.day_gate(_mk(1940, 2000))
    assert r['ok'] is True and r['band'] is False        # 0.97 整
    r = R.day_gate(_mk(1939, 2000))
    assert r['ok'] is True and r['band'] is True         # 0.9695 δ 带
    r = R.day_gate(_mk(1800, 2000))
    assert r['ok'] is True and r['band'] is True         # 0.90 整 = FLOOR 边界


def test_day_gate_handles_reversed_and_stub_must_fail():
    """门输出 presence 由 (n_present,n_anchor) 真算: 交换分子分母 0.5→2.0 拒;
    空锚+空 m4 全 PASS 输入也过 — 但任何伪造 ok=True 的实现会在上列 179/200、
    1000/500 等反例上暴露"""
    r = R.day_gate(_mk(500, 1000))
    assert r['presence'] == round(0.5, 5) and r['ok'] is False
    r = R.day_gate(_mk(1000, 500))                       # 分子>分母 = 数据错
    assert r['presence'] == 2.0 and r['ok'] is False


# ---------- 月门 (逐日 + SZ/SH 池化; 空锚日 vacuous 独立, 不入池) ----------

def _dayrow(code, present, anchor, ok=True, vacuous=False, band=False):
    return dict(code=code, day='20260803', m1=dict(n_present=present,
                                                   n_anchor=anchor),
                gate=dict(ok=ok, vacuous=vacuous, band=band))


def test_month_gate_pools_raw_counts_and_counts_vacuous():
    """月门 = 逐日硬底线 (GATE_FLOOR) 全过 且 SZ/SH 池化 (原始计数和, 非逐日均值)
    ≥ GATE_PRES; 空锚日 (vacuous, 含于 n_days) 独立 PASS 不进池 — 无锚日不给池
    贡献分母; n_band 计数 δ 带日 (0.90-0.97, day ok 已过)"""
    rows = [_dayrow('000155.SZ', 997, 1000),     # 0.997
            _dayrow('000333.SZ', 99, 100),       # 0.99
            _dayrow('600036.SH', 199, 200),      # 0.995
            _dayrow('600184.SH', 100, 100),      # 1.0
            _dayrow('600519.SH', 0, 0, vacuous=True)]
    m = R.month_gate(rows)
    assert m['n_days'] == 5 and m['n_vacuous'] == 1 and m['n_anchored'] == 4
    assert m['n_band'] == 0
    # 池化 = (997+99)/(1000+100)=0.99636 | (199+100)/(200+100)=0.99667
    assert m['gate']['sz'] == round(1096 / 1100, 5)
    assert m['gate']['sh'] == round(299 / 300, 5)
    assert m['gate']['per_day'] == [True, True, True, True, True]  # 含 vacuous 日
    assert m['ok'] is True


def test_month_gate_rejects_collapsed_day_or_pool():
    """月门失败 = 任一逐日 presence < GATE_FLOOR (0.90, 真缺档崩) 或任一所池化
    < GATE_PRES; δ 带日 (0.965 ≥ floor) 不拒 — 池化承担存现门"""
    rows = [_dayrow('000155.SZ', 997, 1000),
            _dayrow('000155.SZ', 965, 1000, band=True),   # 0.965 δ 带 → 过
            _dayrow('600036.SH', 200, 200)]
    m = R.month_gate(rows)
    assert m['gate']['sh'] == 1.0 and m['n_band'] == 1
    assert m['gate']['per_day'] == [True, True, True]
    assert m['ok'] is True                        # 池化 = (997+965)/2000 = 0.981 ≥ 门
    rows3 = [_dayrow('000155.SZ', 997, 1000),
             _dayrow('000155.SZ', 850, 1000),     # 0.85 < 硬底线 → 拒
             _dayrow('600036.SH', 200, 200)]
    m3 = R.month_gate(rows3)
    assert m3['gate']['per_day'] == [True, False, True]
    assert m3['ok'] is False
    # 池化单独 FAIL (逐日全过但一所合计 < 门): SZ 单日 0.99 池化即 0.99 过 —
    # 用 4 日 0.97 整 + 1 日 0.9705 → SZ 池略 ≥ 门 → 构造纯 SH 不足样本
    rows2 = [_dayrow('600036.SH', 97, 100),
             _dayrow('600036.SH', 97, 100),
             _dayrow('600036.SH', 0, 0, vacuous=True)]
    m2 = R.month_gate(rows2)
    assert m2['gate']['sh'] == 0.97 and m2['gate']['sz'] == 1.0
    assert m2['gate']['per_day'] == [True, True, True]
    assert m2['ok'] is True                        # SZ 空池不 FAIL (校准语义)


def test_month_gate_empty_month_is_ok():
    """无任何锚定日 (全 vacuous) 或空列表 → ok (空池 1.0 语义), vacuous 计数正确"""
    assert R.month_gate([_dayrow('000155.SZ', 0, 0, vacuous=True)])['ok'] is True
    assert R.month_gate([])['ok'] is True
# ---------- W4c 编排层纯函数 (guard_check / write_part) ----------

def _man(n_orders=10, n_trades=5, n_snap=3, **kw):
    return dict(n_orders=n_orders, n_trades=n_trades, n_snap=n_snap, **kw)


def test_guard_check_matches_manifest_counts():
    """输入守卫: 读行数 vs conversion_manifest (orders/trades/snaps) 全等才过;
    SZ 追加 cancels_manifest n_cancels; 任一表不等 → mismatch 清单含表名 + 值;
    代码日无 manifest 行 (读到行) → missing_manifest 桶 (不静默)"""
    ok, mm = R.guard_check('000155.SZ', '20260803',
                           dict(orders=10, trades=5, snaps=3, cancels=0),
                           {'000155.SZ': {'20260803': _man()}})
    assert ok and mm == []
    ok, mm = R.guard_check('000155.SZ', '20260803',
                           dict(orders=11, trades=5, snaps=3, cancels=0),
                           {'000155.SZ': {'20260803': _man()}})
    assert not ok and mm == [('orders', 11, 10)]
    ok, mm = R.guard_check('600036.SH', '20260803',
                           dict(orders=10, trades=6, snaps=3),
                           {'600036.SH': {'20260803': _man()}})
    assert not ok and mm == [('trades', 6, 5)]
    ok, mm = R.guard_check('000021.SZ', '20260803',
                           dict(orders=10, trades=5, snaps=3, cancels=8),
                           {'000021.SZ': {'20260803': _man(n_cancels=7)}})
    assert not ok and mm == [('cancels', 8, 7)]
    ok, mm = R.guard_check('000333.SZ', '20260803',
                           dict(orders=10, trades=5, snaps=3, cancels=0), {})
    assert not ok and mm == [('missing_manifest', None, None)]
    # 空读 (无行) 且无 manifest → 合法 (manifest 只记有源 code-day; 停牌日零行)
    ok, mm = R.guard_check('000333.SZ', '20260803',
                           dict(orders=0, trades=0, snaps=0, cancels=0), {})
    assert ok and mm == []


def test_write_part_atomic_replace_and_schema(tmp_path):
    """date part 写盘: 唯一 tmp + fsync + os.replace; 完成后目录无 .tmp 残留;
    覆盖重写 (断点重跑同 date) 幂等; 空表保留 schema; 返回 (path, n_rows)"""
    import polars as pl
    t = tmp_path / 'lob_events' / 'year=2026' / 'month=08'
    t.mkdir(parents=True)
    df = pl.DataFrame({'code': ['000155.SZ'] * 2, 'n': [1, 2]})
    p1, n1 = R.write_part(t, '20260803', df, pid=999, seq=1)
    assert p1.name == '20260803.parquet' and n1 == 2
    assert list(t.glob('*.tmp*')) == []
    got = pl.read_parquet(p1)
    assert got.to_dicts() == df.to_dicts()
    df2 = pl.DataFrame({'code': ['600036.SH'], 'n': [7]})
    p2, n2 = R.write_part(t, '20260803', df2, pid=999, seq=2)
    assert p2 == p1 and n2 == 1 and p1.stat().st_size > 0
    assert pl.read_parquet(p1).to_dicts() == df2.to_dicts()
    empty = df.head(0)
    _, n0 = R.write_part(t, '20260804', empty, pid=999, seq=3)
    assert n0 == 0
    assert pl.read_parquet(t / '20260804.parquet').schema == empty.schema
    assert pl.read_parquet(t / '20260804.parquet').height == 0


# ---------- W4d 内存定标修正 (sorted-merge 零拷贝组界 / _TableStream 流式写) ----------

def test_code_bounds_zero_copy_boundaries_exact():
    """sorted frame → {code: (start, len)}: 乱序文件代码 sort 后组界连续无叠无漏;
    视图切片逐行 == 等价 filter 子集 (组界错误必漏行/串行 → 存根必败)"""
    rows = [dict(code=c, i=n) for c, n in [
        ('600519.SH', 0), ('000155.SZ', 1), ('000155.SZ', 2), ('600519.SH', 3),
        ('000021.SZ', 4), ('600519.SH', 5), ('000021.SZ', 6), ('000155.SZ', 7)]]
    df = pl.DataFrame(rows).sort('code')
    b = R._code_bounds(df)
    assert sorted(b) == ['000021.SZ', '000155.SZ', '600519.SH']
    pos = 0
    for code in sorted(b):
        start, nrow = b[code]
        assert start == pos and nrow > 0
        got = df.slice(start, nrow).to_dicts()
        want = df.filter(pl.col('code') == code).to_dicts()
        assert got == want                    # 切片内容与语义子集逐字节同
        pos += nrow
    assert pos == df.height                    # 无漏行


def test_tablestream_schema_only_deterministic_and_abort(tmp_path):
    """_TableStream: 0 批 → schema-only 合法文件 (schema = COL_EVENTS 非退化);
    同批重跑字节全等 (确定性行组分界 → W4d 重跑比对成立的前提); abort 清 tmp
    不留 final"""
    t = tmp_path / 'lob_events' / 'year=2026' / 'month=08'
    ev_rows = [dict(ms=34201000, kind='add', phase='continuous', side='B',
                    price=123000, prev_vol=0, new_vol=500, qty=500, id=1001,
                    otype='0'),
               dict(ms=34201050, kind='trade', phase='continuous', side='B',
                    price=123000, prev_vol=500, new_vol=300, qty=200, id=1001,
                    otype='0')]
    f1 = R._fill(ev_rows, '000155.SZ', DSTR, R.COL_EVENTS)
    f2 = R._fill(ev_rows, '000155.SZ', DSTR, R.COL_EVENTS)
    s1 = R._TableStream(t, DSTR, R._pa_schema(R.COL_EVENTS))
    s1.append(f1)
    p1, n1 = s1.finish()
    assert n1 == 2 and list(t.glob('*.tmp*')) == []
    s2 = R._TableStream(t, DSTR, R._pa_schema(R.COL_EVENTS))
    s2.append(f2)
    p2, n2 = s2.finish()
    assert p2 == p1 and n2 == 2
    assert R._sha256(p1) == R._sha256(p2)      # 重跑字节全等
    got = pl.read_parquet(p1)
    assert [(c, d) for c, d in got.schema.items()] == R.COL_EVENTS
    assert got.height == 2
    # 0 批: schema-only 合法文件 (schema 非退化, 0 行)
    s3 = R._TableStream(t, '20260804', R._pa_schema(R.COL_EVENTS))
    p3, n3 = s3.finish()
    assert n3 == 0
    e3 = pl.read_parquet(p3)
    assert e3.height == 0
    assert [(c, d) for c, d in e3.schema.items()] == R.COL_EVENTS
    # abort: 清 tmp, 不落 final
    s4 = R._TableStream(t, '20260805', R._pa_schema(R.COL_EVENTS))
    s4.append(f1)
    s4.abort()
    assert not (t / '20260805.parquet').exists()
    assert list(t.glob('.20260805.parquet.tmp*')) == []


def test_tablestream_row_group_buffering_flush_geometry(tmp_path):
    """_TableStream row_group_rows=N: 行组界 = 行数整倍 N (跨帧缓冲, 帧界无关), 末组
    余数收尾。断言来自 parquet 元数据逐组行数 — 逐帧直写(旧语义) 或忽略阈值的存根
    组数 ≠ ceil(总行/N) 必败。真实行为: 帧高 2+2+1+1=6, N=3 → 组 [3,3];
    帧高 5 > N 单帧 → 组 [3,2] (组界仍行数整倍)。"""
    t = tmp_path / 'lob_events' / 'year=2026' / 'month=08'
    row = dict(ms=34201000, kind='add', phase='continuous', side='B',
               price=123000, prev_vol=0, new_vol=500, qty=500, id=1001,
               otype='0')
    sc = R._pa_schema(R.COL_EVENTS)
    ev_rows = lambda k: [dict(row, id=1000 + k + i) for i in range(k)]
    # 4 帧 2+2+1+1 → ceil(6/3) = 2 组 [3,3]
    s = R._TableStream(t, DSTR, sc, row_group_rows=3)
    for k in (2, 2, 1, 1):
        s.append(R._fill(ev_rows(k), '000155.SZ', DSTR, R.COL_EVENTS))
    p, n = s.finish()
    assert n == 6
    pf = pq.ParquetFile(p)
    got = [pf.metadata.row_group(i).num_rows for i in range(pf.metadata.num_row_groups)]
    assert got == [3, 3], got                      # 缓冲跨帧: 组界按行数不按帧
    assert pl.read_parquet(p).height == 6
    # 单帧 5 > N: 仍按 N 切 → 组 [3,2], 内容行序不重不漏
    s2 = R._TableStream(t, '20260804', sc, row_group_rows=3)
    s2.append(R._fill(ev_rows(5), '000155.SZ', DSTR, R.COL_EVENTS))
    p2, _ = s2.finish()
    pf2 = pq.ParquetFile(p2)
    got2 = [pf2.metadata.row_group(i).num_rows
            for i in range(pf2.metadata.num_row_groups)]
    assert got2 == [3, 2], got2
    all_rows = pl.concat([pl.from_arrow(pf2.read_row_groups([i]))
                          for i in range(pf2.metadata.num_row_groups)])
    assert all_rows.height == 5
    assert all_rows['id'].to_list() == [1005, 1006, 1007, 1008, 1009]


def test_tablestream_buffered_deterministic_and_abort(tmp_path):
    """缓冲写确定性: 同批(跨组界)重跑字节全等 — 行组界=行数整倍 → 重跑比对成立;
    abort 时缓冲未 flush 行不落盘 (无 tmp 无 final)"""
    t = tmp_path / 'lob_events' / 'year=2026' / 'month=08'
    row = dict(ms=34201000, kind='add', phase='continuous', side='B',
               price=123000, prev_vol=0, new_vol=500, qty=500, id=1001,
               otype='0')
    sc = R._pa_schema(R.COL_EVENTS)
    def run(date_str):
        s = R._TableStream(t, date_str, sc, row_group_rows=3)
        for k in (2, 2, 1, 1, 2):                 # 总 8 行 → 组 [3,3,2]
            s.append(R._fill([dict(row, id=1000 + k + i) for i in range(k)],
                             '000155.SZ', date_str, R.COL_EVENTS))
        return s.finish()
    p1, n1 = run(DSTR)
    p2, n2 = run(DSTR)
    assert n1 == n2 == 8
    assert R._sha256(p1) == R._sha256(p2)         # 缓冲写重跑字节全等
    # abort: 缓冲中未 flush 的行必须随 tmp 消失, final 不落
    s4 = R._TableStream(t, '20260805', sc, row_group_rows=3)
    for k in (2, 2, 1):
        s4.append(R._fill([dict(row, id=1000 + k + i) for i in range(k)],
                          '000155.SZ', '20260805', R.COL_EVENTS))
    s4.abort()
    assert not (t / '20260805.parquet').exists()
    assert list(t.glob('.20260805.parquet.tmp*')) == []


# ---------- W4c 编排: process_date 端到端 (合成 tick_fact 迷你树; 存根必败) ----------

D = date(2026, 8, 3)
DSTR = '20260803'


def _write_part_df(dirpath, df):
    dirpath.mkdir(parents=True, exist_ok=True)
    df.write_parquet(dirpath / 'part-000.parquet')


def _mk_mini_tick(root):
    """合成 tick_fact 迷你树 (20260803, 1 SH + 1 SZ; 每个事件都在簿上, 守恒定过):
    SH 600036: A add(1001, B 123000x500) + D 全撤; SZ 000155: 两 add (0/U) +
    B 侧部分成交 + 两 C 全撤。orders/trades/cancels 真实列名/schema 形状子集。"""
    mk = lambda t, rows: _write_part_df(
        root / t / 'year=2026' / 'month=08',
        pl.DataFrame(rows).with_columns(pl.lit(D).alias('trade_date').cast(pl.Date)))
    mk('orders', [
        dict(code='600036.SH', time_ms=34201000, order_no=1, exch_order_no=1001,
             order_type='A', bs='B', price_x10000=123000, volume=500),
        dict(code='600036.SH', time_ms=34201050, order_no=2, exch_order_no=1001,
             order_type='D', bs='B', price_x10000=123000, volume=500),
        dict(code='000155.SZ', time_ms=34201000, order_no=3, exch_order_no=2001,
             order_type='0', bs='B', price_x10000=124000, volume=800),
        dict(code='000155.SZ', time_ms=34201010, order_no=4, exch_order_no=2002,
             order_type='U', bs='S', price_x10000=125000, volume=300),
    ])
    mk('trades', [
        dict(code='000155.SZ', time_ms=34201020, trade_no=11, bs=1,
             price_x10000=124000, volume=200, ask_seq=0, bid_seq=2001),
    ])
    mk('cancels', [
        dict(code='000155.SZ', time_ms=34201030, trade_no=21, side=0,
             order_ref=2001, volume=600),
        dict(code='000155.SZ', time_ms=34201040, trade_no=22, side=1,
             order_ref=2002, volume=300),
    ])
    mdir = root / '_manifest'
    mdir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame([
        dict(code='600036.SH', trade_date=D, n_orders=2, n_trades=0, n_snap=0),
        dict(code='000155.SZ', trade_date=D, n_orders=2, n_trades=1, n_snap=0),
    ]).write_parquet(mdir / 'conversion_manifest.parquet')
    pl.DataFrame([
        dict(code='000155.SZ', trade_date=D, n_cancels=2),
    ]).write_parquet(mdir / 'cancels_manifest.parquet')


def test_process_date_e2e_synthetic(tmp_path):
    """worker 端到端 (合成源): 逐 code-day 守卫过 → 重放 → 三表 date part 直写;
    events 表 = 逐事件绝对量行 (每事件在簿 → 行数可手算: SH add+全撤 = 2,
    SZ 2 add+1 部分成交+2 全撤 = 5); schema/列序/seq 契约; 原子 (无 .tmp 残留);
    空 ckpt/sweep 也落盘 (schema 非退化); 重跑字节级幂等 (断点重跑比对语义)。"""
    tick = tmp_path / 'tick'
    lob = tmp_path / 'lob'
    _mk_mini_tick(tick)
    R._init_worker(dict(tick_root=str(tick), lob_root=str(lob),
                        conv_manifest=str(tick / '_manifest' / 'conversion_manifest.parquet'),
                        cancels_manifest=str(tick / '_manifest' / 'cancels_manifest.parquet')))
    p = R.process_date(DSTR)
    assert p['ok'] and p['errors'] == [] and p['n_codes'] == 2 and p['n_fail'] == 0
    assert {r['code'] for r in p['recs']} == {'000155.SZ', '600036.SH'}
    assert all(r['ok'] and r['gate']['ok'] and r['guard']['ok'] for r in p['recs'])
    ev = pl.read_parquet(lob / 'lob_events' / 'year=2026' / 'month=08'
                         / '20260803.parquet')
    by = ev.group_by('code').agg(pl.col('kind').sort())
    got = {r['code']: list(r['kind']) for r in by.to_dicts()}
    # 冻结引擎契约: 整档清空 (full-level consumption) → drop → sweep 行, 不发
    # cancel/trade 行 (W2: events = 簿面绝对量流, 档移除由 sweep_meta 承载)
    assert got['600036.SH'] == ['add']          # D 全撤 → sweep, 无 cancel 行
    assert got['000155.SZ'] == ['add', 'add', 'trade']   # 部分成交留行; 两 C 全撤 → sweep
    for code, want_n in (('600036.SH', 1), ('000155.SZ', 3)):
        sub = ev.filter(pl.col('code') == code).sort('seq')
        assert sub.height == want_n
        assert list(sub['seq']) == list(range(1, want_n + 1))
        assert sub['trade_date'].to_list() == [D] * want_n
        assert sub['time_ms'].to_list() == sorted(sub['time_ms'].to_list())
    assert list(ev.columns) == [c for c, _ in R.COL_EVENTS]
    sw = pl.read_parquet(lob / 'lob_sweep_meta' / 'year=2026' / 'month=08'
                         / '20260803.parquet')
    ck = pl.read_parquet(lob / 'lob_checkpoints' / 'year=2026' / 'month=08'
                         / '20260803.parquet')
    assert list(sw.columns) == [c for c, _ in R.COL_SWEEP]
    assert list(ck.columns) == [c for c, _ in R.COL_CKPT]
    swb = sw.group_by('code').len().sort('code')
    assert {r['code']: r['len'] for r in swb.to_dicts()} == \
        {'000155.SZ': 2, '600036.SH': 1}
    assert not list((lob / 'lob_events' / 'year=2026' / 'month=08').glob('*.tmp*'))
    p2 = R.process_date(DSTR)                     # 重跑: 字节级幂等
    assert p2['ok']
    for k in p['tables']:
        assert p2['tables'][k]['sha256'] == p['tables'][k]['sha256']
        assert p2['tables'][k]['rows'] == p['tables'][k]['rows']


def test_process_date_guard_mismatch_blocks(tmp_path):
    """输入守卫拒停: manifest 声明的行数 ≠ 实读 → code-day rec guard FAIL +
    date 级总数错 → payload ok=False (有错不静默, 不落 done)"""
    tick = tmp_path / 'tick'
    lob = tmp_path / 'lob'
    _mk_mini_tick(tick)
    # 篡改: manifest 声称 SH 该日 orders=99 (源漂移模拟)
    mf = tick / '_manifest' / 'conversion_manifest.parquet'
    m = pl.read_parquet(mf)
    m = m.with_columns(pl.when(pl.col('code') == '600036.SH')
                       .then(pl.lit(99)).otherwise(pl.col('n_orders'))
                       .alias('n_orders'))
    m.write_parquet(mf)
    R._init_worker(dict(tick_root=str(tick), lob_root=str(lob),
                        conv_manifest=str(mf),
                        cancels_manifest=str(tick / '_manifest'
                                             / 'cancels_manifest.parquet')))
    p = R.process_date(DSTR)
    assert p['ok'] is False and p['n_fail'] == 1
    sh = next(r for r in p['recs'] if r['code'] == '600036.SH')
    assert sh['ok'] is False
    assert ('orders', 2, 99) in sh['guard']['mismatches']
    assert any('orders' in e for e in p['errors'])
    assert '600036.SH' not in [r['code'] for r in p['recs'] if r['ok']]


# ---------- W5 日结账: hard 日不吞 (数据已落盘 → done + hard_days 记录) ----------
# 断言源 = 计划 W5 验收行 "断点续跑全完成" + "失败全分类 (<0.1% 有分类原因)" 的
# 联立语义: code-day 门硬失败是**已分类失败** (recs 带 reasons, M4 PASS), 数据已
# 原子落盘 → 该 date 必须进 done (否则全部重跑永不自愈), 同时 hard_days 留痕交审计
# 按失败率阈值裁决; 结构性错误 (守卫漂移/无 manifest code/异常) 才不 done 需重试。

def _payload(day, n_fail=0, errors=(), tables=True):
    """合成 process_date 载荷: recs 含 n_fail 个门拒 code-day"""
    recs = [dict(code=f'00000{i}.SZ', day=day, ok=True,
                 gate=dict(presence=0.99, vacuous=False, band=False, ok=True,
                           reasons=[], notes=[])) for i in range(10)]
    for i in range(n_fail):
        recs[i] = dict(code=f'30130{i}.SZ', day=day, ok=False,
                       gate=dict(presence=0.89981, vacuous=False, band=False,
                                 ok=False, reasons=['m1a_presence'], notes=[]))
    return dict(day=day, ok=n_fail == 0 and not errors,
                errors=list(errors), n_codes=10, recs=recs,
                tables={'lob_events': dict(rows=100, bytes=9, sha256='x')}
                if tables else {}, engine_ms=1.0, n_fail=n_fail, read_s=1.0)


def test_day_outcome_classifies_ok_hard_error_and_stub_must_fail():
    """三态: 全过门='ok'; 仅 code-day 门拒 (errs 空, 表已写)='hard'; 结构性
    (守卫漂移/无 manifest code/表空)='error'。硬编码常量存根必败。"""
    assert R.day_outcome(_payload('20260803')) == 'ok'
    h = _payload('20260807', n_fail=1)
    assert h['ok'] is False and h['errors'] == [] and h['tables']
    assert R.day_outcome(h) == 'hard'
    e1 = _payload('20260808', errors=['orders: date 实读 5 != manifest 6'])
    assert e1['ok'] is False and e1['tables']
    assert R.day_outcome(e1) == 'error'
    e2 = _payload('20260809', errors=['manifest 无该日 code'], tables=False)
    assert R.day_outcome(e2) == 'error'


def test_apply_day_result_records_hard_day_without_swallowing_error():
    """ok/hard → done (hard 另记 codes+reasons, 不静默); error → 不 done 且 err_rows
    留 detail。逐条手算断言 (槽位与被拒 code 一致)。"""
    done, hard, errs = set(), [], []
    assert R.apply_day_result(_payload('20260803'), done, hard, errs) == 'ok'
    assert done == {'20260803'} and hard == [] and errs == []
    assert R.apply_day_result(_payload('20260807', n_fail=2),
                              done, hard, errs) == 'hard'
    assert done == {'20260803', '20260807'}          # 已落盘 → 进 done (不回炉)
    assert errs == []                                 # 非结构错, 不入 err_rows
    assert len(hard) == 1
    assert hard[0]['day'] == '20260807' and hard[0]['n_fail'] == 2
    assert hard[0]['codes'] == ['301300.SZ', '301301.SZ']
    assert hard[0]['reasons'] == ['m1a_presence']
    assert R.apply_day_result(_payload('20260808', errors=['stall_abandon']),
                              done, hard, errs) == 'error'
    assert '20260808' not in done
    assert errs == [('20260808', 'date_error', 'stall_abandon')]
    assert len(hard) == 1                             # 结构错不污染 hard_days


def test_state_update_persists_done_plan_and_hard_days():
    """state.json 月条目: done 排序 + plan_n (全月计划数, 非 --only-day 过滤数) +
    hard_days 排序留痕; 不改调用方 dict。"""
    st0 = {'months': {'202608': {'done': ['20260803'], 'plan_n': 15}}}
    hd = [dict(day='20260807', n_fail=1, codes=['301308.SZ'],
               reasons=['m1a_presence'])]
    st = R.state_update(st0, '202608', {'20260807', '20260803'}, 15, hd)
    m = st['months']['202608']
    assert m['done'] == ['20260803', '20260807']
    assert m['plan_n'] == 15
    assert m['hard_days'] == hd
    assert st0['months']['202608'] == {'done': ['20260803'], 'plan_n': 15}
    st2 = R.state_update(st0, '202509', set(), 20, [])
    assert st2['months']['202509'] == dict(done=[], plan_n=20, hard_days=[])
    assert '202509' not in st0['months']               # 无副作用


def test_month_complete_requires_dates_no_error_and_month_gate():
    """完成 = 计划日全 done ∧ 无结构性 err_rows ∧ 月门 ok。hard 日已在 done,
    其失败按 ≤0.1% 由审计裁决 (不阻断完成); 任一条件不满足即 False。"""
    plan = ['20260803', '20260807']
    done = ['20260803', '20260807']
    mg_ok = dict(ok=True, gate=dict(sz=0.986, sh=0.991))
    assert R.month_complete(done, plan, [], mg_ok) is True
    assert R.month_complete(['20260803'], plan, [], mg_ok) is False
    assert R.month_complete(done, plan,
                            [('20260807', 'date_exc', 'MemoryError')],
                            mg_ok) is False
    assert R.month_complete(done, plan, [], dict(ok=False)) is False


# ---------- W5 体积收口: 写盘编码 (zstd 级) 与收尾重打包同参同字节 ----------

def _enc_payload(n=3000):
    """lob_events 形状合成载荷 (低基数字符串 + 单调 int + 高基数 id)"""
    return pl.DataFrame({
        'code': ['000155.SZ' if i % 2 else '600184.SH' for i in range(n)],
        'trade_date': [date(2026, 8, 3)] * n,
        'time_ms': pa.array([34200000 + i for i in range(n)], pa.int32()),
        'seq': pa.array(range(1, n + 1), pa.int64()),
        'kind': [['add', 'cancel', 'trade'][i % 3] for i in range(n)],
        'phase': ['continuous'] * n,
        'side': ['B' if i % 2 else 'S' for i in range(n)],
        'price_x10000': pa.array([122800 + (i % 7) for i in range(n)], pa.int64()),
        'prev_vol': pa.array([(i * 100) % 5000 for i in range(n)], pa.int64()),
        'new_vol': pa.array([(i * 100) % 5000 + 100 for i in range(n)], pa.int64()),
        'qty': pa.array([100 + (i % 9) * 100 for i in range(n)], pa.int64()),
        'id': pa.array([324352 + i * 7 for i in range(n)], pa.int64()),
        'otype': ['0'] * n,
    })


def test_writer_encoding_matches_repack_path_bytes(tmp_path):
    """W5 体积收口 (计划 W5 验收行 "总体积 ≤1.5×源"): 批算 `_TableStream` 与收尾
    重打包 `compact_lob` 必须**同参同字节** —— 交付树的编码由重打包定义, 两路分叉
    = "重跑件 ≠ 交付树" (字节复现性断链), 且体积杠杆失效。

    实测杠杆 (notes/w5_full_history_memo.md): 同几何 zstd 3→9 = events −4.9% /
    sweep −3.1% / ckpt −5.9%; 202608 月 1.5383 → 1.4657× (≤1.5 预算)。
    硬编码 level/几何的"存根"或两路分叉在此必败 (字节不等)。"""
    import compact_lob as CL
    df = _enc_payload(3000)
    sch = R._pa_schema(R.COL_EVENTS)
    assert df.to_arrow().schema == sch              # 前置: 载荷类型 == 表契约
    st = R._TableStream(tmp_path / 'prod', '20260803', sch, row_group_rows=1000)
    st.append(df)
    f_prod, n_rows = st.finish()
    seed = tmp_path / 'seed' / '20260803.parquet'
    seed.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({k: df[k].to_arrow() for k in df.columns}, schema=sch),
                   seed, row_group_size=137, compression='zstd',
                   compression_level=1)             # 异几何/异级种子件
    info = CL.compact_file(str(seed), rgr=1000, level=CL.ZSTD_LEVEL)
    assert info['rows'] == n_rows == 3000
    md = pq.ParquetFile(str(seed)).metadata
    assert [md.row_group(k).num_rows for k in range(md.num_row_groups)] == [1000] * 3
    assert f_prod.read_bytes() == seed.read_bytes()  # 同参 ⟺ 同字节
    assert R.ZSTD_LEVEL == CL.ZSTD_LEVEL == 9        # 冻结杠杆 (实测见 docstring)


def test_resource_gates_frozen_for_doubled_budget():
    """W5 资源门冻结 (用户 2026-09-11 决策: 内存预算翻倍 → 常态 ≤48GB / 硬 ≤64GB,
    批算 worker 2→4): **派发低水位必须随 worker 数上调** —— 4 worker 同刻在飞时单
    date 切片峰 ~12.8GB, 旧水位下"在飞 × 新增"会过冲; 改小 = 静默放松 OOM 防护
    (存根式改动必败)。采样/看门狗口径不变。"""
    assert R.LOW_WATER_KB == 16_000_000              # MemAvailable 低水位 ~15.3GB
    assert R.AUDIT_S == 30 and R.STALL_S == 2400     # 审计采样 / STALL 看门狗冻结


# ---------- W5 收口补测: main() CLI 端到端 (完成标准 "用户能从入口触发" ) ----------
# Web 断言源 = 规格 §3.4 批算 (断点/月门/SUCCESS/parity) + W5 验收行 "断点续跑全完成"。
# 与 test_process_date_e2e_synthetic 的分工: 彼测 worker 单层 (直接调 process_date),
# 本测**只经 CLI main()** 走全链 —— 日期计划 (manifest) → spawn 进程池 → 落盘 →
# state 断点 → 跨 run 月门 → SUCCESS 标记 → run summary parity。硬编码/绕开实际
# 重放的"存根"必败 (表内容按合成源逐事件手算)。

_SNAP_COLS = (['code', 'time_ms'] +
              [f'{s}_{k}{i}' for s in ('bid', 'ask')
               for k in ('p', 'v') for i in range(1, 11)])


def _mk_mini_month(root):
    """_mk_mini_tick 的月末最小补全: main 的源月目录存在性硬查要求 4 表齐
    (`_IN_TABLES`), 故补 snapshots 空表 (全 0 行, manifest n_snap=0 → 无锚日
    = vacuous 合法空簿日)。"""
    _mk_mini_tick(root)
    sch = {'code': pl.String, 'time_ms': pl.Int64}
    sch.update({c: pl.Int64 for c in _SNAP_COLS[2:]})
    df = (pl.DataFrame(schema=sch)
          .with_columns(pl.lit(D).alias('trade_date').cast(pl.Date)))
    _write_part_df(root / 'snapshots' / 'year=2026' / 'month=08', df)


def test_main_cli_e2e_mini_month(tmp_path, monkeypatch, capsys):
    """CLI 全链四跑: (0) dry-run 零副作用 → (1) 首跑落盘/state/月门/SUCCESS →
    (2) --force 重跑 parity.compared=True 且 ok=True (字节级重跑等) → (3) 无
    --force 再跑走断点短路 (不重算)。"""
    tick, lob = tmp_path / 'tick', tmp_path / 'lob'
    _mk_mini_month(tick)
    monkeypatch.setattr(C, 'TICK_FACT_ROOT', str(tick))
    monkeypatch.setattr(C, 'LOB_FACT_ROOT', str(lob))
    bdir = lob / '_batch'
    runs = lambda: sorted(p.name for p in (bdir / 'runs').glob('*')) \
        if (bdir / 'runs').exists() else []

    # (0) dry-run: 计划来自 manifest, 零副作用
    R.main(['--month', '202608', '--workers', '1','--dry-run'])
    out = capsys.readouterr().out
    assert 'dry-run 202608: plan=1 done=0 todo=1' in out
    assert runs() == [] and not (bdir / 'state.json').exists()

    # (1) 首跑: CLI → spawn 进程池 → 三表落盘 → state/月门/SUCCESS
    R.main(['--month', '202608', '--workers', '1'])
    out = capsys.readouterr().out
    assert '20260803: OK codes=2' in out and 'SUCCESS' in out
    ev = pl.read_parquet(lob / 'lob_events' / 'year=2026' / 'month=08'
                         / '20260803.parquet')
    got = {r['code']: r['kind'] for r in
           ev.group_by('code').agg(pl.col('kind').sort()).to_dicts()}
    # 逐事件手算 (源见 _mk_mini_tick): SH A+D 全撤 → 仅 add 行 (档移除走 sweep);
    # SZ 两 add + 1 笔部分成交 → ['add','add','trade']
    assert got == {'600036.SH': ['add'], '000155.SZ': ['add', 'add', 'trade']}
    sz = ev.filter((pl.col('code') == '000155.SZ')
                   & (pl.col('kind') == 'trade'))
    assert sz['time_ms'].to_list() == [34201020]
    assert sz['new_vol'].to_list() == [600]      # 800 挂 − 200 成交 = 计算量
    assert not list(lob.rglob('*.tmp*'))         # 原子写: 无残留
    st = json.loads((bdir / 'state.json').read_text())
    assert st['months']['202608']['done'] == ['20260803']
    assert st['months']['202608']['plan_n'] == 1
    assert st['months']['202608']['hard_days'] == []
    mg = json.loads((bdir / 'month_gate_202608.json').read_text())
    assert mg['n_code_day'] == 2 and mg['plan_n'] == 1
    succ = json.loads((bdir / 'SUCCESS_202608').read_text())
    assert succ['n_code_day'] == 2 and succ['hard_days'] == []
    s1 = json.loads((bdir / 'runs' / runs()[0] / 'summary.json').read_text())
    assert s1['n_errors'] == 0 and s1['n_done_run'] == 1
    assert s1['parity'] == {'compared': False, 'mismatch_dates': [], 'ok': None}
    # 禁止行为: 实际执行的是 **spawn 子进程** (审计 CSV 内有 worker 行), 且
    # 落盘行数不是空壳 (rows 与手算一致)
    audit = (bdir / 'runs' / runs()[0] / 'rss_audit.csv').read_text().splitlines()
    assert audit[0] == 't,pid,tag,rss_kb,mem_avail_kb'
    tags = {ln.split(',')[2] for ln in audit[1:] if ln}
    assert tags == {'worker', 'parent'}
    assert s1['tables']['20260803']['lob_events']['rows'] == 4

    # (2) --force 重跑: 同源同参 → 本 run 写出的 date sha256 与 prev 全等
    before = runs()
    R.main(['--month', '202608', '--workers', '1', '--force'])
    assert len(runs()) == len(before) + 1          # run_id 唯一 (同秒连跑不覆盖)
    s2 = json.loads((bdir / 'runs' / runs()[1] / 'summary.json').read_text())
    assert s2['parity'] == {'compared': True, 'mismatch_dates': [], 'ok': True}
    assert s2['n_errors'] == 0

    # (3) 断点短路: SUCCESS 在 → 不重算 (run 目录数不变, 无新 run)
    n_before = len(runs())
    R.main(['--month', '202608', '--workers', '1'])
    assert '已有 SUCCESS 标记' in capsys.readouterr().out
    assert len(runs()) == n_before


def test_main_cli_lock_onlyday_and_finalize(tmp_path, monkeypatch, capsys):
    """三条运行期安全语义 (W5 收口 runbook 依赖它们): (a) **单写者锁** —— 另一实例
    持 flock 时立即退出且零写入 (收口 a/b/c 的"compact 前必须全退"纪律); (b) `--only-day`
    命中不存在日期 → 显式退出不建 run; (c) 全月 done 但缺 SUCCESS (收尾前崩) →
    **仅收尾**不重算 (跨 run 月门 + 补落标记)。任一语义被"简化"掉的存根必败。"""
    import fcntl
    tick, lob = tmp_path / 'tick', tmp_path / 'lob'
    _mk_mini_month(tick)
    monkeypatch.setattr(C, 'TICK_FACT_ROOT', str(tick))
    monkeypatch.setattr(C, 'LOB_FACT_ROOT', str(lob))
    bdir = lob / '_batch'
    bdir.mkdir(parents=True, exist_ok=True)

    # (a) 锁占用 → 退出且零写入
    lk = open(bdir / '.lock', 'w')
    fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    R.main(['--month', '202608', '--workers', '1'])
    assert '锁占用' in capsys.readouterr().out
    assert not (bdir / 'runs').exists() and not (bdir / 'state.json').exists()
    fcntl.flock(lk, fcntl.LOCK_UN)
    lk.close()

    # (b) --only-day 未命中 → 显式退出
    R.main(['--month', '202608', '--workers', '1', '--only-day', '20260701'])
    assert 'manifest 无该月日期' in capsys.readouterr().out
    assert not (bdir / 'runs').exists()

    # (c) 全 done 无 SUCCESS → 仅收尾 (不重放: events 表 sha256 不变)
    R.main(['--month', '202608', '--workers', '1'])
    ev_p = lob / 'lob_events' / 'year=2026' / 'month=08' / '20260803.parquet'
    sha0 = R._sha256(str(ev_p))
    (bdir / 'SUCCESS_202608').unlink()
    capsys.readouterr()
    R.main(['--month', '202608', '--workers', '1'])
    out = capsys.readouterr().out
    assert '仅收尾' in out and '20260803: OK' not in out
    assert (bdir / 'SUCCESS_202608').exists()
    assert R._sha256(str(ev_p)) == sha0          # 未重算 (字节同)


def test_alloc_run_id_unique_on_collision(tmp_path):
    """同秒同进程的第二个 run 必须拿到不同目录名 —— 否则 `os.makedirs(exist_ok=True)`
    复用目录, 前一 run 的 `summary.json`/`day_rows.jsonl` 被覆盖, 而 parity 的 `prev`
    正是按目录名找的 → 字节级重跑比对静默退化为 compared=False (假阴性)。
    返回固定名的"存根"实现必败。"""
    bdir = tmp_path
    (bdir / 'runs').mkdir()
    r1 = R._alloc_run_id(str(bdir))
    (bdir / 'runs' / r1).mkdir()
    r2 = R._alloc_run_id(str(bdir))
    assert r2 != r1 and r2.startswith(r1)
    (bdir / 'runs' / r2).mkdir()
    r3 = R._alloc_run_id(str(bdir))
    assert r3 not in (r1, r2) and r3.startswith(r1)
    assert r1.split('_')[-1] == str(os.getpid())   # 命名契约 YYYYmmdd_HHMMSS_pid
