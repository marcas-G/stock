#!/usr/bin/env python
"""C1 池公式路径补充验证：universe.formula 走 prepare_formula_pipeline →
_normalize_pool_formula → _pool_cond_frame → compute_formula（同一
reject_future_shifts 门）。负下标拒绝；正向 close[1] 可用。"""
import sys
sys.path.insert(0, 'platform/tests'); sys.path.insert(0, 'platform/src')
import dualbridge, test_qfq_chunk_invariance as tq  # noqa: E402
from pathlib import Path  # noqa: E402
from factorlab.app.context import RunContext  # noqa: E402
from factorlab.app.run import run_factor  # noqa: E402
from factorlab.core.spec import FactorSpec  # noqa: E402
import yaml  # noqa: E402

db = Path('/tmp/opencode/eng-probe/pool_probe.duckdb')
dualbridge.seed_duckdb(db, tq._qfq_tables(n=60))


def run(pool_formula):
    spec = FactorSpec.model_validate(yaml.safe_load(f"""
name: pool_probe
category: custom
direction: 1
universe:
  formula: "{pool_formula}"
date:
  start: "2024-01-02"
  end: "2024-03-01"
adjustment: raw
formula: |
  signal = close
process: []
"""))
    return run_factor(spec, RunContext(data_backend='duckdb', db_path=db,
                                       output_dir=Path('/tmp/opencode/eng-probe/o_pool'),
                                       adjustment='raw'))


for pf in ("close[-1] > 5", "close[1] >= 0"):
    try:
        r = run(pf)
        print(f"pool {pf!r}: ACCEPTED rows={r.panel.height}")
    except Exception as e:
        print(f"pool {pf!r}: REJECT {type(e).__name__}: {str(e)[:120]}")
