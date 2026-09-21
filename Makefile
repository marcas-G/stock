# stock 工作区：唯一门入口
SHELL := /bin/bash
PLATFORM_PY := platform/.venv/bin/python

.PHONY: help test-platform test-research test-all gates verify-fast verify-deep lint-factors index index-check reconcile data-update check-r22-baseline svc-image clean

# 研究产物区根（R37）：QUANTRESEARCH_ROOT env 可覆盖（缺省 /data/students/gaolei/
# quantresearch，解析单点在 research/tools/factor_lib/quantresearch_paths.py 与
# platform/src/factorlab/config.py）；索引/spec/档案门均对其运行。

help:
	@echo "make test-platform   平台全量测试（约 13 分钟；最近基线见 governance/evidence/verification/R24/00-baseline/）"
	@echo "make test-research   工具/研究/ops 测试（单解释器：平台 venv 3.13）"
	@echo "make gates           全套常驻门（结构/契约/标记/旧路径/索引/文档路径/台账口径）"
	@echo "make verify-fast     单一入口 fast 档（云端等价：离线门 + 三组 pytest；无 CH/无产物区）"
	@echo "make verify-deep     单一入口 deep 档（宿主：全量门 + CH 集成 + 在盘数据 + 研究产物门）"
	@echo "make lint-factors    全库因子 spec lint（QUANTRESEARCH_ROOT/factor；单进程批跑）"
	@echo "make index           重生成 $$QUANTRESEARCH_ROOT/index/{factors,strategies,composites}.md"
	@echo "make index-check     索引/成对门一致性检查（byte-equality；对 $$QUANTRESEARCH_ROOT）"
	@echo "make reconcile       CH 灌入对账（14 表含 moneyflow/fundamentals；依赖 ClickHouse 在线）"
	@echo "make data-update     夸克网盘数据更新全链（sync→build→verify→R22 基线指纹化自动刷新；8GB 内存护栏）"
	@echo "make check-r22-baseline  R22 基线数据指纹检查（只报告漂移，零写入）"
	@echo "make svc-image REF=<ref> [STABLE=1]  按 git ref 干净树构建挖矿服务镜像 factorlab-svc:<短sha>"
	@echo "make clean           清理 __pycache__ / .pytest_cache（本地缓存，可再生）"

test-platform:
	cd platform && .venv/bin/python -m pytest -q

test-research:
	$(PLATFORM_PY) -m pytest platform/tools -q
	$(PLATFORM_PY) -m pytest research/tools -q
	$(PLATFORM_PY) -m pytest governance/ops -q

audit-library:
	$(PLATFORM_PY) research/tools/factor_lib/reference_audit.py

test-all: test-platform test-research

gates:
	bash governance/ops/gates.sh

# 唯一验证入口（命令真相源 = governance/ops/verify.sh；workflow/timer/人均只调它）
verify-fast:
	bash governance/ops/verify.sh --profile fast

verify-deep:
	bash governance/ops/verify.sh --profile deep

lint-factors:
	platform/.venv/bin/factorlab lint --all

# 生成器读 QUANTRESEARCH_ROOT（默认本机产物区）；产物落 <root>/index/。
index:
	$(PLATFORM_PY) research/tools/factor_lib/build_index.py
	$(PLATFORM_PY) research/tools/factor_lib/build_strategy_index.py
	$(PLATFORM_PY) research/tools/factor_lib/build_composite_index.py

# 索引一致性门（R37 起独立目标：hosted CI 无产物区时由 workflow 守卫跳过）。
index-check:
	$(PLATFORM_PY) research/tools/factor_lib/build_index.py --check
	$(PLATFORM_PY) research/tools/factor_lib/build_strategy_index.py --check
	$(PLATFORM_PY) research/tools/factor_lib/build_composite_index.py --check

# CH 灌入对账（R4d：唯一对账入口；终评 I3 扩 moneyflow/fundamentals）。
# 需 ClickHouse 在线 + 平台 venv（clickhouse_connect）；源侧 zip/parquet 由脚本自行解析。
reconcile:
	$(PLATFORM_PY) platform/tools/ch_ingest/reconcile.py

