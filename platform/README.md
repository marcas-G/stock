# factorlab

个人因子计算平台（M1–M8 已交付）：spec.yaml 因子 DSL（`expr_codegen` + `polars_ta` 内核，
TS/CS/GP 分区 + 池公式 + 多输出）、分块计算、日频/分钟双链、Rust `quant_core` 周频评估、
分层回测、Web 可视化、M7 策略组合、M8 执行运行时、PIT 正确性收口、读路径双后端
（DuckDB 平台库 | ClickHouse）。

**权威文档**：`../knowledge/contracts/interface.md`（CLI/Spec/DSL/Python API 全量）+ `../knowledge/contracts/catalog.md`
（列/算子活目录）。本 README 只给入口。

## 快速开始

```bash
# 环境：本工作树自带 uv 管理的 venv（Python 3.13）
.venv/bin/python -m pip --version 2>/dev/null || export PATH=/home/gaolei/.local/bin:$PATH
# 两个 editable（factorlab + 评估内核 shim）一条命令重装并断言落位：
bash ../governance/ops/reinstall_editable.sh

# 测试
.venv/bin/python -m pytest tests/ -q
```

## CLI 一览

| 命令 | 作用 |
|---|---|
| `factorlab version` / `lint <spec.yaml>` | 版本 / spec 校验 |
| `factorlab run <spec.yaml>` | 跑因子：计算 → 评估 → 分层回测 → artifact（`--chunk-days` 分块） |
| `factorlab list` / `show <name>` | 因子清单 / 单因子详情 |
| `factorlab corr` / `svd` / `resic` | 相关性 / SVD / 横截面联合诊断 |
| `factorlab serve` | Web 可视化（FastAPI） |
| `factorlab op list\|doc\|add\|remove` | 算子注册表管理 |
| `factorlab catalog dump\|docs` | 列/算子活目录（`../knowledge/contracts/catalog.md` 同源生成） |

## 数据后端

- **ch（生产）**：`FACTORLAB_DATA_BACKEND=ch`（`FACTORLAB_CH_HOST/PORT/DATABASE`）。
  事实库更新链 = 夸克网盘（唯一外部源）→ `make data-update`
  （`platform/tools/pan_update/README.md`）→ 转换/灌入 `platform/tools/ch_ingest/`；
  语义详见 `../knowledge/contracts/interface.md` §8 与 `data-ops-playbook.md` §0。
- **duckdb（历史/测试）**：平台库 `data/factorlab.duckdb`（相对 CWD；
  `FACTORLAB_PLATFORM_DB` 可覆盖）仅为历史/测试只读库——旧写路径（teajoin 源）
  已退役（2026-09-17 Plan P T11），不再有维护命令。

## 仓库纪律（摘要，2026-09-15 单仓单树）

- **单仓单树**：本目录 = 平台树（`platform/`）；研究内容在兄弟目录 `../research/`，
  工作区文档/证据在 `../docs/`。**旧的双分支纪律（main=平台 / research=研究）已退役**——
  现在按**目录**分权，一次改动涉及多棵树时按目录分别提交；目录边界与提交前缀见
  [../CLAUDE.md](../CLAUDE.md) 与 [../AGENTS.md](../AGENTS.md)。
- **`tools/` 是数据生产线工具集**（R27 从 `research/tools/` 归位；转换/灌库/LOB/下载/池分层等），
  测试用平台 venv：`.venv/bin/python -m pytest tools -q`（或根目录 `make test-research`）。
- 任何代码改动遵循 TDD（先失败测试再加实现），提交前全量 `pytest -q` 通过；
  文档与实现同步（`../knowledge/contracts/interface.md` / `../knowledge/contracts/catalog.md`）。
