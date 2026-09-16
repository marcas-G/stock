# 顶层目录重整（方案 A）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal**：把"同一类东西三处"收敛为单点入口：文档与知识 → `knowledge/`、治理与证据 → `governance/`、运行产物 → `runs/`；代码树（platform/research）与 data/ 零位移。

**Architecture**：方案 A（R04 报告 §5 / 结构提案）——仅在**文档/证据/脚本/产物**层搬迁；每步先落"新路径存在 + 旧路径零活引用"断言，再 `git mv`；历史冻结文档正文不改，只加旧→新映射表。

**Tech Stack**：git（git mv 保留历史）、bash gates、pytest、Python 生成器（build_index / gen_op_catalog / annotate）。

**Spec**：`docs/reviews/r04-efficiency-2026-09-16/report.md` §5/§7（决策）
**用户已拍板**：D3 契约→`knowledge/contracts/`；D1 证据→`governance/evidence/`；D5 `projects/ashare_alpha3`→`_archive/`；快速项另行授权（见报告 §7）。
**待定**：D2（两树 `docs/` 留 3 行指针壳——本计划按"留壳"执行，若用户改判"删目录"则 Task 10 同步调整）；D4（方案 B 本计划不涉及）。

## Global Constraints（红线与纪律）

- **不动**：`platform/src`、`platform/tests`、`platform/kernels/quant_core`、`research/factor`、`data/`（零写）、历史冻结文档正文（spec/plan/verification/reviews 已写内容）。
  - ⚠️ **红线修订（2026-09-16）**：原"`research/tools/**` 不动"作废——8 项数据生产线工具 + `lib` 迁 `platform/tools/`、T1/T2 合并为单解释器，见同目录 `tools-migration-plan.md` 与 `tools-reorg-decisions.md`。本计划涉及这些文件的路径引用（如 `build_index.py`、`datapaths.py`）时，以迁移后路径为准；两计划**不同窗口执行**，避免同文件冲突（冲突点：`Makefile`、`factor_lib` 路径引用）。
- **单批 `git mv`**：保留文件历史；禁止 rm+add。搬迁前 `git status --porcelain` 必须为空（freeze 窗口）。
- **门先于搬迁**：每步先改门/测试中的路径判据或加映射豁免，再移动文件；每步结束跑指定验证。
- **备份**：Task 0 打 tag（`pre-r04-restructure`）+ 两枚 bundle 到 `_archive/backups/`（沿用 R0 先例）。
- **一次提交一棵树/一个主题**：提交信息 `<type>(scope): <做了什么>`；大批量搬迁按 Task 分次提交。
- **仓外同步是验收项**：`/data/students/gaolei/.claude/skills/factorlab-{dsl,data,ch-pipeline,backtest,evaluate}/` 引用旧路径，必须与 P1 同批更新。
- **基线**（Task 0 采集，作为验收对照）：`make gates` 全绿；平台全量 ≥2969 passed/13 skipped；研究 T2 268/10 + T1 116；`build_index --check` 一致；`data/` mtime/容量快照。

---

## 文件结构（目标态）

```
stock/                                  # 根 = 治理薄层（白名单定稿 15 项）
├── README.md CLAUDE.md AGENTS.md Makefile .gitignore .claude/
├── platform/                           # 引擎（src/tests/kernels 不动；仅留 README + scripts/gen_op_catalog）
├── research/                           # 研究（factor/tools 不动；仅留 README）
├── knowledge/                          # 【新】文档与知识唯一入口
│   ├── README.md                       # 文档地图（找文档唯一入口）
│   ├── contracts/                      # ← platform/docs/{interface,catalog,data-ops-playbook,teajoin-guide}.md
│   ├── design/{platform,research,workspace}/   # ← 三处 superpowers + reviews 设计
│   ├── dossiers/{factors,strategies,factor-mining-playbook.md}  # ← research/docs
│   ├── handbooks/                      # ← docs/递归式…手册.md
│   └── index/factors.md                # ← docs/index/factors.md（生成物）
├── governance/                         # 【新】治理与证据
│   ├── README.md
│   ├── ops/                            # ← scripts/
│   ├── workspace/                      # ← docs/{data-map,directory-conventions,pending-items,archive-policy,...}
│   └── evidence/{verification,reviews}/  # ← docs/verification + docs/reviews
├── runs/platform/                      # 【新，本地】← platform/results/；FACTORLAB_RESULTS_DIR 指向此
├── data/                               # 【本地】零写
├── projects/                           # 【本地】Task 11 归 _archive 后移除
└── _archive/                           # 【本地】过渡
```

