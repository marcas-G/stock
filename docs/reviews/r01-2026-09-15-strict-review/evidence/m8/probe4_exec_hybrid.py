"""Probe: stale-manifest hybrid load after failed overwrite (M8 execution_store)."""
import datetime, sys, shutil
sys.path.insert(0, "/data/students/gaolei/stock/platform/tests")
import duckdb, polars as pl
from pathlib import Path

from factorlab.app.bootstrap import open_read
from factorlab.app.backtest import ExecutionSpec, run_backtest
from factorlab.core.domain import TargetPortfolio, TargetPortfolioMeta
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
import factorlab.adapters.execution_store as ES

D1 = datetime.date(2024, 1, 2)
D2 = datetime.date(2024, 1, 3)
D3 = datetime.date(2024, 1, 4)
D5 = datetime.date(2024, 1, 5)
D8 = datetime.date(2024, 1, 8)
D9 = datetime.date(2024, 1, 9)

dbp = "/tmp/opencode/reviewer-m8/probe4.duckdb"
if Path(dbp).exists():
    Path(dbp).unlink()
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

# run A prices
for d, a, b in ((D2, 10.0, 20.0), (D3, 11.0, 21.0)):
    add_daily(d, "000001.SZ", a)
    add_daily(d, "600000.SH", b)
db.close()

def target():
    rows = [(D1, "000001.SZ", 0.5), (D1, "600000.SH", 0.5), (D2, "000001.SZ", 1.0)]
    frame = pl.DataFrame(rows, schema=["decision_date", "code", "target_weight"], orient="row")
    frame = frame.with_columns(pl.col("decision_date").cast(pl.Date),
                               pl.col("code").cast(pl.String),
                               pl.col("target_weight").cast(pl.Float64))
    return TargetPortfolio(frame=frame, decision_dates=(D1, D2),
                           meta=TargetPortfolioMeta(strategy_name="s", source_signal_name="sig",
                                                    source_timing=DEFAULT_EOD_SIGNAL_TIMING,
                                                    gross_exposure=1.0))

spec = ExecutionSpec.model_validate({"initial_cash": 1_000_000.0})
run_a = run_backtest(target(), spec, open_read(db_path=dbp))
out = Path("/tmp/opencode/reviewer-m8/probe4_artifacts")
if out.exists():
    shutil.rmtree(out)
ES.save_backtest_result(run_a, out, created_at="A")
loaded_a = ES.load_backtest_result(out)
print("A nav:", loaded_a.nav_series.frame["nav"].to_list())

# change prices in DB (run B same trades, different marks/NAVs -> different cash path)
db = duckdb.connect(dbp)
db.execute("DELETE FROM daily")
db.execute("DELETE FROM stk_limit")
for d, a, b in ((D2, 10.0, 20.0), (D3, 14.0, 16.0)):
    add_daily(d, "000001.SZ", a)
    add_daily(d, "600000.SH", b)
db.close()
run_b = run_backtest(target(), spec, open_read(db_path=dbp))
print("B nav:", run_b.nav_series.frame["nav"].to_list())

# inject failure at the LAST data write before manifest: nav/nav_series.parquet
import factorlab.adapters.atomicio as AI
orig = AI.atomic_write_parquet
calls = {"n": 0}
def flaky(frame, path):
    calls["n"] += 1
    if str(path).endswith("nav/nav_series.parquet"):
        raise OSError("disk full simulated at nav write")
    return orig(frame, path)
AI.atomic_write_parquet = flaky
try:
    ES.save_backtest_result(run_b, out, created_at="B")
except OSError as e:
    print("write B failed as simulated:", e)
finally:
    AI.atomic_write_parquet = orig

try:
    hybrid = ES.load_backtest_result(out)
    print("LOADED HYBRID: artifacts =", len(hybrid.artifacts),
          "nav_series =", hybrid.nav_series.frame["nav"].to_list())
    print("  artifact.nav values:", [a.nav.nav for a in hybrid.artifacts])
    print("  final cash:", hybrid.final_state.cash)
except Exception as e:
    print("hybrid load failed:", type(e).__name__, e)
