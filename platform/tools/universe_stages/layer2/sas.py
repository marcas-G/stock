import numpy as np
import pandas as pd


def label_shocks(bars5m: pd.DataFrame, cfg: dict):
    """bar 级 Sell Shock 标注（z-score 定义，与 scripts/ 下已验证的 SAS 语义一致（R20：sas_v0/v1.py 已不存在，语义由 tests/test_sas_zscore.py 固化））：
    - 仅 session_class==0（纯连续竞价）
    - r = close/prev_close-1（同日限制，跨日不计）
    - r_mean/r_std/amount_med = 滚动 z_window bar，shift(1) 只含过去
    - is_shock = shock_z < shock_z_thr & amount_ratio > amount_ratio_thr
    """
    x = bars5m[bars5m['session_class'] == 0].copy().sort_values(['code', 'datetime'])
    if x.empty:
        return x, pd.Series(dtype=bool)
    win = int(cfg.get('z_window', 40))
    minp = int(cfg.get('z_min_periods', 10))
    x['prev_close'] = x.groupby(['code', 'trade_date'])['close'].shift(1)
    x['r'] = x['close'] / x['prev_close'] - 1
    g = x.groupby('code')
    x['r_mean'] = g['r'].transform(
        lambda s: s.rolling(win, min_periods=minp).mean().shift(1))
    x['r_std'] = g['r'].transform(
        lambda s: s.rolling(win, min_periods=minp).std().shift(1))
    x['amount_med'] = g['amount'].transform(
        lambda s: s.rolling(win, min_periods=minp).median().shift(1))
    x['shock_z'] = (x['r'] - x['r_mean']) / x['r_std'].replace(0, np.nan)
    x['amount_ratio'] = x['amount'] / x['amount_med'].replace(0, np.nan)
    is_shock = ((x['shock_z'] < float(cfg.get('shock_z_thr', -2.0))) &
                (x['amount_ratio'] > float(cfg.get('amount_ratio_thr', 1.5))) &
                x['r'].notna()).fillna(False)
    return x, is_shock


def shock_events(bars: pd.DataFrame, is_shock: pd.Series, cfg: dict) -> pd.DataFrame:
    """shock bar 前向指标（同日限制）：rec_h / pi_h"""
    ev = bars[is_shock].copy()
    if ev.empty:
        return ev
    for h in (1, 3, 6, 12):
        ev[f'rec_{h}'] = bars.groupby(['code', 'trade_date'])['close'].shift(-h) \
                             .reindex(ev.index) / ev['close'] - 1
        ev[f'pi_{h}'] = bars.groupby(['code', 'trade_date'])['close'].shift(-h) \
                            .reindex(ev.index) / ev['prev_close'] - 1
    return ev


def build_event_features(bars5m: pd.DataFrame, cfg: dict):
    """每股一行聚合特征（列名沿用已验证形式（同上，见 tests/test_sas_zscore.py））"""
    hv = float(cfg.get('shock_amount_ratio', 2.0))
    bars, is_shock = label_shocks(bars5m, cfg)
    ev = shock_events(bars, is_shock, cfg)
    if ev.empty:
        return pd.DataFrame(columns=['code', 'shock_count', 'hv_event_count',
                                     'hv_event_share', 'shock_drop_mean',
                                     'amount_ratio_mean', 'hv_depth', 'hv_pi3',
                                     'hv_pi12', 'hv_absorption_spread_3']), ev
    ev['shock_depth'] = -ev['r']
    ev['high_vol'] = ev['amount_ratio'] >= hv
    rows = []
    for code, g in ev.groupby('code'):
        hi = g[g['high_vol']]
        lo = g[~g['high_vol']]
        rows.append({
            'code': code,
            'shock_count': len(g),
            'hv_event_count': len(hi),
            'hv_event_share': len(hi) / len(g) if len(g) else np.nan,
            'shock_drop_mean': g['shock_depth'].mean(),
            'amount_ratio_mean': g['amount_ratio'].mean(),
            'hv_depth': hi['shock_depth'].mean() if len(hi) else np.nan,
            'hv_pi3': (-hi['pi_3']).mean() if len(hi) else np.nan,
            'hv_pi12': (-hi['pi_12']).mean() if len(hi) else np.nan,
            'hv_absorption_spread_3': ((-hi['pi_3']).mean() - (-lo['pi_3']).mean())
                if len(hi) and len(lo) else np.nan,
        })
    return pd.DataFrame(rows), ev
