from __future__ import annotations
import numpy as np
import pandas as pd

ELIGIBLE_NEED = [
    'market_cap', 'drawdown', 'stress', 'down_decay', 'low_break_eff',
    'no_new_low', 'activity_compress', 'turnover_health', 'vol_compress',
    'bottom_stability', 'range60', 'bottom_rebound', 'start_distance',
    'rs_change', 'efficiency'
]

OUTPUT_FACTOR_COLUMNS = [
    'code','score','market_cap','price','no_new_low',
    's_smallcap','s_activity','s_vol','s_range','s_bottom','s_rebound','s_start','s_no_new_low',
    'activity_compress','vol_compress','range60','bottom_stability','bottom_rebound','start_distance',
    'drawdown','stress','down_decay','low_break_eff','turnover_health','rs_change','efficiency','clv20',
    'r20','r60','r120'
]


def safe_float(x):
    try:
        x = float(x)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def safe_div(a, b):
    try:
        a, b = float(a), float(b)
        if not np.isfinite(a) or not np.isfinite(b) or abs(b) < 1e-12:
            return np.nan
        return a / b
    except Exception:
        return np.nan


def calc_down_impact(ret, activity):
    mask = (ret < 0) & np.isfinite(ret) & np.isfinite(activity) & (activity > 0)
    if mask.sum() < 5:
        return np.nan
    denominator = activity[mask].sum()
    if denominator <= 0:
        return np.nan
    return np.abs(ret[mask]).sum() / denominator


def calc_up_impact(ret, activity):
    mask = (ret > 0) & np.isfinite(ret) & np.isfinite(activity) & (activity > 0)
    if mask.sum() < 5:
        return np.nan
    denominator = activity[mask].sum()
    if denominator <= 0:
        return np.nan
    return ret[mask].sum() / denominator


def base_filter(fundamentals: pd.DataFrame, factor_date, cfg: dict) -> pd.DataFrame:
    d = pd.Timestamp(factor_date).normalize()
    df = fundamentals.copy()
    for c in ['market_cap','pe_ratio','operating_revenue','total_assets','total_liability']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['debt_ratio'] = df['total_liability'] / df['total_assets']
    df = df.replace([np.inf,-np.inf], np.nan).dropna(subset=[
        'code','market_cap','pe_ratio','operating_revenue','total_assets','total_liability','debt_ratio'
    ])
    list_days = (d - pd.to_datetime(df['list_date']).dt.normalize()).dt.days
    mask = (
        df['market_cap'].between(float(cfg['min_cap_cny']), float(cfg['max_cap_cny'])) &
        (df['pe_ratio'] > 0) & (df['operating_revenue'] > 0) & (df['total_assets'] > 0) &
        (df['debt_ratio'] <= float(cfg['max_debt_ratio'])) &
        (list_days >= int(cfg['min_list_days'])) &
        (~df['is_st'].fillna(False).astype(bool))
    )
    return df.loc[mask].copy().reset_index(drop=True)


def fast_pre_filter(daily90: pd.DataFrame, stocks, cfg: dict) -> list[str]:
    survivors = []
    if len(stocks) == 0 or daily90.empty:
        return survivors
    x = daily90[daily90['code'].isin(stocks)].copy()
    for code, df in x.groupby('code', sort=False):
        df = df.sort_values('trade_date').copy()
        df['pre_close'] = pd.to_numeric(df['pre_close'], errors='coerce')
        df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
        df = df.dropna(subset=['pre_close','amount'])
        df = df[df['amount'] > 0]
        if len(df) < int(cfg['fast_min_days']):
            continue
        close = df['pre_close'].to_numpy(float)
        money = df['amount'].to_numpy(float)
        if len(close) < 61:
            continue
        r20 = close[-1]/close[-21]-1.0
        r60 = close[-1]/close[-61]-1.0
        median_money_20 = np.median(money[-20:])
        if median_money_20 < float(cfg['min_median_amount_20']):
            continue
        if r20 < float(cfg['r20_min']) or r20 > float(cfg['r20_max']):
            continue
        if r60 < float(cfg['r60_min']) or r60 > float(cfg['r60_max']):
            continue
        survivors.append(code)
    return survivors


