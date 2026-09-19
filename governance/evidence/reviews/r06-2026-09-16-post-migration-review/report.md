# R06 迁移后复查报告（2026-09-16）

- **轮次**：R06「post-migration strict review」｜ **HEAD**：`9fd442f`（复查期间）
- **范围**：R24 目录/工具/解释器迁移落地面 + R27/R28 新增量（策略 Plan S 实现、台账门 Q7）
- **方法**：三路并行**对抗性**复查（路径完整性 / 功能回归 / 台账与验收证据），全部命令 + 原始输出留证；
  reviewer 对 R24/R27/R28 自证声明逐条独立复跑
- **结论**：**无 C**。R24/R27/R28 的可复验声明**全部实证通过**（无伪造）；问题集中在
  **台账闭环从未落地、门对证据列零覆盖、挖矿在途导致门红、坐标残留**
- **证据索引**：`evidence/paths/`（30 文件）｜`evidence/regression/`（16 文件）｜`evidence/ledger/`（26+ 文件）

## §1 独立复跑通过清单（对抗性复核）

| # | 声明来源 | 声明 | 独立复跑实测 | 判定 |
|---|---|---|---|---|
| 1 | R24 基线 | 平台全量 3150/13/0 | **3150 passed / 13 skipped / 0 failed**（803.9–809.9s，两路独立复跑） | ✅ 精确一致 |
| 2 | R24/R27 | `make test-research` 单解释器 | `platform/tools` **337 passed**；`research/tools` 57 passed / 2 skipped / **2 failed**（挖矿在途，见 I1） | ✅ 实装（失败归因明确） |
| 3 | R24 | 旧→新映射 14 行、旧路径零活引用 | 新根全存在；18/18 旧路径消失；同模式 grep 复跑活命中仅映射表/历史注记 | ✅ 14/14 |
| 4 | R24 | runs 单点 `runs/platform` | `cd /tmp && factorlab show …` exit 0；CLI 时间戳 == runs 副本 ≠ 根 `results/` legacy | ✅ |
| 5 | R24 | `data/` 零写（78120 文件 sha256） | before==after==`1ed59103`，353G | ✅ |
| 6 | R27 | 工具裸跑 212（9+11+192） | **212 passed** | ✅ |
| 7 | R28 | 首例策略 5 决策 / NAV +2.2257% / 176 笔 / fees 18290.52 / DD −0.3140% | 逐项精确一致；**11 帧与落盘产物 ALL_FRAMES_EQUAL: True** | ✅ |
| 8 | 策略入口修复（cc829ad） | 结果根跟随 settings、stale 报错清晰 | 默认真跑 NAV 9992407.37→10214807.42（+2.23%）与 e580987 AFTER 一致；空目录报错与 BEFORE 逐字相同 | ✅（但缺测试保护，见 I5） |
| 9 | 数据面 | CH daily 对账 | `reconcile.py daily` 全库一致（daily/adj_factor/daily_basic/stk_limit/adj_detail/adj_event 全对） | ✅ |

## §2 Important findings（8 条，已登记 `findings.md`）

