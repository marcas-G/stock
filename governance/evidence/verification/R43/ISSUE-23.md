# Issue #23 — infer `factor admit` reference scale from the spec

Date: 2026-09-25

## Problem

`flab factor admit` defaulted to the `daily` reference group for every candidate.
Minute-interface specs therefore needed an explicit `--scales minute`, despite
the loaded spec already declaring `interface: bars_1m`.

## Change

- If `--scales` is omitted, `factor admit` selects `minute` for
  `interface: bars_1m`; `daily` for `interface: daily` and for specs with no
  interface field.
- An explicit `--scales` value takes precedence over the inferred scale.
- The `factor.admit` registry default is `null` so the handler can distinguish
  omission from an explicit choice. CLI help and `describe` document the
  automatic mapping and override behavior.
- Scale inference is now kept separate from diagnostic cadence: `bars_1m`
  selects the minute reference group, while the candidate spec's
  `evaluation_frequency` selects daily/weekly resIC and its forward label.
  This prevents a minute candidate from silently falling back to weekly/5d
  diagnostics.
- Tests replace `_final_test_gate` with a stub. They verify the selected scale
  and reference members without executing a real final test.

## Verification

The parameterized tests first failed for the `bars_1m` implicit-scale case,
the registry default, and CLI help:

- RED command: `platform/.venv/bin/python -m pytest -q platform/tests/test_research_factor.py -k 'factor_admit_scales_follow_spec_interface or cli_research_factor_admit_help or factor_commands_registered_with_schemas'`
- Raw RED output: `ISSUE-23-red.log` (3 failed, 4 passed).
- GREEN command: `platform/.venv/bin/python -m pytest -q platform/tests/test_research_factor.py -k 'factor_admit_scales_follow_spec_interface or cli_research_factor_admit_help or factor_commands_registered_with_schemas or factor_admit_bad_spec_is_lint or factor_admit_independent_is_can_join'`
- Raw GREEN output: `ISSUE-23-green-final.log` (**9 passed**, 43 deselected).
- Documentation path guard: `platform/.venv/bin/python -m pytest -q platform/tests/test_doc_paths_exist.py`
- Raw output: `ISSUE-23-doc-paths.log` (**11 passed**).
- Final serialized GREEN rerun: `governance/ops/heavy.sh platform/.venv/bin/python -m pytest -q platform/tests/test_research_factor.py -k 'factor_admit_scales_follow_spec_interface or cli_research_factor_admit_help or factor_commands_registered_with_schemas or factor_admit_bad_spec_is_lint or factor_admit_independent_is_can_join'`
- Raw output: `ISSUE-23-green-window.log` (**9 passed**, 43 deselected).
- Final documentation path guard rerun: `platform/.venv/bin/python -m pytest -q platform/tests/test_doc_paths_exist.py`; raw output `ISSUE-23-doc-paths-final.log` (**11 passed**).
- Lockbox/admit integration is covered by the combined R43 rerun:
  **155 passed**, including inferred scale, cadence selection, frozen
  diagnostic metadata, and old-frozen migration.

The scale-focused tests use temporary specs and a temporary reference YAML, with
the final-test gate replaced by a deterministic stub. The lockbox suite separately
uses a real temporary SQLite ledger and final-test lane double to verify the
non-stubbed integration contract; no live ClickHouse run was invoked. The implementation
is in `7d3a2b9` and the lockbox-off cadence regressions are in `9d61058`.
