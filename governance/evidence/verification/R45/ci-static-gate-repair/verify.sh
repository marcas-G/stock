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
  echo '$ env -u FACTORLAB_PIPELINE platform/.venv/bin/python -m pytest -q research/tools/research_flows/tests research/tools/xscore/tests/test_manifest.py'
  env -u FACTORLAB_PIPELINE "$PY" -m pytest -q \
    research/tools/research_flows/tests \
    research/tools/xscore/tests/test_manifest.py
} > "$EVIDENCE_DIR/tests.log" 2>&1

{
  echo '$ platform/.venv/bin/python governance/ops/check_tool_layering.py'
  "$PY" governance/ops/check_tool_layering.py
  echo '$ platform/.venv/bin/python governance/ops/check_dataiface.py'
  "$PY" governance/ops/check_dataiface.py
  echo '$ env -u PYTHONPATH platform/.venv/bin/python research/tools/xscore/pipeline/score_once.py --help'
  env -u PYTHONPATH "$PY" research/tools/xscore/pipeline/score_once.py --help
  echo '$ platform/.venv/bin/python -m py_compile ...'
  "$PY" -m py_compile \
    research/tools/research_flows/flow_contracts.py \
    research/tools/research_flows/factor_mining.py \
    research/tools/research_flows/xscore_flow.py \
    research/tools/research_flows/strategy_execution.py \
    research/tools/xscore/pipeline/xlib.py \
    research/tools/lib/xscore_lockbox.py
  echo '$ git diff --check'
  git diff --check
  echo 'static checks passed'
} > "$EVIDENCE_DIR/static-checks.log" 2>&1
