import datetime
import sys

sys.path.insert(0, '/data/students/gaolei/stock/research/tools')
sys.path.insert(0, '/data/students/gaolei/stock/research/tools/strategies')
import polars as pl
from strategy_crash_bottom import strategy_backtest, strategy_long_backtest, monte_carlo

rows = []
for d in (datetime.date(2024, 1, 5), datetime.date(2024, 1, 12),
          datetime.date(2024, 1, 26)):
    for i, c in enumerate(('A', 'B', 'C', 'D')):
        rows.append(dict(date=d, code=c, signal=4.0 - i, forward_return_5d=0.03))
p = pl.DataFrame(rows)
r = strategy_backtest(p, k=2, cost_bps=35)
print('episodes:', [(e.get('start'), e.get('end')) for e in r['episodes']])
print('weeks:', r['weeks'], 'total_cost:', r['total_cost'],
      'avg_turnover:', r['avg_turnover'])
print('weekly_returns:', [round(x, 6) for x in r['weekly_returns']])

# long + mc episode mode
p2 = pl.DataFrame(rows)
mkt = pl.DataFrame({'date': [datetime.date(2024, 1, 5), datetime.date(2024, 1, 12),
                             datetime.date(2024, 1, 26)],
                    'mkt20': [-0.10, -0.09, -0.09]})
r2 = strategy_long_backtest(p2, k=2, cost_bps=0, mkt20=mkt, batches=2, batch_gap=1)
try:
    mc = monte_carlo(r2['weekly_returns'], r2['episodes'], n_sims=5, mode='episode')
    print('mc long ok', mc['n_units'])
except Exception as e:
    print('mc long FAILED:', type(e).__name__, e)

# --- limit-down join format probe ---
import datetime as _dt
d = _dt.date(2024, 1, 5)
pnl = pl.DataFrame([{'date': d, 'code': '000001.SZ', 'signal': 1.0,
                     'forward_return_5d': 0.10}])
ld6 = pl.DataFrame({'date': [d], 'code': ['000001'], 'pct_chg': [-10.0]})
r6 = strategy_backtest(pnl, limit_down=ld6, k=1, cost_bps=0)
print('panel .SZ + limit_down 6-digit -> weeks', r6['weeks'], 'nav', r6['nav'])
lds = pl.DataFrame({'date': [d], 'code': ['000001.SZ'], 'pct_chg': [-10.0]})
rs = strategy_backtest(pnl, limit_down=lds, k=1, cost_bps=0)
print('panel .SZ + limit_down .SZ     -> weeks', rs.get('weeks'), 'err', rs.get('error'))
