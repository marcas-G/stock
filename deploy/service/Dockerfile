# FactorLab 挖矿服务镜像（R39 Task 6；规格 §3/§7/§8）。
#
# - 源码 = `make svc-image` 用 `git archive <ref>` 导出的**干净树**（context 根），
#   运行期不挂源码（规格 §8："永不挂源码"）；
# - 依赖/钉版唯一来源 = 仓内 `governance/ops/ci_env.sh`（镜像内直连 PyPI；
#   不引入第二套锁文件/第二条装法，版本变化只改 ci_env.sh）；
# - 版本记录（/version 契约）由 Makefile 以 build-arg 注入，随镜像冻结。
FROM python:3.13-slim

ARG GIT_SHA=""
ARG IMAGE_TAG=""
ARG BUILT_AT=""
ARG UV_LOCK_HASH=""

ENV FACTORLAB_IMAGE_REF="${IMAGE_TAG}" \
    FACTORLAB_IMAGE_TAG="${IMAGE_TAG}" \
    FACTORLAB_GIT_SHA="${GIT_SHA}" \
    FACTORLAB_UV_LOCK_HASH="${UV_LOCK_HASH}" \
    FACTORLAB_BUILT_AT="${BUILT_AT}" \
    HOME=/home/factorlab

# 运行期以宿主 uid=1010 身份跑（`--user 1010:1010`）：补同名用户/组 + 可写 HOME
# （装配点 ensure_assembly() 会建 ~/.factorlab/plugins，root 不可写会让入口炸）。
RUN mkdir -p /home/factorlab \
 && groupadd -g 1010 factorlab \
 && useradd -u 1010 -g factorlab -d /home/factorlab -M -s /usr/sbin/nologin factorlab \
 && chown -R 1010:1010 /home/factorlab

COPY . /opt/factorlab
WORKDIR /opt/factorlab

# curl = 容器 healthcheck（规格 §8）；ca-certificates 供镜像内 pip TLS。
# ci_env.sh 幂等：建 platform/.venv + `-e ./platform[dev]` + 钉版，末尾自断言落位。
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && bash governance/ops/ci_env.sh \
 && platform/.venv/bin/python -c 'import factorlab; print("factorlab:", factorlab.__file__)'

ENTRYPOINT ["/opt/factorlab/platform/.venv/bin/factorlab"]
CMD ["service", "--host", "127.0.0.1", "--port", "8787"]
