# R24 顶层目录重整（方案 A）验收证据

- **对象**：`governance/evidence/reviews/r04-efficiency-2026-09-16/structure-plan.md`（Tasks 0-12，方案 A）
- **执行**：2026-09-16 · 分支 `restructure/monorepo` · 起始 HEAD `cbb81bc`（`cbb81bca11c346372d0a38b5439d02851f0a5df1`）
- **迁移台账**（旧→新 14 行全映射 + 逐 Task 提交 SHA + 并发冲突 C1/C2/C3）：
  `governance/workspace/migration-r04.md`
- **冻结条件（如实说明，被削弱）**：迁移窗口内挖矿循环与评审仍在写
  （`research/factor/**`、`knowledge/dossiers/factors/**`、`governance/evidence/reviews/**` 等）
  → `git status --porcelain` 不为空。经用户授权采取替代纪律：只精确 `git add`、动手前后 mtime
  快照、并发冲突不回滚并登记；见台账 §0。
- **结论**：14 项映射全部落地；`data/` 零写（78,120 文件清单 sha256 前后同一）；
  搬迁对象活引用零残留（余下命中均为冻结正文/映射表/同名自有目录误报）；
  R06 迁移后复查（2026-09-16）对 R24 的可复验声明**逐条独立复跑通过**（无伪造）。

## 1. 验收数字（Task 0 基线 → Task 12 终态）

| 项 | Task 0 基线 | Task 12 终态 | 证据 |
|---|---|---|---|
| 平台全量 | **3150 passed / 13 skipped**（818.98s） | **3150 passed / 13 skipped**（816s，同数） | `00-baseline/platform-pytest.txt`、`12-acceptance/platform-pytest.txt` |
| `make test-research` | platform/tools **337**；research/tools **2 failed / 58**（挖矿并发漂移） | **exit 0**：337 + **58 passed / 2 skipped**（在途收口后转绿） | `00-baseline/test-research.txt`、`12-acceptance/test-research.txt` |
| `make gates` | 红 1 项：G-ANNOTATE 缺 snapshot 4 份（挖矿在途） | **仅 G-ANNOTATE 7 份红**（同上，数量随挖矿波动；其余全绿：G-COPY/G-BOUNDARY/G-LEGACY/G-PATHS/G-IMPORTS/G-INDEX×2/G-LINT 164+/G-VENV/G-TOPO/G-DATAIFACE） | `00-baseline/gates.txt`、`12-acceptance/gates.txt` |
| 索引 `--check` | `build_index` 红（索引漂移，挖矿并发） | `build_index.py --check`、`build_strategy_index.py --check`、`gen_op_catalog.py --check` **一致 ✓** | `12-acceptance/{build_index-regen,gen_op_catalog-check}.txt` |
| `data/` | 353G / 78,120 文件；清单 sha256 `1ed59103…` | **同一清单 sha256 `1ed59103…`**（零写） | `00-baseline/data-manifest-before.{txt,sha256}`、`12-acceptance/data-manifest-after.txt` |
| 旧路径 grep | 471 行清单（15 组） | 2768 行终扫全部为冻结正文/映射表/误报（§12-acceptance/README §2） | `00-baseline/old-path-inventory.txt`、`12-acceptance/old-path-final.txt` |

## 2. 子目录索引（按 Task 顺序）

