#!/usr/bin/env bash
# 安装/启动 Prefect runner（用户级 systemd）：把 xscore 流水线注册为 deployments。
# 前置：prefect-server 已运行（install_prefect_server.sh）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENV="$ROOT/research/.venv"
PIPE="$ROOT/research/tools/xscore/pipeline"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/prefect-runner.service"

[ -x "$VENV/bin/python" ] || { echo "缺少 $VENV（先装 prefect）"; exit 1; }
curl -sf -o /dev/null http://127.0.0.1:4200/api/health || {
  echo "prefect-server 未就绪，先跑 governance/ops/install_prefect_server.sh"; exit 1; }
mkdir -p "$UNIT_DIR"
cat > "$UNIT" <<UNIT_EOF
[Unit]
Description=Prefect runner (xscore deployments, user-level)
After=network.target prefect-server.service

[Service]
Type=simple
WorkingDirectory=$PIPE
Environment=PREFECT_API_URL=http://127.0.0.1:4200/api
ExecStart=$VENV/bin/python $PIPE/serve.py
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
UNIT_EOF

systemctl --user daemon-reload
systemctl --user enable --now prefect-runner.service
sleep 6
systemctl --user --no-pager status prefect-runner.service | head -6
echo "== deployments =="
for i in $(seq 1 10); do
  out=$(PREFECT_API_URL=http://127.0.0.1:4200/api "$VENV/bin/prefect" deployment ls 2>/dev/null || true)
  echo "$out" | grep -q "xscore-m0-split" && { echo "$out"; exit 0; }
  sleep 3
done
echo "deployment 未就绪：journalctl --user -u prefect-runner -n 50"
exit 1
