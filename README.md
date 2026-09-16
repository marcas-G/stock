# stock —— 策略挖掘工作区（单仓单树）

一个仓库、三棵树、一份本地数据。所有代码与文档都在版本控制内；**数据与运行产物只在本地**（`data/` 353G，gitignore）。

> **R24 顶层目录重整（方案 A，2026-09-16）**：文档与知识 → `knowledge/`、治理与证据 →
> `governance/`、运行产物 → `runs/`；代码树（`platform/`、`research/`）与 `data/` 零位移。
> 迁移台账（旧→新映射）：[governance/workspace/migration-r04.md](governance/workspace/migration-r04.md)。

```
stock/                     ← 仓库根（治理薄层，白名单定稿 15 项）
├── platform/              平台：因子 DSL 计算引擎（factorlab）+ 数据生产线工具集（tools/，R27 起）
├── research/              研究：因子库（factor/）、剩余工具（tools/：strategies、factor_lib）
├── knowledge/             文档与知识唯一入口：契约（contracts/）、设计（design/）、
│                          档案（dossiers/）、手册（handbooks/）、索引（index/）
├── governance/            治理与证据：门与脚本（ops/）、工作区约定（workspace/）、
│                          验证与评审（evidence/）
├── runs/                  【本地】运行产物（runs/platform/<名>/，不入库）
├── data/                  【本地】事实库与原始数据（353G，不入库）
├── projects/              【本地】过渡：ashare_alpha3（R24 Task 11 归档 → _archive/）
└── _archive/              【本地】30 天归档区（不入库）
```

## 我该从哪里开始

| 我想… | 去哪 |
|---|---|
| 写一个因子并跑出结果 | [knowledge/dossiers/factor-authoring-manual.md](knowledge/dossiers/factor-authoring-manual.md)（单页闭环：写→自检→跑→读数→入库） |
| 看数据放在哪、谁生产它 | [governance/workspace/data-map.md](governance/workspace/data-map.md)（数据资产唯一权威） |
| 看目录/命名约定 | [governance/workspace/directory-conventions.md](governance/workspace/directory-conventions.md)（结构唯一权威） |
| 改平台代码（引擎/DSL/评估） | `platform/src/factorlab/` + [knowledge/contracts/interface.md](knowledge/contracts/interface.md) |
| 用数据生产线工具（LOB/灌库/转换/下载） | `platform/tools/` + 各工具 README（策略工具仍在 `research/tools/`） |
| 看平台设计与里程碑 | `knowledge/design/platform/`（spec 唯一副本） |
| 看研究档案（因子/策略） | `knowledge/dossiers/factors/`、`knowledge/dossiers/strategies/` |
| 看某轮工作的验证证据 | [governance/evidence/verification/](governance/evidence/verification/) |
| 看评审台账（报告/findings） | [governance/evidence/reviews/](governance/evidence/reviews/) |
| 知道还有什么没做 | [governance/workspace/pending-items.md](governance/workspace/pending-items.md) |

## 跑因子（最短路径）

> 完整闭环（含入库/提交/常见坑）：[knowledge/dossiers/factor-authoring-manual.md](knowledge/dossiers/factor-authoring-manual.md)

```bash
cd platform                                   # 平台 venv 在这里
.venv/bin/factorlab lint   ../research/factor/<族>/<名>.yaml      # 秒级语法/白名单门
FACTORLAB_DATA_BACKEND=ch .venv/bin/factorlab run ../research/factor/<族>/<名>.yaml
FACTORLAB_RESULTS_DIR=results .venv/bin/factorlab show <名>       # IC/分层/换手完整档案
```

产物落 `platform/results/<名>/`（R24 Task 8 后默认 `runs/platform/<名>/`；本地，不入库）。库级分析：`list` / `corr` / `svd` / `resic` / `serve`。

## 跑测试与门

```bash
make test-platform        # 平台全量（约 13 分钟）
make test-research        # 工具/研究测试（单解释器：平台 venv 3.13）
make gates                # 结构/契约/标记/旧路径/索引 全套常驻门（governance/ops/gates.sh）
```

## 环境事实（容易踩的坑）

- **单解释器（2026-09-16 R27 起）**：平台与全部工具/研究测试统一用 `platform/.venv`（Python 3.13，uv 管理）；
  `emb`（3.11）已退役为工具解释器（外部 env 保留，非工具依赖）。
- 工具引用平台经 `_env.py` 落位断言（解析到别处即 RuntimeError）——防装成别的副本/误挂 PYTHONPATH。
- **当前生产读路径是 ClickHouse**（`FACTORLAB_DATA_BACKEND=ch`）：平台 duckdb 库不存在（teajoin token 过期，见 [governance/workspace/pending-items.md](governance/workspace/pending-items.md) #1）。
- **`data/` 零改动**：任何操作都不得写入 `data/`；只读消费。
- **重任务内存护栏**：全市场/分钟链 `factorlab run` 前设 `FACTORLAB_MAX_MEMORY=8GB`（显式设置即启用进程看门狗 + RLIMIT_AS 硬上限）——见 [knowledge/contracts/interface.md](knowledge/contracts/interface.md) §1「进程内存护栏」与 [AGENTS.md](AGENTS.md)「重任务运行协议」。
- `projects/` 是历史遗留：合并前的两个旧克隆已于 2026-09-15 删除（R17）；`quant_core_shim` 已收编为 **`platform/kernels/quant_core/`**（R18）；`ashare_alpha3` 已收编完成（数据侧 R19 → `platform/tools/ashare_ingest/`，股票池段 R20 → `platform/tools/universe_stages/`），旧目录仅作本地历史参考。**不要再往里放新东西**——新代码进三棵树。

## 文档地图

| 目录 | 内容 |
|---|---|
| `knowledge/` | **文档与知识唯一入口**：契约（contracts/）、设计（design/）、因子/策略档案（dossiers/）、手册（handbooks/）、索引（index/） |
| `governance/` | **治理与证据**：门与脚本（ops/）、工作区约定与台账（workspace/）、验证与评审（evidence/） |
| `platform/`、`research/` | 代码树（README/CLAUDE 各自环境事实）；R24 后 `platform/docs`、`research/docs` 仅留指针壳（D2） |
