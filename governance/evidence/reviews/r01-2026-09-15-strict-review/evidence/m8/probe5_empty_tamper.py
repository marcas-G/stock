"""Probe: empty BacktestResult persistence + tamper fail-closed behavior."""
import datetime, json, shutil
from pathlib import Path
import polars as pl

from factorlab.adapters.execution_store import (save_backtest_result,
                                                load_backtest_result)
from factorlab.core.domain.backtest import BacktestResult, NavSeries
from factorlab.core.domain.execution import PortfolioState, PortfolioStatePhase

empty_pos = pl.DataFrame({"code": pl.Series([], dtype=pl.String),
                          "quantity": pl.Series([], dtype=pl.Int64),
                          "sellable_quantity": pl.Series([], dtype=pl.Int64)})
nav = NavSeries(frame=pl.DataFrame({"execution_date": pl.Series([], dtype=pl.Date),
                                    "cash": pl.Series([], dtype=pl.Float64),
                                    "market_value": pl.Series([], dtype=pl.Float64),
                                    "nav": pl.Series([], dtype=pl.Float64)}))
final = PortfolioState(as_of_date=datetime.date(2024, 1, 2),
                       phase=PortfolioStatePhase.PRE_EXECUTION,
                       cash=1000.0, positions=empty_pos)
r = BacktestResult(artifacts=(), nav_series=nav, final_state=final)
out = Path("/tmp/opencode/reviewer-m8/probe5_empty")
if out.exists():
    shutil.rmtree(out)
m = save_backtest_result(r, out, created_at="X")
print("saved empty:", m.artifact_count, m.execution_date_start, m.execution_date_end)
r2 = load_backtest_result(out)
print("loaded empty:", len(r2.artifacts), r2.nav_series.frame.height,
      r2.final_state.cash, r2.final_state.as_of_date)

# --- tamper: unknown schema_version -> must fail
raw = json.loads((out / "manifest.json").read_text())
raw["schema_version"] = "2"
(out / "manifest.json").write_text(json.dumps(raw))
try:
    load_backtest_result(out)
    print("BUG: unknown schema_version silently loaded")
except ValueError as e:
    print("unknown version fail-closed:", str(e)[:70])

# --- tamper: missing file
raw["schema_version"] = "1"
(out / "manifest.json").write_text(json.dumps(raw))
(out / "artifacts" / "fills.parquet").unlink()
try:
    load_backtest_result(out)
    print("BUG: missing file silently loaded")
except ValueError as e:
    print("missing file fail-closed:", str(e)[:70])

# --- tamper: nav_series row swap on a real run (older artifact nav)
print("\n-- swap nav from another run (demo of no cross-check) --")
from factorlab.adapters.atomicio import atomic_write_parquet
frame = pl.read_parquet(out / "nav/nav_series.parquet")
print("(skip; covered by probe4 hybrid)")
