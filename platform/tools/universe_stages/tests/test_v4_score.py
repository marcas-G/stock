import pandas as pd
from layer1.v4 import score_factor_v4


def test_score_orders_smallcap_when_other_features_equal():
    df=pd.DataFrame({
        'code':['A','B'], 'market_cap':[10,20], 'activity_compress':[1,1],
        'vol_compress':[.88,.88], 'range60':[.33,.33], 'bottom_stability':[.52,.52],
        'bottom_rebound':[.075,.075], 'start_distance':[-.025,-.025], 'no_new_low':[1,1],
        'drawdown':[.2,.2],'stress':[.1,.1],'down_decay':[1,1],'low_break_eff':[.01,.01],
        'turnover_health':[1,1],'rs_change':[0,0],'efficiency':[1,1]
    })
    out=score_factor_v4(df,{})
    assert out.iloc[0]['code']=='A'
    assert 's_smallcap' in out.columns
