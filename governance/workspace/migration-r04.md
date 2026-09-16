# R24 顶层目录重整（方案 A）迁移台账

**计划**：`governance/evidence/reviews/r04-efficiency-2026-09-16/structure-plan.md`（Tasks 0-12，方案 A）
**执行**：2026-09-16 · 分支 `restructure/monorepo`
**起始 HEAD**：`cbb81bca11c346372d0a38b5439d02851f0a5df1`（`cbb81bc`）

## 0. 冻结状态与偏差声明（重要）

计划 Task 0.1 要求"停挖矿与停止并发提交、`git status --porcelain` 为空"。
**实际无法满足**：本迁移窗口内挖矿循环仍在写 `research/factor/**`、`research/docs/factors/**`、
`research/tools/factor_lib/gen_minute_pool.py`（在途），评审仍在写 `governance/evidence/reviews/**`。
经用户明确授权（2026-09-16），采取替代纪律执行：

1. 备份照做（tag + bundle，见 §2）；
2. 只精确 `git add` 迁移相关文件，绝不 `git add -A`；每提交前 `git status` 检查；
3. Task 4（research/docs）与 Task 6（governance/evidence/reviews）动手前后做 mtime 快照比对；
   若检测到并发修改：不回滚，如实记录冲突；
4. `data/` 零写：Task 0/12 前后 `du -sh` + 全文件 mtime/size 清单 sha256 比对；
5. 挖矿在途导致的门红（如 G-ANNOTATE、G-INDEX 漂移）如实归因，不改挖矿文件。

起始 `git status --porcelain` 非空快照：`governance/evidence/verification/R24/00-baseline/status-before.txt`（18 项，均为挖矿在途）。

## 1. 基线与实测对照（2026-09-16）

计划基线数字（≥2969/13、T2 268/10、T1 116）已于 R27/R28 后过时，以本窗口实测为准：

| 项 | 实测 | 证据 | 归因 |
|---|---|---|---|
| 平台全量 | **3150 passed / 13 skipped**（818.98s） | `governance/evidence/verification/R24/00-baseline/platform-pytest.txt` | 用户给定对照 |
| platform/tools | **337 passed** | `governance/evidence/verification/R24/00-baseline/test-research.txt` | 全绿 |
| research/tools | **2 failed / 58 passed** | 同上 | **挖矿并发漂移**：新 yaml 使索引 164 vs 163；`reversal_20d/cumret_freq.md` 档案缺失（非 R24 引入） |
| `make gates` | 红 1 项：G-ANNOTATE 缺 snapshot 4 份 | `governance/evidence/verification/R24/00-baseline/gates.txt` | **挖矿在途**：`liquidity/level_ma20.md` 等 4 份在写档案；G-LINT 163/163 绿 |
| `build_index --check` | 红（doc 陈旧，同索引漂移） | `governance/evidence/verification/R24/00-baseline/build_index-check.txt` | 挖矿并发 |
| data/ 容量 | **353G，78120 文件** | `governance/evidence/verification/R24/00-baseline/data-du.txt` + `data-manifest-before.{txt,sha256}` | 零写对照基线 |

旧路径全库引用清单（471 行，15 组）：`governance/evidence/verification/R24/00-baseline/old-path-inventory.txt`。

## 2. 备份

- tag：`pre-r04-restructure` → `cbb81bca11c346372d0a38b5439d02851f0a5df1`
- bundle：`_archive/backups/pre-r04-restructure-2026-09-16-root.bundle`（`git bundle create --all`；`git bundle verify` 通过："完整历史"）
- 单仓单树 → 单枚 root bundle 即可（R0 两枚分仓策略仅适用于分仓时代）。

## 3. 旧→新映射总表

| # | 旧路径 | 新路径 | Task | 状态 |
|---|---|---|---|---|
| 1 | `platform/docs/{interface,catalog,data-ops-playbook,teajoin-guide}.md` | `knowledge/contracts/` | 2 | ✅ `e3d2887` |
| 2 | `platform/docs/superpowers/{plans,specs}` | `knowledge/design/platform/` | 3 | ✅ `305b757` |
| 3 | `research/docs/superpowers/{plans,specs}` | `knowledge/design/research/` | 3 | ✅ `305b757` |
| 4 | `research/docs/{factors,strategies,factor-mining-playbook.md}` | `knowledge/dossiers/` | 4 | ✅ `6aaefad` |
| 5 | `docs/reviews/2026-09-15-{open-operators,minute-execution}/`、`2026-09-16-strategy-decomposition/`（计划补遗） | `knowledge/design/workspace/` | 3 | ✅ `305b757` |
| 6 | `docs/{data-map,directory-conventions,pending-items,archive-policy,traceability-matrix,remote-cleanup-checklist,workspace-p0p8}.md` | `governance/workspace/` | 5 | ✅ `f238ea5` |
| 7 | `docs/递归式需求驱动系统工程开发手册：NASA Systems Engineering × V-Model.md` | `knowledge/handbooks/` | 4 | ✅ `6aaefad` |
| 8 | `docs/index/{factors,strategies}.md`（strategies 为计划补遗） | `knowledge/index/` | 4 | ✅ `6aaefad` |
| 9 | `docs/verification/` | `governance/evidence/verification/` | 6 | ✅ `583217f` |
| 10 | `docs/reviews/`（余下） | `governance/evidence/reviews/` | 6 | ✅ `583217f` |
| 11 | `scripts/*` | `governance/ops/` | 7 | ✅ `227acd9` |
| 12 | `platform/results/` | `runs/platform/` | 8 | ✅ `TBD-T8` |
| 13 | 两树 `AGENTS.md` 并入根；`CLAUDE.md` 薄化；`platform/docs`、`research/docs` 留壳 | — | 10 | ☐ |
| 14 | `projects/ashare_alpha3` | `_archive/2026-09-16-ashare-alpha3/` | 11 | ✅ `TBD-T11` |

