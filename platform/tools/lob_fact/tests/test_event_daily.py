"""事件原生日聚合（event_daily）TDD（红→绿）

断言源（本测试 = 口径唯一权威）：
  - 输入 = lob_fact/lob_events + lob_sweep_meta 生产表（W4/W6 门固化产物）
  - 统计清单（每 code-day 一行）：
    n_add/n_cancel/n_trade：kind 计数
    add_vol/cancel_vol/trade_vol：各 kind Σqty
    cancel_rate = cancel_vol/add_vol（add_vol==0 → null）
    burst：max_cancel_vol_1s / max_add_vol_1s = 1 秒桶（time_ms//1000）内最大桶量
    lifetime：cancel 与其 add（按 (code,id) 连接）时间差；
      **仅统计 id 在当日本只 code 恰有一条 add 的 cancel**（歧义 id 剔除）；
      n_cancel_matched / cancel_match_ratio / lifetime_mean / lifetime_med /
      lifetime_p90（linear 分位）/ flash_cancel_share（<1000ms 占 matched 比例）
    trade_gap_med / trade_gap_p90：同 code trade 按 seq 升序相邻时间差
    avg_trade_qty / max_trade_qty
    sweep：n_sweep_rows / sweep_vol_sum / sweep_vol_max（Σ/max vol_before）；
      sweep_buy_vol = Σ vol_before(side='S')（吃卖档=主动买）、
      sweep_sell_vol = Σ vol_before(side='B')；sweep_imb=(buy-sell)/(buy+sell)
      （分母 0 → null）
  - 禁止行为：串 code；硬编码存根（两真实 code 统计必须不同）；
    trade_gap 与独立 numpy 差分实现一致（反自洽错误）。
"""
import os
import sys

import polars as pl
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # lob_fact/
from pipeline import event_daily as ED

LOB_ROOT = '/data/students/gaolei/stock/data/fact/lob_fact/'


def _ev(code, ms, kind, side, qty, oid=0):
    return dict(code=code, time_ms=ms, seq=ms, kind=kind, side=side,
                qty=qty, id=oid)


def _sw(code, ms, side, vol, tail):
    return dict(code=code, time_ms=ms, seq=ms, side=side,
                vol_before=vol, tail_order=tail)


def _mk_events():
    return pl.DataFrame([
        # AAA: adds
        _ev('AAA.SZ', 1000, 'add', 'B', 500, 1),
        _ev('AAA.SZ', 1000, 'add', 'B', 100, 2),
        _ev('AAA.SZ', 2000, 'add', 'B', 400, 3),
        # AAA: cancels（id1 matched 500ms；id9 无 add）
        _ev('AAA.SZ', 1500, 'cancel', 'B', 500, 1),
        _ev('AAA.SZ', 5000, 'cancel', 'B', 200, 9),
        # AAA: trades
        _ev('AAA.SZ', 3000, 'trade', 'B', 100, 0),
        _ev('AAA.SZ', 3000, 'trade', 'S', 100, 0),
        _ev('AAA.SZ', 10000, 'trade', 'B', 800, 0),
        # BBB: 歧义 id（两条 add 同 id）→ matched=0
        _ev('BBB.SZ', 1000, 'add', 'S', 100, 5),
        _ev('BBB.SZ', 2000, 'add', 'S', 100, 5),
        _ev('BBB.SZ', 2500, 'cancel', 'S', 100, 5),
    ])


def _mk_sweeps():
    return pl.DataFrame([
        _sw('AAA.SZ', 3000, 'S', 300, 11),
        _sw('AAA.SZ', 3001, 'S', 100, 11),
        _sw('AAA.SZ', 9000, 'B', 50, 12),
    ])


