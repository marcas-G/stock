# stock 工作区项目指南（单仓单树，2026-09-15 起）

一个仓库、三棵树：`platform/`（平台代码）· `research/`（因子与工具）· `docs/`（文档）。
**旧的双分支纪律（main=平台 / research=研究）已退役**——现在按**目录**分权，不再按分支。

## 硬性要求

### 目录分权（取代旧分支纪律，最高优先级）

- **`platform/`** 只收平台改动：`platform/src/factorlab/`、`platform/tests/`、`platform/docs/`（契约 4 篇 + superpowers）、`platform/scripts/`。
  提交前缀用平台语义：`feat(engine)` / `fix(adapters)` / `docs(interface)` / `refactor(core)`。
- **`research/`** 只收研究内容：`research/factor/`、`research/tools/`（剩余：`strategies/`、`factor_lib/`）、`research/docs/`（factors/strategies/playbook）。
  提交前缀用研究语义：`feat(factor)` / `feat(tools)` / `docs(factors)`。
- **`docs/`**（根）只收工作区级文档与验证证据。
- 一次改动同时涉及多棵树 → **分目录分别提交**（一个提交只描述一棵树的改动）。
- `data/`、`_archive/`、`projects/`、`results/` **永不入库**（根 `.gitignore` 白名单 + 显式忽略三重保护）。

### 数据纪律（最高优先级）

- **`data/` 零改动**：任何命令都不得写入（只读消费）。`data/` 与 `_archive/` 的 mtime/容量基线在 `docs/verification/`。
- 数据位置与血缘以 `docs/data-map.md` 为唯一权威；目录/命名约定以 `docs/directory-conventions.md` 为唯一权威；
  两者冲突时**以磁盘为准并修订文档**。
- 数据接口（读/写/契约）见 `docs/verification/R4/`（收口记录）与 `knowledge/contracts/interface.md` §4。

### 文档和测试必须做好、写全面（最高优先级）

**测试**：
- TDD：先写失败测试再实现；覆盖正常/边界/错误路径；断言真实行为，不用 mock 糊弄。
- 依赖外部资源（CH / 本地事实库）的测试：环境缺失时 **skip 而非假通过**。
- 提交前跑对应测试：平台 `cd platform && .venv/bin/python -m pytest -q`（基线 **3099 passed / 13 skipped**，
  R23/R27 两次实测一致）；工具/研究 `make test-research`（= `platform/tools` **337** + `research/tools` **35**；
  **单解释器**（平台 venv 3.13），R27 实测，清单与迁移前 372 逐一相同）。
  （旧 T1/T2 两腿口径（emb + 平台 venv）已退役；`emb` 不再是工具依赖。）
- 工具拓扑门：`python scripts/check_tool_layering.py`（core 不反依赖 / 生产不带诊断 /
  工具不互相 import / lib 是叶子）+ `--selftest`。
- 数据接口门（AST）：`python scripts/check_dataiface.py`（ENFORCED：研究侧分区字面量 / 标记路径构造；
  REPORT：平台表名 / 研究侧直读）+ `--selftest`。
- 涉及数据窗口/分组/对齐语义的改动必须有能捕获跨资产泄漏、未来函数、错位的回归测试。

**文档**：
- 平台 API/CLI/DSL 变更 → `knowledge/contracts/interface.md`；设计与里程碑 → `knowledge/design/platform/{plans,specs}`。
- 因子新增/改名 → `research/docs/factors/<族>/<名>.md` 同名档案 + 重生成 `docs/index/factors.md`（有 byte-equality 门）。
- 文档与实现冲突时改文档；实现中发现的设计缺口写进对应 spec 或 `docs/pending-items.md`。

## 架构分层（平台包）

```
surfaces(cli,web) → app(装配) → ports(6 契约) → core(纯核)
                     ↑ adapters(I/O) → ports/core        config.py = L0 配置叶（谁都可读，core 禁）
```
- **`core/`**：纯计算（domain/ops/engine/eval/factor/process/execution/strategy/factio）。**不得** import
  duckdb/clickhouse_connect/requests，不得 import `factorlab.{config,ports,adapters,app,surfaces,artifacts,...}`，
  不得出现 `.read_parquet/.scan_parquet/.write_parquet/.glob`（`read_text` 载配置允许）。
  门 = `platform/tests/test_architecture.py`（静态 AST + 隔离运行双门）。
- **`ports/`**：六条 Protocol（零实现）；桩在 `platform/tests/_doubles.py`。
- **`adapters/`**：全部 I/O（`read/*`、`duckdb_read`、`ch_read`、`tick_read`、`parquet_artifacts`、`panel_store`、
  `results_fs`、`plugins`、`process_ops`…）。
- **`app/`**：装配（`bootstrap`、`run`、`evaluate`、`context`）——**唯一评估装配点**。
- **`surfaces/`**：CLI 与 Web（只做参数解析与呈现）。
- `knowledge/contracts/interface.md` §4 是模块路径权威，有 `platform/tests/test_doc_paths_exist.py` 自动核。

## 环境事实

- 平台 venv：`platform/.venv`（Python 3.13，uv）。移动目录后重装：
  `cd platform && uv pip install --python .venv/bin/python -e . --no-deps --no-build-isolation`。
- **单解释器**：全部工具/研究测试用 `platform/.venv`（Python 3.13，uv）；平台代码经 `_env.py`
  落位断言（解析到别处即 RuntimeError；防装成别的副本/误挂 PYTHONPATH）。
  `emb`（3.11）已退役为工具解释器（2026-09-16 R27；外部 env 保留，非工具依赖）。
- CH：`127.0.0.1:8123` db=factorlab。目标机 16GB 无页面文件 → 批算单进程 + 流式 + 及时释放。
- 评估内核 `quant_core`（shim）自 R18 起在 **`platform/kernels/quant_core/`**（内核发行物唯一声明点，
  仅装入 `platform/.venv`；emb 不装）。重装 + 落位断言：`bash scripts/reinstall_editable.sh`。
- `projects/ashare_alpha3` 已在 R19/R20 完成收编，仅作本地历史参考；**不要再往里放新东西**。
- lob_fact 校准常量（W1 冻结值 + `pins.sha256` 金样）**不可改**：改动即让 191 测试与历史结论失效。
