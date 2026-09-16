#!/usr/bin/env bash
# pan_update 定时任务安装（Plan P T9）：systemd --user timer 优先（每日 08:10），
# `systemctl --user` 不可用 → 打印 crontab 行与手工说明（设计 §5，不静默失败）。
#
# 用法：bash governance/ops/install_pan_timer.sh install|uninstall|status
# 退出码：0 成功；2 用法错误。
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
UNIT_NAME="pan-data-update"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE="$UNIT_DIR/$UNIT_NAME.service"
TIMER="$UNIT_DIR/$UNIT_NAME.timer"
LOG_DIR="$ROOT/runs/platform/logs"
CRON_LINE="10 8 * * * cd $ROOT && FACTORLAB_MAX_MEMORY=8GB make data-update >> $LOG_DIR/pan_update-cron.log 2>&1"

has_user_systemd() {
  command -v systemctl >/dev/null 2>&1 || return 1
  systemctl --user show-environment >/dev/null 2>&1 || return 1
}

fallback() {
  cat <<EOF
[回退] systemd --user 不可用 → 用户 crontab（crontab -e 加入下面一行）：
$CRON_LINE
核对：crontab -l；日志：$LOG_DIR/pan_update-cron.log
手工验证：cd $ROOT && make data-update
EOF
}

write_units() {
  mkdir -p "$UNIT_DIR"
  cat > "$SERVICE" <<EOF
[Unit]
Description=FactorLab pan data update (quark share sync/build/verify)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT
Environment=FACTORLAB_MAX_MEMORY=8GB
ExecStart=/usr/bin/env bash -lc 'make data-update'
TimeoutStartSec=12h
Nice=10
EOF
  cat > "$TIMER" <<EOF
[Unit]
Description=Daily FactorLab pan data update (08:10)

[Timer]
OnCalendar=*-*-* 08:10:00
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
EOF
}

install_() {
  if has_user_systemd; then
    write_units
    systemctl --user daemon-reload || true
    if systemctl --user enable --now "$UNIT_NAME.timer" 2>/dev/null; then
      echo "[ok] 已安装并启动 $UNIT_NAME.timer（每日 08:10）"
      systemctl --user list-timers "$UNIT_NAME.timer" --no-pager 2>/dev/null || true
      echo "验证：systemctl --user status $UNIT_NAME.timer；journalctl --user -u $UNIT_NAME.service"
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
    echo "[ok] 已卸载 $UNIT_NAME.timer / $UNIT_NAME.service"
  else
    echo "[info] systemd --user 不可用（无 unit 可卸）。若曾加 crontab 行请手工删除："
    echo "$CRON_LINE"
  fi
}

status_() {
  echo "repo: $ROOT"
  if [ -f "$TIMER" ]; then echo "timer 文件：存在（$TIMER）"; else echo "timer 文件：不存在"; fi
  if has_user_systemd; then
    systemctl --user list-timers "$UNIT_NAME.timer" --no-pager 2>&1 || true
    systemctl --user is-enabled "$UNIT_NAME.timer" 2>&1 || true
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
