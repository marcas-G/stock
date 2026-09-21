#!/usr/bin/env bash
# verify.sh —— stock 唯一验证入口（fast/deep 双档位，命令真相源）。
#
# 用法：bash governance/ops/verify.sh --profile fast|deep [--root DIR]
#   fast：云端等价（GitHub-hosted：无 CH / 无研究产物区 / checkout 即 STOCK_ROOT）
#         gates.sh --offline → ci_env.sh（钉版唯一出处）→ 三组 pytest（fast 表达式，逐组不早退）
#   deep：宿主树（有 CH + 在盘数据 + 研究产物区）
#         fast 门部分 → 完整 make gates → 平台全量/CH 集成/tools/research（deep 表达式）→ 研究产物门
# 退出码：0=全过；1=有失败；2=环境不满足（deep 前置检查 CH 127.0.0.1:8123 不通等）
#
# 纪律：
#   - 不注入线程/内存 env（R36 教训：POLARS_MAX_THREADS 改浮点求和顺序，撞精确校验）；
#     本脚本只设置测试环境语义（后端/端口/根路径/渲染宽度）。
#   - 失败不早退：逐 step 打印 `[verify] step=<name> rc=<n>`，末尾汇总后统一退出。
#   - marker 表达式冻结（规格 §4）：
#       fast: not needs_ch and not data_on_disk and not host_root and not tick_paused and not inflight and not known_red
#       deep: not tick_paused and not inflight and not known_red
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

PROFILE=""
ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE=${2:-}; shift 2 ;;
    --root) ROOT=${2:-}; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "[verify] 未知参数：$1" >&2; exit 2 ;;
  esac
done
case "$PROFILE" in
  fast|deep) ;;
  *) echo "[verify] 用法：bash governance/ops/verify.sh --profile fast|deep [--root DIR]" >&2; exit 2 ;;
esac
ROOT=${ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}
ROOT=$(cd "$ROOT" 2>/dev/null && pwd) || { echo "[verify] 根目录不存在：$ROOT" >&2; exit 2; }
[ -d "$ROOT/.git" ] || { echo "[verify] 非 git 仓库根（缺 .git）：$ROOT" >&2; exit 2; }
[ -d "$ROOT/platform" ] || { echo "[verify] 非 stock 仓根（缺 platform/）：$ROOT" >&2; exit 2; }

FAST_EXPR="not needs_ch and not data_on_disk and not host_root and not tick_paused and not inflight and not known_red"
DEEP_EXPR="not tick_paused and not inflight and not known_red"
VENV_PY="$ROOT/platform/.venv/bin/python"

# 无 TTY 时 click/rich 在导入期按默认宽度（80）渲染，拆断 --help 文本断言（旧 CI 口径）。
export COLUMNS=${COLUMNS:-200}

if [ "$PROFILE" = fast ]; then
  # 云端等价语义（旧 ci.yml env；本地同样成立——fast 结果应与云端一致）。
  export FACTORLAB_DATA_BACKEND=${FACTORLAB_DATA_BACKEND:-duckdb}
  export FACTORLAB_CH_HOST=${FACTORLAB_CH_HOST:-127.0.0.1}
  export FACTORLAB_CH_PORT=${FACTORLAB_CH_PORT:-1}
  export FACTORLAB_STOCK_ROOT=${FACTORLAB_STOCK_ROOT:-$ROOT}
  export LOB_BATCH_LOW_WATER_KB=${LOB_BATCH_LOW_WATER_KB:-1048576}
  export QUANTRESEARCH_ROOT=${QUANTRESEARCH_ROOT:-$ROOT/.ci-no-product-area}
  TIMEOUT=180
  EXPR="$FAST_EXPR"
  echo "[verify] fast 云端等价环境：CH=${FACTORLAB_CH_HOST}:${FACTORLAB_CH_PORT}（不可达）、QUANTRESEARCH_ROOT=${QUANTRESEARCH_ROOT}、STOCK_ROOT=${FACTORLAB_STOCK_ROOT}"
else
  CH_URL="http://${FACTORLAB_CH_HOST:-127.0.0.1}:${FACTORLAB_CH_PORT:-8123}/ping"
  ping=$(curl -fsS --max-time 5 "$CH_URL" 2>/dev/null || true)
  if [ "$ping" != "Ok." ]; then
    echo "[verify] deep 环境不满足：ClickHouse $CH_URL 不通（ping='$ping'）。" >&2
    echo "[verify] 指引：启动 ClickHouse（127.0.0.1:8123，db=factorlab）后重试；" >&2
    echo "[verify]       只跑云端档位用 --profile fast；宿主深检亦可由夜间 timer 触发（factorlab-nightly-verify）。" >&2
    exit 2
  fi
  echo "[verify] deep 前置检查：ClickHouse $CH_URL → $ping"
  TIMEOUT=900
  EXPR="$DEEP_EXPR"