def test_event_day_stats_synthetic():
    out = ED.event_day_stats(_mk_events(), _mk_sweeps()).sort('code')
    assert out['code'].to_list() == ['AAA.SZ', 'BBB.SZ']
    a = out.row(0, named=True)
    assert (a['n_add'], a['n_cancel'], a['n_trade']) == (3, 2, 3)
    assert (a['add_vol'], a['cancel_vol'], a['trade_vol']) == (1000, 700, 1000)
    assert abs(a['cancel_rate'] - 0.7) < 1e-12
    # burst：cancel 桶 sec1=500, sec5=200 → 500；add 桶 sec1=600, sec2=400 → 600
    assert a['max_cancel_vol_1s'] == 500
    assert a['max_add_vol_1s'] == 600
    # lifetime：仅 id1 matched（歧义/无 add 剔除）
    assert a['n_cancel_matched'] == 1
    assert abs(a['cancel_match_ratio'] - 0.5) < 1e-12
    assert abs(a['lifetime_mean'] - 500.0) < 1e-9
    assert abs(a['lifetime_med'] - 500.0) < 1e-9
    assert abs(a['lifetime_p90'] - 500.0) < 1e-9
    assert abs(a['flash_cancel_share'] - 1.0) < 1e-12
    # trade gaps：[0, 7000] → med 3500；linear p90 = 6300
    assert abs(a['trade_gap_med'] - 3500.0) < 1e-9
    assert abs(a['trade_gap_p90'] - 6300.0) < 1e-9
    assert abs(a['avg_trade_qty'] - 1000 / 3) < 1e-12
    assert a['max_trade_qty'] == 800
    # sweep
    assert a['n_sweep_rows'] == 3
    assert a['sweep_vol_sum'] == 450
    assert a['sweep_vol_max'] == 300
    assert a['sweep_buy_vol'] == 400
    assert a['sweep_sell_vol'] == 50
    assert abs(a['sweep_imb'] - (400 - 50) / 450) < 1e-12
    # BBB：歧义 id → matched=0，lifetime 全 null
    b = out.row(1, named=True)
    assert b['n_cancel_matched'] == 0
    assert b['lifetime_mean'] is None or (b['lifetime_mean'] != b['lifetime_mean'])
    assert (b['n_add'], b['n_cancel']) == (2, 1)


def test_guard_no_trades():
    """无 trade 的 code：gap/avg/max 全 null，不崩。"""
    ev = pl.DataFrame([_ev('CCC.SZ', 1000, 'add', 'B', 100, 1)])
    out = ED.event_day_stats(ev, pl.DataFrame(schema={
        'code': pl.String, 'time_ms': pl.Int64, 'seq': pl.Int64,
        'side': pl.String, 'vol_before': pl.Int64, 'tail_order': pl.Int64}))
    assert out['code'].to_list() == ['CCC.SZ']
    assert out['n_trade'][0] == 0
    assert out['trade_gap_med'][0] is None or float(out['trade_gap_med'][0]) != out['trade_gap_med'][0]
    assert out['sweep_vol_sum'][0] == 0


def test_guard_zero_add_cancel_rate_null():
    ev = pl.DataFrame([_ev('DDD.SZ', 1000, 'cancel', 'B', 100, 1)])
    out = ED.event_day_stats(ev, pl.DataFrame(schema={
        'code': pl.String, 'time_ms': pl.Int64, 'seq': pl.Int64,
        'side': pl.String, 'vol_before': pl.Int64, 'tail_order': pl.Int64}))
    assert out['cancel_rate'][0] is None or float(out['cancel_rate'][0]) != out['cancel_rate'][0]


# ---------- 时段化聚合（am / pm / close30） ----------

