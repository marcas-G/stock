# 验证证据区（governance/evidence/verification；R24 前 `docs/verification`）

> **R24 路径映射（2026-09-16）**：本目录 `docs/verification/` → `governance/evidence/verification/`；
> 评审台账 `docs/reviews/` → `governance/evidence/reviews/`。历史引用按本 README「旧路径映射」表解析。

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
| `R12/` | **平台侧 results 单点收口**（架构门列出 4 处绕过：correlation/web/cli 直读 + publish 非原子 → 全改经 `results_fs`/`panel_store`） |
| `R13/` | **原子写单点**（四份实现收成 `adapters/atomicio` + execution_store 补齐；抓回 R12 的产物权限 0600 回归） |
| `R14/` | **P-5 收口**（契约补 throttle/on_tick/pool_hook 三缝 + 派单改 FIFO；`run_lob_batch` 切换，真数据单日 **sha256 全等**；顺带修好该工具"直接跑不起来"） |
| `R15/` | **职责拆分**（`ch_ingest` 五职责→三模块+门面、`1m_features` 单体→三模块；第 5 个自建池切 P-5；真 CH 对账全库一致 + check-day `max\|Δ\|=0`） |
| `R16/` | **quark 传输层单点**（三个入口各自的 `http`/`get_stoken`/下载/cookie 收进 `quark_client`；逐字对照 + 行为清单留证） |
| `R17/` | **`projects/` 遗留克隆清理**（合并前两克隆删除；删前核验干净 + 历史/tag/bundle 三重覆盖；文档同步为"盘上=文档"） |
| `R18/` | **quant_core 内核包收编**（`projects/quant_core_shim` → `platform/kernels/quant_core`；跨枝取回契约文档与逐期对拍测试 + 勘误头块；删 emb 无消费者安装；G-VENV 增正/反向断言 + 负向自检） |
| `R19/` | **ashare 数据侧收编**（`ashare_alpha3` 的数据更新部分 → `research/tools/ashare_ingest/`；`12` → `ch_ingest/adj_backfill.py`；修 06 的 merge dtype 与 02 的未来日期两个真 bug；05 新旧 JSON 逐字节相同；G-TOPO 抓出 `config.py` 撞名 → 改 `datapaths.py`） |
| `R20/` | **ashare 股票池段收编**（`ashare_alpha3` 的 layer1-3 + 10/11/20/30/40 + references/tests → `research/tools/universe_stages/`；G-TOPO 抓出 `datapaths.py` 同名撞名 → 改 `universe_paths.py`；补第三层脚本；T2/T1 245/59；全门绿） |
| `R27/` | **工具迁移 + 单解释器化**（8 项数据生产线工具 + `lib/` + `_env.py`：`research/tools/` → `platform/tools/`；平台 venv 单解释器，`emb` 退役；G-TOPO/G-CONTRACT/G-READ 双树判据 + G-COPY 归位例外；convert_tick 内容逐值等价 + reconcile 全库一致；冻结条件被挖矿在途削弱，逐条偏差见 `R27/README.md`） |

> 计划里的 R3（研究侧结构）与 R4（数据接口）在执行中合并为 R4 一批完成（两者动的是同一批文件）。

## 归档战役

| 目录 | 战役 | 当时的入口文档 |
|---|---|---|
| `archive/2026-09-workspace-cleanup/` | 工作区清理（S1–S5 + final） | `docs/workspace-p0p8.md`（已冻结） |
| `archive/2026-09-mining-refactor/` | 策略挖掘系统深度重构（WS0–WS7 + POST-WS8） | `knowledge/design/platform/specs/2026-09-12-mining-system-refactor-design.md` |

**旧路径映射**（历史文档里的指针按此解析——冻结文档不改写）：

| 历史引用 | 现在位置 |
|---|---|
| `docs/verification/S1…S5/`、`final/` | `governance/evidence/verification/archive/2026-09-workspace-cleanup/…` |
| `docs/verification/WS0…WS7/`、`POST-WS8/` | `governance/evidence/verification/archive/2026-09-mining-refactor/…` |
| `projects/quant-platform-main/docs/…` | `knowledge/…`（R24 后；此前为 `platform/docs/…`） |
| `projects/quant-platform-research/tools/…` | `research/tools/…`（R27 前的中间位置；见下一行） |
| `research/tools/<工具>/…`（converters/quark_download/ch_ingest/ashare_ingest/universe_stages/1m_features/lob_fact/lib） | `platform/tools/<工具>/…`（R27 工具归位；`strategies/`、`factor_lib/` 仍留 `research/tools/`） |

**缺口补记（不补造）**：`archive/2026-09-mining-refactor/WS7/` 无 `status.md`（当时只落了
`7-platform-full.log`）；`WS8` 无独立目录（其证据在 `POST-WS8/` 与 `workspace-cleanup/final/`）。
