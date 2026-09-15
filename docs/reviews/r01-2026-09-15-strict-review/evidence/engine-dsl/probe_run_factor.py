import sys
from pathlib import Path

PLATFORM = Path("/data/students/gaolei/stock/platform")
sys.path.insert(0, str(PLATFORM / "tests"))
sys.path.insert(0, str(PLATFORM / "src"))

import dualbridge  # noqa: E402
import test_qfq_chunk_invariance as tq  # noqa: E402

from factorlab.app.context import RunContext  # noqa: E402
from factorlab.app.run import run_factor  # noqa: E402
from factorlab.core.spec import FactorSpec  # noqa: E402
import yaml  # noqa: E402

DB = Path("/tmp/opencode/reviewer-dsl/probe.duckdb")
if DB.exists():
    DB.unlink()
dualbridge.seed_duckdb(DB, tq._qfq_tables(n=60))

out = Path("/tmp/opencode/reviewer-dsl/run_future_subscript")
spec = FactorSpec.model_validate(yaml.safe_load(f"""
name: future_subscript_probe
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-03-01"
formula: |
  signal = close[-1]
process: []
"""))
res = run_factor(spec, RunContext(data_backend="duckdb", db_path=DB,
                                  output_dir=out, adjustment="raw"))
frame = res.signal_artifact.frame
panel = res.panel
# build check: signal(t) == raw close(t+1)?
import polars as pl  # noqa: E402
merged = panel.select(["date", "code", "signal", "close"]).sort(["code", "date"])
merged = merged.with_columns(pl.col("close").shift(-1).alias("__next_close"))
nn = merged.filter(pl.col("signal").is_not_null())
match = nn["signal"].to_list() == nn["__next_close"].to_list()
print("signal rows:", frame.height, "last date:", frame["date"].max())
print("signal(t) == close(t+1) for all non-null rows:", match)
print(frame.head(3))

# control: explicit negative shift must be rejected by engine
spec_bad = FactorSpec.model_validate(yaml.safe_load("""
name: neg_shift_control
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-03-01"
formula: |
  signal = ts_delay(close, -1)
process: []
"""))
try:
    run_factor(spec_bad, RunContext(data_backend="duckdb", db_path=DB,
                                    output_dir=Path("/tmp/opencode/reviewer-dsl/run_neg"),
                                    adjustment="raw"))
    print("CONTROL ts_delay(close,-1): ACCEPTED (unexpected)")
except Exception as e:
    print("CONTROL ts_delay(close,-1): rejected ->", type(e).__name__, str(e)[:90])
