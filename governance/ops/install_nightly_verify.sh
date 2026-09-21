#!/usr/bin/env bash
# factorlab-nightly-verify systemd user service/timer 安装（规格 §5）。
#
# 每日 03:00（宿主本地时区 CST）执行 governance/ops/nightly-verify.sh
# （内部经 heavy.sh → verify.sh --profile deep）；Persistent=true 补跑错过的窗口。
# 日志：$QUANTRESEARCH_ROOT/results/platform/.nightly/（last.json 每次更新）。
#
# 用法：bash governance/ops/install_nightly_verify.sh install|uninstall|status
# 退出码：0 成功；2 用法错误。
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
UNIT_NAME="factorlab-nightly-verify"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE="$UNIT_DIR/$UNIT_NAME.service"
TIMER="$UNIT_DIR/$UNIT_NAME.timer"
RESEARCH_ROOT="${QUANTRESEARCH_ROOT:-/data/students/gaolei/quantresearch}"
LOG_DIR="$RESEARCH_ROOT/results/platform/.nightly"
CRON_LINE="0 3 * * * cd $ROOT && QUANTRESEARCH_ROOT=$RESEARCH_ROOT bash governance/ops/nightly-verify.sh >> $LOG_DIR/cron.log 2>&1"

has_user_systemd() {
  command -v systemctl >/dev/null 2>&1 || return 1
  systemctl --user show-environment >/dev/null 2>&1 || return 1
}

fallback() {
  # cron 重定向在命令启动前发生：日志目录必须先在（否则 crontab 行必失败）。
  mkdir -p "$LOG_DIR"
  cat <<EOF
[回退] systemd --user 不可用 → 用户 crontab（crontab -e 加入下面一行）：
$CRON_LINE
核对：crontab -l；日志：$LOG_DIR/cron.log
手工验证：cd $ROOT && bash governance/ops/nightly-verify.sh
EOF
}

write_units() {
  mkdir -p "$UNIT_DIR"
  cat > "$SERVICE" <<EOF
[Unit]
Description=FactorLab nightly deep verify (postsubmit; verify.sh --profile deep)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT
Environment=TZ=Asia/Shanghai
ExecStart=/usr/bin/env bash -lc 'bash governance/ops/nightly-verify.sh'
TimeoutStartSec=3h
EOF
  cat > "$TIMER" <<EOF
[Unit]
Description=FactorLab nightly deep verify timer (03:00 CST)

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
}

install_() {
  if has_user_systemd; then
    write_units
    systemctl --user daemon-reload || true
    if systemctl --user enable --now "$UNIT_NAME.timer" 2>/dev/null; then
      echo "[ok] 已安装并启动 $UNIT_NAME.timer（每日 03:00；Persistent=true）"
      systemctl --user list-timers "$UNIT_NAME.timer" --no-pager 2>/dev/null || true
      echo "验证：systemctl --user status $UNIT_NAME.timer；" \
           "journalctl --user -u $UNIT_NAME.service；日志 $LOG_DIR"
      return 0
    fi
    echo "[warn] systemd --user 安装失败 → crontab 回退" >&2
  fi
  fallback
}

uninstall_() {
  if has_user_systemd; then
    systemctl --user disable --now "$UNIT_NAME.timer" 2>/dev/null || true
    rm -f "$SERVICE" "$TIMER"
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user reset-failed "$UNIT_NAME.service" 2>/dev/null || true
    echo "[ok] 已卸载 $UNIT_NAME.timer / $UNIT_NAME.service（日志与 last.json 保留在 $LOG_DIR）"
  else
    echo "[info] systemd --user 不可用（无 unit 可卸）。若曾加 crontab 行请手工删除："
    echo "$CRON_LINE"
  fi
}

status_() {
  echo "repo: $ROOT"
  echo "log dir: $LOG_DIR"
  if [ -f "$TIMER" ]; then echo "timer 文件：存在（$TIMER）"; else echo "timer 文件：不存在"; fi
  if [ -f "$SERVICE" ]; then echo "service 文件：存在（$SERVICE）"; else echo "service 文件：不存在"; fi
  if has_user_systemd; then
    systemctl --user is-enabled "$UNIT_NAME.timer" 2>&1 || true
    systemctl --user list-timers "$UNIT_NAME.timer" --no-pager 2>&1 || true
  else
    echo "systemd --user：不可用"
    fallback
  fi
  return 0
}

case "${1:-}" in
  install) install_ ;;
  uninstall) uninstall_ ;;
  status) status_ ;;
  *) echo "用法：bash $0 install|uninstall|status" >&2; exit 2 ;;
esac
