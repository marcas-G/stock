# FactorLab 项目指南

个人因子 DSL 计算平台（`expr_codegen` + `polars_ta` 内核，Rust `quant_core` 评估，
平台自有 DuckDB 数据层）。工作流与技能约定见 `AGENTS.md`（Superpowers 框架）。

## 硬性要求

### 目录分权（2026-09-15 起，取代旧的两分支纪律；最高优先级）

本目录 = 单仓单树里的**平台树**（`stock/platform/`）。旧的双分支纪律（main=平台 /
research=研究、独立 worktree）已退役，改按**目录**分权：

- 本目录只收平台改动：`src/factorlab/`、`tests/`、`docs/`（契约 4 篇 + superpowers）、`scripts/`。
  提交前缀用平台语义：`feat(engine)` / `fix(adapters)` / `docs(interface)` / `refactor(core)`。
- 研究内容在**兄弟目录** `../research/`（`factor/`、`tools/`、`docs/`）——**不进本目录**。
  跨树改动分目录分别提交。
- `results/`（运行产物）与 `data/`、`.venv/` 一律不入库（根 `.gitignore` 三重保护）。
- 工作区级约定（数据地图、目录约定、未决事项）在仓库根 `docs/`，本文件不重复。

### 文档和测试必须做好、写全面（最高优先级）

任何代码改动，验收时同时检查文档与测试是否同步、全面，不满足不算完成：

**测试**：
- 所有功能遵循 TDD：先写失败测试，再写最小实现，测试转绿才提交。
- 测试必须覆盖**正常路径、边界条件、错误路径**三类场景，禁止只测 happy path。
- 测试验证真实行为（真实数据/真实计算），不用 mock 糊弄；依赖外部资源的
  集成测试用 `@pytest.mark.integration` 标记，环境缺失时 skip 而非假实现。
- 涉及数据窗口/分组/对齐语义（TS/CS/GP 分区、周频对齐、停牌补全等）必须有
  能捕获跨资产泄漏、未来函数、错位这类错误的回归测试（多资产/多日期面板）。
- 提交前运行全量测试套件并确认全部通过（`python -m pytest -q`）。

**文档**：
- 代码提交必须同步更新相应文档：新增/变更的 Python API、CLI 命令、DSL 语法
  写入 `docs/interface.md`；设计决策与里程碑写入 `docs/superpowers/specs/` 与
  `docs/superpowers/plans/`。
- 新模块、新接口必须有使用说明（签名、行为、错误语义），不允许"代码即文档"。
- 文档与实现冲突时，文档必须修订到与实现一致，并在 `docs/interface.md` 注明。
- 实现中发现的设计缺口（计划/规格与实现不符）必须记录到对应设计/计划文档。

## 架构分层（2026-09-12 深度重构后，spec = docs/superpowers/specs/2026-09-12-mining-system-refactor-design.md）

```
surfaces/ (cli/web)  →  app/ (bootstrap/run/evaluate)  →  ports/ (6 条契约)  →  core/ (纯核)
                          ↑ adapters/ (duckdb/ch 读、parquet 写、tick 读、面板、批算、plugins) → ports/core
```

- **`core/`**：纯计算核（domain/ops/engine/eval/strategy/执行纯子集/factio）。**不得** import
  duckdb/clickhouse_connect/requests，不得 import `factorlab.{data,ports,adapters,app,surfaces,
  artifacts,cli,web,process}`，不得出现 `.read_parquet/.scan_parquet/.write_parquet/.glob/
  .write_text`（`read_text` 载配置允许）。门 = `tests/test_architecture.py`（静态 AST + 隔离运行双门）。
- **`ports/`**：读/写/面板/事实源/批算编排/评估内核六条 Protocol（零实现）——扩展缝，测试桩在 `tests/_doubles.py`。
- **`adapters/`**：全部 I/O（`duckdb_read`/`ch_read`/`read/*`/`tick_read`/`parquet_artifacts`/
  `strategy_artifacts`/`execution_store`/`panel_store`/`results_fs`/`batch`/`plugins`…）。
- **`app/`**：装配（`bootstrap.open_read/install_operators`、`run.run_factor/run_factor_minute`、
  `evaluate.evaluate_run/publish_run`）——**唯一评估装配点**。
- **`surfaces/`**：CLI 与 Web（只做参数解析与呈现）。
- 旧路径已退役（`factorlab.data.*`、`factorlab.artifacts`、`factorlab.engine.*` 等 → 新层）；
  `docs/interface.md` §4 是路径权威，且有 `tests/test_doc_paths_exist.py` 自动核。

## 环境事实

- Python 3.13（本目录自带 uv 管理 venv：`.venv/`；解释器 `.venv/bin/python`）。
  目录移动后需重装 editable：
  `uv pip install --python .venv/bin/python -e . --no-deps --no-build-isolation`；
  评估依赖 `quant_core`（shim 包在 `../quant_core_shim`，同样以 editable 装入本 venv）。
- 平台库 `data/factorlab.duckdb`（`settings.platform_db`）**当前不存在**（teajoin token 过期）：
  生产读路径用 `FACTORLAB_DATA_BACKEND=ch`（ClickHouse）。写入仅经
  `factorlab data rebuild/update/refresh`。
- 数据位置以仓库根 `docs/data-map.md` 为唯一权威；本目录**不得写入** `../data/`。
- `daily.code` 为纯数字（`000001`），`stock_basic_tushare.ts_code` 带后缀
  （`000001.SZ`）；`symbol` 列是两者桥梁。
- 目标机器约 16GB 内存且无页面文件：SQL-first、float32、DuckDB `memory_limit`
  等内存护栏是运行时硬约束（主 spec 6.1）。
