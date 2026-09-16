# stock —— 策略挖掘工作区（单仓单树）

一个仓库、三棵树、一份本地数据。所有代码与文档都在版本控制内；**数据与运行产物只在本地**（`data/` 392G，gitignore）。

```
stock/                     ← 仓库根（= 远端 github.com/marcas-G/stock 的默认分支）
├── platform/              平台：因子 DSL 计算引擎（Python 包 factorlab）
├── research/              研究：因子库（factor/）、工具链（tools/）、研究档案（docs/）
├── docs/                  文档：工作区约定 + 数据地图 + 平台契约 + 研究档案 + 验证证据
├── data/                  【本地】事实库与原始数据（392G，不入库）
├── _archive/              【本地】30 天归档区（不入库）
└── projects/              【本地】ashare_alpha3 已收编完成，仅作历史参考（不入库，见下）
```

## 我该从哪里开始

| 我想… | 去哪 |
|---|---|
| 写一个因子并跑出结果 | `research/factor/<族>/<名>.yaml` + 命令见下「跑因子」 |
| 看数据放在哪、谁生产它 | [docs/data-map.md](docs/data-map.md)（数据资产唯一权威） |
| 看目录/命名约定 | [docs/directory-conventions.md](docs/directory-conventions.md)（结构唯一权威） |
| 改平台代码（引擎/DSL/评估） | `platform/src/factorlab/` + [platform/docs/interface.md](platform/docs/interface.md) |
| 用研究工具（LOB/灌库/转换/下载） | `research/tools/` + 各工具 README |
| 看平台设计与里程碑 | `platform/docs/superpowers/`（spec 唯一副本） |
| 看研究档案（因子/策略） | `research/docs/factors/`、`research/docs/strategies/` |
| 看某轮工作的验证证据 | [docs/verification/](docs/verification/) |
| 知道还有什么没做 | [docs/pending-items.md](docs/pending-items.md) |

## 跑因子（最短路径）

```bash
cd platform                                   # 平台 venv 在这里
.venv/bin/factorlab lint   ../research/factor/<族>/<名>.yaml      # 秒级语法/白名单门
FACTORLAB_DATA_BACKEND=ch .venv/bin/factorlab run ../research/factor/<族>/<名>.yaml
FACTORLAB_RESULTS_DIR=results .venv/bin/factorlab show <名>       # IC/分层/换手完整档案
```

产物落 `platform/results/<名>/`（本地，不入库）。库级分析：`list` / `corr` / `svd` / `resic` / `serve`。

## 跑测试与门

```bash
make test-platform        # 平台全量（约 7 分钟；基线 2570 passed / 13 skipped（R18 后））
make test-research        # 研究侧 T2（emb 3.11）+ T1（平台 venv）
make gates                # 结构/契约/标记/旧路径/索引 全套常驻门
```

## 环境事实（容易踩的坑）

- **两套解释器（刻意不统一）**：平台 = `platform/.venv`（Python 3.13，uv 管理）；研究 T2 = `emb`（3.11，`/data/students/gaolei/anaconda3/envs/emb`）。
  **T1 = 平台 venv**：`research/tools/strategies`、`1m_features`、**`ch_ingest`**（它模块级 import `clickhouse_connect`）。
- `emb` **装不了** `factorlab`（包要求 ≥3.13）——T2 靠 `research/tools/_env.py` 注入 `platform/src` 并做落位断言，这是唯一注入点。
- **当前生产读路径是 ClickHouse**（`FACTORLAB_DATA_BACKEND=ch`）：平台 duckdb 库不存在（teajoin token 过期，见 [docs/pending-items.md](docs/pending-items.md) #1）。
- **`data/` 零改动**：任何操作都不得写入 `data/`；只读消费。
- **重任务内存护栏**：全市场/分钟链 `factorlab run` 前设 `FACTORLAB_MAX_MEMORY=8GB`（显式设置即启用进程看门狗 + RLIMIT_AS 硬上限）——见 [platform/docs/interface.md](platform/docs/interface.md) §1「进程内存护栏」与 [AGENTS.md](AGENTS.md)「重任务运行协议」。
- `projects/` 是历史遗留：合并前的两个旧克隆已于 2026-09-15 删除（R17）；`quant_core_shim` 已收编为 **`platform/kernels/quant_core/`**（R18）；`ashare_alpha3` 已收编完成（数据侧 R19 → `research/tools/ashare_ingest/`，股票池段 R20 → `research/tools/universe_stages/`），旧目录仅作本地历史参考。**不要再往里放新东西**——新代码进三棵树。

## 文档地图

| 目录 | 内容 |
|---|---|
| `docs/`（根） | 工作区约定、数据地图、未决事项、追踪矩阵、手册、验证证据、因子索引 |
| `platform/docs/` | 平台契约（interface/catalog/data-ops-playbook/teajoin-guide）+ 设计与计划（superpowers/） |
| `research/docs/` | 因子档案（factors/，与 `research/factor/` 一一对应）、策略档案、挖因子 playbook |
