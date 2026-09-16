# R06 团队修复证据汇总 —— 坐标 / 技能 / 清理 / 杂项（2026-09-16）

- **复查来源**：`governance/evidence/reviews/r06-2026-09-16-post-migration-review/report.md`
  （R06 迁移后复查；三路并行对抗复查，无 C）
- **本批范围**：R06-SKILL-I2、R06-MIG-I3、R06-M4/M5/M6/M7/M8/M9/M10
  （其余 R06 发现由并行开发者处理，其证据在本目录 `ledger/`、`cli-strategy/` 子目录）
- **起始 HEAD**：`08a327b`（本批期间）｜ **纪律**：只精确 `git add`；挖矿/评审/其他开发者在途文件未提交
- **最终门态**：`13-gates-final.txt`（`make gates` **exit 0，全绿**；G-LEGACY 两段/索引/annotate/venv/lint/台账全 ✓）；
  并行开发者提交落地后 HEAD 复核（`14-gates-final-head.txt`，`f3968f4` 之上）**仍 exit 0 全绿**

## 逐 finding

| ID | 根因 | 改动 | 提交 | 验证 | 证据 |
|---|---|---|---|---|---|
| R06-SKILL-I2 | factor-mine 技能坐标未随 R24 更新，单点化后仍写根 `results/` | `SKILL.md` 4 处坐标 → `runs/platform`（round 记录 → `runs/platform/_mine_rounds/`）；`python3 build_index` → 平台 venv；已存在的 `_mine_round_{1..12}.md` 物理迁移（sha256 前后同一） | `00e749f` | 前后 sha256 逐一相等（HASH-MATCH: OK）；目录 ls 确认 | `09-mine-rounds-move.txt` |
| R06-MIG-I3 | 根 `results/` 2.6G 残留未清且未登记 pending | 盘点 → 分类处置 → 清理 → pending #22 登记（详见下节"清理量化"） | `19e15cc` | 盘点逐文件 sha256；删除项 tar 备份 + sha256；`results/` rmdir | `08-mig-i3-inventory.{py,txt,json}`、`10-mig-i3-cleanup.txt` |
| R06-M4 | R24 验收目录无顶层 README | 新增 `governance/evidence/verification/R24/README.md`（索引 00→15 + 16-r06-fixes；基线/终态数字、映射、冲突 C1/C2/C3、残余、复现命令） | `bcfe38d` | 目录清单与内容对照 | 文件本体 |
| R06-M5 | `Makefile:32-33`/`gates.sh:74` 走系统 `python3`（anaconda 3.10） | Makefile `index` → `$(PLATFORM_PY)`；G-ANNOTATE → `$PLATFORM/.venv/bin/python`；`topo()`/`dataiface()` 缺 venv 时**响亮失败**（不再回落 python3） | `3ed89ce` | 全量 `make gates` exit 0（G-ANNOTATE/G-INDEX/G-LINT 均经平台 venv） | `13-gates-final.txt` |
| R06-M6 | `gates.sh:21 FROZEN` 死配置零引用且含过时路径 | 删除变量，注释说明"实际豁免以判据行内 pathspec 为准" | `3ed89ce` | `git grep FROZEN -- governance/` 仅剩判据注释；门绿 | `13-gates-final.txt` |
| R06-M7 | emb python 钉死（w5 runbook）+ reinstall 尾注引 emb | `w5_closure.sh` → 平台 venv 派生路径；`reinstall_editable.sh` 尾注改单解释器口径（不改变实际行为） | `b482414` | emb 零残留；`bash -n` 两脚本 OK；PY 求值解析到平台 venv | `11-m7-emb-refs.txt` |
| R06-M8 | `RunContext.output_dir` 默认 `Path("results")`（相对 CWD，单点漏洞） | 默认 → `settings.results_dir`；新增回归测试（TDD：先失败后通过） | `301f24b` | RED→GREEN；指定三文件 `pytest` **131 passed**（220s） | `04-m8-tdd-red.txt`、`05-m8-tdd-green.txt` |
| R06-M9 | G-LEGACY 只拦旧仓库名：`research/docs`/`docs/reviews`/`docs/index`/`platform/results`/已迁工具/emb 均漏 | G-LEGACY 扩第二段判据（9 类模式 + 冻结/映射豁免）；**负向注入自检**证明修前漏报、修后抓到；同步清 7 处活引用（config/check_imports/.gitignore/READMEs/档案快照/strategy 注释） | `3ed89ce`（+`d9c3634`） | 注入 `docs/reviews`+`platform/results`+`research/docs` → 修前 ✓ 漏报、修后 ✗ 抓到 4 处；清理后零残留 | `01-m9-injection-before.txt`、`02-m9-injection-after.txt`、`03*` |
| R06-M10 | 手册/playbook 落 `knowledge/dossiers/`（dossiers=档案） | `git mv` playbook + `mv`（untracked→归档 tracked）authoring-manual → `knowledge/handbooks/`；全库活引用同步（root/知识/研究 README、模板、playbook 内互链、技能）；档案正文历史模板行按映射注记不改写 | `d9c3634` | 活残留 grep 零命中（仅映射注/迁移台账） | `06-m10-move.txt`、`07-m10-refs.txt` |

