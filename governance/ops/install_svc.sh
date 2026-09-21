#!/usr/bin/env bash
# factorlab-svc systemd user unit 安装/卸载/状态（R39 Task 6）。
#
# 用法：bash governance/ops/install_svc.sh install|uninstall|status
#   install   render 模板 → $SYSTEMD_UNIT_DIR/factorlab-svc.service → enable + restart
#   uninstall disable --now + 删 unit + daemon-reload + 清容器
#   status    systemctl 状态 + docker ps + /health
set -euo pipefail

ACTION=${1:-}
[ -n "$ACTION" ] || { echo "用法：$0 install|uninstall|status" >&2; exit 2; }

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
UNIT_NAME=factorlab-svc.service
TEMPLATE="$REPO_ROOT/deploy/service/$UNIT_NAME"
SYSTEMD_UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
UNIT_PATH="$SYSTEMD_UNIT_DIR/$UNIT_NAME"

case "$ACTION" in
  install)
    [ -f "$TEMPLATE" ] || { echo "[install_svc] 模板缺失：$TEMPLATE" >&2; exit 2; }
    mkdir -p "$SYSTEMD_UNIT_DIR"
    sed "s|@REPO_ROOT@|$REPO_ROOT|g" "$TEMPLATE" > "$UNIT_PATH"
    systemctl --user daemon-reload
    systemctl --user enable "$UNIT_NAME"
    systemctl --user restart "$UNIT_NAME"   # 已运行 → 应用新 unit；未运行 → start
    if systemctl --user is-active --quiet "$UNIT_NAME"; then
      echo "[install_svc] $UNIT_NAME 已安装并 active（unit=$UNIT_PATH）"
    else
      echo "[install_svc] $UNIT_NAME 未 active，排查：systemctl --user status $UNIT_NAME" >&2
      exit 1
    fi
    ;;
  uninstall)
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
    rm -f "$UNIT_PATH"
    systemctl --user daemon-reload
    docker rm -f factorlab-svc >/dev/null 2>&1 || true
    echo "[install_svc] 已卸载 $UNIT_NAME 与容器 factorlab-svc"
    ;;
  status)
    systemctl --user status "$UNIT_NAME" --no-pager || true
    echo "---- docker ----"
    docker ps --filter "name=factorlab-svc" \
      --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' || true
    echo "---- /health ----"
    curl -fsS http://127.0.0.1:8787/health || true
    echo
    ;;
  *)
    echo "用法：$0 install|uninstall|status" >&2
    exit 2
    ;;
esac