| ID | 问题 | 关键位置 | 证据 |
|---|---|---|---|
| R06-MIG-I1 | **HEAD 上 `make gates` 红**：G-ANNOTATE（`netflow_vol.md` 缺 `snapshot:`，已提交）+ G-INDEX（盘上 167 vs 生成器 168，untracked `accel_inflow.yaml` 未归档）——挖矿在途未收口 | `governance/ops/gates.sh:74`；`knowledge/dossiers/factors/reversal_20d/netflow_vol.md`；`research/factor/liquidity/accel_inflow.yaml` | `paths/run_make_gates.txt`、`regression/gates_full.txt`、`ledger/rerun-*.txt` |
| R06-SKILL-I2 | **单点化后仍在写根 `results/`**：factor-mine 技能坐标未更新，14:21 单点化提交后仍活跃写 `results/_mine_round_{8,9,10}.md`（14:53/15:12/16:12），同期 run 产物落 `runs/platform` → 记录与产物分裂 | `.claude/skills/factor-mine/SKILL.md:89,116,122,139` | `paths/root_results_writer_evidence.txt` |
| R06-MIG-I3 | **根 `results/` 2.6G 残留未清**：产物全 ≤14:19（迁移前），清理项在 `migration-r04.md:68 C3` 标"未竟"但未登记 `pending-items`，与"运行产物单点化"声明不符 | 根 `results/`；`governance/workspace/pending-items.md` | `paths/root_results_writer_evidence.txt` |
| R06-LEDGER-I1 | **台账闭环从未落地**：93/93 行 `fixed-claimed`、**复查列 92/93 为空**；R02 声称"56 verified/7 reopened"从未回填状态列 → "唯一状态源"不可信 | `governance/evidence/reviews/findings.md`（全表）；README §流程 3 | `ledger/analyze_ledger.out.txt` |
| R06-LEDGER-I2 | **门对「修复说明」证据路径零覆盖**：全表 269 个该列路径 token 0/269 在反引号内；注入"未加反引号死路径"→ 门 exit 0 | `governance/ops/check_reviews.py:299-306` | `ledger/gate-adv-m7-*.txt`、`gate-backtick-coverage.txt` |
| R06-LEDGER-I3 | **门不区分"有证据/有文字"**：注入`已修复，无证据。`（非空无 commit/路径）→ 门 exit 0 | `governance/ops/check_reviews.py:295-296` | `ledger/gate-adv-m5-*.txt` |
| R06-TEST-I5 | **策略入口 E2E 测试指向死路径**：`_PLATFORM_RESULTS = _REPO/"platform"/"results"`（已迁）→ 集成测试恒 skip，cc829ad 修复无回归保护 | `research/tools/strategies/tests/test_run_strategy_cli.py:27,204,235` | `regression/strategy_extras_and_source_proof.txt` |
| R06-TOOLS-I6 | 死符号残留：`run_strategy.py:22 _REPO_ROOT` 定义后零引用（cc829ad 清理不彻底） | `research/tools/strategies/run_strategy.py:22` | 同上 |

## §3 Minor findings（10 条，仅报告；M 按约定不进台账）

| ID | 问题 | 位置 |
|---|---|---|
| R06-M1 | 因子档案快照引用死路径/旧根（`platform/results/…` 已不存在；部分引根 `results/…`，与姊妹文件 `runs/…` 不一致） | `knowledge/dossiers/factors/volatility/max_effect_20d_high.md:83`、`low_vol_20d_park.md:84` 等 |
| R06-M2 | reviews README 过时：结构块仍写 `docs/reviews/`（与 :20 注记矛盾）；批次表三行"待执行"均已落地；:70 `T2 (emb)+T1` 与单解释器不符 | `governance/evidence/reviews/README.md:9,24-28,70` |
| R06-M3 | README「M 不进台账」与台账中 6 行 M（R03-M1..M5、R05-M1）矛盾 | README :63 vs findings.md |
| R06-M4 | R24 验收目录无顶层 README（仅 `12-acceptance/README.md` 代行，与 R27/R28 惯例不一致） | `governance/evidence/verification/R24/` |
| R06-M5 | `Makefile:32-33` / `gates.sh:74` 走系统 `python3`（anaconda 3.10.9）而非平台 venv 3.13——功能正常但与"单解释器"声明不符 | `Makefile`、`governance/ops/gates.sh` |
| R06-M6 | `gates.sh:21 FROZEN` 死配置零引用且含已迁路径；`:48` 同 pathspec | `governance/ops/gates.sh` |
| R06-M7 | T2 残留：`platform/tools/lob_fact/w5_closure.sh:7` 钉死 emb python（tracked runbook）；`reinstall_editable.sh:38-39` 尾注同引 | 两文件 |
| R06-M8 | 单点漏洞：`RunContext()` 默认 `output_dir=Path("results")`（相对 CWD）——CLI 显式传 settings 无碍，直接调用者会落 `./results` | `platform/src/factorlab/app/context.py:27` |
| R06-M9 | 门覆盖盲区：G-LEGACY 只拦旧仓库名，不覆盖 `research/docs`/`docs/reviews`/`docs/index`/`platform/results`/已迁工具/`emb/bin/python`——本轮漏网均不会被门发现 | `governance/ops/gates.sh:42-54` |
| R06-M10 | 手册/playbook 落 `knowledge/dossiers/`（dossiers 应为因子/策略档案；结构计划目标为 `knowledge/handbooks/`） | `knowledge/dossiers/factor-{authoring-manual,mining-playbook}.md` |