---

### Task 0: 冻结、基线与搬迁清单（P0，0.5 日）

**Files:**
- Create: `governance/workspace/migration-r04.md`（旧→新映射 + 引用清单 + 阶段记录）
- Create: `_archive/backups/pre-r04-restructure-<date>.bundle`（两枚）

- [ ] **Step 0.1 冻结**：与用户/团队确认停挖矿与停止并发提交；`git status --porcelain` 为空；记录 `git rev-parse HEAD` 到 migration-r04.md。
- [ ] **Step 0.2 备份**：`git tag pre-r04-restructure`；`git bundle create _archive/backups/pre-r04-restructure-<date>-root.bundle --all`（或按 R0 两枚分仓策略），校验 bundle。
- [ ] **Step 0.3 基线采集**（写入 `docs/verification/R24/00-baseline/`）：
  ```bash
  make gates 2>&1 | tail -5
  cd platform && .venv/bin/python -m pytest -q 2>&1 | tail -3
  make test-research 2>&1 | tail -5
  python3 research/tools/factor_lib/build_index.py --check
  du -sh data/ | tail -1   # 可与 P1 后比对
  ```
- [ ] **Step 0.4 全库旧路径引用清单**：
  ```bash
  for p in "platform/docs/superpowers" "research/docs/superpowers" "research/docs/factors" \
           "docs/verification" "docs/reviews" "docs/index" "docs/data-map" "docs/directory-conventions" \
           "docs/pending-items" "docs/archive-policy" "scripts/"; do
    echo "== $p"; git grep -n "$p" -- ':!docs/verification' ':!docs/reviews' | head -50
  done
  ```
  输出整理进 `migration-r04.md`（精确到 file:line）。
- [ ] **Step 0.5 提交**：`git add governance/workspace/migration-r04.md docs/verification/R24` → `docs(workspace): R24 目录重整 P0 冻结/基线/引用清单`

### Task 1: 新骨架与根文档定稿（P1a，0.5 日）

**Files:**
- Create: `knowledge/README.md`、`governance/README.md`、`runs/README.md`（后二者可 Task 8/9 填内容）
- Modify: 根 `README.md`（树图补 `scripts/`→待迁、`runs/`、`knowledge/`、`governance/`）、`docs/directory-conventions.md`（白名单定稿 15 项；剔除幽灵 `docs/specs`；登记 `.claude/`、`scripts/`）

- [ ] **Step 1.1** 建目录：`mkdir -p knowledge/{contracts,design/{platform,research,workspace},dossiers,handbooks,index} governance/{ops,workspace,evidence/{verification,reviews}}`
- [ ] **Step 1.2** 写 `knowledge/README.md`：回答"我要找 X（契约/设计/档案/手册/索引）→ 去哪"；写 `governance/README.md`：治理/ops/证据三节。
- [ ] **Step 1.3** 根 README 与公约更新（此步只改文本，不动文件）。
- [ ] **Step 1.4 验证**：`git grep -n "docs/superpowers\|docs/specs" README.md docs/directory-conventions.md` → 无幽灵引用。
- [ ] **Step 1.5 提交**：`docs(workspace): R24 目录重整骨架与根文档定稿`

### Task 2: 契约搬迁（P1b）+ G-COPY 判据切换

**Files:**（映射 #1）
- Move: `platform/docs/{interface,catalog,data-ops-playbook,teajoin-guide}.md` → `knowledge/contracts/`
- Modify: `scripts/gates.sh`（G-COPY 单副本判据 :32-35；FROZEN :21 的幽灵 `:!docs/specs` 顺带删除）、`platform/tests/test_catalog.py:428`、`platform/tests/test_doc_paths_exist.py:16`、`platform/scripts/gen_op_catalog.py`（输出路径）、`platform/src/factorlab/adapters/catalog.py:683`（source_ref）、`platform/README.md`

