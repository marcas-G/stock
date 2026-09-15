import numpy as np
import pandas as pd


def classify_tick_side(df: pd.DataFrame) -> pd.Series:
    if 'side' in df.columns:
        s=df['side'].astype(str).str.lower(); out=pd.Series(0,index=df.index,dtype='int8')
        out[s.isin(['b','buy','1','买','主动买'])]=1
        out[s.isin(['s','sell','-1','卖','主动卖'])]=-1
        if (out!=0).any(): return out
    dp=df['price'].diff(); return np.sign(dp).replace(0,np.nan).ffill().fillna(0).astype('int8')


def tick_window_features(df: pd.DataFrame, large_trade_quantile=0.95) -> dict:
    if df.empty: return {}
    x=df.copy().sort_values('timestamp'); x['side_sign']=classify_tick_side(x)
    x['signed_amount']=x['amount']*x['side_sign']; x['signed_volume']=x['volume']*x['side_sign']
    q=x['amount'].quantile(large_trade_quantile); large=x[x['amount']>=q]
    sell=x[x['side_sign']<0]; buy=x[x['side_sign']>0]
    p0,p1=float(x['price'].iloc[0]),float(x['price'].iloc[-1]); impact=(p1/p0-1.0) if p0>0 else np.nan
    net_sell=max(0.0,-float(x['signed_amount'].sum()))
    total=float(x['amount'].sum())
    return {
        'tick_count':len(x),'amount_total':total,'signed_amount':float(x['signed_amount'].sum()),
        'sell_amount':float(sell['amount'].sum()),'buy_amount':float(buy['amount'].sum()),
        'large_trade_amount_share':float(large['amount'].sum()/total) if total>0 else np.nan,
        'large_sell_amount_share':float(large.loc[large['side_sign']<0,'amount'].sum()/total) if total>0 else np.nan,
        'window_return':impact,'price_impact_per_net_sell_1m':impact/(net_sell/1e6) if net_sell>0 else np.nan,
    }


def event_reversal_features(df,event_time,pre_seconds=1800,post_seconds=3600):
    t=pd.Timestamp(event_time)
    pre=df[(df['timestamp']>=t-pd.Timedelta(seconds=pre_seconds))&(df['timestamp']<t)].copy()
    post=df[(df['timestamp']>=t)&(df['timestamp']<=t+pd.Timedelta(seconds=post_seconds))].copy()
    a=tick_window_features(pre); b=tick_window_features(post)
    return {
        'pre_signed_amount':a.get('signed_amount',np.nan),'post_signed_amount':b.get('signed_amount',np.nan),
        'flow_reversal':b.get('signed_amount',np.nan)-a.get('signed_amount',np.nan),
        'post_impact_per_net_sell_1m':b.get('price_impact_per_net_sell_1m',np.nan),
        'post_window_return':b.get('window_return',np.nan),
    }
