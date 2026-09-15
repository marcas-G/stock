# 验证证据区（docs/verification）

**政策**：**当前战役**的证据放顶层 `R*/`；战役完结后整目录 `git mv` 进
`archive/<YYYY-MM>-<战役名>/`（字节不变、历史保留）。每份证据含：命令、原始输出、门结果、
以及"与计划的偏差"（若有）。

## 当前战役：单仓单树重构（2026-09-15）

| 目录 | 内容 |
|---|---|
| `R0/` | 基线冻结（三仓 HEAD、2486+13 / 183+24、check-day、金样 pins、**数据接口基线**、bundle 备份） |
| `R1/` | 三段历史合并（纯移动门 754 文件 0 丢失；blob 差异逐条符合政策） |
| `R2/` | 三树搬迁（platform/ research/ docs/）+ 平台分层收口 + **运行时产物迁移补记** |
| `R4/` | **数据接口收口**（读侧对照 11/11 逐字节、契约单点真数据对照、写侧单点与 state 迁移） |
| `R5/` | 因子库分族 + 索引（含"5 组重复公式 0 个真重复"的实测结论） |
| `R6/` | 文档收口（5 类矛盾单点化、旧路径清零、验证流水归档） |
| `R7/` | 收口推送 + P8 判定（远端默认分支 `restructure/monorepo`；旧三支保留 30 天） |
| `R8/` | **死代码/冗余清理**（平台包归位+死码、研究侧写侧收敛、3 个无测试工具补冒烟、AST 数据接口门） |
| `R9/` | **未竟项收口**（G-READ AST 转强制、MonthWriter 并入 writekit、调仓成本建模、P4-batch_flock 平台实现；R8c 漏接线抓回） |
| `R10/` | **收口后半**（调仓成本 spec 级接线；两份编排样板切到 BatchFlock 并扩 5 个契约缝，真数据内容逐值等价） |
| `R11/` | **解耦与可复用**（G-TOPO 拓扑门抓出并拆掉三处耦合；月分片写入骨架 `lib/monthflow` 单点化） |

> 计划里的 R3（研究侧结构）与 R4（数据接口）在执行中合并为 R4 一批完成（两者动的是同一批文件）。

## 归档战役

| 目录 | 战役 | 当时的入口文档 |
|---|---|---|
| `archive/2026-09-workspace-cleanup/` | 工作区清理（S1–S5 + final） | `docs/workspace-p0p8.md`（已冻结） |
| `archive/2026-09-mining-refactor/` | 策略挖掘系统深度重构（WS0–WS7 + POST-WS8） | `platform/docs/superpowers/specs/2026-09-12-mining-system-refactor-design.md` |

**旧路径映射**（历史文档里的指针按此解析——冻结文档不改写）：

| 历史引用 | 现在位置 |
|---|---|
| `docs/verification/S1…S5/`、`final/` | `docs/verification/archive/2026-09-workspace-cleanup/…` |
| `docs/verification/WS0…WS7/`、`POST-WS8/` | `docs/verification/archive/2026-09-mining-refactor/…` |
| `projects/quant-platform-main/docs/…` | `platform/docs/…` |
| `projects/quant-platform-research/tools/…` | `research/tools/…` |

**缺口补记（不补造）**：`archive/2026-09-mining-refactor/WS7/` 无 `status.md`（当时只落了
`7-platform-full.log`）；`WS8` 无独立目录（其证据在 `POST-WS8/` 与 `workspace-cleanup/final/`）。
