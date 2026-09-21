#!/usr/bin/env bash
# test_markers_equivalence.sh —— Task 1 硬验收：pytest markers 排除集 ≡ 旧硬编码 --deselect。
#
# 规格：knowledge/design/platform/specs/2026-09-21-cicd-streamlining-addendum.md §3/§4
# 计划：knowledge/design/platform/plans/2026-09-21-cicd-streamlining.md Task 1
#
# 断言（fast 表达式冻结）：
#   1) 旧 deselect 集里的每个 nodeid 都不出现在 fast marker 收集结果里；
#   2) 每腿 marker 表达式收集行数 == 旧 --deselect 收集行数（防排除扩大/缩小）。
# 附核 deep 表达式：data_on_disk/host_root 用例被收集，tick_paused/inflight/known_red 仍排除。
# 口径：platform 侧 cwd=platform；tools/research 侧 cwd=仓根（与旧 CI 命令一致；
#       platform/tests 与 platform/tools 不能同次收集——test_cli.py 基名冲突）。
# 退出码：0 全等价；1 有 mismatch。
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
PY="$ROOT/platform/.venv/bin/python"
FAST="not needs_ch and not data_on_disk and not host_root and not tick_paused and not inflight and not known_red"
DEEP="not tick_paused and not inflight and not known_red"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

FAIL=0
pass() { echo "  ✓ $*"; }
fail() { echo "  ✗ $*"; FAIL=1; }

# collect <cwd> <tag> [pytest args...] → $WORK/<tag>.txt + <tag>.nodes（nodeid 行）+ <tag>.count
collect() {
  local cwd=$1 tag=$2; shift 2
  ( cd "$cwd" && "$PY" -m pytest -q --collect-only -p no:cacheprovider "$@" ) \
    >"$WORK/$tag.txt" 2>&1
  grep -E '^[^ ]+::' "$WORK/$tag.txt" | LC_ALL=C sort >"$WORK/$tag.nodes" || true
  wc -l <"$WORK/$tag.nodes" | tr -d ' ' >"$WORK/$tag.count"
}

count() { cat "$WORK/$1.count"; }
has() { grep -qF "$2" "$WORK/$1.nodes"; }

check_absent() { # <leg> <tag> <node 子串>
  if has "$2" "$3"; then fail "[$1] $3 仍出现在 $2 收集结果中"; else pass "[$1] $3 已被排除"; fi
}
check_present() { # <leg> <tag> <node 子串>
  if has "$2" "$3"; then pass "[$1] $3 被收集（deep）"; else fail "[$1] $3 未被收集（应为 deep 跑）"; fi
}
check_equiv() { # <leg> <old_tag> <new_tag>
  local co cn
  co=$(count "$2"); cn=$(count "$3")
  if [ "$co" = "$cn" ]; then
    pass "[$1] 收集行数一致：old(--deselect)=$co  new(markers)=$cn"
  else
    fail "[$1] 收集行数不一致：old(--deselect)=$co  new(markers)=$cn（排除扩大/缩小？）"
  fi
}

echo "== markers 等价性（fast 表达式冻结）: $FAST =="
echo

# ── 腿 1：platform/tests（cwd=platform，nodeid 相对 platform/）───────────────
echo "[P1] platform/tests（cwd=platform）"
collect "$ROOT/platform" p1-base tests
collect "$ROOT/platform" p1-old tests --deselect tests/test_factio.py::test_paths_roots_exist_or_skip
collect "$ROOT/platform" p1-new tests -m "$FAST"
collect "$ROOT/platform" p1-deep tests -m "$DEEP"
check_present "P1/base" p1-base "tests/test_factio.py::test_paths_roots_exist_or_skip"
check_absent "P1/old" p1-old "tests/test_factio.py::test_paths_roots_exist_or_skip"
check_absent "P1/new" p1-new "tests/test_factio.py::test_paths_roots_exist_or_skip"
check_equiv "P1" p1-old p1-new
check_present "P1/deep" p1-deep "tests/test_factio.py::test_paths_roots_exist_or_skip"
printf '  · pytest 摘要：%s\n' "$(grep -E 'tests? collected' "$WORK/p1-new.txt" | tail -1)"
echo