- [ ] **Step 2.1** 先改门与测试的路径判据（旧目录→新目录），此时会红（文件未动）。
- [ ] **Step 2.2** `git mv platform/docs/interface.md platform/docs/catalog.md platform/docs/data-ops-playbook.md platform/docs/teajoin-guide.md knowledge/contracts/`
- [ ] **Step 2.3 验证**：`.venv/bin/python -m pytest tests/test_catalog.py tests/test_doc_paths_exist.py -q` 绿；`python platform/scripts/gen_op_catalog.py --check`；`bash scripts/gates.sh --structure` 绿。
- [ ] **Step 2.4 提交**：`refactor(docs): R24 契约迁 knowledge/contracts + G-COPY 判据切换`

### Task 3: 设计/计划搬迁（P1c）

**Files:**（映射 #2/#3/#5）
- Move: `platform/docs/superpowers/{plans,specs}` → `knowledge/design/platform/`；`research/docs/superpowers/*` → `knowledge/design/research/`；`docs/reviews/2026-09-15-{open-operators,minute-execution}/` → `knowledge/design/workspace/`
- Modify: `scripts/gates.sh`（FROZEN :21、G-LEGACY 豁免 :47）、`.claude/skills/factor-mine/SKILL.md:9`、`research/README.md:14`、`docs/reviews/README.md`（结构节）、`platform/tests/test_window_spec.py:3`（docstring）；历史引用加映射：`docs/verification/R22/open-operators-summary.md:3`

- [ ] **Step 3.1** 改门/引用 → 3.2 `git mv`（保持 `specs/`、`plans/` 子结构）→ 3.3 验证（`make gates`、`pytest tests/test_window_spec.py -q`、`.claude/skills/factor-mine` 引用 grep 零旧路径）→ 3.4 提交 `refactor(docs): R24 设计/计划单点化到 knowledge/design`

### Task 4: 档案/索引/手册搬迁（P1d）+ 生成器输入输出切换

**Files:**（映射 #4/#7/#8）
- Move: `research/docs/{factors,strategies,factor-mining-playbook.md}` → `knowledge/dossiers/`；`docs/递归式需求驱动系统工程开发手册：NASA Systems Engineering × V-Model.md` → `knowledge/handbooks/`；`docs/index/factors.md` → `knowledge/index/factors.md`
- Modify: `research/tools/factor_lib/build_index.py`（DOCS 输入 :22、OUT :23）、`research/tools/factor_lib/tests/test_index.py:24`、`Makefile:13,36`、`scripts/gates.sh:66`（G-INDEX）、`.claude/skills/factor-mine/SKILL.md`（≈15 处）、`assumption-review.md:74`、`code-review.md:8-24`、`research/README.md:11`、`research/CLAUDE.md:39-41`、`platform/tests/test_doc_paths_exist.py`（如引用档案）

- [ ] **Step 4.1** 先改 `build_index.py` 与 `test_index.py`（生成器是索引唯一写入方）→ 4.2 `git mv` 三组 → 4.3 重新生成：`python3 research/tools/factor_lib/build_index.py`（档案链接指向新路径）→ `--check` 一致 → 4.4 `pytest research/tools/factor_lib/tests -q` 与 C 全库 lint 抽查 → 4.5 提交 `refactor(research): R24 档案/索引/手册迁 knowledge + 生成器路径切换`

### Task 5: 治理文档搬迁（P1e）

**Files:**（映射 #6）
- Move: `docs/{data-map,directory-conventions,pending-items,archive-policy,traceability-matrix,remote-cleanup-checklist,workspace-p0p8}.md` → `governance/workspace/`
- Modify: 全仓引用 ≥20 处（以 Task 0 清单为准），至少含：
  `research/tools/universe_stages/universe_paths.py`、`research/tools/ashare_ingest/datapaths.py`、
  `platform/src/factorlab/{core/factio/paths.py,ports/batch.py,adapters/batch_flock.py}`、
  `platform/CLAUDE.md`、`scripts/gates.sh`（豁免文件名 :49-50）、`data-map.md` 自身路径列

- [ ] **Step 5.1** 先跑 Task 0 清单逐条更新代码/docstring 引用（保持 `../` 相对层级正确）→ 5.2 `git mv` → 5.3 验证：`make gates` 全绿；`grep -rn "docs/data-map\|docs/pending-items\|docs/directory-conventions" --include=*.{py,md,sh} | grep -v 冻结` 零命中 → 5.4 提交 `refactor(docs): R24 治理文档迁 governance/workspace`

