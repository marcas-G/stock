# factorlab

个人因子计算平台（M1–M8 已交付）：spec.yaml 因子 DSL（`expr_codegen` + `polars_ta` 内核，
TS/CS/GP 分区 + 池公式 + 多输出）、分块计算、日频/分钟双链、Rust `quant_core` 周频评估、
分层回测、Web 可视化、M7 策略组合、M8 执行运行时、PIT 正确性收口、读路径双后端
（DuckDB 平台库 | ClickHouse）。

**权威文档**：`docs/interface.md`（CLI/Spec/DSL/Python API 全量）+ `docs/catalog.md`
（列/算子活目录）。本 README 只给入口。

## 快速开始

```bash
# 环境：本工作树自带 uv 管理的 venv（Python 3.13）
.venv/bin/python -m pip --version 2>/dev/null || export PATH=/home/gaolei/.local/bin:$PATH
uv pip install --python .venv/bin/python -e . --no-deps      # editable 重装（换工作树后需重跑）
uv pip install --python .venv/bin/python -e ../quant_core_shim --no-deps   # 评估内核 shim

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
| `factorlab data rebuild\|refresh\|update\|verify` | 平台库（DuckDB）数据链 |
| `factorlab catalog dump\|docs` | 列/算子活目录（docs/catalog.md 同源生成） |

## 数据后端

- **duckdb**（默认）：平台库 `data/factorlab.duckdb`（相对 CWD；`FACTORLAB_PLATFORM_DB`
  可覆盖），由 `factorlab data rebuild/update/refresh` 维护（数据源 = teajoin Tushare 代理）。
- **ch**：`FACTORLAB_DATA_BACKEND=ch`（`FACTORLAB_CH_HOST/PORT/DATABASE`），ClickHouse
  事实库由工作区 `projects/quant-platform-research/tools/ch_ingest/` 灌入。

## 仓库纪律（摘要）

- `main` 只收平台改动（`src/factorlab/`、`tests/`、`docs/interface.md`、
  `docs/superpowers/`、`docs/data-ops-playbook.md`、`docs/teajoin-guide.md`、README、pyproject）；
  研究内容（`factor/`、`docs/factors/`、`tools/`、`docs/strategies/`）进 **`research` 分支**
  （同仓库的第二个 worktree：`../quant-platform-research`）。
- 任何代码改动遵循 TDD（先失败测试再加实现），提交前全量 `pytest -q` 通过；
  文档与实现同步（详见 `CLAUDE.md`）。
