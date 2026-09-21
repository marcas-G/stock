# stock 工作区：唯一门入口
SHELL := /bin/bash
PLATFORM_PY := platform/.venv/bin/python

.PHONY: help test-platform test-research test-all gates verify-fast verify-deep lint-factors index index-check reconcile data-update check-r22-baseline clean

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

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name .pytest_cache -type d -prune -exec rm -rf {} + 2>/dev/null || true
