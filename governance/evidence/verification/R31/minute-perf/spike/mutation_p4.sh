#!/usr/bin/env bash
# R09-PERF-P4 突变检验：5 处「预算门存根 / 并行分支关 / 设置不注入 / 全局客户端
# 回退 / 估算常量清零」必须被测试打死；每处突变后自动恢复（trap EXIT）。
#
# 用法（仓库根）：bash governance/evidence/verification/R31/minute-perf/spike/mutation_p4.sh
# 输出即 spike/mutation-p4.txt 的来源。
set -uo pipefail

SPIKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SPIKE_DIR"
while [ ! -d "$ROOT/research/factor" ] && [ "$ROOT" != "/" ]; do
    ROOT="$(dirname "$ROOT")"
done
PY="$ROOT/platform/.venv/bin/python"
MEM="$ROOT/platform/src/factorlab/app/memory.py"
RUN="$ROOT/platform/src/factorlab/app/run.py"
INTRA="$ROOT/platform/src/factorlab/adapters/intraday.py"
CHREAD="$ROOT/platform/src/factorlab/adapters/ch_read.py"
BAK="$(mktemp -d)"
cp "$MEM" "$RUN" "$INTRA" "$CHREAD" "$BAK/"
restore_files() {
    cp "$BAK/memory.py" "$MEM"; cp "$BAK/run.py" "$RUN"
    cp "$BAK/intraday.py" "$INTRA"; cp "$BAK/ch_read.py" "$CHREAD"
}
cleanup() { restore_files; rm -rf "$BAK"; }
trap cleanup EXIT

run() {
    cd "$ROOT/platform" || exit 2
    .venv/bin/python -m pytest "$@" -q 2>&1 | tail -3
    cd "$ROOT" || exit 2
}

echo "===== M1: workers 预算门存根化（guard 恒 return）"
"$PY" - "$MEM" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = "    if n_workers <= 1:\n        return\n"
new = "    if True:\n        return\n"
assert s.count(old) == 1
p.write_text(s.replace(old, new))
EOF
run "tests/test_minute_engine.py::test_minute_chunk_workers_over_budget_refused_before_read"
restore_files

echo "===== M2: chunk 并行分支关闭（workers 恒走顺序路径）"
"$PY" - "$RUN" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = "            if chunk_workers <= 1:"
new = "            if True:"
assert s.count(old) == 1
p.write_text(s.replace(old, new))
EOF
run "tests/test_minute_engine.py::test_minute_chunk_workers_actually_overlap"
restore_files

echo "===== M3: bars 批读查询设置不注入（settings= 丢弃）"
"$PY" - "$INTRA" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = "        settings=bars_read_settings())"
new = "        )"
assert s.count(old) == 1
p.write_text(s.replace(old, new))
EOF
run "tests/test_ch_read_tuning.py::test_bars_read_settings_startup_on_batch_read_sql"
restore_files

echo "===== M4: CH 客户端回退进程级共享（线程级单例被破坏）"
"$PY" - "$CHREAD" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = '    client = getattr(_local, "client", None)'
new = '    client = globals().get("_mut_client", None)'
assert s.count(old) == 1
s = s.replace(old, new)
old2 = "        _local.client = client"
new2 = '        globals()["_mut_client"] = client'
assert s.count(old2) == 1
p.write_text(s.replace(old2, new2))
EOF
run "tests/test_ch_read_tuning.py::test_get_client_thread_local_and_concurrent_queries"
restore_files

echo "===== M5: 每 chunk 峰值估算常量清零（预算门失效）"
"$PY" - "$MEM" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = "MINUTE_CHUNK_WORKER_PEAK_BYTES = int(3.6 * 1024 ** 3)"
new = "MINUTE_CHUNK_WORKER_PEAK_BYTES = 0"
assert s.count(old) == 1
p.write_text(s.replace(old, new))
EOF
run "tests/test_minute_engine.py::test_minute_chunk_workers_over_budget_refused_before_read"
restore_files

echo "===== 恢复后回归"
run tests/test_minute_engine.py tests/test_ch_read_tuning.py
