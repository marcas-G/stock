#!/usr/bin/env bash
# R36-CI-I1 复现：POLARS_MAX_THREADS=8 → backtest cash bridge（精确相等）失败
# 证据链：A) 无该 env 通过；B) 有该 env（heavy.sh 默认注入值）失败；
#         C) 同 B 插桩打印精确差值 → 量级为浮点求和顺序噪声。
set -euo pipefail
PROBE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "$PROBE_DIR" rev-parse --show-toplevel)"
cd "$ROOT/platform"
TEST='tests/test_run_strategy.py::test_run_strategy_records_data_quality_five_fields_in_manifest[ch]'

echo '## A) 干净环境（无 POLARS_MAX_THREADS）'
env -u POLARS_MAX_THREADS .venv/bin/python -m pytest "$TEST" -q 2>&1 | tail -2 || true

echo
echo '## B) POLARS_MAX_THREADS=8（= heavy.sh 默认注入）'
POLARS_MAX_THREADS=8 .venv/bin/python -m pytest "$TEST" -q 2>&1 | tail -4 || true

echo
echo '## C) 同 B，插桩打印 cash bridge 精确差值（-p probe_diff）'
POLARS_MAX_THREADS=8 PYTHONPATH="$PROBE_DIR" \
  .venv/bin/python -m pytest -p probe_diff "$TEST" -q -s 2>&1 \
  | grep -E 'PROBE|failed|passed' | head -6 || true
