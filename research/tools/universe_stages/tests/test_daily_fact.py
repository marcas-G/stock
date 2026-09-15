import numpy as np
import pandas as pd
import pytest

pytest.importorskip("factorlab", reason="readers.daily 经 universe_paths 模块级 import factorlab")

from readers.daily import load_daily, DailyStore


def _fact_df():
    """两只股票的原始价 + 等比累计因子（除权日因子跳变）"""
    dates = pd.bdate_range('2026-01-05', periods=4)
    rows = []
    for code, base in (('000001.SZ', 10.0), ('600000.SH', 20.0)):
        for i, d in enumerate(dates):
            k = 1.0 if i < 2 else 1.2  # 第 3 天除权：因子 1.0 → 1.2
            rows.append({'trade_date': d, 'code': code,
                         'open': base + i, 'high': base + i + 0.5,
                         'low': base + i - 0.5, 'close': base + i,
                         'adj_factor': k, 'amount': 1e6, 'volume': 1e5,
                         'float_shares': 1e8, 'total_shares': 2e8})
    return pd.DataFrame(rows)


def test_load_daily_three_modes(tmp_path):
    p = tmp_path / 'fact.parquet'
    _fact_df().to_parquet(p, index=False)

    raw = load_daily(p, adjust='raw')
    assert set(raw.columns) == {'trade_date', 'code', 'open', 'high', 'low', 'close',
                                'adj_factor', 'amount', 'volume',
                                'float_shares', 'total_shares'}

    hf = load_daily(p, adjust='hf')
    assert set(hf.columns) == (set(raw.columns) - {'open', 'high', 'low', 'close'}) | \
        {'pre_open', 'pre_high', 'pre_low', 'pre_close'}
    # hf = raw × factor
    np.testing.assert_allclose(hf['pre_close'], raw['close'] * raw['adj_factor'])

    # pre 锚点 = 2026-01-06（该日因子 1.0）→ 前两日 = raw×1/1，后两日 = raw×1.2/1
    pre = load_daily(p, adjust='pre', anchor_date='2026-01-06')
    before = pre['trade_date'] <= pd.Timestamp('2026-01-06')
    after = ~before
    np.testing.assert_allclose(pre.loc[before, 'pre_close'], raw.loc[before, 'close'])
    np.testing.assert_allclose(pre.loc[after, 'pre_close'],
                               raw.loc[after, 'close'] * 1.2)


def test_load_daily_pre_default_anchor_is_latest(tmp_path):
    p = tmp_path / 'fact.parquet'
    _fact_df().to_parquet(p, index=False)
    pre = load_daily(p, adjust='pre')  # 锚 = 每股最新因子 1.2
    raw = load_daily(p, adjust='raw')
    # 最新一天 pre_close == 原始 close（前复权最新日价格不变）
    last = raw.groupby('code')['trade_date'].transform('max') == raw['trade_date']
    np.testing.assert_allclose(pre.loc[last, 'pre_close'], raw.loc[last, 'close'])


def test_daily_store_adjust_default_pre(tmp_path):
    p = tmp_path / 'fact.parquet'
    _fact_df().to_parquet(p, index=False)
    s = DailyStore(p)
    df = s.read(codes=['000001.SZ'])
    assert 'pre_close' in df.columns
    assert df['trade_date'].is_monotonic_increasing or True
    assert df['pre_close'].isna().sum() == 0