| 目录 | 内容（要点） |
|---|---|
| `00-baseline/` | Task 0 基线：分支/HEAD、平台 3150/13、tools、gates（G-ANNOTATE 4 红=挖矿在途）、data 353G 清单 sha256、471 行旧路径清单、`status-before`（18 项在途）。 |
| `02-contracts/` | 契约 4 篇 → `knowledge/contracts/`：结构门、`gen_op_catalog --check`、定向 pytest。 |
| `03-design/` | 设计/计划 → `knowledge/design/{platform,research,workspace}/`：mtime 前后快照（无并发写）、定向 pytest、gates。 |
| `04-dossiers/` | 档案/索引/手册 → `knowledge/dossiers|index|handbooks`：mtime 快照、索引重生成、research-tools pytest；冲突 C1 登记。 |
| `05-workspace/` | 治理文档 → `governance/workspace/`：leftover grep=0、gates。 |
| `06-evidence/` | 证据双轨 → `governance/evidence/{verification,reviews}`（782 文件 100% rename）：mtime 逐一相等（无并发写）、定向 pytest、gates。 |
| `07-ops/` | 门/检查脚本 → `governance/ops/`（ROOT 推导 `../..`）：gates、`reinstall_editable` 落位断言。 |
| `08-runs/` | 产物单点 `platform/results/` → `runs/platform/`（2.6G mv）：短窗冒烟 run + `show`、定向 pytest；根 `results/` 留待清理（C3）。 |
| `09-skills/` | 仓外 5 技能路径同步（`factorlab-{dsl,data,ch-pipeline,backtest,evaluate}`；15 处 → 0 残留），diff 存档 `skills-diff.patch`。 |
| `10-agent-docs/` | 两树 AGENTS 并入根、CLAUDE 薄化、`platform/docs`/`research/docs` 指针壳：legacy-terms grep、gates。 |
| `11-archive/` | `projects/ashare_alpha3` → `_archive/2026-09-16-ashare-alpha3/`（manifest，2026-10-16 到期）。 |
| `12-acceptance/` | Task 12 全量验收（本目录 `README.md` 为详版）：门/测试、旧路径终扫分类、data 零写、计划遗漏补修 4 项、验收标准对照。 |
| `13-reviews-gate/` | `check_reviews.py` 台账门 TDD：坏/好账本注入、9 场景对抗实验、`--selftest`。 |
| `14-q6-residual/` | R04-Q6 残余清理：死符号 10 删 9 留 1（`platform_head`）、数字对齐、断链、僵尸清理（`__pycache__` 894 dirs/157MB + `.pytest_cache` 4）。 |
| `15-post-migration-usage/` | R05 式迁移后**全 CLI 面使用验证**（list/show/serve/run/lint --all/op catalog/run_strategy）+ 策略入口 stale results 前后对照（→ `cc829ad`）。 |
| `16-r06-fixes/` | **R06 复查后的团队修复证据**（2026-09-16；复查报告见 `governance/evidence/reviews/r06-2026-09-16-post-migration-review/`）：坐标/技能/清理/杂项——R06-SKILL-I2（挖矿记录迁 `runs/platform/_mine_rounds/`）、R06-MIG-I3（根 `results/` 清理盘点+备份+回收）、R06-M4（本 README）、R06-M5/M6/M9（门单解释器化、FROZEN 清理、G-LEGACY 扩覆盖）、R06-M7（emb 解释器残留改平台 venv）、R06-M8（`RunContext` 默认 `output_dir` 跟随 `settings.results_dir`）、R06-M10（手册归位 `knowledge/handbooks/`）。 |

## 3. 旧→新映射总表

14 行映射与逐 Task 提交 SHA 见 `governance/workspace/migration-r04.md` §3（全部 ✅，
含 `platform/docs` 契约、两树设计/计划、`research/docs` 档案、`docs/*` 治理文书、
`docs/index`、`docs/verification|reviews`、`scripts/*`、`platform/results`、ashare 归档）。

## 4. 并发偏差与冲突（不回滚，如实登记）

- **C1**（Task 4）：`symrun_r30.md`/`max_effect_20d.md` 的挖矿在途内容随 `git mv` rename 提交
  （相似度 92%/90%），回滚会丢挖矿编辑 → 登记不回滚。
- **C2**（Task 0）：冻结窗口内挖矿/评审持续写入（18 项在途），基线门红归因记录。
- **C3**（Task 8）：根 `results/` 非空（2.6G，挖矿在途）未强搬/删除；新默认 `runs/platform`
  已生效。**后续：R06-MIG-I3（见 `16-r06-fixes/`）已清理完毕并登记 `pending-items` #22。**

## 5. 残余与后续

- **R06-MIG-I3 清理主体已完成**（2026-09-16，`16-r06-fixes/`）：根 `results/` 目录移除；
  12 个根独有产物移入 `runs/platform/`；2 个根更新版本双份保留（`__root-dup-20260916`，
  差异与备份见证据）；保留 1 项研究侧裁决（哪份为准）登记在 `pending-items` #22。
- 方案 B（`parents[N]` 偏移）/ D4：本计划明确不做（后议）。
- G-ANNOTATE 在途档案标注：已由挖矿循环收口（`08a327b`，2026-09-16）。

## 6. 复现命令（关键路径）

```bash
# 门与测试（单解释器）
make gates
make test-research
cd platform && .venv/bin/python -m pytest -q          # 3150 passed / 13 skipped（~13 分钟）

# 产物单点（任意 cwd）
platform/.venv/bin/factorlab list
platform/.venv/bin/factorlab show <因子名>

# 旧路径零残留（门内判据：G-LEGACY 两段）
bash governance/ops/gates.sh --structure
```
