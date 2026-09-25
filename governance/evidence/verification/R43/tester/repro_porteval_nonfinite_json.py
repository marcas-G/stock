#!/usr/bin/env python3
"""Reproduce non-finite metrics in porteval's constant-return test case."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
PORTEVAL = ROOT / "research" / "tools" / "porteval"
sys.path.insert(0, str(PORTEVAL))
sys.path.insert(0, str(PORTEVAL / "tests"))

import test_engine  # noqa: E402
import pv_engine  # noqa: E402


def reject_nonstandard_constant(value: str):
    raise ValueError(f"non-standard JSON constant: {value}")


ctx = test_engine._ctx(D=6, N=4)
ctx["ret_close"][:] = 0.001
ctx["ret_open"][:] = 0.001
result = pv_engine.simulate(
    **ctx,
    cfg=pv_engine.PortfolioConfig(
        selection="top_n", top_n=2, fee_bps=0.0,
    ),
)

metrics = {key: result[key] for key in ("ir", "vol", "sharpe", "excess")}
print("metrics=", metrics)
print("finite_or_null=", {
    key: value is None or math.isfinite(value) for key, value in metrics.items()
})

# Match the strict production writers in porteval/run.py and
# pv_engine.run_from_npz.
payload = pv_engine._strict_json_dumps(result)
print("serialized_metrics=", {
    key: json.loads(payload, parse_constant=reject_nonstandard_constant)[key]
    for key in ("ir", "sharpe")
})
try:
    json.loads(payload, parse_constant=reject_nonstandard_constant)
except ValueError as exc:
    print("strict_json=REJECTED:", exc)
else:
    print("strict_json=ACCEPTED")