def calc_factor(df: pd.DataFrame, index_close: pd.Series | None, cfg: dict):
    """Exact local port of the frozen jqdata V4 calc_factor semantics."""
    df = df.copy().sort_values('trade_date')
    for col in ['pre_close','pre_high','pre_low','amount']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['pre_close','pre_high','pre_low','amount'])
    df = df[df['amount'] > 0]
    if len(df) < int(cfg['detail_min_days']):
        return None
    # The source already receives only the last 520 market dates; this tail is retained for exact semantics.
    df = df.tail(int(cfg.get('history_count', 520)))
    close = df['pre_close'].to_numpy(float)
    high = df['pre_high'].to_numpy(float)
    low = df['pre_low'].to_numpy(float)
    money = df['amount'].to_numpy(float)
    if len(close) < int(cfg['detail_min_days']):
        return None

    ret = np.empty(len(close)); ret[:] = np.nan
    ret[1:] = close[1:]/close[:-1]-1.0

    positive_money = money[-250:][money[-250:] > 0]
    if len(positive_money) < 100:
        return None
    base_money = np.median(positive_money)
    if not np.isfinite(base_money) or base_money <= 0:
        return None
    activity = np.clip(money/base_money, 0, 5)

    r20 = close[-1]/close[-21]-1.0
    r60 = close[-1]/close[-61]-1.0
    r120 = close[-1]/close[-121]-1.0
    if r20 < cfg['r20_min'] or r20 > cfg['r20_max']:
        return None
    if r60 < cfg['r60_min'] or r60 > cfg['r60_max']:
        return None
    median_money_20 = np.median(money[-20:])
    if median_money_20 < cfg['min_median_amount_20']:
        return None

    high500 = np.max(high[-500:])
    if high500 <= 0:
        return None
    drawdown = 1.0 - close[-1]/high500

    stress_ret, stress_act = ret[-250:-40], activity[-250:-40]
    stress_mask = (stress_ret < 0) & np.isfinite(stress_ret) & np.isfinite(stress_act)
    stress = np.nan if stress_mask.sum() < 20 else np.mean(np.abs(stress_ret[stress_mask]) * stress_act[stress_mask])

    old_di = calc_down_impact(ret[-160:-40], activity[-160:-40])
    recent_di = calc_down_impact(ret[-40:], activity[-40:])
    down_decay = safe_div(old_di, recent_di)
    if np.isfinite(down_decay):
        down_decay = min(down_decay, 4.0)

    old_low, new_low = np.min(low[-180:-60]), np.min(low[-60:])
    if old_low <= 0:
        low_break_eff, no_new_low = np.nan, np.nan
    else:
        downside_extension = max(0.0, (old_low-new_low)/old_low)
        no_new_low = 1.0 if downside_extension <= 1e-12 else 0.0
        recent60_ret, recent60_act = ret[-60:], activity[-60:]
        down_mask = (recent60_ret < 0) & np.isfinite(recent60_ret) & np.isfinite(recent60_act)
        down_activity = recent60_act[down_mask].sum()
        low_break_eff = np.nan if down_activity <= 0 else downside_extension/down_activity

    activity_compress = safe_div(np.mean(activity[-20:]), np.mean(activity[-120:-20]))
    turnover_health = safe_div(np.mean(money[-20:]), np.mean(money[-120:-20]))
    if not np.isfinite(turnover_health) or turnover_health < float(cfg['min_turnover_health']):
        return None

    rv20 = np.nanstd(ret[-20:])
    rv_old = np.nanstd(ret[-120:-20])
    vol_compress = safe_div(rv20, rv_old)
    low120, high120 = np.min(low[-120:]), np.max(high[-120:])
    bottom_stability = safe_div(high120-low120, low120)
    low60, high60 = np.min(low[-60:]), np.max(high[-60:])
    range60 = safe_div(high60-low60, low60)
    low20 = np.min(low[-20:])
    bottom_rebound = safe_div(close[-1], low20)
    if np.isfinite(bottom_rebound):
        bottom_rebound -= 1.0
    if np.isfinite(bottom_rebound) and bottom_rebound > float(cfg['max_bottom_rebound']):
        return None
    start_distance = 0.60*r20 + 0.40*r60

    rs_change = np.nan
    if index_close is not None and len(index_close) >= 61:
        idx = np.asarray(index_close, dtype=float)
        idx20 = idx[-1]/idx[-21]-1.0
        idx60 = idx[-1]/idx[-61]-1.0
        rs20 = r20-idx20
        rs60 = r60-idx60
        rs_change = rs20-rs60/3.0

    up_imp = calc_up_impact(ret[-60:], activity[-60:])
    down_imp = calc_down_impact(ret[-60:], activity[-60:])
    efficiency = safe_div(up_imp, down_imp)
    if np.isfinite(efficiency):
        efficiency = min(efficiency, 3.0)

    range20_arr = high[-20:]-low[-20:]
    valid = range20_arr > 0
    clv20 = np.mean((close[-20:][valid]-low[-20:][valid])/range20_arr[valid]) if valid.sum() >= 10 else np.nan

    # In jqdata fq='pre', the last adjusted price equals the raw factor-date price.
    # Local pre_close may be on a different constant scale; price is diagnostic only.
    price = close[-1]
    return {
        'price':price, 'median_money_20':median_money_20,
        'r20':r20,'r60':r60,'r120':r120,
        'drawdown':drawdown,'stress':stress,'down_decay':down_decay,
        'low_break_eff':low_break_eff,'no_new_low':no_new_low,
        'activity_compress':activity_compress,'turnover_health':turnover_health,
        'vol_compress':vol_compress,'bottom_stability':bottom_stability,'range60':range60,
        'bottom_rebound':bottom_rebound,'start_distance':start_distance,
        'rs_change':rs_change,'efficiency':efficiency,'clv20':clv20,
        'valid_days':len(df)
    }


