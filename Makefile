# stock 工作区：唯一门入口
SHELL := /bin/bash
PLATFORM_PY := platform/.venv/bin/python

.PHONY: help test-platform test-research test-all gates lint-factors index reconcile clean

help:
	@echo "make test-platform   平台全量测试（约 13 分钟；基线见 governance/evidence/verification/R27/）"
	@echo "make test-research   工具/研究测试（单解释器：平台 venv 3.13）"
	@echo "make gates           全套常驻门（结构/契约/标记/旧路径/索引/文档路径）"
	@echo "make lint-factors    全库因子 spec lint（单进程批跑；任一失败非零退出）"
	@echo "make index           重生成 knowledge/index/factors.md + knowledge/index/strategies.md"
	@echo "make reconcile       CH 灌入对账（唯一对账入口；依赖 ClickHouse 在线）"

test-platform:
	cd platform && .venv/bin/python -m pytest -q

test-research:
	$(PLATFORM_PY) -m pytest platform/tools -q
	$(PLATFORM_PY) -m pytest research/tools -q

test-all: test-platform test-research

gates:
	bash governance/ops/gates.sh

lint-factors:
	platform/.venv/bin/factorlab lint --all

index:
	python3 research/tools/factor_lib/build_index.py
	python3 research/tools/factor_lib/build_strategy_index.py

# CH 灌入对账（R4d：唯一对账入口）。需 ClickHouse 在线 + 平台 venv（clickhouse_connect）。
reconcile:
	$(PLATFORM_PY) platform/tools/ch_ingest/reconcile.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