## 3b. 并发冲突记录（如实标注，不回滚）

| # | Task | 文件 | 事实 | 处置 |
|---|---|---|---|---|
| C1 | 4 | `knowledge/dossiers/factors/vol_run_energy/symrun_r30.md`、`knowledge/dossiers/factors/volatility/max_effect_20d.md` | 挖矿在途改写（mtime 2026-09-16 < Task 4 前）；`git mv` 会以**工作区内容**落 index，故两文件的挖矿在途内容随 6aaefad 的 rename 一并提交（相似度 92%/90%，非 100%） | 不回滚（回滚会丢弃挖矿在途编辑）；已在本表登记；后续 Task 6 对移动目录**不做 `git add -u`**，并把"移动文件 vs HEAD 内容差异清单"先落证据再提交 |
| C3 | 8 | 根 `results/`（非空：`_mine_round_*.md` + 因子产物，挖矿在途写中） | 计划假定『根 results/ 为空』不成立；强搬/删除会打断挖矿在途 run | 未动根 `results/`；`.gitignore` 保留 `results/` 过渡忽略；默认产物改 `runs/platform` 后新 run 不再落根 `results/`；挖矿停机后清理（未竟项） |
| C2 | 0 | `research/docs/factors/**` 等 18 项 | 冻结窗口内挖矿/评审持续写入（基线 `make test-research` 2 red、G-ANNOTATE 4 red） | 归因记录，未改挖矿文件 |

**Task 4 附注**：`research/docs/factor-authoring-manual.md`（untracked，挖矿/文档在途）经用户预期允许的目录搬迁行为物理移到 `knowledge/dossiers/factor-authoring-manual.md`，**保持 untracked，未随本批提交**；`knowledge/dossiers/factors/intraday/` 等挖矿 untracked 文件同样保持 untracked。`governance/evidence/verification/R21/EVID/annotate_factor_archives.py` 的 DOCS 硬编码路径 `research/docs/factors` → `knowledge/dossiers/factors`（修复前门退化为"0 份 ✓"永真，已恢复真实计数：5 份缺 snapshot，归因挖矿在途）。

## 4. 阶段记录

| Task | 提交 | 结果 | 证据 |
|---|---|---|---|
| 0 | `fdf000e` | 冻结偏差声明 + 基线 + 清单 | `governance/evidence/verification/R24/00-baseline/` |
| 1 | `b891639` | 骨架 + 根 README/公约定稿（白名单 15 项）+ `.gitignore` 放行 knowledge/governance | `governance/evidence/verification/R24/` |
| 2 | `e3d2887` | 契约迁 `knowledge/contracts/`；G-COPY 新判据；全库 84 处契约引用重指（含代码 docstring/错误文案） | `governance/evidence/verification/R24/02-contracts/` |
| 3 | `305b757` | 设计/计划迁 `knowledge/design/{platform,research,workspace}`（100% rename）；minute-execution/strategy-decomposition 引用重指 | `governance/evidence/verification/R24/03-design/` |
| 4 | `6aaefad` | 档案/索引/手册迁 `knowledge/dossiers|index|handbooks`；生成器输入输出切换；annotate 脚本路径修复；见冲突 C1 | `governance/evidence/verification/R24/04-dossiers/` |
| 5 | `f238ea5` | 治理文档迁 `governance/workspace/`；31 处引用重指；leftover grep = 0 | `governance/evidence/verification/R24/05-workspace/` |
| 6 | `583217f` | 证据双轨迁 `governance/evidence/{verification,reviews}`（782 文件 100% rename；mtime 前后逐一相等，无并发写）；96 处活引用重指；两个 README 加旧→新映射；`make gates` 全绿 | `governance/evidence/verification/R24/06-evidence/` |
| 6 | | | |
| 7 | `227acd9` | 门与检查脚本迁 `governance/ops/`；ROOT 推导 `../..`；`make gates`/reinstall_editable 新路径验证 | `governance/evidence/verification/R24/07-ops/` |
| 8 | `024e45f` | `platform/results/` → `runs/platform/`（2.6G mv）；`results_dir` 默认改包内派生绝对路径（与 cwd 无关）；短窗冒烟落 `runs/platform/r12_smoke` 且 `show` 可读；补 test_architecture 契约单点判据（Task 2 遗漏）；根 `results/`（挖矿在途）未清，见 C3 | `governance/evidence/verification/R24/08-runs/` |
| 9 | `75dc818` | 仓外 5 技能路径同步（26 处 → 0 残留）；diff 证据落盘；`factor-mine`（仓内）复检 0 残留 | `governance/evidence/verification/R24/09-skills/` |
| 10 | `898a4a1` | 两树 AGENTS 并入根；CLAUDE 薄化；docs 指针壳；白名单 15→14 待 Task 11 | `governance/evidence/verification/R24/10-agent-docs/` |
| 11 | 见下 | `projects/ashare_alpha3` → `_archive/2026-09-16-ashare-alpha3/`（manifest + 到期 2026-10-16）；根白名单收敛 14 项；相关文档同步 | `governance/evidence/verification/R24/11-archive/` |
| 12 | | | |
