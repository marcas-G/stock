# 工作区 P0-P8（System-of-Interest = `/data/students/gaolei/stock`）

按《递归式需求驱动系统工程开发手册》（NASA SE × V-Model）对工作区本身做的系统工程分解。
方法论：P0-P4 递归定义（本文件）→ 阶段执行 S1-S5（P5/P6）→ 逐层验证（P7）→ 使命判定（P8）。
五条纪律贯穿：No Leap / No Orphan / Single Primary Ownership / No Hidden Design /
Evidence over Claim。

层级声明：本文件只覆盖 workspace 层。`quant-platform` 仓库内部与 `ashare_alpha3` 内部
属递归子树（各自需重新跑 P0-P4），登记于 `pending-items.md`。

## P0 — Problem Definition

**P0-Q1 谁在什么情况下遇到什么核心问题**

研究者 gaolei 在个人量化研究工作区（392G、26 个顶层条目、跨 2026-07 至 09 的多次数据战役）
需要可靠地定位、维护、演进数据资产与代码项目，但由于多战役产物平铺堆积（6 个并排大数据
目录）、双 worktree 之外另有对象库分叉的孤立克隆、绝对路径三轨并存、33G 可证明垃圾，
目前无法可靠地：①判定任一数据的唯一权威副本；②安全移动或删除任何大目录而不破坏工具链；
③回答"哪个仓库是主仓库、哪些提交会丢失"。

**P0-Q2 实质后果**

- 392G 中 33G 是可证明垃圾（duckdb 临时文件）+ 约 5G 重复快照/旧副本（旧 daily 快照 368M、
  备份克隆 975M、验证 staging 等）；
- 孤立克隆 `quant-platform-main.local-backup-20260903` 含 4 个主仓库没有的提交
  （Phase 4/6 里程碑、ingest 校准经验）——照现状删除即永久丢失；
- 文档与实况脱节：README 停留 M1、CLAUDE.md 指错 worktree 位置、AGENTS.md 引用不存在的
  框架、results/ 条款与 .gitignore 自相矛盾；
- 三轨绝对路径（lob_fact config / ch_ingest / ashare_alpha3）使"改一处路径"成为隐性断链
  风险，且 60 处硬编码路径散落于无版本控制的脚本。

**P0-Q3 为什么现有方式无法充分解决**

现有手段是"记住路径 + 硬编码绝对路径"，但目录会迁移、工作区会扩张，记忆与硬编码在目录
变更时全部失效；且 git 之外的资产（数据、脚本、shim）无任何版本与文档承载。

**P0-Q4 什么现实结果意味着问题得到解决**

研究者打开 stock 根看到 ≤7 个约定条目；任一数据资产在 data-map 中可定位，且其生产/消费
链路在新路径下实测可用（平台测试可跑、lob_fact 读路径通、CH 链路通）；全工作区 grep 无
活跃旧路径死链；磁盘可证明回收 ≥33G；git 实体唯一（主仓库对象库含全部 4 个抢救提交）。

**P0-Q5 范围**

- In Scope：目录归并（data/ + projects/）、归档式清理、路径重指、worktree 迁移与指针保全、
  备份克隆提交抢救、根 workspace 仓库与文档体系、仓库内文档修复、远端清理**清单**。
- Out of Scope（登记 `pending-items.md`）：platform 仓库内部重构、factorlab.duckdb 重建、
  panel 批量产出、7z 解包、远端 push/分支删除执行、archived 到期清理、ashare_alpha3 内部
  重构、quant_core Rust 内核。

## P1 — Stakeholder & Operational Context

- **Stakeholders**：gaolei（研究者，唯一人类干系人；关切：数据不丢、链路不断、根目录可读）；
  未来 Claude Code 协作代理（文档消费者；关切：文档契约准确）；外部系统（关切：接口契约不变）。
- **SoI 边界**：`/data/students/gaolei/stock` 内全部资产（数据、代码、文档、归档区、根仓库）。
- **外部实体**：ClickHouse 127.0.0.1:8123（在线服务，不依赖工作区文件路径）；teajoin.com
  API（token 2026-08-22 已过期，重建 duckdb 需先 redeem）；quark.cn 网盘；
  github.com/marcas-G/quant-platform；anaconda3 envs/emb。
- **正常运行场景**：研究者在新路径下运行 factorlab CLI、lob_fact 批任务、ch_ingest 灌库、
  ashare_alpha3 验证——全部读新路径。
