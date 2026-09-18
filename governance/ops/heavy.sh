#!/usr/bin/env bash
# heavy.sh —— 重任务闸（R30 主机内存保护体系）。
#
# 重任务（全市场/长窗 factorlab run、分钟链、灌库/回测批跑）一律经本闸启动：
#   1) flock 限 2 并发（HEAVY_MAX_CONCURRENT 可调）——槽满则排队等待；
#   2) 启动前检查可用内存，<8GB（HEAVY_MIN_AVAILABLE_GB）拒绝并提示；
#   3) 默认注入内存/线程护栏 + nice -n 10（已显式导出的用户值优先，不覆盖）：
#        FACTORLAB_MAX_MEMORY=${FACTORLAB_MAX_MEMORY:-8GB}
#        FACTORLAB_MIN_AVAILABLE_MEMORY=${FACTORLAB_MIN_AVAILABLE_MEMORY:-6GB}
#        OMP_NUM_THREADS=8 / POLARS_MAX_THREADS=8
# 取证明细（谁在占内存）：~/.local/state/memguard/memlog.tsv（memguard 守护写）。
#
# 用法： governance/ops/heavy.sh <命令...>
# 测试： platform/.venv/bin/python -m pytest governance/ops/tests/test_heavy_sh.py -q
set -euo pipefail

HEAVY_MAX_CONCURRENT="${HEAVY_MAX_CONCURRENT:-2}"
HEAVY_MIN_AVAILABLE_GB="${HEAVY_MIN_AVAILABLE_GB:-8}"
HEAVY_LOCK_DIR="${HEAVY_LOCK_DIR:-${XDG_RUNTIME_DIR:-/tmp}/factorlab-heavy-locks}"
HEAVY_MEMINFO_FILE="${HEAVY_MEMINFO_FILE:-/proc/meminfo}"

if [ "$#" -eq 0 ]; then
    echo "用法: heavy.sh <命令...>（重任务闸：限 ${HEAVY_MAX_CONCURRENT} 并发 + 可用内存 >=${HEAVY_MIN_AVAILABLE_GB}GB）" >&2
    exit 2
fi

mkdir -p "$HEAVY_LOCK_DIR"
chmod 700 "$HEAVY_LOCK_DIR" 2>/dev/null || true

# ---- 1) 并发槽（flock；fd 随进程退出自动释放） ----
slot=""
while [ -z "$slot" ]; do
    busy=1
    for i in $(seq 0 $((HEAVY_MAX_CONCURRENT - 1))); do
        fd=$((200 + i))
        eval "exec ${fd}>\"${HEAVY_LOCK_DIR}/slot${i}\""
        if flock -n "$fd"; then
            slot="$i"
            busy=0
            break
        fi
        eval "exec ${fd}>&-"
    done
    if [ -z "$slot" ]; then
        if [ "$busy" -eq 1 ]; then
            echo "heavy.sh: 并发槽 ${HEAVY_MAX_CONCURRENT}/${HEAVY_MAX_CONCURRENT} 已满，排队等待..." >&2
        fi
        sleep 1
    fi
done

# ---- 2) 可用内存闸 ----
avail_kb="$(awk '/^MemAvailable:/{print $2; exit}' "$HEAVY_MEMINFO_FILE" 2>/dev/null || true)"
if [ -z "$avail_kb" ]; then
    echo "heavy.sh: 无法从 $HEAVY_MEMINFO_FILE 读取 MemAvailable —— 拒绝启动" >&2
    exit 4
fi
min_kb=$((HEAVY_MIN_AVAILABLE_GB * 1024 * 1024))
avail_gb="$(awk -v k="$avail_kb" 'BEGIN{printf "%.1f", k/1048576}')"
if [ "$avail_kb" -lt "$min_kb" ]; then
    echo "heavy.sh: 可用内存不足 ${HEAVY_MIN_AVAILABLE_GB}GB（当前 ${avail_gb}GB）——拒绝启动重任务。" >&2
    echo "  等待内存释放；占用线索见 ~/.local/state/memguard/memlog.tsv（memguard 10s 采样）。" >&2
    exit 3
fi

# ---- 3) 注入护栏并执行 ----
export FACTORLAB_MAX_MEMORY="${FACTORLAB_MAX_MEMORY:-8GB}"
export FACTORLAB_MIN_AVAILABLE_MEMORY="${FACTORLAB_MIN_AVAILABLE_MEMORY:-6GB}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-8}"
export POLARS_MAX_THREADS="${POLARS_MAX_THREADS:-8}"
echo "heavy.sh: slot=$((slot + 1))/${HEAVY_MAX_CONCURRENT} avail=${avail_gb}GB " \
     "max_memory=${FACTORLAB_MAX_MEMORY} -> $*" >&2
exec nice -n 10 "$@"