附带（M9 暴露的 M1 类坐标）：`volatility/max_effect_20d_high.md`、`low_vol_20d_park.md` 的
数据快照引用 → `runs/platform/<名>/summary.json`（随 `d9c3634`）。

## 根 `results/` 清理量化（R06-MIG-I3）

| 项 | 值 |
|---|---|
| 清理前 | **2,696,083,710 B（2.6G）/ 96 文件** |
| 仅存于根 → 移入 `runs/platform/`（12 个因子产物） | 见 `08-mig-i3-inventory.txt`（合计并入 2.16 GiB） |
| 双份保留（根更新，不覆盖；含说明文件） | `max_effect_20d_high__root-dup-20260916`、`value_bp__root-dup-20260916`（差异：summary.json + weekly.parquet；其余大文件 sha256 全等） |
| 删除（runs 更新，根为 legacy 旧态） | `low_vol_20d`、`low_vol_20d_park` = **371,693,758 B（0.35 GiB）** |
| 删除前备份 | `_archive/backups/r06-mig-i3-root-results-differing-files-2026-09-16.tar.gz`（32,837,256 B；sha256 `ad7b7b1b…`；含 6 个差异小文件） |
| 挖矿记录 | `_mine_round_{1..12}.md` → `runs/platform/_mine_rounds/`（sha256 前后同一） |
| 结果 | 根 `results/` 目录移除（不再写入）；`runs/platform` 3.9G → **6.2G**（单点收口） |
| 净释放磁盘 | **371,693,758 B**；根树 2.6G 去重收敛 |

## 门态归因与并行性说明

- `00-gates-before.txt`（17:22）：全绿（本批起点）。
- `12-gates-full-after.txt`（17:37）：**G-INDEX 瞬时红**——挖矿在途新增 `extsum` 等 yaml 未入索引
  （本批未动索引生成器输入；重生成后一致，见 13-gates-final）。**未随本批提交**
  `knowledge/index/factors.md`（属挖矿在途状态）。
- `13-gates-final.txt`（17:4x）：**exit 0 全绿**。
- `14-gates-final-head.txt`（并行提交 `2177f29`（台账门）、`f3968f4`（TEST-I5）落地后）：**exit 0 全绿**；
  `f3968f4` 一并收录本批对 `test_run_strategy_cli.py` 的注释坐标一行（其提交范围含该文件）。
- **并行开发者豁免**：`research/tools/strategies/{run_strategy.py,tests/test_run_strategy_cli.py}`
  正被 R06-TEST-I5/TOOLS-I6 修复（在途），其旧路径历史注记/反向断言按文件豁免
  （见 `gates.sh` G-LEGACY 判据注释）；本批未提交这两文件的在途改动（仅 test 文件一行
  注释坐标已随工作区，归其提交方）。
- 未动：`governance/evidence/reviews/**`（reviewer）、`check_reviews.py`（台账开发者）、
  `knowledge/design/workspace/**`、`research/factor/**`、`knowledge/dossiers/factors/**`（挖矿在途）。

## 残余 / 未竟

1. `__root-dup-20260916` 两份以哪个为准：研究侧裁决后合并/删除其一（`pending-items` #22）。
2. 活跃挖矿会话若仍按旧坐标写根 `results/`：按新坐标迁回（技能已更新；`results/` 已移除）。
3. 档案正文中历史 `results/<name>/`（2026-08 时代）引用：按"历史档案正文不改写"保留
   （非活指针；门不覆盖裸 `results/`，M1 其余条目仍在台账）。
4. G-LEGACY 第二段为**工作树判据**（`git grep`）：未跟踪的在途文件提交后才会被门覆盖
   （与既有第一段同语义）。
