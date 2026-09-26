#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../../../.." && pwd)"
EVIDENCE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PY="$ROOT/platform/.venv/bin/python"
export FACTORLAB_STOCK_ROOT="$ROOT"
export QUANTRESEARCH_ROOT="${QUANTRESEARCH_ROOT:-$ROOT/.ci-no-product-area}"
export PYTHONPATH="$ROOT/platform/src:$ROOT/research/tools${PYTHONPATH:+:$PYTHONPATH}"

{
  echo '$ env -u FACTORLAB_PIPELINE platform/.venv/bin/python -m pytest -q platform/tests/test_architecture.py::test_tools_declare_env_single_point research/tools/research_flows research/tools/xscore/tests/test_ref_sync.py research/tools/xscore/tests/test_manifest.py'
  env -u FACTORLAB_PIPELINE "$PY" -m pytest -q \
    platform/tests/test_architecture.py::test_tools_declare_env_single_point \
    research/tools/research_flows \
    research/tools/xscore/tests/test_ref_sync.py \
    research/tools/xscore/tests/test_manifest.py
} > "$EVIDENCE_DIR/ci-equivalent-tests.log" 2>&1

{
  echo '$ env -u FACTORLAB_PIPELINE platform/.venv/bin/python -m py_compile research/tools/research_flows/xscore_flow.py research/tools/research_flows/tests/test_xscore_flow.py research/tools/research_flows/tests/test_strategy_execution.py research/tools/xscore/pipeline/data_prep.py research/tools/xscore/pipeline/xlib.py research/tools/xscore/tests/test_ref_sync.py'
  env -u FACTORLAB_PIPELINE "$PY" -m py_compile \
    research/tools/research_flows/xscore_flow.py \
    research/tools/research_flows/tests/test_xscore_flow.py \
    research/tools/research_flows/tests/test_strategy_execution.py \
    research/tools/xscore/pipeline/data_prep.py \
    research/tools/xscore/pipeline/xlib.py \
    research/tools/xscore/tests/test_ref_sync.py
  echo '$ git diff --check'
  git diff --check
  echo 'static checks passed'
} > "$EVIDENCE_DIR/static-checks.log" 2>&1
