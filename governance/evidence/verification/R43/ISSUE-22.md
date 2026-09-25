# Issue #22 — `flab factor resic` horizon and frequency selection

Date: 2026-09-25

## Problem

`flab factor resic` previously used weekly-aligned panels and
`forward_return_5d` unconditionally. Daily-evaluated minute factors therefore
could not be checked against their intended daily, 1-day forward label.

## Change

- Added `--frequency daily|weekly`. With no new options, behavior remains
  weekly + `forward_return_5d` for compatibility. Explicit `--frequency daily`
  keeps each date's cross-section, bypasses `align_weekly`, and defaults to
  `forward_return_1d`; explicit `--frequency weekly` uses
  `forward_return_5d`.
- Added `--horizon N`, mapping a positive integer to
  `forward_return_<N>d`. An explicit horizon overrides the frequency default.
- Added `--fwd-col`; when supplied, it takes precedence over `--horizon`.
- Added the parameters, defaults, help text, and examples to the research
  command registry, so CLI parsing and `describe` expose the same contract.
  The registry keeps `frequency=None` as its parser default so the facade can
  distinguish omitted legacy behavior from an explicitly selected frequency;
  `describe` and help explain the effective defaults.
- Responses identify the selected `frequency` and `fwd_col`. Daily responses
  include `n_periods` alongside the legacy `n_weeks` field.
- The same cadence resolver now feeds `factor admit` and `factor ref add`:
  daily specs use `forward_return_1d`, while weekly specs use their declared
  `target`. Final-test frozen diagnostics record `diagnostics_schema`,
  `frequency`, and `fwd_col`; older frozen files are recomputed from existing
  `_5y` panels without rerunning or re-registering the final test.
- Invalid frequency/horizon returns `USAGE`; a missing selected label column
  returns `DATA`. No statistical verdict thresholds changed.

The unparameterized weekly/5d path is an intentional legacy exception to D11's
normal factor-side 1d target. Selecting the new daily path defaults to 1d;
nonstandard horizons remain explicit.

In `--against` mode, forward-return-dependent statistics (`r2_lib`, resIC,
retention, and verdict inputs) use the selected frequency and label.
`corr_max`/`corr_mean` remain the existing weekly, signal-only correlation
measure; they do not consume the selected forward label.

## Verification

The regression tests first failed because the facade ignored the new arguments,
the explicit daily path still selected the legacy 5-day label, invalid options
were not rejected, and the registry did not describe the new flags:

- Mapping RED command: `platform/.venv/bin/python -m pytest -q platform/tests/test_research_factor.py -k 'resic_daily_horizon or resic_explicit_horizon or resic_against_reference_accepts_daily or factor_commands_registered_with_schemas or cli_research_factor_resic_daily_flags'`
- Raw RED output: `issue-22-mapping-red.log` (4 failed, 1 passed; expected failures show explicit daily still picked 5d and registry default could not distinguish omitted frequency).
- Focused GREEN command: `platform/.venv/bin/python -m pytest -q platform/tests/test_research_factor.py -k 'resic_daily_horizon or resic_explicit_horizon or resic_forward_col_overrides or resic_against_reference_accepts_daily or resic_rejects_invalid_horizon or resic_rejects_missing_forward_column or factor_commands_registered_with_schemas or cli_research_factor_resic_daily_flags or factor_resic_against_reference_incremental or factor_resic_mutual_mode'`
- Raw focused GREEN output: `issue-22-mapping-green.log` (**11 passed**, 35 deselected).
- Related compatibility suites: `platform/.venv/bin/python -m pytest -q platform/tests/test_cross_section.py platform/tests/test_cli_resic.py platform/tests/test_reference_library.py platform/tests/test_research_factor.py`
- Raw output: `issue-22-related-suite-final.log` (**92 passed**, 4 existing Polars pivot deprecation warnings).
- Cadence/lockbox integration rerun:
  `governance/ops/heavy.sh platform/.venv/bin/python -m pytest -q
  platform/tests/test_research_factor.py platform/tests/test_lockbox_admit.py
  platform/tests/test_cross_section.py platform/tests/test_reference_library.py
  platform/tests/test_doc_paths_exist.py` — **155 passed**.
- Registry/help example path guard: `platform/.venv/bin/python -m pytest -q platform/tests/test_doc_paths_exist.py`
- Raw output: `issue-22-doc-paths-final.log` (**11 passed**).

The API reads already-produced `results/<factor>/panel.parquet` files; this
change does not query ClickHouse or alter factor computation. The CLI and API
tests wrote and read real temporary parquet panels with multiple dates per week,
and the lockbox tests verify the final-test cadence and migration behavior with
temporary SQLite/parquet fixtures, so a live ClickHouse run was not required
for this issue. The original feature and its admit/ref-add integration are now
covered by commits `7d3a2b9` and `9d61058`; a reviewer should still confirm
the production reference-library policy before closing the finding.
