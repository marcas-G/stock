#!/usr/bin/env bash
# 运行 xscore 流水线（接入本地 Prefect server UI）。
# 用法: run.sh configs/m0-split.yaml [max_workers]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export PREFECT_API_URL="${PREFECT_API_URL:-http://127.0.0.1:4200/api}"
CFG="${1:?usage: run.sh <config.yaml> [max_workers]}"
WORKERS="${2:-2}"
exec "$HERE/../../../.venv/bin/python" "$HERE/flows.py" --config "$CFG" --max-workers "$WORKERS"
