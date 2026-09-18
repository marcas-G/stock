#!/usr/bin/env bash
# R09 复评 F1 突变检验：3 处「门恒放行 / 估值恒定不回 chunk_days / 调用点传
# 常量块长」必须被 F1 新测试打死；每处突变后自动恢复（trap EXIT）。
# 输出即 fix1/mutation_f1.txt 的来源。
#
# 用法（仓库根）：bash governance/evidence/verification/R31/minute-perf/after-p4/fix1/mutation_f1.sh
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$DIR"
while [ ! -d "$ROOT/research/factor" ] && [ "$ROOT" != "/" ]; do
    ROOT="$(dirname "$ROOT")"
done
PY="$ROOT/platform/.venv/bin/python"
MEM="$ROOT/platform/src/factorlab/app/memory.py"
RUN="$ROOT/platform/src/factorlab/app/run.py"
BAK="$(mktemp -d)"
cp "$MEM" "$RUN" "$BAK/"
restore_files() { cp "$BAK/memory.py" "$MEM"; cp "$BAK/run.py" "$RUN"; }
cleanup() { restore_files; rm -rf "$BAK"; }
trap cleanup EXIT

run() {
    cd "$ROOT/platform" || exit 2
    .venv/bin/python -m pytest "$@" -q 2>&1 | tail -4
    cd "$ROOT" || exit 2
}

F1_TESTS=(
    "tests/test_memory_guard.py::test_guard_chunk_workers_default_20_n2_refused_under_8gb"
    "tests/test_memory_guard.py::test_guard_chunk_workers_chunk10_n2_allowed_under_8gb"
    "tests/test_memory_guard.py::test_guard_chunk_workers_small_chunk_keeps_calibration_floor"
    "tests/test_minute_engine.py::test_minute_chunk_workers_default_chunk20_n2_refused_under_8gb"
)

echo "===== M-F1a: 预算门恒放行（guard 无条件 return——复评 F1 原缺陷）"
"$PY" - "$MEM" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = "    if n_workers <= 1:\n        return\n"
new = "    if True:\n        return\n"
assert s.count(old) == 1
p.write_text(s.replace(old, new))
EOF
run "${F1_TESTS[@]}"
restore_files

echo "===== M-F1b: 估值恒定 3.6GB/chunk（忽略 chunk_days——原公式缺陷）"
"$PY" - "$MEM" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = "    per_chunk = minute_chunk_worker_peak_bytes(chunk_days)\n"
new = "    per_chunk = MINUTE_CHUNK_WORKER_PEAK_BYTES\n"
assert s.count(old) == 1
p.write_text(s.replace(old, new))
EOF
run "${F1_TESTS[@]}"
restore_files

echo "===== M-F1c: 调用点传常量块长 10（chunk_days 解析/透传漂移）"
"$PY" - "$RUN" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = "                               chunk_days=chunk_days)"
new = "                               chunk_days=10)"
assert s.count(old) == 1
p.write_text(s.replace(old, new))
EOF
run "${F1_TESTS[@]}"
restore_files

echo "===== 恢复后回归"
run tests/test_memory_guard.py tests/test_minute_fold.py "tests/test_minute_engine.py::test_minute_chunk_workers_default_chunk20_n2_refused_under_8gb" "tests/test_minute_engine.py::test_minute_chunk_workers_over_budget_refused_before_read"
