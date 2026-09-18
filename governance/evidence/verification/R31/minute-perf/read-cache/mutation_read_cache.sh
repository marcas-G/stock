#!/usr/bin/env bash
# R31 读缓存突变检验：6 处变异全部应被测试杀死（>=4 杀为必达，实做 6）。
# 就地改源文件（备份/还原），逐突变跑定向测试；任一存活即记录 SURVIVED。
#
# 用法（仓库根）：bash governance/evidence/verification/R31/minute-perf/read-cache/mutation_read_cache.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [ ! -d "$ROOT/research/factor" ] && [ "$ROOT" != "/" ]; do ROOT="$(dirname "$ROOT")"; done
PLATFORM="$ROOT/platform"
PY="$PLATFORM/.venv/bin/python"
CC="$PLATFORM/src/factorlab/adapters/read/chunk_cache.py"
BK="$CC.bak.$$"
cp "$CC" "$BK"
trap 'cp "$BK" "$CC"; rm -f "$BK"' EXIT

mutate() {  # mutate <name> <old> <new>
    "$PY" - "$CC" "$2" "$3" <<'PYEOF'
import sys
from pathlib import Path
path, old, new = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text(encoding="utf-8")
if old not in text:
    print(f"MUTATE-FAIL: 未找到替换锚点: {old[:60]!r}")
    sys.exit(3)
path.write_text(text.replace(old, new, 1), encoding="utf-8")
PYEOF
}

run_case() {  # run_case <name> <tests...>
    local name="$1"; shift
    echo "---- [$name] pytest $*"
    if (cd "$PLATFORM" && "$PY" -m pytest "$@" -q --no-header -x) > /tmp/opencode/mut.out 2>&1; then
        echo "[$name] SURVIVED（测试仍绿——突变未被杀）"
        tail -3 /tmp/opencode/mut.out | sed 's/^/    /'
    else
        echo "[$name] KILLED"
        grep -E "FAILED|ERROR" /tmp/opencode/mut.out | head -3 | sed 's/^/    /'
    fi
}

echo "== R31 读缓存突变（基线 6 处；期望全部 KILLED） =="
t0=$(date -Iseconds); echo "start=$t0 commit=$(git -C "$ROOT" rev-parse HEAD)"

# M1 指纹恒相等
mutate "M1" "$(cat <<'EOF'
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
EOF
)" "    fingerprint = 'deadbeef'" && \
run_case "M1-fingerprint-const" tests/test_read_cache.py::test_fingerprint_sensitive_to_parts_rows_mtime_and_maxdt tests/test_read_cache.py::test_codes_ch_fingerprint_change_invalidates
cp "$BK" "$CC"

# M2 键忽略指纹
mutate "M2" '        ",".join(sorted(set(columns))),
        fingerprint,
    ))' '        ",".join(sorted(set(columns))),
    ))' && \
run_case "M2-key-ignores-fingerprint" tests/test_read_cache.py::test_key_stable_and_sensitive_to_every_input tests/test_read_cache.py::test_codes_ch_fingerprint_change_invalidates
cp "$BK" "$CC"

# M3 缓存恒命中（probe 忽略 key，任意条目即返回）
mutate "M3" '            entry = manifest["entries"].get(key)
            if entry is None:
                return None, "miss", "no_entry"' '            entry = (next(iter(manifest["entries"].values()), None))
            if entry is None:
                return None, "miss", "no_entry"' && \
run_case "M3-always-hit" tests/test_read_cache.py::test_load_unknown_key_is_miss tests/test_read_cache.py::test_codes_ch_fingerprint_change_invalidates
cp "$BK" "$CC"

# M4 损坏不回退（校验失败直接抛）
mutate "M4" '        except Exception as exc:  # noqa: BLE001 —— 任何坏条目都回退直读
            self._drop(key, reason=str(exc))
            return None, f"{type(exc).__name__}: {exc}"' '        except Exception as exc:  # noqa: BLE001
            raise' && \
run_case "M4-corrupt-raises" tests/test_read_cache.py::test_corrupted_file_falls_back_and_drops_entry tests/test_read_cache.py::test_codes_ch_corrupt_falls_back_direct_and_recovers
cp "$BK" "$CC"

# M5 开关恒开（忽略 enabled=False）
mutate "M5" '    if enabled is False or (enabled is None and not cfg.enabled):
        return None' '    if False:
        return None' && \
run_case "M5-switch-ignored" tests/test_read_cache.py::test_switch_off_returns_none_and_creates_nothing tests/test_read_cache.py::test_codes_ch_cache_off_by_flag_and_env
cp "$BK" "$CC"

# M6 LRU -> FIFO（淘汰按 created_at 而不是 last_access）
mutate "M6" '            victim = min(entries, key=lambda k: float(entries[k]["last_access"]))' '            victim = min(entries, key=lambda k: float(entries[k]["created_at"]))' && \
run_case "M6-lru-to-fifo" tests/test_read_cache.py::test_lru_eviction_keeps_recently_accessed
cp "$BK" "$CC"

echo "== 还原后复跑全量 read_cache =="
(cd "$PLATFORM" && "$PY" -m pytest tests/test_read_cache.py tests/test_ch_arrow_stream.py -q --no-header) 2>&1 | tail -2
echo "end=$(date -Iseconds)"
