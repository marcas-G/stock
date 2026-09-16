"""Probe: stale-manifest hybrid load after a failed overwrite (M7 strategy artifacts)."""
import datetime, sys
sys.path.insert(0, "/data/students/gaolei/stock/platform/tests")
import polars as pl
from pathlib import Path

from factorlab.core.domain.frames import SignalArtifact, SignalMeta
from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING
from factorlab.core.strategy import SelectionSpec, StrategySpec, WeightingSpec
from factorlab.core.strategy.constructor import construct_target_portfolio
from factorlab.core.strategy.schedule import build_rebalance_schedule
import factorlab.adapters.strategy_artifacts as SA

D1 = datetime.date(2024, 1, 2)
D2 = datetime.date(2024, 1, 3)

def signal(name="alpha_x", weights=None):
    frame = pl.DataFrame({
        "date": pl.Series([D1, D1, D2, D2], dtype=pl.Date),
        "code": pl.Series(["000001.SZ", "600000.SH"] * 2, dtype=pl.String),
        "signal": pl.Series([2.0, 1.0, 2.0, 1.0], dtype=pl.Float64),
    })
    return SignalArtifact(frame=frame, meta=SignalMeta(
        name=name, frequency="1d", timing=DEFAULT_EOD_SIGNAL_TIMING,
        adjustment="qfq"))

def spec(name="strategy_x", signal_name="alpha_x"):
    return StrategySpec.model_validate({"name": name, "signal_name": signal_name,
        "direction": 1, "selection": {"method": "top_k", "k": 1},
        "weighting": {"method": "equal_weight"}})

out = Path("/tmp/opencode/reviewer-m8/strategy_dir")
import shutil
if out.exists():
    shutil.rmtree(out)

# 1) write a valid bundle A
sa = signal()
sp = spec()
sch = build_rebalance_schedule(sa, sp)
tp = construct_target_portfolio(sa, sp)
SA.write_strategy_artifacts(out, source_signal=sa, spec=sp, schedule=sch, target=tp)
b = SA.load_strategy_artifacts(out)
print("A loaded:", b.target.frame.height, b.spec.name)

# 2) start overwriting with a *different* bundle B (different signal name/spec) but
#    fail at the schedule step.
sa_b = signal(name="alpha_y")
sp_b = spec(name="strategy_y", signal_name="alpha_y")
sch_b = build_rebalance_schedule(sa_b, sp_b)
tp_b = construct_target_portfolio(sa_b, sp_b)

orig = SA._atomic_write_file
calls = {"n": 0}
def flaky(path, writer):
    calls["n"] += 1
    if calls["n"] == 2:
        raise OSError("disk full simulated during schedule write")
    return orig(path, writer)
SA._atomic_write_file = flaky
try:
    SA.write_strategy_artifacts(out, source_signal=sa_b, spec=sp_b,
                                schedule=sch_b, target=tp_b)
except OSError as e:
    print("write failed as simulated:", e)
finally:
    SA._atomic_write_file = orig

# 3) Can we still load? Which provenance wins?
try:
    bb = SA.load_strategy_artifacts(out)
    print("LOADED HYBRID: spec.name =", bb.spec.name, "signal_name =", bb.spec.signal_name,
          "target source_signal_name =", bb.target.meta.source_signal_name,
          "rows =", bb.target.frame.height)
    print("target frame:\n", bb.target.frame)
except Exception as e:
    print("load failed:", type(e).__name__, e)
