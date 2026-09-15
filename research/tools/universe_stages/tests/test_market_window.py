import pandas as pd
import pytest

pytest.importorskip("factorlab", reason="readers.daily 经 universe_paths 模块级 import factorlab")

from readers.daily import DailyStore


def test_market_window_semantics():
    dates=pd.bdate_range('2026-01-01', periods=100)
    rows=[]
    for d in dates:
        rows.append({'trade_date':d,'code':'A','pre_open':1,'pre_high':1,'pre_low':1,'pre_close':1,'amount':1,'volume':1})
    for d in dates[::2]:
        rows.append({'trade_date':d,'code':'B','pre_open':1,'pre_high':1,'pre_low':1,'pre_close':1,'amount':1,'volume':1})
    s=DailyStore('/dev/null')
    s._df=pd.DataFrame(rows).sort_values(['trade_date','code']).reset_index(drop=True)
    w=s.window_market_dates(['B'],dates[-1],90)
    assert w['trade_date'].min() >= dates[-90]
    assert len(w) == 45
