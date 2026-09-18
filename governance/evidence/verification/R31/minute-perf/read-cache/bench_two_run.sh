#!/usr/bin/env bash
# R31 读缓存两连跑（off/cold/warm ×2 轮）：同窗同因子（am_pm_vol，2024-01-02..
# 2024-03-29，chunk 10），读段墙钟 before(off)→首次(cold)→二次(warm)。
#
# 口径：与 bench.sh 相同 env（8GB 护栏、ch 后端、heavy.sh）；因在途 DQ 读取门
# 未覆盖历史窗（UNKNOWN），经 bench_driver.py 调 execute_run(dataset=None)
# ——性能口径不涉门（见驱动 docstring）。
#
# 用法（仓库根）：
#   bash governance/evidence/verification/R31/minute-perf/read-cache/bench_two_run.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [ ! -d "$ROOT/research/factor" ] && [ "$ROOT" != "/" ]; do ROOT="$(dirname "$ROOT")"; done
OUT="$ROOT/governance/evidence/verification/R31/minute-perf/read-cache"
SPEC="$OUT/am_pm_vol__2024-01-02_2024-03-29.yaml"
CACHE_DIR="${R31_CACHE_DIR:-/tmp/opencode/r31-read-cache-bench}"
PY="$ROOT/platform/.venv/bin/python"
HEAVY="$ROOT/governance/ops/heavy.sh"

export FACTORLAB_DATA_BACKEND=ch
export FACTORLAB_MAX_MEMORY=8GB
export FACTORLAB_MIN_AVAILABLE_MEMORY=6GB
export FACTORLAB_ST_DEGRADE=allow
export FACTORLAB_MINUTE_UNCOVERED=drop
export OMP_NUM_THREADS=8
export POLARS_MAX_THREADS=8

[ -f "$SPEC" ] || { echo "缺 spec: $SPEC（先跑一次 bench.sh 生成）" >&2; exit 2; }
[ "${R31_KEEP_CACHE:-0}" = "1" ] || rm -rf "$CACHE_DIR"   # cold-1 从空缓存开始（可复现）

run() {  # run <轮次> <state> <read_cache> [cache_dir]
    local round="$1" state="$2" rc="$3" cdir="${4:-}"
    local out="$OUT/bench-${state}-${round}"
    rm -rf "$out"; mkdir -p "$out"
    echo "== round$round $state (READ_CACHE=$rc cdir=${cdir:-none}) $(date -Iseconds)"
    FACTORLAB_READ_CACHE="$rc" FACTORLAB_READ_CACHE_DIR="${cdir:-$CACHE_DIR}" \
        /usr/bin/time -v -o "$out/time.txt" \
        timeout --signal=TERM --kill-after=60 1500 \
        "$HEAVY" "$PY" "$OUT/bench_driver.py" "$SPEC" "$out" \
        > "$out/run.log" 2>&1
    echo "rc=$? -> $out"
}

# 轮 1：off（基线）→ cold（空缓存首跑，写盘）→ warm（命中）
# 轮 2：off（基线复测）→ warm（同缓存命中；不再重复 cold）
run 1 off 0 "$CACHE_DIR"
run 1 cold 1 "$CACHE_DIR"
run 1 warm 1 "$CACHE_DIR"
run 2 off 0 "$CACHE_DIR"
run 2 warm 1 "$CACHE_DIR"
echo "done（摘要见各 bench-*/profile.json；缓存目录 $CACHE_DIR）"