def calculate_market(daily520: pd.DataFrame, stocks, fundamentals: pd.DataFrame, index_close, cfg: dict) -> pd.DataFrame:
    if not stocks:
        return pd.DataFrame()
    fund_map = fundamentals.set_index('code')
    result=[]
    x=daily520[daily520['code'].isin(stocks)]
    for code, stock_df in x.groupby('code', sort=False):
        if code not in fund_map.index:
            continue
        factor=calc_factor(stock_df,index_close,cfg)
        if factor is None:
            continue
        f=fund_map.loc[code]
        if isinstance(f,pd.DataFrame): f=f.iloc[-1]
        row={'code':code,'market_cap':safe_float(f['market_cap']),'pe_ratio':safe_float(f['pe_ratio']),'debt_ratio':safe_float(f['debt_ratio'])}
        row.update(factor); result.append(row)
    return pd.DataFrame(result)


def score_factor_v4(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df=df.copy().replace([np.inf,-np.inf],np.nan)
    df=df.dropna(subset=ELIGIBLE_NEED)
    if len(df)==0: return df
    df['s_smallcap']=(-df['market_cap']).rank(pct=True)
    df['s_activity']=(-df['activity_compress']).rank(pct=True)
    df['s_vol']=(-abs(df['vol_compress']-float(cfg.get('target_vol',0.88)))).rank(pct=True)
    df['s_range']=(-abs(df['range60']-float(cfg.get('target_range60',0.33)))).rank(pct=True)
    df['s_bottom']=(-abs(df['bottom_stability']-float(cfg.get('target_bottom',0.52)))).rank(pct=True)
    df['s_rebound']=(-abs(df['bottom_rebound']-float(cfg.get('target_rebound',0.075)))).rank(pct=True)
    df['s_start']=(-abs(df['start_distance']-float(cfg.get('target_start',-0.025)))).rank(pct=True)
    df['s_no_new_low']=df['no_new_low']
    df['score']=100.0*(
        .30*df['s_smallcap']+.20*df['s_activity']+.10*df['s_vol']+.10*df['s_range']+
        .10*df['s_bottom']+.05*df['s_rebound']+.05*df['s_start']+.10*df['s_no_new_low']
    )
    # Exact source only sorts on score; do not add a new tie-breaker in V4.0 parity mode.
    return df.sort_values('score',ascending=False).reset_index(drop=True)


def run_v4_snapshot(daily_store, fundamentals, index_close, scan_date, factor_date, cfg):
    base=base_filter(fundamentals,factor_date,cfg)
    base_codes=base['code'].tolist()
    daily90=daily_store.window_market_dates(base_codes,factor_date,int(cfg.get('fast_history_count',90)))
    fast_codes=fast_pre_filter(daily90,base_codes,cfg)
    daily520=daily_store.window_market_dates(fast_codes,factor_date,int(cfg.get('history_count',520)))
    factor_df=calculate_market(daily520,fast_codes,base,index_close,cfg)
    ranked=score_factor_v4(factor_df,cfg)
    ranked['rank']=np.arange(1,len(ranked)+1)
    ranked.insert(0,'factor_date',pd.Timestamp(factor_date).normalize())
    ranked.insert(0,'scan_date',pd.Timestamp(scan_date).normalize())
    top300=ranked.head(int(cfg.get('top300',300))).copy()
    top100=ranked.head(int(cfg.get('top100',100))).copy()
    summary={
        'scan_date':str(pd.Timestamp(scan_date).date()),'factor_date':str(pd.Timestamp(factor_date).date()),
        'B':len(base_codes),'F':len(fast_codes),'E':len(ranked),'T300':len(top300),'T100':len(top100)
    }
    return top300,top100,ranked,summary
