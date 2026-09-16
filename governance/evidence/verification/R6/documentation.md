# R6 文档收口（2026-09-15）

## 5 类矛盾的处置（逐条）

| # | 矛盾（R0 盘点证据） | 处置 |
|---|---|---|
| **a** | 归档批次表**双写**（`archive-policy.md:11-14` 与 `data-map.md:44-47`，且制度要求两处同步） | **事实单点 = `archive-policy.md` §归档批次**；`data-map.md` §D 改为**指针**（不再复制表） |
| **b** | 到期三决策点**双写**（`archive-policy.md:32-37` 与 `pending-items.md#6`） | 登记册单点 = `pending-items.md` #6；`archive-policy.md` 到期程序第 4 步改为**指针** |
| **c** | 远端改名事实四处散布且矛盾（旧名 `quant-platform`、过期 SHA `c1b43cb`） | 活文档（根 README/CLAUDE、research/platform README）统一写 **`marcas-G/stock`**；含旧名与旧 SHA 的三份文档（workspace-p0p8 / traceability-matrix / remote-cleanup-checklist）**加冻结横幅**（保留当时证据，不再当活文档读） |
| **d** | 数据布局**裁决权互斥**（两份文件各自宣称"赢过磁盘"） | **分层裁决**（两份文档头部均已写明）：`directory-conventions.md` = 结构与分类规则的唯一权威；`data-map.md` = 数据资产**实例**（谁生产谁消费、到期日）的唯一权威；**两者与磁盘冲突时以磁盘为准并修订文档** |
| **e** | 30 天 TTL 复述 4 处；磁盘"96% 占用"作为未决理由 | TTL **政策单点 = `archive-policy.md`**（正文声明"其余文档只引用不复述"），`directory-conventions.md` 改为指针；磁盘口径更新为**实测值 + 复核时点**（`data-map.md` A11、`pending-items.md` #3） |

## 旧路径清零（G-LEGACY：36 → 0）

重写的活文档：`research/README.md`（整篇，单树布局）、`research/CLAUDE.md`（整篇，目录分权）、
`docs/data-map.md`（A/B/C 段 11 处 + 维护规则分层）、`docs/directory-conventions.md`
（根白名单 12 项 / 三棵树 / 撤销 worktree 迁移程序）、`platform/README.md`、`platform/AGENTS.md`、
`.claude/skills/factor-mine/SKILL.md`（FLAB 路径）、`research/pyproject.toml` 注释、
`research/tools/lob_fact/w5_closure.sh`（硬编码 cd → `dirname $0`）、
`research/tools/strategies/tests/test_strategy.py`（docstring 命令）、`docs/pending-items.md` #5。

**冻结**（加横幅，不改写）：`workspace-p0p8.md`、`traceability-matrix.md`、`remote-cleanup-checklist.md`
（记录当时真实目录/克隆名/证据路径），并在 `docs/verification/README.md` 给出**旧路径映射表**供解析。

## 验证流水归档

`docs/verification/`：当前战役 `R0/R1/R2/R4/R5/R6` 留顶层；
`archive/2026-09-workspace-cleanup/{S1..S5,final}`、`archive/2026-09-mining-refactor/{WS0..WS7,POST-WS8}`
（`git mv`，字节不变）。新增 `docs/verification/README.md`：政策 + 映射 + **缺口补记**
（WS7 无 status.md、WS8 无独立目录——补记不补造）。
计划里的 R3 与 R4 在执行中合并（动的是同一批文件），已在 README 说明。

## 门

见 `docs/verification/R6/gates.log`（结构门全绿 + 两树测试）。