## §4 `check_reviews` 门对抗实验（9 场景）

| 场景 | m1 非法状态 | m2 缺修复说明 | m3 列数不足 | m4 ID 重复 | m5 空证据文字 | m6 反引号死路径 | m7 裸文本死路径 | m8 空状态 | m9 统计不符 |
|---|---|---|---|---|---|---|---|---|---|
| 门行为 | ✅ RED | ✅ RED | ✅ RED | ✅ RED | **exit 0** ❌ | ✅ RED | **exit 0** ❌ | ✅ RED | ✅ RED |

结论：门对 7/9 类注入有牙齿；**对"证据真实存在"无覆盖**（I2/I3）。原始输出见 `ledger/gate-adv-*.txt`。

## §5 reviewer 自修（本轮内完成）

- `knowledge/dossiers/factor-authoring-manual.md`：8 处旧坐标（playbook/interface/catalog/results/dossiers/index/1m_features）→ 新树；
- 策略三文档：`README.md` 状态改"已实施并验收（7e03acb/126def4；R28）"；`plan.md` 加实施状态注；
  `design.md` 旧坐标清单（M 级）待随文清理；
- 根 README 链接核对（指向 `knowledge/dossiers/` 实存文件，OK）。

## §6 处置建议（给开发团队）

1. **门回绿（最高优先）**：`accel_inflow` 补档案+重生索引（或撤 spec）、`netflow_vol` 补 `snapshot:` → `make gates` 回绿（I1）；
2. **坐标收口**：factor-mine 技能改新树；清根 `results/`（或明确保留策略）并登记 `pending-items`（I2/I3/M1/M5/M7/M10）；
3. **台账制度落地**：逐行回填复查列（或裁决"reviewer 复查后置 verified"的执行方式），消除 93/93 fixed-claimed 假象（I1）；
4. **门加固**：`check_reviews` 增加"修复说明含 commit/路径 token 且路径存在"判据（I2/I3）；
5. **测试与清理**：策略集成测试改新结果根（I5）；删死符号（I6）；G-LEGACY 扩覆盖清单（M9）。

## §7 R06 修复复查与台账回填（reviewer，2026-09-16 收口）

**修复复查（全部独立复跑，非看代码）**：

| 项 | 复核命令/方法 | 结果 |
|---|---|---|
| 门终态 | `make gates`（HEAD `82673a2` 后） | **exit 0 全绿** |
| 平台全量 | `FACTORLAB_MAX_MEMORY=8GB pytest -q`（reviewer 亲跑） | **3151 passed / 13 skipped / 0 failed（816s）**，与团队声称一致 |
| LEDGER-I2/I3 门加固 | 重跑 9 场景对抗注入（`gate_adversarial.py`） | **9/9 RED**（m5 空证据 / m7 裸文本死路径已抓到） |
| MIG-I1 | gates 绿 + 索引/快照门 | verified |
| SKILL-I2 | factor-mine 技能零 `results/` 写点 | verified |
| MIG-I3 | 根 `results/` 已清除 | verified |
| TEST-I5 | 策略集成用例不再 skip（10 passed，读 `settings.results_dir`） | verified |
| TOOLS-I6 | `_REPO_ROOT` 已删除 | verified |
| R02-C1/C2 定点 | `test_minute_gate.py + test_pit_staleness.py` | 41 passed |
| R05 定点 | `test_op_classification/test_spec_strict/test_eval_alignment` + `op list --catalog`(528) | 44 passed / 528 条 |

**台账回填（92 行存量 + R06 8 行）**：
- R01 63 行 → R02 §1 判定（7 条 reopened/partial 注明"→ R22 批次闭环"）；
- R02 12 行 / R03 13 行 → R22/R23 批次验收 + R06 全量/门 + 证据 token 门；
- R05 5 行 → R23 证据 + R06 定点复跑（R05-C1 状态列同步修正为 verified）；
- R06 8 行 → 本轮对抗复查；
- 终态：**101/101 行 `verified`、复查列覆盖 101/101**；`check_reviews --closure` 仅余 8 条**非致命**告警
  （R02 历史报告散文称 partial vs 台账已回填 verified 的口径差，属历史文本不改写）；
- 备份：`evidence/ledger/findings_pre_backfill.md`；门输出 `evidence/ledger/final-gates-after-backfill.txt`。