fi

STEP_NAMES=()
STEP_RCS=()
step() {  # $1=step 名，余下=命令；记录 rc 不早退
  local name=$1; shift
  echo ""
  echo "---- [verify] step=$name ----"
  "$@"
  local rc=$?
  STEP_NAMES+=("$name")
  STEP_RCS+=("$rc")
  echo "[verify] step=$name rc=$rc"
}

# ── 步骤（cwd/env 各自封装；门与测试均按冻结表达式）────────────────
gates_offline() { ( cd "$ROOT" && bash governance/ops/gates.sh --offline ); }
gates_full() { ( cd "$ROOT" && make gates ); }
ci_env_step() { bash "$ROOT/governance/ops/ci_env.sh" --dir "$ROOT"; }

pytest_platform() { ( cd "$ROOT/platform" && "$VENV_PY" -m pytest -q --timeout=$TIMEOUT --timeout-method=thread -m "$EXPR" ); }
pytest_tools() { ( cd "$ROOT" && "$VENV_PY" -m pytest platform/tools -q --timeout=$TIMEOUT --timeout-method=thread -m "$EXPR" ); }
pytest_research() { ( cd "$ROOT" && "$VENV_PY" -m pytest research/tools -q --timeout=$TIMEOUT --timeout-method=thread -m "$EXPR" ); }
pytest_gov() { ( cd "$ROOT" && "$VENV_PY" -m pytest governance/ops -q --timeout=300 --timeout-method=thread -m "$EXPR" ); }

ch_integration() {
  ( cd "$ROOT/platform" \
    && FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow FACTORLAB_MINUTE_UNCOVERED=drop \
       "$VENV_PY" -m pytest -q -m integration --timeout=900 --timeout-method=thread )
}

# 研究产物门（deep；按 Makefile 现有目标名调用）
product_lint() { ( cd "$ROOT" && make lint-factors ); }
product_index_check() { ( cd "$ROOT" && make index-check ); }
product_index_regen_check() { ( cd "$ROOT" && make index && make index-check ); }
product_annotate() { ( cd "$ROOT" && "$VENV_PY" governance/evidence/verification/R21/EVID/annotate_factor_archives.py --check ); }
# research_tidy 为**报告口径**（对齐 selfhosted-verify 既有政策）：产物区目录公约
# 有 R37 预存遗留（裸文件/缺 manifest/scratch 命名），非代码回归；非零只 WARN 不阻塞，
# 整改后升级硬门。注意 WARN 行不得出现 `rc=` 字样（nightly_notify 会把它当失败步骤）。
product_tidy() {
  local out inner
  out=$( cd "$ROOT" && "$VENV_PY" governance/ops/research_tidy.py 2>&1 )
  inner=$?
  printf '%s\n' "$out"
  if [ "$inner" != "0" ]; then
    echo "[verify] WARN product-tidy 退出码 $inner（R37 预存产物区公约遗留，报告口径不阻塞；整改后升硬门）"
  fi
  return 0
}

if [ "$PROFILE" = fast ]; then
  step gates-offline gates_offline
  step ci-env ci_env_step
  step pytest-platform pytest_platform
  step pytest-platform-tools pytest_tools
  step pytest-research-tools pytest_research
  step pytest-governance-ops pytest_gov
else
  # deep：先 fast 门部分；再全量门/CH/带数据测试/研究产物门（失败不早退）。
  step gates-offline gates_offline
  step ci-env ci_env_step
  step gates-full gates_full
  step pytest-platform pytest_platform
  step ch-integration ch_integration
  step pytest-platform-tools pytest_tools
  step pytest-research-tools pytest_research
  step pytest-governance-ops pytest_gov
  step product-lint product_lint
  step product-index-check product_index_check
  step product-index-regen-check product_index_regen_check
  step product-tidy product_tidy
  step product-annotate-snapshot product_annotate
fi

echo ""
echo "== [verify] 汇总（profile=$PROFILE root=$ROOT）=="
WORST=0
i=0
while [ $i -lt ${#STEP_NAMES[@]} ]; do
  rc=${STEP_RCS[$i]}
  printf '  %-24s rc=%s\n' "${STEP_NAMES[$i]}" "$rc"
  if [ "$rc" != "0" ]; then
    if [ "$rc" = "2" ] && [ "$WORST" = "0" ]; then WORST=2; else WORST=1; fi
  fi
  i=$((i + 1))
done
echo "== [verify] 结论：rc=$WORST（0 全过 / 1 有失败 / 2 环境不满足）=="
exit "$WORST"