def test_event_day_stats_segments_synthetic():
    """时段列口径：am(<11:30)、pm(>13:00)、close30(>=14:30)。
    close30 同时属于 pm——段间可重叠，close30 是 pm 的子段。"""
    from core import config as C
    rows = [
        _ev('AAA.SZ', C.OPEN + 60_000, 'add', 'B', 100, 1),      # am
        _ev('AAA.SZ', C.OPEN + 60_500, 'cancel', 'B', 40, 1),  # am, flash(500ms)
        _ev('AAA.SZ', C.LUNCH_END + 60_000, 'add', 'B', 200, 2), # pm 非 close30
        _ev('AAA.SZ', C.CLOSE - 60_000, 'add', 'B', 300, 3),     # close30（14:59）
        _ev('AAA.SZ', C.CLOSE - 60_000, 'cancel', 'B', 300, 4),  # close30 cancel（id4 无 add）
    ]
    sws = pl.DataFrame([
        _sw('AAA.SZ', C.OPEN + 60_000, 'S', 10, 1),   # am buy 主动
        _sw('AAA.SZ', C.CLOSE - 120_000, 'B', 20, 2),  # close30 sell 主动（14:58）
    ])
    out = ED.event_day_stats(pl.DataFrame(rows), sws)
    a = out.row(0, named=True)
    assert a['n_add_am'] == 1 and a['add_vol_am'] == 100
    assert a['n_cancel_am'] == 1 and a['cancel_vol_am'] == 40
    assert abs(a['cancel_rate_am'] - 0.4) < 1e-12
    assert abs(a['flash_share_am'] - 1.0) < 1e-12      # am 唯一 matched 是 flash
    assert a['n_add_pm'] == 2 and a['add_vol_pm'] == 500   # pm 含 close30
    assert a['n_add_close30'] == 1 and a['add_vol_close30'] == 300
    assert a['n_cancel_close30'] == 1 and a['cancel_vol_close30'] == 300
    assert a['flash_share_close30'] is None or float(a['flash_share_close30']) != a['flash_share_close30']
    assert a['sweep_buy_vol_am'] == 10 and a['sweep_buy_vol_close30'] == 0
    assert a['sweep_sell_vol_close30'] == 20 and a['sweep_vol_sum_close30'] == 20
    assert a['n_sweep_rows_close30'] == 1
    # 全期列不受影响
    assert a['n_add'] == 3 and a['add_vol'] == 600

# ---------- 真实数据：与独立实现交叉验证 + 反存根 ----------

DAY = '20250812'
CODES = ['000155.SZ', '000021.SZ', '600036.SH', '600184.SH']


def _load(day, table):
    d = f"{LOB_ROOT}/{table}/year={day[:4]}/month={day[4:6]}/{day}.parquet"
    return pl.read_parquet(d)


@pytest.fixture(scope='module')
def real_day():
    ev = _load(DAY, 'lob_events')
    sw = _load(DAY, 'lob_sweep_meta')
    return ED.event_day_stats(ev, sw)


def test_real_counts_match_independent(real_day):
    ev = _load(DAY, 'lob_events')
    for code in CODES:
        sub = ev.filter(pl.col('code') == code)
        row = real_day.filter(pl.col('code') == code)
        vc = {r['kind']: r['count'] for r in sub['kind'].value_counts().to_dicts()}
        assert row['n_add'][0] == vc.get('add', 0)
        assert row['n_cancel'][0] == vc.get('cancel', 0)
        assert row['n_trade'][0] == vc.get('trade', 0)
        assert abs(row['cancel_rate'][0] -
                   row['cancel_vol'][0] / row['add_vol'][0]) < 1e-12


def test_real_trade_gap_independent(real_day):
    import numpy as np
    ev = _load(DAY, 'lob_events')
    for code in CODES:
        ts = (ev.filter((pl.col('code') == code) & (pl.col('kind') == 'trade'))
              .sort('seq')['time_ms'].to_numpy())
        if len(ts) < 2:
            continue
        gaps = np.diff(ts)
        assert abs(float(real_day.filter(pl.col('code') == code)['trade_gap_med'][0])
                   - float(np.median(gaps))) < 1e-6


def test_real_codes_differ(real_day):
    vals = (real_day.filter(pl.col('code').is_in(CODES))
            .select(['n_add', 'cancel_rate', 'lifetime_med', 'sweep_imb'])
            .sort('n_add', descending=True))
    assert vals.height == 4
    assert len(set(vals['lifetime_med'].cast(pl.String).to_list())) >= 3
