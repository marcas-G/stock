# 追踪矩阵（traceability-matrix）

需求 → 逻辑功能 → 架构元素 → 阶段任务 → 验证方法 → 证据路径 → 状态。
任何人问"为什么有这个目录/这个文件"可向上追；问"怎么证明做到了"可向下追。

状态图例：● 已验证 / ◐ 进行中 / ○ 待验证

| REQ | 声明（简） | LF | 架构元素 | 阶段任务 | 验证方法 | 证据 | 状态 |
|---|---|---|---|---|---|---|---|
| REQ-WS-001 | 根收敛 7 项 | LF-05 | `/`（根） | S1 归档、S2 归并、S3 项目归并、S4 文档 | Inspection | `S4/01-gates.log`、`final/01-gates.log` §1 | ● |
| REQ-WS-002 | 先归档后删除 + manifest | LF-03/04/10 | `_archive/` | S1 | Inspection + Demonstration | `S1/manifest.md`、`S1/01-archive.log` | ● |
| REQ-WS-003 | 直删仅限可证明垃圾 | LF-02/03 | `_archive/` 政策 | S1 | Analysis | `S1/02-delete-gates.log`（lsof/模式/空目录） | ● |
| REQ-WS-004 | data-map 无孤儿 | LF-01/08 | `docs/data-map.md` | S2 血缘 + S4 成文 | Inspection | `S2/02-post-verify.log`、`docs/data-map.md`（A9 待考已登记 pending #9） | ● |
| REQ-WS-005 | 活跃旧路径死链 = 0 | LF-06 | 各 config 单点 | S3 | Test（grep 门） | `S3/05-verify.log`（=0）、`S3/tasklist.md` | ● |
| REQ-WS-006 | 三链新路径可跑 | LF-06/09 | projects/* | S3 冒烟、S5 全量 | Demonstration | `S3/06-smoke-and-shim.log`、`S5/05-pytest-after.log`（2423 passed）、`S5/06-ch-reconcile.log`（全库一致） | ● |
| REQ-WS-007 | git 实体唯一 + 4 提交保全 | LF-07 | `projects/quant-platform-main` | S3 抢救 | Test | `S3/07-backup-rescue.log`（cat-file=e66b349） | ● |
| REQ-WS-008 | 根仓库只跟踪文档白名单 | LF-08 | `/.gitignore` | S4 | Inspection | `S4/01-gates.log`（白名单外=0）、`final/01-gates.log` §2 | ● |
| REQ-WS-009 | 回收 ≥33G | LF-05 | `data/` | S1 | Test | `S1/03-delete-verify.log`（+35.26G） | ● |
| REQ-WS-010 | 每阶段证据目录 | LF-09 | `docs/verification/` | S1-S5 | Inspection | 各 `verification/S*/` 非空 | ● |
| DER-001 | worktree 迁移程序（无 repair） | LF-07 | research/.git 指针 | S3 | Test | `S3/01-pointer-snapshot.log`、`S3/02-worktree-move.log` | ● |
| DER-002 | minutes×bars_1m 血缘对照 | LF-01 | `data/raw/minutes`↔`data/fact/bars_1m` | S2 | Analysis | `S2/der2_*_months.txt`（80=80 一致） | ● |
| DER-003 | panel_* 随 lob_fact 不拆 | LF-05 | `data/fact/lob_fact/panel_*` | S2 | Inspection | `S2/01-move.log`（整体 rename） | ● |
| DER-004 | validation 为 calib 资产不归档 | LF-02 | `data/calib/validation` | S2 + S3 重指 | Test | `S3/05-verify.log`（converter 路径存在） | ● |
| DER-005 | ch_ingest 入库前 diff | LF-07 | `tools/ch_ingest`（research） | S3 | Analysis | Plan 期两副本 diff（stock 版权威）+ research 提交 69be9f3 | ● |

## 递归子树（本层不做，登记待办）

| 子树 | 为什么需要下一层 | 登记位置 |
|---|---|---|
| `projects/quant-platform-main`（+research）仓库内部 | src 分层、results/ 口径、duckdb 数据链重建、两分支 docs 重叠仍有架构不确定性 | `pending-items.md` #5 |
| `projects/ashare_alpha3` 内部 | layer1-3 管线、fundamentals 缺源、golden 链路待考 | `pending-items.md` #5/#9 |

## 变更程序

1. 新增需求/元素 → 先在本表加行（含 LF 与验证方法），再实施。
2. 实施完成 → 把证据路径填实、状态翻 ●。
3. 需求作废 → 行标记删除线 + 原因，不删行（保留追溯）。
