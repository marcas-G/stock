# Issue #26 — nested-window codegen fail-loud guard

## Issue and scope

Issue #26 reports that a composite expression applying `ts_rank` to an earlier
rolling intermediate can yield an all-NaN signal on the CH-backed run, while
the component expressions and a separately materialized intermediate work.
It requests a nested-window regression and an explicit failure for an all-NaN
compiled output.

This change is limited to `platform/src/factorlab/core/engine/compute.py` and
`platform/tests/test_compute.py`.

## Changes

- After code generation, each declared floating-point output is checked. If it
  has at least one non-null value and every non-null value is NaN,
  `compute_formula` raises `FactorDSLError`, naming the output and pointing to
  nested-window intermediates or invalid inputs.
- An all-null output remains allowed. This preserves the existing warmup
  behavior when a rolling window has not filled.
- Added a two-asset regression using the issue's nested `ts_count` /
  `ts_rank` / nested energy shape. Its direct composite output is compared
  cell-by-cell with a control that materializes the window intermediates before
  applying the next window.
- Added guards for all-NaN output and for leading null warmup rows.

## Verification

The required RED run added both regressions before the implementation:

```text
platform/.venv/bin/python -m pytest platform/tests/test_compute.py -k 'nested_window_intermediate or rejects_all_nan_output' -q
```

Result: `1 failed, 1 passed`. The nested-versus-materialized comparison passed
on the local synthetic panel; the all-NaN case failed because the compiler
returned NaN values without raising. Raw output: `ISSUE-26-red.log`.

After the guard was added:

| Command | Result | Raw output |
|---|---:|---|
| Focused nested-window and all-NaN tests | 2 passed | `ISSUE-26-focused-green.log` |
| `platform/.venv/bin/python -m pytest platform/tests/test_compute.py -q` | 28 passed | `ISSUE-26-test-compute.log` |
| `governance/ops/heavy.sh platform/.venv/bin/python -m pytest platform/tests/test_minute_engine.py -q` | 44 passed | `ISSUE-26-test-minute-engine.log` |

## Remaining live gate

The local synthetic regression does **not** reproduce the reported silent
all-NaN nested-window defect: the direct issue-shaped expression produces
finite values and matches the materialized control on two assets. Historical
probe runs under `/tmp/opencode/probe/` terminate with the `ashare_daily`
health read gate reporting partition `2026-09-10` as `UNKNOWN`; they do not
provide a completed CH-backed reproduction or acceptance run.

No high-cost CH factor run was started in this task. Therefore this evidence
confirms the compiled-output fail-loud guard and its local tests, but does not
confirm that nested-window generation itself is fixed on the real CH spec.
Keep Issue #26 open until the issue spec completes through a valid CH-backed
run and its output is checked.
