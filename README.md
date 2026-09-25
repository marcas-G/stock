# stock —— 策略挖掘工作区（单仓单树 + 研究产物区）

一个仓库、三棵树、一份本地数据；**研究产物区独立**（R37 Phase 2，2026-09-20）：因子/策略/
合成分 spec、档案、索引迁出主仓到 `quantresearch/`（env `QUANTRESEARCH_ROOT`，缺省
`/data/students/gaolei/quantresearch`）——主仓只留工具，产物区非 git 仓、按目录公约管理。
所有代码与文档都在版本控制内；**数据与运行产物只在本地**（`data/` 体量随灌入变化，权威见 [governance/workspace/data-map.md](governance/workspace/data-map.md)；2026-09-16 快照 ≈353G，gitignore）。

> **R24 顶层目录重整（方案 A，2026-09-16）**：文档与知识 → `knowledge/`、治理与证据 →
> `governance/`、运行产物 → `runs/`；代码树（`platform/`、`research/`）与 `data/` 零位移。
> 迁移台账（旧→新映射）：[governance/workspace/migration-r04.md](governance/workspace/migration-r04.md)。
> **R37 Phase 2（2026-09-20）**：研究产物迁出主仓（`QUANTRESEARCH_ROOT`）——本仓
> `research/factor`、`research/strategy`、`research/composites`、`knowledge/dossiers`、
> `knowledge/index` 不再存在；目录公约 §7。[治理/证据](governance/evidence/verification/R37/migrate-products/)

```
stock/                     ← 仓库根（治理薄层，白名单 14 项）
├── platform/              平台：因子 DSL 计算引擎（factorlab）+ 数据生产线工具集（tools/，R27 起）
├── research/              研究工具（tools/：strategies、factor_lib）——研究产物在 quantresearch/
├── knowledge/             文档与知识唯一入口：契约（contracts/）、设计（design/）、手册（handbooks/）
├── governance/            治理与证据：门与脚本（ops/）、工作区约定（workspace/）、
│                          验证与评审（evidence/）
├── runs/                  【本地】运行产物（runs/platform/<名>/，不入库）
├── data/                  【本地】事实库与原始数据（不入库；体量与清单见 data-map）
└── _archive/              【本地】30 天归档区（不入库）

quantresearch/             ← 研究产物区（env QUANTRESEARCH_ROOT；非 git 仓，公约见其 CONVENTIONS.md）
├── factor/                因子 spec（原 research/factor/）
├── strategy/              策略 spec（原 research/strategy/）
├── composites/            合成分 spec + 实现（原 research/composites/）
├── dossiers/              研究档案（原 knowledge/dossiers/）
└── index/                 机器索引（原 knowledge/index/；自动生成，禁手改）
```

## 我该从哪里开始

| 我想… | 去哪 |
|---|---|
| 写一个因子并跑出结果 | [knowledge/handbooks/factor-authoring-manual.md](knowledge/handbooks/factor-authoring-manual.md)（单页闭环：写→自检→跑→读数→入库） |
| 看数据放在哪、谁生产它 | [governance/workspace/data-map.md](governance/workspace/data-map.md)（数据资产唯一权威） |
| 看目录/命名约定 | [governance/workspace/directory-conventions.md](governance/workspace/directory-conventions.md)（结构唯一权威） |
| 改平台代码（引擎/DSL/评估） | `platform/src/factorlab/` + [knowledge/contracts/interface.md](knowledge/contracts/interface.md) |
| 用数据生产线工具（LOB/灌库/转换/下载） | `platform/tools/` + 各工具 README（策略工具仍在 `research/tools/`） |
| 看平台设计与里程碑 | `knowledge/design/platform/`（spec 唯一副本） |
| 看研究档案（因子/策略） | 研究产物区 `$QUANTRESEARCH_ROOT/dossiers/{factors,strategies}/` |
| 看某轮工作的验证证据 | [governance/evidence/verification/](governance/evidence/verification/) |
| 看评审台账（报告/findings） | [governance/evidence/reviews/](governance/evidence/reviews/) |
| 知道还有什么没做 | [governance/workspace/pending-items.md](governance/workspace/pending-items.md) |