### Task 6: 证据双轨搬迁（P1f）

**Files:**（映射 #9/#10，依用户决策 D1）
- Move: `docs/verification/` → `governance/evidence/verification/`；`docs/reviews/`（余下：README/findings/r01..r04）→ `governance/evidence/reviews/`
- Modify: `platform/tests/test_regression_152.py:27`、`platform/tests/test_cli_lint_params.py:99`、研究测试 5 处（`universe_stages/tests/{test_cli_bootstrap,test_layer3_tick_guard,test_preflight}.py`、`ashare_ingest/tests/test_import_daily_delisted.py`、`ch_ingest/tests/test_ingest_daily_derive.py`）、`scripts/gates.sh:21,47`（FROZEN/G-LEGACY 豁免）、两个 README 加旧→新映射表

- [ ] **Step 6.1** 先在 `governance/evidence/reviews/README.md`（搬迁后为准）与 verification README 写旧→新映射（R6 先例格式）→ 6.2 `git mv` → 6.3 验证：`pytest tests/test_regression_152.py -q`（integration 标记可 skip 但收集必须过）、`pytest tests/test_cli_lint_params.py -q`、研究 T1/T2 定向 → 6.4 提交 `refactor(evidence): R24 证据与评审台账迁 governance/evidence`

### Task 7: 脚本搬迁（P1g）

**Files:**（映射 #11）
- Move: `scripts/*` → `governance/ops/`
- Modify: `Makefile:28`、`platform/CLAUDE.md:64`、`platform/README.md:17`（`bash ../scripts/reinstall_editable.sh`）、各脚本 `REPO`/`ROOT` 推导（`gates.sh:12`、`check_imports.py`、`check_tool_layering.py`、`check_dataiface.py`）、根 `CLAUDE.md:34-37`、`AGENTS.md`

- [ ] **Step 7.1** 改脚本内根推导（`parents[N]` 与 `REPO`）→ 7.2 `git mv scripts/* governance/ops/` → 7.3 验证：`make gates`（改用 `governance/ops/gates.sh`）、`bash governance/ops/reinstall_editable.sh`（只读校验模式）、`check_*` selftest 全过 → 7.4 提交 `refactor(scripts): R24 门与检查脚本迁 governance/ops`

### Task 8: 产物收口（P2a）

**Files:**（映射 #12）
- Move: `platform/results/` → `runs/platform/`（本地目录，`git mv` 不适用——用 `mv`；两者皆 gitignore）；删除根空 `results/`
- Modify: `.gitignore:17`（`results/` → `/runs/`）、`README.md:35,38`、`platform/src/factorlab/config.py`（`results_dir` 默认）、相关测试若硬编码 `platform/results`

- [ ] **Step 8.1** 改 config 默认与文档 → 8.2 `mv platform/results runs/platform`；`rmdir results` → 8.3 冒烟：短窗 `factorlab run` 产物落 `runs/platform/<name>/` 且 `factorlab show` 可读 → 8.4 写 `runs/README.md`（run 目录↔档案映射：`runs/platform/<name>` → `knowledge/dossiers/<族>/<stem>.md`）→ 8.5 提交 `refactor(runs): R24 运行产物单点化到 runs/platform`

### Task 9: 仓外技能同步（P1 验收项）

**Files:**
- Modify（仓外）：`/data/students/gaolei/.claude/skills/factorlab-{dsl,data,ch-pipeline,backtest,evaluate}/SKILL.md`（旧 `platform/docs/interface.md` 等路径 → `knowledge/contracts/` 等）
- Modify（仓内）：`.claude/skills/factor-mine/*` 已在 Task 3/4 覆盖，本任务补 grep 校验

- [ ] **Step 9.1** 逐文件改路径引用 → 9.2 验证：`grep -rn "platform/docs\|docs/interface\|docs/catalog\|research/docs" /data/students/gaolei/.claude/skills/factorlab-*/SKILL.md` 零命中 → 9.3 记录到 `migration-r04.md`（技能为仓外，随仓提交无法覆盖，需在验收证据中注明）→ 提交 `docs(workspace): R24 仓外技能路径同步记录`