# 夸克网盘数据更新全链（Plan P；需 quark_cookies.txt；重任务内存护栏 FACTORLAB_MAX_MEMORY）。
# 默认 all = sync → build（含 CH 灌入）→ verify；详见 platform/tools/pan_update/README.md。
# 收尾：R22 值级基线指纹化自动刷新（--auto：指纹变才刷；失败不半写，见 R31 ci-bootstrap 证据）。
data-update:
	FACTORLAB_MAX_MEMORY=8GB $(PLATFORM_PY) platform/tools/pan_update/cli.py all
	FACTORLAB_MAX_MEMORY=8GB $(PLATFORM_PY) governance/ops/refresh_r22_baseline.py --auto

# R22 基线数据指纹检查（只报告漂移，零写入；漂移 exit 1）。
check-r22-baseline:
	$(PLATFORM_PY) governance/ops/refresh_r22_baseline.py --check

# R39 Task 6：按 git ref 构建挖矿服务镜像（规格 §8）。
# 干净树 = `git archive <REF>`（不含未提交改动）→ docker build；tag factorlab-svc:<短sha>，
# STABLE=1 追加 factorlab-svc:stable；版本记录写 <QR>/results/platform/.service/image.json。
# 用法：make svc-image REF=<git-ref|HEAD> [STABLE=1]
svc-image:
	@if [ -z "$(REF)" ]; then echo "用法：make svc-image REF=<git-ref|HEAD> [STABLE=1]" >&2; exit 2; fi
	@set -euo pipefail; \
	FULL=$$(git rev-parse --verify '$(REF)^{commit}'); \
	SHA=$$(git rev-parse --short "$$FULL"); \
	TAG="factorlab-svc:$$SHA"; \
	QR=$${QUANTRESEARCH_ROOT:-/data/students/gaolei/quantresearch}; \
	CTX=$$(mktemp -d /tmp/factorlab-svc-ctx.XXXXXX); \
	trap 'rm -rf "$$CTX"' EXIT; \
	echo "[svc-image] ref=$(REF) sha=$$SHA tag=$$TAG"; \
	git archive --format=tar "$$FULL" | tar -x -C "$$CTX"; \
	cp deploy/service/.dockerignore "$$CTX/.dockerignore"; \
	BUILT_AT=$$(date -u +%Y-%m-%dT%H:%M:%SZ); \
	UV_HASH=$$(sha256sum platform/uv.lock | cut -d' ' -f1); \
	docker build -f deploy/service/Dockerfile \
	  --build-arg "GIT_SHA=$$SHA" --build-arg "IMAGE_TAG=$$TAG" \
	  --build-arg "BUILT_AT=$$BUILT_AT" --build-arg "UV_LOCK_HASH=$$UV_HASH" \
	  -t "$$TAG" "$$CTX"; \
	if [ -n "$(STABLE)" ]; then docker tag "$$TAG" factorlab-svc:stable; fi; \
	mkdir -p "$$QR/results/platform/.service"; \
	REF_VALUE='$(REF)' SHA_VALUE="$$SHA" TAG_VALUE="$$TAG" STABLE_VALUE="$(STABLE)" \
	BUILT_AT_VALUE="$$BUILT_AT" \
	IMAGE_JSON="$$QR/results/platform/.service/image.json" \
	python3 -c 'import json,os,pathlib; tags=[os.environ["TAG_VALUE"]]+(["factorlab-svc:stable"] if os.environ["STABLE_VALUE"] else []); p=pathlib.Path(os.environ["IMAGE_JSON"]); p.write_text(json.dumps({"ref": os.environ["REF_VALUE"], "sha": os.environ["SHA_VALUE"], "tags": tags, "built_at": os.environ["BUILT_AT_VALUE"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")'; \
	echo "[svc-image] 完成：$$TAG（image.json 已更新）"

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name .pytest_cache -type d -prune -exec rm -rf {} + 2>/dev/null || true

