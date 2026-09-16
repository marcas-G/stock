# stock 工作区：唯一门入口
SHELL := /bin/bash
PLATFORM_PY := platform/.venv/bin/python
EMB_PY      := /data/students/gaolei/anaconda3/envs/emb/bin/python

.PHONY: help test-platform test-research test-all gates lint-factors index reconcile clean

help:
	@echo "make test-platform   平台全量测试（约 7 分钟；基线 2570 passed / 13 skipped（R18 后））"
	@echo "make test-research   研究侧 T2（emb）+ T1（平台 venv）"
	@echo "make gates           全套常驻门（结构/契约/标记/旧路径/索引/文档路径）"
	@echo "make lint-factors    全库因子 spec lint（单进程批跑；任一失败非零退出）"
	@echo "make index           重生成 docs/index/factors.md"
	@echo "make reconcile       CH 灌入对账（唯一对账入口；依赖 ClickHouse 在线）"

test-platform:
	cd platform && .venv/bin/python -m pytest -q

test-research:
	$(EMB_PY) -m pytest research/tools -q
	$(PLATFORM_PY) -m pytest research/tools/strategies/tests research/tools/ch_ingest/tests \
	  research/tools/factor_lib/tests research/tools/1m_features/tests \
	  research/tools/ashare_ingest/tests research/tools/universe_stages/tests -q

test-all: test-platform test-research

gates:
	bash scripts/gates.sh

lint-factors:
	platform/.venv/bin/factorlab lint --all

index:
	python3 research/tools/factor_lib/build_index.py

# CH 灌入对账（R4d：唯一对账入口）。需 ClickHouse 在线 + 平台 venv（clickhouse_connect）。
reconcile:
	$(PLATFORM_PY) research/tools/ch_ingest/reconcile.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
