#!/usr/bin/env bash
# nightly-verify.sh —— 夜间深检（postsubmit）：验证体系（verify.sh）的宿主腿（规格 §5）。
#
# 流程：`nice -n 10` + 内存预检后执行 `bash governance/ops/verify.sh --profile <NIGHTLY_PROFILE>`
#   （**不走 heavy.sh**：R36 教训——heavy.sh 注入 POLARS/OMP/MAX_MEMORY 会污染测试环境；
#    夜间单作业，宿主内存由 memguard 兜底）
# stdout+stderr 落 `$QUANTRESEARCH_ROOT/results/platform/.nightly/<YYYYmmdd-HHMMSS>.log`，
# 每次运行都写 `last.json`（rc/timestamp/log_path/profile/failed_steps）；
# rc != 0 → 调 `nightly_notify.py`（marker `<!-- nightly:YYYY-MM-DD -->` 幂等开/追加 issue）。
#
# 环境变量：
#   NIGHTLY_PROFILE      verify 档位（默认 deep；fast 用于轻量演练）
#   NIGHTLY_LOG_DIR      日志目录覆盖（默认 $QUANTRESEARCH_ROOT/results/platform/.nightly，
#                        QUANTRESEARCH_ROOT 缺省 /data/students/gaolei/quantresearch）
#   NIGHTLY_FORCE_FAIL=1 故障注入：不执行 verify，直接构造 rc=1（演练通知链路）
#   NIGHTLY_DRY_RUN=1    只打印将执行的命令，不落盘
#   NIGHTLY_VERIFY_CMD   覆盖被执行的命令（测试/演练用；默认 nice + verify.sh）
#
# 安装/启停：`bash governance/ops/install_nightly_verify.sh install|uninstall|status`
#（systemd user timer `factorlab-nightly-verify.timer`，每日 03:00）。
set -uo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$OPS_DIR/../.." && pwd)"
PROFILE="${NIGHTLY_PROFILE:-deep}"
LOG_DIR="${NIGHTLY_LOG_DIR:-${QUANTRESEARCH_ROOT:-/data/students/gaolei/quantresearch}/results/platform/.nightly}"
STAMP="$(TZ=Asia/Shanghai date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/$STAMP.log"
LAST_JSON="$LOG_DIR/last.json"
DATE_STR="$(TZ=Asia/Shanghai date +%F)"
NOTIFY="$OPS_DIR/nightly_notify.py"

if [ -x "$ROOT/platform/.venv/bin/python" ]; then
    PY="$ROOT/platform/.venv/bin/python"
else
    PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ]; then
    echo "nightly-verify.sh: 找不到 python3（也无法平台 venv）" >&2
    exit 2
fi

if [ -n "${NIGHTLY_VERIFY_CMD:-}" ]; then
    read -r -a VERIFY_CMD <<<"$NIGHTLY_VERIFY_CMD"
else
    VERIFY_CMD=(nice -n 10 bash "$ROOT/governance/ops/verify.sh" --profile "$PROFILE")
fi

if [ "${NIGHTLY_DRY_RUN:-0}" = "1" ]; then
    printf 'nightly-verify: profile=%s log_dir=%s\nverify cmd: %s\n' \
        "$PROFILE" "$LOG_DIR" "${VERIFY_CMD[*]}"
    exit 0
fi

mkdir -p "$LOG_DIR"

if [ "${NIGHTLY_FORCE_FAIL:-0}" = "1" ]; then
    printf '[nightly] %s 故障注入 NIGHTLY_FORCE_FAIL=1：未执行 verify，构造 rc=1\n' \
        "$(TZ=Asia/Shanghai date -Is)" >>"$LOG"
    rc=1
else
    AVAIL_KB=$(awk '/MemAvailable/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
    printf '[nightly] %s profile=%s log=%s\n' \
        "$(TZ=Asia/Shanghai date -Is)" "$PROFILE" "$LOG" >>"$LOG"
    printf '[nightly] cmd: %s\n' "${VERIFY_CMD[*]}" >>"$LOG"
    printf '[nightly] avail=%sMB\n' "$((AVAIL_KB/1024))" >>"$LOG"
    if [ "$PROFILE" = "deep" ] && [ "${AVAIL_KB:-0}" -lt 8388608 ]; then
        printf '[nightly] 可用内存 <8GB，跳过 deep（rc=2）\n' >>"$LOG"
        rc=2
        "$PY" "$NOTIFY" --last-json "$LAST_JSON" --rc "$rc" --log "$LOG" \
            --profile "$PROFILE" --date "$DATE_STR" >>"$LOG" 2>&1 || true
        exit "$rc"
    fi
    ( cd "$ROOT" && "${VERIFY_CMD[@]}" ) >>"$LOG" 2>&1
    rc=$?
    printf '[nightly] verify rc=%d\n' "$rc" >>"$LOG"
fi

"$PY" "$NOTIFY" --last-json "$LAST_JSON" --rc "$rc" --log "$LOG" \
    --profile "$PROFILE" --date "$DATE_STR" >>"$LOG" 2>&1 || \
    printf '[nightly] 写 last.json 失败（见上）\n' >>"$LOG"

if [ "$rc" -ne 0 ]; then
    if "$PY" "$NOTIFY" --rc "$rc" --log "$LOG" --profile "$PROFILE" \
        --date "$DATE_STR" >>"$LOG" 2>&1; then
        printf '[nightly] notify rc=0（issue 见上）\n' >>"$LOG"
    else
        printf '[nightly] notify 失败：请手工检查 %s\n' "$LOG" >>"$LOG"
    fi
fi

echo "nightly-verify: profile=$PROFILE rc=$rc log=$LOG"
exit "$rc"
