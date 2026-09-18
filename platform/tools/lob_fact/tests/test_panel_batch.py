"""面板批算（A 路直折 1m）+ 日聚合 TDD（红→绿）

断言源：
  1. pending-items #2「panel 批量产出（tick 因子面板）…按日流式」
  2. W6 冻结语义：面板列契约 = factor_panel.PANEL_COLS；1m 直折与金样
     panel_1m 逐列全等（金样 = W6 A/B 双路过门真实产物）
  3. 日聚合口径（本测试为唯一权威定义，实现须逐值一致）：
     - 按 (code, trade_date) 分组，n_samples = 组内行数
     - FLOAT_COLS（5 列）：{c}_mean / {c}_std（总体 std, ddof=0）/ {c}_last
       （time_ms 最大行）/ {c}_max / {c}_min
     - 流水列（9 列）：{f}_sum
     - nq 列（4 列）：{c}_mean
     - 段均值：{c}_am_mean（time_ms < LUNCH_START）/ {c}_pm_mean（> LUNCH_END）
     - 尾段：{c}_last30_mean = 该 code 按 time_ms 升序最后 min(30, n) 行均值
  4. 禁止行为：金样路径错误必须抛错（不许静默空返回）；聚合不许串 code。
"""
import os
import sys
from datetime import date

import polars as pl
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # lob_fact/
from core import config as C
from core import factor_panel as FP
from pipeline import panel_batch as PB

LOB_ROOT = '/data/students/gaolei/stock/data/fact/lob_fact/'
GOLDEN_1 = f'{LOB_ROOT}panel_1m/year=2025/month=08/20250812.parquet'
GOLDEN_2 = f'{LOB_ROOT}panel_1m/year=2026/month=08/20260803.parquet'


# ---------- chunk_codes ----------

def test_chunk_codes_partitions():
    got = PB.chunk_codes(list(range(10)), 4)
    assert got == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]]
    flat = [x for ch in got for x in ch]
    assert sorted(flat) == list(range(10))
    assert PB.chunk_codes([], 4) == []


# ---------- fold_code_day：金样逐列全等 ----------

def _compare(mine, g):
    assert mine.height == g.height
    for col in g.columns:
        if col in ('code', 'grid'):
            diff = int((mine[col] != g[col]).sum())
        elif col == 'trade_date':
            diff = int((mine[col].cast(g[col].dtype) != g[col]).sum())
        elif g[col].dtype.is_integer():
            diff = int((mine[col].cast(g[col].dtype) != g[col]).sum())
        else:
            diff = int(((mine[col].cast(pl.Float64) - g[col].cast(pl.Float64)).abs() > 1e-9).sum())
        assert diff == 0, f'{col}: {diff} 行不一致'


def _fold_golden(path, day):
    golden = pl.read_parquet(path)
    for code in golden['code'].unique():
        g = golden.filter(pl.col('code') == code).sort('time_ms')
        mine = PB.fold_code_day(code, day, LOB_ROOT).sort('time_ms')
        assert mine.columns == list(g.columns) or set(mine.columns) == set(g.columns)
        _compare(mine, g)


def test_fold_matches_golden_20250812():
    _fold_golden(GOLDEN_1, date(2025, 8, 12))


def test_fold_matches_golden_20260803():
    _fold_golden(GOLDEN_2, date(2026, 8, 3))


def test_fold_missing_code_raises():
    with pytest.raises(FileNotFoundError):
        PB.fold_code_day('999999.SZ', date(2025, 8, 12), LOB_ROOT)


def test_fold_is_not_stub():
    """反存根：两 code 面板必须不同（硬编码存根会同值）。"""
    a = PB.fold_code_day('000021.SZ', date(2025, 8, 12), LOB_ROOT)
    b = PB.fold_code_day('000155.SZ', date(2025, 8, 12), LOB_ROOT)
    assert a.height == b.height
    assert float((a.sort('time_ms')['obi5'] - b.sort('time_ms')['obi5']).abs().sum()) > 1e-6


# ---------- day_agg：手算数字 ----------

def _mk_panel():
    """两 code 各 4 样本（2 上午 + 2 下午），obi5 手工可算。"""
    rows = []
    for code, v in [('AAA', 1.0), ('BBB', -1.0)]:
        for tm, obi5, cr, n_add in [(C.OPEN + 60_000, v, 0.1, 10),
                                    (C.OPEN + 120_000, 3 * v, 0.3, 20),
                                    (C.LUNCH_END + 60_000, 2 * v, 0.2, 5),
                                    (C.LUNCH_END + 120_000, 6 * v, 0.4, 15)]:
            rows.append(dict(code=code, trade_date=date(2025, 8, 12), grid='1m',
                             time_ms=tm, obi5=obi5, cancel_rate=cr, n_add=n_add))
    return pl.DataFrame(rows)


def test_day_agg_values():
    agg = PB.day_agg(_mk_panel()).sort('code')
    assert agg['code'].to_list() == ['AAA', 'BBB']
    a = agg.row(0, named=True)
    assert a['n_samples'] == 4
    assert abs(a['obi5_mean'] - 3.0) < 1e-12
    # 总体 std（ddof=0）：vals [1,3,2,6] mean=3 → var=(4+0+1+9)/4=3.5
    assert abs(a['obi5_std'] - 3.5 ** 0.5) < 1e-12
    assert abs(a['obi5_last'] - 6.0) < 1e-12
    assert abs(a['obi5_max'] - 6.0) < 1e-12
    assert abs(a['obi5_min'] - 1.0) < 1e-12
    assert abs(a['obi5_am_mean'] - 2.0) < 1e-12        # (1+3)/2
    assert abs(a['obi5_pm_mean'] - 4.0) < 1e-12        # (2+6)/2
    assert abs(a['obi5_last30_mean'] - 3.0) < 1e-12    # n<30 → 全组均值
    assert a['n_add_sum'] == 50
    assert abs(a['cancel_rate_mean'] - 0.25) < 1e-12
    b = agg.row(1, named=True)
    assert abs(b['obi5_mean'] + 3.0) < 1e-12           # 不串 code
    assert b['n_add_sum'] == 50


def test_day_agg_last30_window():
    """60 样本时 last30 只看最后 30 行。"""
    rows = [dict(code='AAA', trade_date=date(2025, 8, 12), grid='1m',
                 time_ms=C.OPEN + 60_000 * (i + 1), obi5=float(i),
                 cancel_rate=0.0, n_add=1) for i in range(60)]
    agg = PB.day_agg(pl.DataFrame(rows))
    assert agg['n_samples'][0] == 60
    assert abs(agg['obi5_last30_mean'][0] - 44.5) < 1e-12  # 30..59 均值
    assert abs(agg['obi5_mean'][0] - 29.5) < 1e-12