- **关键异常场景**：①误归档重要物 → 30 天内可 mv 恢复；②worktree 指针损坏 → 有预存指针
  快照可按程序恢复；③阶段中断 → rename 原子性使目录状态要么旧要么新，阶段可重入。
- **运行环境约束**：16GB 内存无 swap（禁止并行大 IO；同盘 mv 为 rename 级秒操作）；
  Linux 5.4；git 2.17.1（无 worktree repair）；磁盘 /dev/sda5 96% 占用，**禁止一切复制/
  解包类操作**（2026-09-12 清理后余量 362G）。

## P2 — Requirements

| ID | 声明 | 验证方法 | 接受准则 | 状态 |
|---|---|---|---|---|
| REQ-WS-001 | 根收敛至 7 项（README/.gitignore/.git/docs/data/projects/_archive） | Inspection | ls -a 逐项一致 | ○ S4 后核 |
| REQ-WS-002 | 非可证明垃圾先入 _archive/，保留 30 天，每批 manifest（来源/原因/恢复/到期日） | Inspection+Demonstration | manifest 逐行核对 + 抽样恢复演练 | ● S1 |
| REQ-WS-003 | 直删仅限可证明垃圾，每项附证明与引用门输出 | Analysis | 删除清单每行含垃圾证明 | ● S1 |
| REQ-WS-004 | 每数据资产在 data-map.md 有唯一位置/唯一生产者/血缘/更新方式 | Inspection | 覆盖全部数据单元无孤儿 | ○ S4 |
| REQ-WS-005 | 活跃 py/yaml/sh 旧绝对路径死链 = 0（历史文档豁免另表） | Test | grep 门输出 = 0 | ● S3 |
| REQ-WS-006 | 三链新路径实测可跑：平台 pytest、lob_fact 读路径、CH 链路 | Demonstration | 命令输出存档且 PASS | ○ S5 全量 |
| REQ-WS-007 | git 实体唯一；备份克隆 4 提交抢救入 archive/ 命名空间后才归档 | Test | cat-file -t e66b349 = commit | ● S3 |
| REQ-WS-008 | 根仓库只跟踪文档白名单（git ls-files ⊆ 白名单） | Inspection | ls-files 逐行核对 | ○ S4 |
| REQ-WS-009 | 直删回收 ≥33G；重复快照离开活动区 | Test | du/df 前后对照存档 | ● S1（+35.26G） |
| REQ-WS-010 | 每阶段收尾有可核对证据目录（Evidence over Claim） | Inspection | 证据目录非空 | ● 各阶段 |

（● = 已验证，○ = 待收口）

## P3 — Logical Decomposition

| LF | 逻辑功能 | Satisfies |
|---|---|---|
| LF-01 | Inventory Assets — 清点资产位置/大小/格式/血缘 | REQ-WS-004/010 |
| LF-02 | Classify Assets — 分类判定（raw/fact/panel/calib/ref/项目/归档/垃圾） | REQ-WS-003 |
| LF-03 | Gate Before Remove — 删除/归档前验证门（引用 grep、lsof、血缘、schema 抽样） | REQ-WS-002/003/005 |
| LF-04 | Archive Before Delete — 先 mv 入 _archive/，仅垃圾直删 | REQ-WS-002 |
| LF-05 | Relocate on Same Volume — 同盘 rename 归并，禁止跨盘复制 | REQ-WS-009 |
| LF-06 | Re-point References — 路径重指（config 单点 + 散落常量 + grep 门） | REQ-WS-005/006 |
| LF-07 | Preserve Git Identity — worktree 指针保全 + 孤立提交抢救 | REQ-WS-007 |
| LF-08 | Author Documentation — 数据地图/公约/P0-P8/追踪矩阵 | REQ-WS-004/008 |
| LF-09 | Produce Evidence — 各阶段验证证据 + 收口端到端演示 | REQ-WS-010/006 |
| LF-10 | Support Rollback — 30 天归档恢复 + 指针快照恢复 + 阶段重入 | REQ-WS-002/007 |

**逻辑状态机**：
`NORMAL_OPERATION` →（S1..S5）→ `PENDING_POINTER_FIX`（S2 末至 S3 初：数据已移、路径未
重指的已知破损窗口；禁跑写入方）→ `ARCHIVE_AGING`（30 天计时）→ 回到 NORMAL。

**派生需求（P3-Q6）**：

- DER-001：因 git 2.17.1 无 worktree repair，主 worktree 移动必须走"先移 linked、再移
  main、手工编辑 .git 指针、验证门"程序，且执行前快照指针原文。
