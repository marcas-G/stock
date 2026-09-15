from __future__ import annotations
import numpy as np
import pandas as pd

PARITY_NUMERIC_COLUMNS = [
    'score','market_cap','no_new_low','s_smallcap','s_activity','s_vol','s_range','s_bottom','s_rebound','s_start','s_no_new_low',
    'activity_compress','vol_compress','range60','bottom_stability','bottom_rebound','start_distance',
    'drawdown','stress','down_decay','low_break_eff','turnover_health','rs_change','efficiency','clv20','r20','r60','r120'
]


def _snap(df, scan_date):
    d=pd.Timestamp(scan_date).normalize()
    x=df.copy(); x['scan_date']=pd.to_datetime(x['scan_date']).dt.normalize()
    return x[x['scan_date']==d].copy()


def compare_top300(local: pd.DataFrame, golden: pd.DataFrame, scan_date) -> dict:
    a,b=_snap(local,scan_date),_snap(golden,scan_date)
    aset,bset=set(a['code']),set(b['code']); inter=aset&bset
    out={'scan_date':str(pd.Timestamp(scan_date).date()),'local_n':len(a),'golden_n':len(b),'intersection':len(inter),
         'jaccard':len(inter)/len(aset|bset) if (aset|bset) else np.nan,
         'golden_recall':len(inter)/len(bset) if bset else np.nan}
    if 'rank' in a and 'rank' in b:
        m=a[['code','rank']].merge(b[['code','rank']],on='code',suffixes=('_local','_golden'))
        out['rank_exact_on_overlap']=float((m['rank_local']==m['rank_golden']).mean()) if len(m) else np.nan
        out['rank_mae_on_overlap']=float((m['rank_local']-m['rank_golden']).abs().mean()) if len(m) else np.nan
    common=[c for c in PARITY_NUMERIC_COLUMNS if c in a.columns and c in b.columns]
    if common:
        m=a[['code']+common].merge(b[['code']+common],on='code',suffixes=('_local','_golden'))
        diffs={}
        for c in common:
            x=pd.to_numeric(m[c+'_local'],errors='coerce'); y=pd.to_numeric(m[c+'_golden'],errors='coerce')
            z=(x-y).abs(); diffs[c]={'mae':float(z.mean()) if z.notna().any() else np.nan,'max_abs':float(z.max()) if z.notna().any() else np.nan}
        out['numeric_diffs']=diffs
    return out
