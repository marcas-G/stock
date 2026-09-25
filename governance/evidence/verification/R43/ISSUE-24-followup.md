# R43 admission follow-up: frozen diagnostics, verdict consistency, and scale seeding

Date: 2026-09-26

## Changes

- Validate the complete final-test diagnostic payload before writing or using it:
  required identity/window/cadence fields, schema version, finite numeric metrics,
  and a non-negative period count. Booleans are not numeric metrics.
- If an existing frozen JSON file is malformed, refuse it with
  `LOCKBOX_FINAL_REQUIRED`. If its schema, fields, or values are stale or invalid,
  recompute diagnostics from the existing final panel without registering or
  rerunning the final test. Refuse the result if recomputation still produces
  invalid metrics.
- Preserve valid NumPy floating-point diagnostics during schema validation.
- Align D10 and `factor admit` classifications: correlation at least 0.95 is
  “重复”; the same redundancy conditions and 3.0 admission floor apply in both.
- Make `factor ref add` infer daily/minute scale from the resolved spec, matching
  `factor admit`; explicit `--scales` still wins. Allow an empty group to be
  initialized with a seed and expand an inline empty YAML list while preserving
  the rest of the file.

## Verification

- Focused admission/reference regression:
  `platform/.venv/bin/python -m pytest -q platform/tests/test_research_factor.py platform/tests/test_reference_library.py platform/tests/test_cross_section.py platform/tests/test_lockbox_admit.py`
  — **173 passed**, 4 existing Polars deprecation warnings.
  Raw output: [`ISSUE-24-followup-green.log`](ISSUE-24-followup-green.log).
- Documentation path guard:
  `platform/.venv/bin/python -m pytest -q platform/tests/test_doc_paths_exist.py`
  — **11 passed**.
  Raw output: [`ISSUE-24-followup-doc-paths.log`](ISSUE-24-followup-doc-paths.log).
- `git diff --check` — passed.

The full deep verify and five-year ClickHouse replay were not run because the
host's LLM server was active and the repository's R30 protocol prohibits
concurrent heavy runs. The five-year replay remains the outstanding acceptance
step for Issue #27.