- DER-002：minutes/bars_1m 血缘重叠（80 月对 80 月一致），删除决策推迟到归档到期清理
  （本次仅生成对照证据）。
- DER-003：`factor_panel.py --out` 默认 = LOB_FACT_ROOT → panel_* 随 lob_fact 整体移动不拆。
- DER-004：`validation/date_shift_exceptions.csv` 被 converter 生产路径读取 → validation/
  是 calib 资产不可归档。
- DER-005：ch_ingest 无 git 承载且与备份副本漂移 → 入库前完成两副本 diff（stock 版权威）。

## P4 — Architecture（元素与唯一主责）

| 元素 | Exists To | Owns（唯一权威） | Does Not Own |
|---|---|---|---|
| `stock/.git`（根 workspace 仓库） | 工作区文档与公约的版本权威 | README、.gitignore、docs/ | 任何数据/代码（全部 ignore） |
| `data/` | 全部研究数据资产的物理落位权威 | 目录结构、data-map 对应关系 | 数据的生产逻辑（归各项目） |
| `projects/quant-platform-main`（main worktree） | 平台代码与平台文档的权威 | src/factorlab、tests、docs/interface、catalog、superpowers、README、pyproject | 研究内容（factor/、results/、tools/） |
| `projects/quant-platform-research`（research worktree） | 研究工具与因子档案的权威 | tools/（lob_fact、1m_features、ch_ingest、converters、quark_download）、factor/、docs/factors/、docs/strategies/ | 平台 src（共享对象库但分支内容隔离） |
| `projects/quant_core_shim` | 评估内核契约锚点（quant-core 唯一安装源） | pyproject、quant_core/__init__.py | Rust 内核本体（未来同包名替换） |
| `projects/ashare_alpha3` | A 股 alpha 验证项目 | 项目代码、config、universes/research/validation 子目录 | 被它消费的事实库 |
| `_archive/` | 30 天可逆缓冲（归档先于删除的实体） | 待过期移除物 + 每批 manifest | 活动资产（30 天后才删除） |
| 外部：CH、teajoin、quark、emb | 契约消费者/提供者 | 各自数据与凭据 | — |

**递归判断**：`quant-platform` 仓库内部（src 分层、duckdb 重建、results/ 口径、两分支
docs 重叠）与 `ashare_alpha3` 内部仍有架构不确定性 → 登记为递归子树待办。workspace 层
其余元素均满足 Leaf Criterion（责任/边界/接口/状态明确），直接进入 P5/P6（= S1-S5）。

## P5/P6 — Detailed Design 与 Implementation

即分阶段执行计划 S1-S5（每阶段：串行执行 → 验证门 → 证据存档 → 提交）：

- **S1** 垃圾直删 + 第一波归档 → 证据 `docs/verification/S1/`
- **S2** 数据归并 data/ → 证据 `docs/verification/S2/`
- **S3** 项目归并 projects/ + worktree 迁移 + 路径全量重指 → 证据 `docs/verification/S3/`
- **S4** 根 workspace 仓库 + 文档体系 → 证据 `docs/verification/S4/`
- **S5** 仓库内文档修复 + 收口验证 → 证据 `docs/verification/S5/`

## P7 — Verification（证据索引）

| 需求 | 证据 | 位置 |
|---|---|---|
| REQ-WS-002/003/009 | S1 门输出 + manifest + df 前后 | `docs/verification/S1/` |
| REQ-WS-004（输血） | 血缘对照（minutes 80=80 bars_1m）+ schema 抽样 | `docs/verification/S2/` |
| REQ-WS-005 | grep 门 = 0（活跃死链） | `docs/verification/S3/05-verify.log` |
| REQ-WS-006 | 冒烟（四根+读路径+zip）+ 183 tests | `docs/verification/S3/06-smoke-and-shim.log` |
| REQ-WS-007 | cat-file e66b349 + 4 提交 log | `docs/verification/S3/07-backup-rescue.log` |
| REQ-WS-001/008 | 收口核验（S4/S5） | `docs/verification/final/` |

## P8 — Validation（使命判定，收口时填写）

对照 P0-Q4 逐项：

- [ ] 根 ≤7 项可读
- [ ] data-map 覆盖全部数据单元
- [ ] 三链新路径实测 PASS
- [ ] grep 门活跃死链 = 0
- [ ] 回收 ≥33G（已验证 +35.26G）
- [ ] git 实体唯一且 4 提交保全（已验证）

结论：（待 S5 收口填写）