# ── 腿 2：platform/tools（cwd=仓根；rootdir=platform，nodeid tools/...）───────
echo "[P2] platform/tools（cwd=仓根）"
P2_DESELECT=(
  --deselect tools/lob_fact/tests/test_config_paths.py::test_root_exists_on_disk
  --deselect tools/ch_ingest/tests/test_ch_data_semantics.py::test_reconcile_daily_covers_derived_tables
  --deselect tools/pan_update/tests/test_config.py::test_pan_update_and_ingest_share_data_roots
  --deselect tools/lob_fact/tests/test_run_lob_batch.py::test_main_cli_e2e_mini_month
)
collect "$ROOT" p2-base platform/tools
collect "$ROOT" p2-old platform/tools "${P2_DESELECT[@]}"
collect "$ROOT" p2-new platform/tools -m "$FAST"
collect "$ROOT" p2-deep platform/tools -m "$DEEP"
P2_NODES=(
  "tools/lob_fact/tests/test_config_paths.py::test_root_exists_on_disk"
  "tools/ch_ingest/tests/test_ch_data_semantics.py::test_reconcile_daily_covers_derived_tables"
  "tools/pan_update/tests/test_config.py::test_pan_update_and_ingest_share_data_roots"
  "tools/lob_fact/tests/test_run_lob_batch.py::test_main_cli_e2e_mini_month"
)
for n in "${P2_NODES[@]}"; do
  check_present "P2/base" p2-base "$n"
  check_absent "P2/old" p2-old "$n"
  check_absent "P2/new" p2-new "$n"
done
check_equiv "P2" p2-old p2-new
check_present "P2/deep" p2-deep "tools/lob_fact/tests/test_config_paths.py::test_root_exists_on_disk"
check_present "P2/deep" p2-deep "tools/ch_ingest/tests/test_ch_data_semantics.py::test_reconcile_daily_covers_derived_tables"
check_present "P2/deep" p2-deep "tools/pan_update/tests/test_config.py::test_pan_update_and_ingest_share_data_roots"
check_absent "P2/deep" p2-deep "tools/lob_fact/tests/test_run_lob_batch.py::test_main_cli_e2e_mini_month"
printf '  · pytest 摘要：%s\n' "$(grep -E 'tests? collected' "$WORK/p2-new.txt" | tail -1)"
echo

# ── 腿 3：research/tools（cwd=仓根；rootdir=research，nodeid tools/...）───────
echo "[R] research/tools（cwd=仓根）"
R_DESELECT=(
  --deselect tools/factor_lib/tests/test_index.py::test_every_yaml_has_mirror_doc_and_name_matches
  --deselect tools/strategies/tests/test_run_strategy_cli.py::test_real_run_clean_window_2025_03
  --deselect tools/strategies/tests/test_run_strategy_cli.py::test_real_run_max_hold_excludes_stale_and_renormalizes
)
collect "$ROOT" r-base research/tools
collect "$ROOT" r-old research/tools "${R_DESELECT[@]}"
collect "$ROOT" r-new research/tools -m "$FAST"
collect "$ROOT" r-deep research/tools -m "$DEEP"
R_NODES=(
  "tools/factor_lib/tests/test_index.py::test_every_yaml_has_mirror_doc_and_name_matches"
  "tools/strategies/tests/test_run_strategy_cli.py::test_real_run_clean_window_2025_03"
  "tools/strategies/tests/test_run_strategy_cli.py::test_real_run_max_hold_excludes_stale_and_renormalizes"
)
for n in "${R_NODES[@]}"; do
  check_present "R/base" r-base "$n"
  check_absent "R/old" r-old "$n"
  check_absent "R/new" r-new "$n"
  check_absent "R/deep" r-deep "$n"
done
check_equiv "R" r-old r-new
printf '  · pytest 摘要：%s\n' "$(grep -E 'tests? collected' "$WORK/r-new.txt" | tail -1)"
echo

if [ "$FAIL" = "0" ]; then
  echo "markers 等价性：全绿（fast 排除集 = 旧 --deselect 集；deep 按 marker 语义放行/排除）"
else
  echo "markers 等价性：有 mismatch（见上）"
fi
exit "$FAIL"
