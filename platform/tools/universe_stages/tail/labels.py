from __future__ import annotations
import numpy as np
import pandas as pd


def future_mfe_labels(daily_adj: pd.DataFrame, snapshots: pd.DataFrame, horizons=(60,120)) -> pd.DataFrame:
    """Tail-event labels for snapshot×code.

    Base price = factor_date adjusted close (the last price known when the
    first-layer scan runs at next trading-day open). MFE uses future adjusted
    highs, not future closes. This is a label only and never enters features.

    Required daily columns: trade_date, code, pre_close, pre_high.
    Required snapshot columns: scan_date, factor_date, code.
    """
    d=daily_adj[['trade_date','code','pre_close','pre_high']].copy()
    d['trade_date']=pd.to_datetime(d['trade_date']).dt.normalize()
    s=snapshots[['scan_date','factor_date','code']].drop_duplicates().copy()
    s['scan_date']=pd.to_datetime(s['scan_date']).dt.normalize()
    s['factor_date']=pd.to_datetime(s['factor_date']).dt.normalize()
    out=[]
    for row in s.itertuples(index=False):
        g=d[d['code'].eq(row.code)].sort_values('trade_date')
        base=g[g['trade_date'].eq(row.factor_date)]
        if base.empty: continue
        p0=float(base.iloc[-1]['pre_close'])
        future=g[g['trade_date']>row.factor_date]
        rec={'scan_date':row.scan_date,'factor_date':row.factor_date,'code':row.code}
        for h in horizons:
            path=future.head(int(h))['pre_high'].to_numpy(float)
            rec[f'mfe{h}']=float(np.nanmax(path/p0-1.0)) if p0>0 and len(path) else np.nan
        rec['tail_60_30']=bool(rec.get('mfe60',np.nan)>=0.30)
        rec['tail_120_50']=bool(rec.get('mfe120',np.nan)>=0.50)
        rec['tail_120_80']=bool(rec.get('mfe120',np.nan)>=0.80)
        out.append(rec)
    return pd.DataFrame(out)
