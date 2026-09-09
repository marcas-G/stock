"""W4 批算纯逻辑 TDD（红→绿）: 表→事件映射 / 日表构建 (seq) / 日门 (无 m6) / 月门聚合

断言源 = 规格 §3.2 schema 契约 + §3.3 门语义 (W3 校准单点 GATE_PRES=0.97, m1a 池化) +
逐事件手算数字; 任何硬编码汇总/复制引擎实现的"存根"必败。

映射等价基准: W4a parity_probe 14/14 天逐事件字节对等 (parquet 读链 == raw zip 链),
本文件把 probe 的映射语义固化为模块纯函数 + 合成行测试 (SH A/D/S 分支 / SZ 0/1/U
全收 / qty<=0 跳过 / trades 双 ref 拆条 / cancels side 0=B 1=S)。
"""
import sys, os
from datetime import date

import polars as pl

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


def test_day_gate_presence_boundary_and_vacuous():
    """presence 四舍五入 5 位 ≥ GATE_PRES=0.97 才 PASS (200 档 194=0.97 整过,
    193=0.965 拒); n_anchor=0 → vacuous presence 1.0 过 (停牌/零锚日合法);
    m4 各失败分支独立拒绝且 reasons 并列"""
    assert R.day_gate(_mk(194, 200))['ok'] is True
    assert R.day_gate(_mk(193, 200))['ok'] is False
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
    # presence 计算必须用真计数: 0.97 边界两侧 1 档差即翻转 (硬编码 PASS 存根必败)
    assert R.day_gate(_mk(1000, 2000))['ok'] is False    # 0.5 远低于门
    assert R.day_gate(_mk(1940, 2000))['ok'] is True     # 0.97 整
    assert R.day_gate(_mk(1939, 2000))['ok'] is False    # 0.9695


def test_day_gate_handles_reversed_and_stub_must_fail():
    """门输出 presence 由 (n_present,n_anchor) 真算: 交换分子分母 0.5→2.0 拒;
    空锚+空 m4 全 PASS 输入也过 — 但任何伪造 ok=True 的实现会在上列 193/200
    等反例上暴露"""
    r = R.day_gate(_mk(500, 1000))
    assert r['presence'] == round(0.5, 5) and r['ok'] is False
    r = R.day_gate(_mk(1000, 500))                       # 分子>分母 = 数据错
    assert r['presence'] == 2.0 and r['ok'] is False


# ---------- 月门 (逐日 + SZ/SH 池化; 空锚日 vacuous 独立, 不入池) ----------

def _dayrow(code, present, anchor, ok=True, vacuous=False):
    return dict(code=code, day='20260803', m1=dict(n_present=present,
                                                   n_anchor=anchor),
                gate=dict(ok=ok, vacuous=vacuous))


def test_month_gate_pools_raw_counts_and_counts_vacuous():
    """月门 = 逐日全 PASS 且 SZ/SH 池化 (原始计数和, 非逐日均值) ≥ 门;
    空锚日 (vacuous, 含于 n_days) 独立 PASS 不进池 — 无锚日不给池贡献分母"""
    rows = [_dayrow('000155.SZ', 997, 1000),     # 0.997
            _dayrow('000333.SZ', 99, 100),       # 0.99
            _dayrow('600036.SH', 199, 200),      # 0.995
            _dayrow('600184.SH', 100, 100),      # 1.0
            _dayrow('600519.SH', 0, 0, vacuous=True)]
    m = R.month_gate(rows)
    assert m['n_days'] == 5 and m['n_vacuous'] == 1 and m['n_anchored'] == 4
    # 池化 = (997+99)/(1000+100)=0.99636 | (199+100)/(200+100)=0.99667
    assert m['gate']['sz'] == round(1096 / 1100, 5)
    assert m['gate']['sh'] == round(299 / 300, 5)
    assert m['gate']['per_day'] == [True, True, True, True]
    assert m['ok'] is True


def test_month_gate_rejects_bad_day_or_pool():
    """月门失败 = 任一逐日 FAIL (即使池化过) 或任一所池化 FAIL; 失败日不入池
    (0.965 日会拖 SH 池至 0.9747 仍过 → 门由 per_day 拒)"""
    rows = [_dayrow('000155.SZ', 997, 1000),
            _dayrow('000155.SZ', 965, 1000),    # 0.965 < 门 → per_day 拒
            _dayrow('600036.SH', 200, 200)]
    m = R.month_gate(rows)
    assert m['gate']['sh'] == 1.0
    assert m['gate']['per_day'] == [True, False, True]
    assert m['ok'] is False
    # 池化单独 FAIL (逐日均过但一所合计 < 门): SZ 单日 0.99 池化即 0.99 过 —
    # 用 4 日 0.97 整 + 1 日 0.9705 → SZ 池略 ≥ 门 → 构造纯 SH 不足样本
    rows2 = [_dayrow('600036.SH', 97, 100),
             _dayrow('600036.SH', 97, 100),
             _dayrow('600036.SH', 0, 0, vacuous=True)]
    m2 = R.month_gate(rows2)
    assert m2['gate']['sh'] == 0.97 and m2['gate']['sz'] == 1.0
    assert m2['gate']['per_day'] == [True, True]
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
