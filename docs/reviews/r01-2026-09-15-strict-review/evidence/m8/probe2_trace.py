"""Probe: instrument run_backtest to capture PRE/POST valuations, verify identity."""
import datetime, sys
sys.path.insert(0, "/data/students/gaolei/stock/platform/tests")
import duckdb

from factorlab.app.bootstrap import open_read
from factorlab.app.backtest import ExecutionSpec, run_backtest
from factorlab.core.execution import valuation as valmod

D1 = datetime.date(2024, 1, 2)
D2 = datetime.date(2024, 1, 3)
D3 = datetime.date(2024, 1, 4)
D5 = datetime.date(2024, 1, 5)
D8 = datetime.date(2024, 1, 8)
D9 = datetime.date(2024, 1, 9)

dbp = "/tmp/opencode/reviewer-m8/probe2.duckdb"
import os
for f in (dbp,):
    if os.path.exists(f):
        os.remove(f)
db = duckdb.connect(dbp)
db.execute("CREATE TABLE trade_cal (cal_date VARCHAR, is_open INT)")
for d in (D1, D2, D3, D5, D8, D9):
    db.execute("INSERT INTO trade_cal VALUES (?,1)", (d.strftime("%Y%m%d"),))
db.execute("CREATE TABLE stock_basic (ts_code VARCHAR, symbol VARCHAR, market VARCHAR)")
for c, m in (("000001.SZ", "主板"), ("600000.SH", "主板")):
    db.execute("INSERT INTO stock_basic VALUES (?,?,?)", (c, c[:6], m))
db.execute("CREATE TABLE daily (trade_date VARCHAR, ts_code VARCHAR, open DOUBLE, pre_close DOUBLE)")
db.execute("CREATE TABLE stk_limit (trade_date VARCHAR, ts_code VARCHAR, up_limit DOUBLE, down_limit DOUBLE)")
db.execute("CREATE TABLE adj_event (trade_date VARCHAR, ts_code VARCHAR)")

def add_daily(date, code, open_):
    up, dn = round(open_ * 1.1, 4), round(open_ * 0.9, 4)
    db.execute("INSERT INTO daily VALUES (?,?,?,?)", (date.strftime("%Y%m%d"), code, open_, open_))
    db.execute("INSERT INTO stk_limit VALUES (?,?,?,?)", (date.strftime("%Y%m%d"), code, up, dn))

for d in (D2, D3, D5, D8, D9):
    add_daily(d, "000001.SZ", 10.0 + (d - D2).days * 0.5)
    add_daily(d, "600000.SH", 20.0 + (d - D2).days * 1.0)
db.close()

import polars as pl
from factorlab.core.domain import TargetPortfolio, TargetPortfolioMeta
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING

def target(dates, weights):
    rows = []
    for d, wm in weights:
        for c, w in sorted(wm.items()):
            rows.append((d, c, w))
    frame = pl.DataFrame(rows, schema=["decision_date", "code", "target_weight"], orient="row")
    frame = frame.with_columns(pl.col("decision_date").cast(pl.Date),
                               pl.col("code").cast(pl.String),
                               pl.col("target_weight").cast(pl.Float64))
    return TargetPortfolio(frame=frame, decision_dates=tuple(dates),
                           meta=TargetPortfolioMeta(strategy_name="s", source_signal_name="sig",
                                                    source_timing=DEFAULT_EOD_SIGNAL_TIMING,
                                                    gross_exposure=1.0))

captured = []
orig = valmod.value_portfolio
def spy(state, marks):
    v = orig(state, marks)
    captured.append((state.as_of_date, state.phase.value, v.nav, dict(zip(marks.frame["code"].to_list(), marks.frame["mark_price"].to_list())), state.cash))
    return v
valmod.value_portfolio = spy
# also patch the name imported into backtest module
import factorlab.app.backtest.backtest as bt
bt.value_portfolio = spy

spec = ExecutionSpec.model_validate({"initial_cash": 1_000_000.0,
    "cost_model": {"commission_rate": 0.0003, "minimum_commission": 5.0,
                   "stamp_tax_sell_rate": 0.001, "transfer_fee_rate": 0.00002,
                   "slippage_bps": 0.0}})
t = target((D1, D2, D5), [
    (D1, {"000001.SZ": 0.4, "600000.SH": 0.6}),
    (D2, {"000001.SZ": 1.0}),
    (D5, {}),
])
r = run_backtest(t, spec, open_read(db_path=dbp))

print("events:", [(a.decision_date, a.execution_date) for a in r.artifacts])
for a in r.artifacts:
    print(f"--- {a.execution_date}")
    print("  pre cash", a.pre_state.cash, "post cash", a.post_state.cash)
    print("  fills:")
    for row in a.fills.frame.iter_rows():
        print("    ", row)
    print("  acct: fees", a.accounting.total_fees, "net", a.accounting.net_cash_delta,
          "buygross", a.accounting.buy_gross_notional, "sellgross", a.accounting.sell_gross_notional)
    print("  nav", a.nav.nav, "mv", a.nav.market_value)
    pos = a.post_state.positions
    mv = sum(q * p for q, p in zip(pos["quantity"].to_list(), a.nav.frame["mark_price"].to_list()))
    print("  recomputed MV == nav.mv:", mv == a.nav.market_value)

# spy pairs per event date
print("\ncaptured valuations (date, phase, nav, marks, cash):")
for row in captured:
    print("  ", row)

# identity: post_nav == pre_nav - total_fees (slippage 0)
for a in r.artifacts:
    pre = [c for c in captured if c[0] == a.execution_date and c[1] == "pre_execution"][0]
    post = [c for c in captured if c[0] == a.execution_date and c[1] == "post_execution"][0]
    print(a.execution_date, "pre-post:", pre[2] - post[2], "fees:", a.accounting.total_fees,
          "ok:", abs((pre[2] - post[2]) - a.accounting.total_fees) < 1e-6)

# check round trip persistence
from pathlib import Path
from factorlab.adapters.execution_store import save_backtest_result, load_backtest_result
out = Path("/tmp/opencode/reviewer-m8/probe2_artifacts")
save_backtest_result(r, out, created_at="2026-01-01T00:00:00+00:00")
r2 = load_backtest_result(out)
print("\nroundtrip nav equal:", r2.nav_series.frame.equals(r.nav_series.frame))
print("roundtrip artifacts:", len(r2.artifacts), "final:", r2.final_state.as_of_date, r2.final_state.phase)
for a1, a2 in zip(r.artifacts, r2.artifacts):
    print("  fills equal:", a1.fills.frame.equals(a2.fills.frame),
          "orders equal:", a1.orders.orders.equals(a2.orders.orders),
          "nav equal:", a1.nav.nav == a2.nav.nav,
          "acct equal:", a1.accounting.total_fees == a2.accounting.total_fees)
