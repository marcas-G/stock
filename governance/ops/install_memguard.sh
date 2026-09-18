#!/usr/bin/env bash
# install_memguard.sh —— 安装并立即启动 memguard 用户级常驻守护（R30）。
#
# 首选 systemd user service（~/.config/systemd/user/memguard.service，
# Restart=always；linger 已开启则退出 SSH 仍存活）；若 systemctl --user
# 不可用/启动失败，回退用户 crontab（@reboot + 每分钟保活），并把实际路径
# 写入 ~/.local/state/memguard/install-method。
#
# 幂等：重复执行会重写 unit 并 restart。
# 注意：本机 user cgroup 无法设内存上限（systemd-run --user -p MemoryMax
# 实测失败），故不用 MemoryMax；护栏逻辑全在 memguard 进程内。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${MEMGUARD_PYTHON:-$REPO/platform/.venv/bin/python}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/memguard"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/memguard.service"
EXEC_ARGS=(--interval 2 --memlog-interval 10)

if [ ! -x "$PY" ]; then
    echo "install_memguard: 找不到 python 解释器 $PY（可用 MEMGUARD_PYTHON 覆盖）" >&2
    exit 1
fi
mkdir -p "$UNIT_DIR" "$STATE_DIR"

cat > "$UNIT" <<EOF
[Unit]
Description=memguard host memory guard (R30; kill only heavy procs of $(id -un))
After=default.target

[Service]
Type=simple
ExecStart=$PY $REPO/governance/ops/memguard.py ${EXEC_ARGS[*]}
Restart=always
RestartSec=5
Nice=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

method="systemd-user"
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    systemctl --user daemon-reload
    systemctl --user enable --now memguard.service
    sleep 2
    if ! systemctl --user is-active --quiet memguard.service; then
        echo "install_memguard: systemd user service 未 active，回退 crontab" >&2
        systemctl --user disable memguard.service >/dev/null 2>&1 || true
        method="crontab"
    fi
else
    echo "install_memguard: systemctl --user 不可用，回退 crontab" >&2
    method="crontab"
fi

if [ "$method" = "crontab" ]; then
    CRON_BOOT="@reboot sleep 20; pgrep -f 'memguard.py ${EXEC_ARGS[0]}' >/dev/null 2>&1 || nohup $PY $REPO/governance/ops/memguard.py ${EXEC_ARGS[*]} >> $STATE_DIR/memguard.cron.log 2>&1 &"
    CRON_ALIVE="* * * * * pgrep -f 'memguard.py ${EXEC_ARGS[0]}' >/dev/null 2>&1 || nohup $PY $REPO/governance/ops/memguard.py ${EXEC_ARGS[*]} >> $STATE_DIR/memguard.cron.log 2>&1 &"
    { crontab -l 2>/dev/null | grep -v 'governance/ops/memguard.py' || true; echo "$CRON_BOOT"; echo "$CRON_ALIVE"; } | crontab -
    # 立即以 live 模式拉起一份（crontab 每分钟保活会接管）
    if ! pgrep -f "governance/ops/memguard.py ${EXEC_ARGS[0]}" >/dev/null 2>&1; then
        nohup "$PY" "$REPO/governance/ops/memguard.py" "${EXEC_ARGS[@]}" \
            >> "$STATE_DIR/memguard.cron.log" 2>&1 &
    fi
    sleep 1
fi

echo "$method" > "$STATE_DIR/install-method"
echo "memguard 安装路径: $method"
if [ "$method" = "systemd-user" ]; then
    systemctl --user --no-pager status memguard.service | head -12
else
    pgrep -af "governance/ops/memguard.py" | head -3
fi
