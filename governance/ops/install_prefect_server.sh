#!/usr/bin/env bash
# 安装/启动 Prefect 3 本地 server（用户级 systemd，无需 sudo）。
# UI: http://127.0.0.1:4200   API: http://127.0.0.1:4200/api
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENV="$ROOT/research/.venv"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/prefect-server.service"

[ -x "$VENV/bin/prefect" ] || { echo "缺少 $VENV/bin/prefect（先 uv pip install prefect）"; exit 1; }
mkdir -p "$UNIT_DIR"
cat > "$UNIT" <<EOF
[Unit]
Description=Prefect 3 server (local, user-level)
After=network.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PREFECT_SERVER_API_HOST=127.0.0.1
Environment=PREFECT_SERVER_API_PORT=4200
ExecStart=$VENV/bin/prefect server start --host 127.0.0.1 --port 4200
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now prefect-server.service
sleep 3
systemctl --user --no-pager status prefect-server.service | head -8
echo "== health =="
for i in $(seq 1 10); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:4200/api/health || true)
  [ "$code" = "200" ] && { echo "UI/API OK: http://127.0.0.1:4200"; exit 0; }
  sleep 2
done
echo "health 未就绪（查看日志: journalctl --user -u prefect-server -n 50）"
exit 1
