#!/usr/bin/env bash
set -euo pipefail
set -x

STOCK_ROOT="${STOCK_ROOT:-$PWD}"
PLATFORM_TOOLS_ROOT="${PLATFORM_TOOLS_ROOT:-$STOCK_ROOT}"
PLATFORM_PY="${PLATFORM_PY:-$STOCK_ROOT/platform/.venv/bin/python}"

export PYTHONPATH="$STOCK_ROOT/research/tools:$PLATFORM_TOOLS_ROOT/platform/tools${PYTHONPATH:+:$PYTHONPATH}"
cd "$STOCK_ROOT"

"$STOCK_ROOT/governance/ops/heavy.sh" "$PLATFORM_PY" -m pytest -q research/tools/research_flows/tests research/tools/xscore/tests
"$STOCK_ROOT/governance/ops/heavy.sh" "$PLATFORM_PY" -m pytest -q platform/tests/test_doc_paths_exist.py
"$PLATFORM_PY" governance/ops/check_tool_layering.py
"$PLATFORM_PY" governance/ops/check_tool_layering.py --selftest
"$PLATFORM_PY" -m compileall -q research/tools/research_flows research/tools/xscore research/tools/lib/research_xscore_lockbox.py
git diff --check
git rev-parse HEAD