## 跑因子（最短路径）

> 完整闭环（含入库/提交/常见坑）：[knowledge/handbooks/factor-authoring-manual.md](knowledge/handbooks/factor-authoring-manual.md)

```bash
cd platform                                   # 平台 venv 在这里
QR=${QUANTRESEARCH_ROOT:-/data/students/gaolei/quantresearch}     # 研究产物区根（R37）
.venv/bin/factorlab lint   "$QR/factor/<族>/<名>.yaml"            # 秒级语法/白名单门
FACTORLAB_DATA_BACKEND=ch .venv/bin/factorlab run "$QR/factor/<族>/<名>.yaml"
.venv/bin/factorlab show <名>                                     # IC/分层/换手完整档案
```

产物落仓库根 `runs/platform/<名>/`（R24 起默认 = `settings.results_dir`，从包位置派生、与 cwd 无关；`FACTORLAB_RESULTS_DIR` 可覆盖；本地，不入库）。库级分析：`list` / `corr` / `svd` / `resic` / `serve`。

## 跑测试与门

```bash
make test-platform        # R43 @ a48ff57 快照：4128 passed / 17 skipped；非当前未提交工作树的验证结果
make test-research        # R43 @ a48ff57 快照：platform tools 920、research tools 184、governance ops 219 passed
make gates                # 结构/契约/标记/旧路径/索引/台账口径 全套常驻门（governance/ops/gates.sh）
```

## 环境事实（容易踩的坑）

- **单解释器（2026-09-16 R27 起）**：平台与全部工具/研究测试统一用 `platform/.venv`（Python 3.13，uv 管理）；
  `emb`（3.11）已退役为工具解释器（外部 env 保留，非工具依赖）。
- 工具引用平台经 `_env.py` 落位断言（解析到别处即 RuntimeError）——防装成别的副本/误挂 PYTHONPATH。
- **当前生产读路径是 ClickHouse**（`FACTORLAB_DATA_BACKEND=ch`）：平台 duckdb 库不存在（teajoin token 过期，见 [governance/workspace/pending-items.md](governance/workspace/pending-items.md) #1）。
- **`data/` 零改动**：任何操作都不得写入 `data/`；只读消费。
- **重任务内存护栏**：全市场/分钟链 `factorlab run` 前设 `FACTORLAB_MAX_MEMORY=8GB`（显式设置即启用进程看门狗 + RLIMIT_AS 硬上限）——见 [knowledge/contracts/interface.md](knowledge/contracts/interface.md) §1「进程内存护栏」与 [AGENTS.md](AGENTS.md)「重任务运行协议」。
- `projects/` 是历史遗留：合并前的两个旧克隆已于 2026-09-15 删除（R17）；`quant_core_shim` 收编为 `platform/kernels/quant_core/`（R18）后，评估内核已于 2026-09-17（R30 Task 15/D12）并入 **`platform/src/factorlab/core/eval/kernel.py`**（独立 dist 删除）；`ashare_alpha3` 已收编完成（数据侧 R19 → `platform/tools/ashare_ingest/`，股票池段 R20 → `platform/tools/universe_stages/`），原 `projects/` 目录已于 R24 Task 11 归档至 `_archive/2026-09-16-ashare-alpha3/`（到期 2026-10-16）。**新东西一律进三棵树。**

## 文档地图

| 目录 | 内容 |
|---|---|
| `knowledge/` | **文档与知识唯一入口**：契约（contracts/）、设计（design/）、手册（handbooks/） |
| `governance/` | **治理与证据**：门与脚本（ops/）、工作区约定、文档目录与台账（workspace/）、验证与评审（evidence/） |
| `platform/`、`research/` | 代码树（README/CLAUDE 各自环境事实）；R24 后 `platform/docs`、`research/docs` 仅留指针壳（D2） |

全库文档分类清单与审计快照见
[`governance/workspace/document-catalog.md`](governance/workspace/document-catalog.md)；
发现文档问题时先提交 GitHub Issue，并在修复后更新目录。
