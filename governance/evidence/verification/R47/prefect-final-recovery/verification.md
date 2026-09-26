# Prefect final recovery verification

Date: 2026-09-26

## Test gate

Command:

```bash
PYTHONPATH=platform/tools:platform/src:research/tools \
  platform/.venv/bin/python -m pytest -q \
  platform/tests/test_lockbox_guard.py \
  research/tools/research_flows/tests \
  research/tools/xscore/tests/test_manifest.py \
  --disable-warnings
```

Result: **129 passed, 1 warning in 32.82s**. The warning is Python's
`multiprocessing` fork deprecation emitted by the cross-process attempt-lock
test. Raw output is in `pytest.txt`.

The suite includes interruption/retry tests for factor computation and
lockbox backfill, strategy computation and post-publication backfill, xscore
before/after prepared output, CompositeArtifact publication recovery, mode
environment propagation, final single-candidate validation, and access ID
provenance across multiple factors.

## Static gates

Command:

```bash
platform/.venv/bin/python -m compileall -q \
  platform/src/factorlab/adapters/lockbox_store.py \
  research/tools/lib/xscore_lockbox.py \
  research/tools/research_flows
```

Result: exit 0.

Command:

```bash
git diff --check
```

Result: exit 0.

No production final run or live lockbox access was performed.
