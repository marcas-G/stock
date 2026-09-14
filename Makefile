# stock 工作区：唯一门入口
SHELL := /bin/bash
PLATFORM_PY := platform/.venv/bin/python
EMB_PY      := /data/students/gaolei/anaconda3/envs/emb/bin/python

.PHONY: help test-platform test-research test-all gates lint-factors index clean

help:
	@echo "make test-platform   平台全量测试（约 7 分钟；基线 2486 passed / 13 skipped）"
	@echo "make test-research   研究侧 T2（emb）+ T1（平台 venv）"
	@echo "make gates           全套常驻门（结构/契约/标记/旧路径/索引/文档路径）"
	@echo "make lint-factors    全库因子 spec lint（152/152）"
	@echo "make index           重生成 docs/index/factors.md"

test-platform:
	cd platform && .venv/bin/python -m pytest -q

test-research:
	$(EMB_PY) -m pytest research/tools -q
	$(PLATFORM_PY) -m pytest research/tools/strategies/tests -q

test-all: test-platform test-research

gates:
	bash scripts/gates.sh

lint-factors:
	@ok=0; bad=0; for f in research/factor/*/*.yaml; do \
	  if platform/.venv/bin/factorlab lint "$$f" >/dev/null 2>&1; then ok=$$((ok+1)); else bad=$$((bad+1)); echo "  lint 失败: $$f"; fi; done; \
	  echo "  factor lint: $$ok 通过 / $$bad 失败"

index:
	python3 scripts/gen_factors_index.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
