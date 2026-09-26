#!/usr/bin/env bash
# 运行 xscore 流水线（接入本地 Prefect server UI）。
# 用法: run.sh configs/m0-split.yaml [max_workers]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
export PYTHONPATH="$ROOT/research/tools:$ROOT/platform/tools${PYTHONPATH:+:$PYTHONPATH}"
export PREFECT_API_URL="${PREFECT_API_URL:-http://127.0.0.1:4200/api}"
SVC_ENV="${FACTORLAB_SVC_ENV_FILE:-$HOME/.config/factorlab/service.env}"
if [ -f "$SVC_ENV" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$SVC_ENV"
  set +a
fi
CFG="${1:?usage: run.sh <config.yaml> [max_workers]}"
WORKERS="${2:-2}"
exec "$HERE/../../../.venv/bin/python" "$HERE/flows.py" --config "$CFG" --max-workers "$WORKERS"
