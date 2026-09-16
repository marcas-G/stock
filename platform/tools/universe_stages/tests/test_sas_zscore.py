import numpy as np
import pandas as pd
import pytest

from layer2.sas import label_shocks, build_event_features


def _synthetic_bars(n_codes=2, n_days=3, base_close=(11.0, 3.0), n_bars=48, seed=7):
    """两股不同价格量级的 5m bars：检验同日 prev_close 不跨股串列"""
    rng = np.random.default_rng(seed)
    rows = []
    dates = pd.bdate_range('2026-08-13', periods=n_days)
    for ci, base in enumerate(base_close):
        code = f'{ci:06d}.SZ'
        close = base
        for d in dates:
            for b in range(n_bars):
                t = d + pd.Timedelta(minutes=9 * 60 + 30 + b * 5)
                close = max(0.5, close * (1.0 + rng.normal(0, 0.004)))
                rows.append({'code': code, 'trade_date': d, 'datetime': t,
                             'open': close, 'high': close * 1.002, 'low': close * 0.998,
                             'close': close, 'amount': 1e6 + b, 'session_class': 0})
    return pd.DataFrame(rows), dates


def _inject_shock(df, code, trade_date, bar_idx, drop=0.04, amount=5e6):
    """把某日第 bar_idx 根 bar 改成确定性的深砸放量 shock（-drop，量×5）"""
    idx = df.index[(df['code'] == code) & (df['trade_date'] == trade_date)].tolist()[bar_idx]
    prev = df.at[idx, 'close']
    new = prev * (1 - drop)
    df.loc[idx, 'open'] = prev
    df.loc[idx, 'high'] = prev
    df.loc[idx, 'low'] = new * 0.998
    df.loc[idx, 'close'] = new
    df.loc[idx, 'amount'] = amount
    return df


def test_prev_close_no_cross_code_leak():
    bars, _ = _synthetic_bars(base_close=(11.0, 3.0))
    x, _ = label_shocks(bars, {'z_window': 40, 'z_min_periods': 10})
    r = x['close'] / x['prev_close'] - 1
    # 串列 bug（groupby('trade_date') 缺 code）会让每日首 bar 的 prev_close
    # 取到另一只股的 close（11 元↔3 元）→ r≈+2.7 / -0.73；正常随机游走 r 幅值 <0.1
    assert r.dropna().abs().max() < 0.1
    assert x['prev_close'].notna().sum() > 0


def test_build_event_features_columns():
    bars, dates = _synthetic_bars(base_close=(11.0, 3.0))
    _inject_shock(bars, '000000.SZ', dates[1], 20)
    _inject_shock(bars, '000001.SZ', dates[1], 25)
    cfg = {'shock_z_thr': -2.0, 'amount_ratio_thr': 1.5, 'shock_amount_ratio': 2.0,
           'z_window': 40, 'z_min_periods': 10}
    feat, ev = build_event_features(bars, cfg)
    assert set(feat['code']) == {'000000.SZ', '000001.SZ'}
    assert feat['shock_count'].sum() == len(ev) == 2
    # 每股一个 -4% 深砸放量 shock：depth≈0.04（prev bar 随机游走 → ±~1% 噪音），
    # amount_ratio≈5 ≥ hv 阈值 2
    assert (feat['shock_drop_mean'] > 0.02).all()
    assert (feat['amount_ratio_mean'] > 4).all()
    assert (feat['hv_event_count'] == 1).all()
    assert (feat['hv_event_share'] == 1.0).all()
    for c in ['shock_drop_mean', 'amount_ratio_mean', 'hv_depth', 'hv_pi3', 'hv_pi12']:
        assert c in feat.columns