### Task 10: 两树 agent 文档薄化 + 根白名单定稿（P2b，D2 按"留壳"）

**Files:**（映射 #13）
- Delete: `platform/AGENTS.md`、`research/AGENTS.md`（内容并入根 `AGENTS.md`）
- Modify: `platform/CLAUDE.md`、`research/CLAUDE.md` → ≤15 行指针 + 树内环境事实；`platform/docs/`、`research/docs/` 各留 3 行 `README.md` 指针壳（若 D2 改判删除则整目录删）
- Modify: 根 `README.md`（白名单 15 项定稿）、`governance/workspace/directory-conventions.md`（同步）

- [ ] **Step 10.1** 合并 agent 指南 → 10.2 删两树 AGENTS/薄化 CLAUDE/留壳 → 10.3 验证：`git grep -n "research 分支\|main worktree" -- ':!governance/evidence'` 零命中 → 10.4 提交 `docs(workspace): R24 agent 文档单点化 + 白名单定稿`

### Task 11: 过渡区收尾（P3，D5 已拍板）

**Files:**（映射 #14）
- Move: `projects/ashare_alpha3` → `_archive/<date>-ashare-alpha3/`（附 manifest：来源/原因/恢复命令/到期日）
- Modify: `governance/workspace/directory-conventions.md:49-61`、`data-map.md:42`、`pending-items.md #5/`#6`、`README.md:55`

- [ ] **Step 11.1** 按 `archive-policy.md` 生成 manifest → 11.2 `mv`（无 git，非 git mv）→ 11.3 更新文档与 pending → 11.4 提交 `chore(workspace): R24 projects 过渡区归档（ashare_alpha3 → _archive）`

### Task 12: 全量验收与证据（P1 收口）

- [ ] **Step 12.1 门与测试**：
  ```bash
  make gates
  cd platform && .venv/bin/python -m pytest -q        # ≥2969 passed/13 skipped
  cd .. && make test-research                          # T2 268/10 + T1 ≥116
  ```
- [ ] **Step 12.2 生成物与一致性**：`build_index.py --check`；`gen_op_catalog.py --check`；`annotate --check`；`grep` 旧路径仅命中冻结历史文档与映射表。
- [ ] **Step 12.3 数据基线**：`data/` mtime/容量与 Task 0 基线一致（零写）。
- [ ] **Step 12.4 证据落盘**：`docs/verification/R24/`（命令 + 原始输出 + 门结果），`migration-r04.md` 状态收口。
- [ ] **Step 12.5 提交**：`docs(workspace): R24 目录重整验收证据`

---

## 风险表（沿用结构提案 R-1..R-8）

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | 门/测试硬编码路径遗漏 → 静默旧引用 | Task 0 全库 `git grep` 清单；每 Task 先改判据/映射再移动；门全绿才收 P1 |
| R-2 | 历史文档路径失效 | 各 README 加旧→新映射；冻结目录进 G-LEGACY 豁免 |
| R-3 | 挖矿/团队在途导致基线漂移 | Task 0 强制 freeze；单批 `git mv`；记录 HEAD |
| R-4 | G-INDEX byte-equality 红 | 先改生成器与测试，再重新生成 |
| R-5 | `data/` 被误伤 | 不触碰 data/；Task 0/12 前后比对 mtime/容量 |
| R-6 | 两树 docs 删除后 agent 扫描断裂 | 留 3 行指针壳（D2 待定） |
| R-7 | 用户级技能断链 | Task 9 与 P1 同批；不改技能不得验收 |
| R-8 | 方案 B 的 `parents[N]` 偏移 | 本计划不做方案 B（D4 后议） |

## 验收标准

1. `make gates` 全绿（G-COPY 在新路径判"契约单副本"；G-LEGACY 活文件零旧路径）；
2. 平台全量 ≥2969 passed/13 skipped；T2 268/10 + T1 ≥116；
3. `build_index --check`、`gen_op_catalog --check`、`annotate --check` 全一致；
4. 全库 `git grep` 旧路径仅命中冻结历史文档与映射表；
5. `data/` mtime/容量与基线一致；`runs/platform` 产物可经 `runs/README.md` 指回档案；
6. `knowledge/README.md` 能回答五类"我要找 X"且无死链；仓外技能零旧路径。
