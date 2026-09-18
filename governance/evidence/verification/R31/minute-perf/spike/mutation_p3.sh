#!/usr/bin/env bash
# R09-PERF-I2 突变检验（P3）：至少 3 处「存根/错值/关优化」突变必须被测试打死。
#
# 用法（仓库根）：bash governance/evidence/verification/R31/minute-perf/spike/mutation_p3.sh
# 输出即 spike/mutation-p3.txt 的来源；每处突变后自动恢复（trap EXIT）。
set -uo pipefail

SPIKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SPIKE_DIR"
while [ ! -d "$ROOT/research/factor" ] && [ "$ROOT" != "/" ]; do
    ROOT="$(dirname "$ROOT")"
done
PY="$ROOT/platform/.venv/bin/python"
OPS="$ROOT/platform/src/factorlab/core/ops/minute_ops.py"
FOLD="$ROOT/platform/src/factorlab/core/engine/minute_fold.py"
GATE="$ROOT/platform/src/factorlab/core/engine/minute_gate.py"
BAK="$(mktemp -d)"
cp "$OPS" "$FOLD" "$GATE" "$BAK/"
restore_files() { cp "$BAK/minute_ops.py" "$OPS"; cp "$BAK/minute_fold.py" "$FOLD"; cp "$BAK/minute_gate.py" "$GATE"; }
cleanup() { restore_files; rm -rf "$BAK"; }
trap cleanup EXIT

run() {
    cd "$ROOT/platform" || exit 2
    .venv/bin/python -m pytest "$@" -q 2>&1 | tail -4
    cd "$ROOT" || exit 2
}

echo "===== M1: at_minute 错值（filter(max) -> filter(first)）"
"$PY" - "$OPS" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = "    return x.filter(pl.col(_ORDER) == kk).max().over(_PARTITION)"
new = "    return x.filter(pl.col(_ORDER) == kk).first().over(_PARTITION)"
assert s.count(old) == 1
p.write_text(s.replace(old, new))
EOF
run tests/test_minute_ops.py tests/test_minute_fold.py
restore_files

echo "===== M2: 条件取值改为旧 when/None 全列物化（关优化，数值不变）"
"$PY" - "$FOLD" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = """        if node.op in ("day_max", _AT_MINUTE):
            return (col.filter(cond).max().over(partition)
                    .alias(node.result_temp))
        if node.op == "day_min":
            return (col.filter(cond).min().over(partition)
                    .alias(node.result_temp))"""
new = """        masked = pl.when(cond).then(col).otherwise(None)
        if node.op in ("day_max", _AT_MINUTE):
            return masked.max().over(partition).alias(node.result_temp)
        if node.op == "day_min":
            return masked.min().over(partition).alias(node.result_temp)"""
assert s.count(old) == 1
p.write_text(s.replace(old, new))
EOF
run tests/test_minute_fold.py
restore_files

echo "===== M3: 条件取值识别存根化（_day_node 不再外提 if_else 条件）"
"$PY" - "$FOLD" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = """    if op in _FILTER_OPS:
        extracted = _extract_cond(arg, aliases)
        if extracted is not None:
            x, cond = extracted
            return FoldNode(op, x, cond=cond,
                            cond_pl=_simple_cond(cond, consts))
    return FoldNode(op, arg)"""
new = """    return FoldNode(op, arg)"""
assert s.count(old) == 1
p.write_text(s.replace(old, new))
EOF
run tests/test_minute_fold.py
restore_files

echo "===== M4: day_first/day_last 回退双 over 旧形（关单次聚合优化）"
"$PY" - "$FOLD" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = """    if node.op == "day_last":
        return (col.sort_by(_ORDER).last().over(partition)
                .alias(node.result_temp))
    if node.op == "day_first":
        return (col.sort_by(_ORDER).first().over(partition)
                .alias(node.result_temp))"""
new = """    if node.op == "day_last":
        mmax = pl.col(_ORDER).max().over(partition)
        return (pl.when(pl.col(_ORDER) == mmax).then(col).otherwise(None)
                .max().over(partition).alias(node.result_temp))
    if node.op == "day_first":
        mmin = pl.col(_ORDER).min().over(partition)
        return (pl.when(pl.col(_ORDER) == mmin).then(col).otherwise(None)
                .min().over(partition).alias(node.result_temp))"""
assert s.count(old) == 1
p.write_text(s.replace(old, new))
EOF
run tests/test_minute_fold.py
restore_files

echo "===== M5: at_minute 静态范围门存根化（去 0..239 边界拒绝）"
"$PY" - "$GATE" <<'EOF'
import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
old = """            if k < 0 or k > _GRID_MAX_INDEX:
                raise ValueError(
                    f"at_minute k 必须在 0..239（{node.lineno}:"
                    f"{node.col_offset}，收到 {k}——越界；当日网格 240 行）")
"""
assert s.count(old) == 1
p.write_text(s.replace(old, ""))
EOF
run tests/test_minute_gate.py tests/test_minute_ops.py
restore_files

echo "===== 恢复后回归"
run tests/test_minute_ops.py tests/test_minute_gate.py tests/test_minute_fold.py
