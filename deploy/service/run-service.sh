#!/usr/bin/env bash
# 启动/替换 FactorLab 挖矿服务容器（规格 §3 挂载 / §7 限额；R39 Task 6）。
#
# 用法：deploy/service/run-service.sh [镜像tag]
#   镜像来源优先级：$1 > $FACTORLAB_SVC_IMAGE > <QR>/results/platform/.service/image.json
#   （make svc-image 写入）的 tags[0]。
# systemd user unit 以 FACTORLAB_SVC_HOLD=1 调用：脚本末尾 exec docker wait 前台阻塞，
# 让 Restart=always 能观察到容器退出并重建（Type=simple 否则会立刻退出 → 重启风暴）。
set -euo pipefail

NAME=factorlab-svc
QR=${QUANTRESEARCH_ROOT:-/data/students/gaolei/quantresearch}
REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
UID_GID=${FACTORLAB_SVC_USER:-1010:1010}
DOCKER=${DOCKER:-/usr/bin/docker}
[ -x "$DOCKER" ] || DOCKER=$(command -v docker)

IMAGE=${1:-${FACTORLAB_SVC_IMAGE:-}}
if [ -z "$IMAGE" ]; then
  IMG_JSON="$QR/results/platform/.service/image.json"
  [ -f "$IMG_JSON" ] || {
    echo "[run-service] 未给镜像且无 $IMG_JSON；先 make svc-image REF=<ref>" >&2; exit 2; }
  IMAGE=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["tags"][0])' "$IMG_JSON")
fi

# rw 挂载点先建（避免 docker 以 root 建目录）；ro 目录同样补建，避免 root 属主混入产物区。
for d in results factor experiments composites strategy dossiers index lab; do
  mkdir -p "$QR/$d"
done
mkdir -p "$QR/results/platform/.service/cache"

# 读取门（R31）只认 <repo>/data/health/<dataset>/<partition>.json —— canonical 数据在 CH，
# 唯一需要的小件元数据（73MB）；规格 §3 未列此项 = 缺口（无它一切真作业被拒）。
[ -d "$REPO_ROOT/data/health" ] || {
  echo "[run-service] 缺 $REPO_ROOT/data/health（读取门元数据）；请先发布 health" >&2; exit 2; }

"$DOCKER" rm -f "$NAME" >/dev/null 2>&1 || true

CID=$("$DOCKER" run -d --name "$NAME" --restart unless-stopped \
  --network host --user "$UID_GID" --cpus=8 --memory=16g --pids-limit=256 \
  -e FACTORLAB_DATA_BACKEND=ch \
  -e FACTORLAB_RESULTS_DIR=/quantresearch/results/platform \
  -e QUANTRESEARCH_ROOT=/quantresearch \
  -e FACTORLAB_RESEARCH_ROOT=/quantresearch \
  -e FACTORLAB_READ_CACHE_DIR=/service-cache/bars_1m \
  -e FACTORLAB_CH_MAX_THREADS=8 \
  -e FACTORLAB_MAX_MEMORY=8GB \
  -v "$QR/results:/quantresearch/results:rw" \
  -v "$QR/factor:/quantresearch/factor:rw" \
  -v "$QR/experiments:/quantresearch/experiments:rw" \
  -v "$QR/composites:/quantresearch/composites:ro" \
  -v "$QR/strategy:/quantresearch/strategy:ro" \
  -v "$QR/dossiers:/quantresearch/dossiers:ro" \
  -v "$QR/index:/quantresearch/index:ro" \
  -v "$QR/lab:/quantresearch/lab:ro" \
  -v "$QR/results/platform/.service/cache:/service-cache:rw" \
  -v "$REPO_ROOT/data/health:/data/students/gaolei/stock/data/health:ro" \
  --health-cmd 'curl -fsS http://127.0.0.1:8787/health' \
  --health-interval 15s --health-timeout 5s --health-retries 5 --health-start-period 30s \
  "$IMAGE")
echo "[run-service] $NAME 已启动：image=$IMAGE cid=${CID:0:12}"

if [ "${FACTORLAB_SVC_HOLD:-0}" = "1" ]; then
  exec "$DOCKER" wait "$NAME"
fi
