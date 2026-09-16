#!/bin/bash
# R05-C1 证据复现：极小平台库（dualbridge seed）上的真实 factorlab run
# 用法: bash c1-demo.sh   （输出即证据；依赖 platform/.venv 与 /tmp/opencode 可写）
set -u
STOCK=/data/students/gaolei/stock
PY=$STOCK/platform/.venv/bin/python
CLI=$STOCK/platform/.venv/bin/factorlab
WORK=/tmp/opencode/r05c1/demo
mkdir -p "$WORK"
cd "$WORK"
cp "$STOCK/platform/tests/test_run_factor.py" /dev/null 2>/dev/null || true

"$PY" "$STOCK/docs/verification/R23/safety/c1-setup-db.py"

echo "===== [1] 人为低阈值 clean abort: FACTORLAB_MAX_MEMORY=1KB ====="
FACTORLAB_PLATFORM_DB=$PWD/q.duckdb FACTORLAB_MAX_MEMORY=1KB \
  "$CLI" run spec.yaml --output-dir out_abort
echo "EXIT=$?"
echo "--- 产物检查: ls out_abort ---"
ls -la out_abort 2>&1 || true
echo "--- 产物检查: loader ---"
FACTORLAB_PLATFORM_DB=$PWD/q.duckdb "$PY" - <<'PYS'
import pathlib, sys
sys.path.insert(0, "/data/students/gaolei/stock/platform/src")
from factorlab.adapters.parquet_artifacts import load_signal_artifact
try:
    load_signal_artifact(pathlib.Path("out_abort"))
    print("BUG: loader accepted a partial artifact!")
except ValueError as e:
    print("LOADER REJECTED:", e)
PYS

echo "===== [2] 默认行为不变（未设 FACTORLAB_MAX_MEMORY） ====="
FACTORLAB_PLATFORM_DB=$PWD/q.duckdb \
  "$CLI" run spec.yaml --output-dir out_default
echo "EXIT=$?"

echo "===== [3] 显式充裕阈值 + RLIMIT_AS 硬上限: FACTORLAB_MAX_MEMORY=100GB ====="
FACTORLAB_PLATFORM_DB=$PWD/q.duckdb FACTORLAB_MAX_MEMORY=100GB \
  "$CLI" run spec.yaml --output-dir out_guarded
echo "EXIT=$?"

echo "===== [4] 两次成功 run 产物可加载（summary/signal rows） ====="
"$PY" - <<'PYS'
import json, pathlib, sys
sys.path.insert(0, "/data/students/gaolei/stock/platform/src")
from factorlab.adapters.parquet_artifacts import load_signal_artifact
for d in ("out_default", "out_guarded"):
    sig = load_signal_artifact(pathlib.Path(d))
    summary = json.loads((pathlib.Path(d) / "summary.json").read_text())
    print(f"{d}: signal_rows={sig.frame.height} panel_rows={summary['panel_rows']} "
          f"runtime_semantics={summary['runtime_semantics']}")
PYS
