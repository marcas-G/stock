# 平台树指南（stock/platform/）—— 薄层指针

本目录 = 单仓单树的**平台树**。工作区总纲与工作流见根 `../CLAUDE.md` 与 `../AGENTS.md`。

- 只收平台改动：`src/factorlab/`、`tests/`、`scripts/`（gen_op_catalog）；提交前缀 `feat(engine)` / `fix(adapters)` / `refactor(core)` / `docs(interface)`。
- 契约 4 篇 → `../knowledge/contracts/`；平台设计/计划 → `../knowledge/design/platform/{specs,plans}/`；运行产物 → 仓库根 `../runs/platform/`（`settings.results_dir` 默认）。
- 架构分层：`surfaces → app → ports → core`，I/O 全在 `adapters/`；门 = `tests/test_architecture.py`（静态 AST + 隔离运行双门）。
- 测试/文档硬要求见根 `AGENTS.md`；全量测试 `cd platform && .venv/bin/python -m pytest -q`。
- 环境事实：Python 3.13 venv `.venv/`；重装 editable + 落位断言 `bash ../governance/ops/reinstall_editable.sh`（评估内核已并入 `core/eval/kernel.py`；独立 quant-core dist 删除，R30 Task 15/D12）。
- 生产读路径 `FACTORLAB_DATA_BACKEND=ch`（平台 duckdb 库不存在）；重任务内存护栏见根 `AGENTS.md`。
- 数据位置权威：`../governance/workspace/data-map.md`；本目录**不得写入** `../data/`。
