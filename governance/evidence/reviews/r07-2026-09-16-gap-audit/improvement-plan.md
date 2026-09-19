# 缺口整改与架构卫生 实施计划（Plan G——gap remediation）

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 R07 审计出的 8 条 I 与架构"漂移"收口——门常绿、契约同步、坐标单点、口子封住、backlog 登记齐。

**Architecture:** 先修根因（模板/在途坐标）再清存量；判据（门）先行加固；契约与代码对齐；数据缺口按"派生/补灌/裁决"三类处置；每个任务独立可验收。

**Spec:** `governance/evidence/reviews/r07-2026-09-16-gap-audit/report.md`（缺口全景 §1 + 8 条 I §2 + 处置建议 §4）；
架构评估与"变干净五件"见同轮 report 与对话记录。

## Global Constraints

- **TDD**：代码改动先失败测试后实现；判据类改动必须带对抗注入实验（正反例）。
- **一次提交一棵树**（platform / research / knowledge / governance / docs 分提）；证据落 `governance/evidence/verification/R29/`。
- **冻结不动**：`governance/evidence/verification/**`、`reviews/r0*/**` 历史正文、`_archive/**`、`data/`（零写）。
- **门先于清理**：先改门/测试判据，再批量重指/重生成。
- **重任务护栏**：`FACTORLAB_MAX_MEMORY=8GB`；不在 LLM/多 agent 高峰并发重任务。
- **基线**：平台全量 3151/13、工具 337、`make gates` 目标 exit 0、台账 101 行（+R07 8 行）。

---

## Task 1（P0）：门回绿 + 坐标根因修复

**Files:**
- Modify: `knowledge/dossiers/factors/_template.md:75`（模板根因）
- Modify: `knowledge/dossiers/factors/volatility/max_effect_20d_extcnt.md:85`（tracked 门红点）
- Modify: 5 个 untracked 在途档案（zmax / intraday_high_time / symrun_r30_streak / symrun_r30_flip / intraday_tail_amt_share）
- Modify: `.claude/skills/factor-mine/SKILL.md`（提交前门子集纪律）
- 存量批处理脚本（一次性，放 `/tmp` 或 `platform/scripts/`）：重指/标注 158 文件 164 处

- [ ] **Step 1: 修模板根因**（先断源）：
  `results/<name>/summary.json` → `runs/platform/<name>/summary.json`；同步模板里其它 `results/` 措辞。
- [ ] **Step 2: 存量分两类处置**（写脚本，先 dry-run 打印 diff）：
  ① 目标 `runs/platform/<name>/` 实存 → 重指（约 26 处）；
  ② 不可复跑/目标不在 → 按 R21 约定加 `snapshot:` 标注并保留原口径说明（约 129 处）。
- [ ] **Step 3: 门回绿验证**：`bash governance/ops/gates.sh` → **exit 0**（G-LEGACY/G-INDEX/G-ANNOTATE 全绿）；索引 `build_index.py` 重生成 + `--check`。
- [ ] **Step 4: 根因纪律**：`factor-mine` 技能补一步"提交前跑门子集"：
  ```bash
  bash governance/ops/gates.sh --structure && python3 research/tools/factor_lib/build_index.py --check
  ```
- [ ] **Step 5: 提交** `fix(workspace): R07-MIG-I1/I2 坐标根因修复 + 门回绿`（含证据）

**Produces**：门 exit 0；模板不再产旧坐标；在途档案坐标干净。

## Task 2（P0）：G-LEGACY 判据加固（R07-GATE-I3）

**Files:**
- Modify: `governance/ops/gates.sh:57-93`（G-LEGACY 模式表与豁免）
- Create/Modify: `governance/evidence/verification/R29/gate-legacy-adversarial.py`（对抗注入）

- [ ] **Step 1: 写对抗测试**（先红）：在 /tmp 构造 5 例：①untracked 档案含 `platform/results`；②裸 `results/<name>`；③`research/tools/lib` 旧引用；④README 非映射行旧引用；⑤tracked 命中（对照）→ 跑门断言 ①-⑤ 全 RED（现状 ①③④会绿=失败）。
- [ ] **Step 2: 实现判据**：
  - 扫描纳入 untracked：`git ls-files -o --exclude-standard`（或 `git grep --untracked`）+ 现有 tracked 扫描；
  - 模式表补：裸 `results/`（负前瞻 `runs/platform`，注意本机 rg 无 PCRE2 → 用 Python 遍历实现）；`research/tools/lib`；补 R27 后新坐标族；
  - 3 个 README 整文件豁免 → **行级豁免**（仅映射注行）。
- [ ] **Step 3: 跑测试转绿** + `--selftest` + `make gates` exit 0（与 Task 1 串行验证）。
- [ ] **Step 4: 提交** `fix(governance): G-LEGACY 纳 untracked/裸 results/行级豁免（R07-GATE-I3）`

## Task 3（P0）：契约同步（R07-CONTRACT-I5 + 陈旧计数）

**Files:**
- Modify: `knowledge/contracts/interface.md:2277`（补 NEXT_WINDOW 段：timing/窗口配置/`MarksPolicy.WINDOW_END_BASED`/失败语义/持久化 v2，引 `2026-09-15-minute-execution` 设计）
- Modify: `knowledge/contracts/catalog.md`（指向 528 分类面：`op list --catalog` + `platform/scripts/gen_op_catalog.py`，或按 Plan 2 同源生成）
- Modify: 陈旧计数与漂移：`governance/ops/check_dataiface.py:225`/`gates.sh:164`（"460 处"→实计 70）、`knowledge/.../data-map.md`（A5 18,124,805；B1 13 表）、`~/.claude/skills/factorlab-ch-pipeline/SKILL.md`（行数）、`R21` 快照表补 circ_mv/index_daily
- Test: `platform/tests/test_doc_paths_exist.py` + 新增断言（interface 必含 NEXT_WINDOW）

- [ ] **Step 1: 写失败断言**：测试断言 `interface.md` 含 `NEXT_WINDOW` 且不含 "NEXT_OPEN only"（防再漂移）。
- [ ] **Step 2: 补契约段 + 计数修正**（以代码实测为准，不抄文档）。
- [ ] **Step 3: 跑测试 + `make gates`**。
- [ ] **Step 4: 提交** `docs(contracts): 契约补 NEXT_WINDOW + 计数对齐（R07-CONTRACT-I5）`

## Task 4（P1）：数据缺口三件（R07-DATA-I4 等）

**Files:**
- Modify: `platform/tools/ch_ingest/ingest_daily.py:172-175`（circ_mv 派生自 `float_shares`；先核实单位=万元并写测试）
- Modify: `knowledge/contracts/interface.md` / DDL 注释（修正"无数据源"表述）
- 裁决：`stock_st` 灌入源 / `index_daily` 补数（`ingest_index_sina.py` 死引用修复或标注不可用）

- [ ] **Step 1: circ_mv 派生**：TDD（单测：给定 float_shares → circ_mv=float_shares×close/1e4 万元口径）→ 重灌受影响月份 → `reconcile.py` 全绿 → 抽样 10 spec 复跑（原全 null → 有值），证据落 R29。
- [ ] **Step 2: stock_st**：查明可得源；不可得则更新 `interface.md`/data-map 表述并保留降级开关（决策记录）。
- [ ] **Step 3: index_daily**：修复死引用（落到真实补数路径）或明确标"暂不可用 + 触发条件"。
- [ ] **Step 4: 提交**（按树分提）+ 证据。

## Task 5（P1）：口子收口（R07-STRAT-I6 / R07-LINT-I7）

**Files:**
- Modify: `platform/src/factorlab/core/engine/semantics.py`（分类表签名 → 库函数 arity/形态静态校验）
- Modify: `platform/src/factorlab/surfaces/cli/main.py`（新增 `lint --strategy <yaml>`：`load_strategy_doc` + direction/池校验）
- Modify: `research/tools/strategies/run_strategy.py`（`universe_override`：消费或显式拒绝，二者必一）

- [ ] **Step 1: arity 测试**（先红）：`ts_cum_count(close,5)` lint 必须 exit≠0 且文案点名"参数个数应为 1"；`ts_cum_count(close)` 通过。
- [ ] **Step 2: 实现静态校验**（用分类表签名；错误含行:列）。
- [ ] **Step 3: 策略 lint 测试**（先红）：坏 YAML/非 ±1 direction/未知因子 → exit≠0；合法首例 YAML → exit 0；`universe_override` 非 null 时行为明确（实现消费或用 `NotImplementedError` 显式拒绝）。
- [ ] **Step 4: 实现 + 全量回归**（平台 3151 → 不回退）。
- [ ] **Step 5: 提交** `feat(platform): lint arity 校验 + lint --strategy + universe_override 收口`

## Task 6（P2）：规则与卫生统一

**Files:**
- Modify: `governance/evidence/reviews/README.md:64`（M 行口径：改"允许 级=M 入台账"或将 6 行 M 迁出，二者择一）
- Modify: `platform/tools/lob_fact/store/compact_lob.py:37`（自举移出 docstring）+ 冒烟测试（直跑 `--help` exit 0）
- Modify/Create: `platform/uv.lock`（或冻结 requirements）+ `reinstall_editable.sh` 说明（venv 可重建路径）
- 协调：`#12②` quark 改名与 `~/.claude/skills/quark-share-download/scripts/` 同批（含 `quark_client.py`）
- 监督：根 `results/` 退场（RunContext 默认已跟随 settings；排查在途会话/技能残余写点）

- [ ] Step 1: M 口径统一（选"允许入台账"，README 与现状一致；`check_reviews` 不改）。
- [ ] Step 2: compact_lob 自举修复（测试：`python3 compact_lob.py --help` exit 0）。
- [ ] Step 3: `uv lock` 生成 + 验证机按声明重建走三门（成本大，可拆子任务或登记 deferred+触发）。
- [ ] Step 4: quark 改名批次（仓内 + 技能副本 + SKILL.md 同批）。
- [ ] Step 5: 根 `results/` 退场复查（`ls results` 应为空/不存在；在途挖矿会话坐标确认）。
- [ ] Step 6: 提交（分树）。

## Task 7（P2）：backlog 登记（pending-items 增补）

**Files:**
- Modify: `governance/workspace/pending-items.md`

- [ ] 增补：**Plan 2**（conformance/算子档案/op_meta/插件元数据）、**Plan 3**（`by=` 截面表达）、**分钟 V2**（量能触发/分钟 NAV/临停规则）、**策略 lint**（若 Task 5 未全做）、**CA Gate 连续回测工作流**、`circ_mv`（完成后标 ✅）。
- [ ] 修正陈旧条目：#9 文字失真（源脚本已归档）、#12① 计数 68→70、#18 依赖数 19、`#11④` 加注现状（catalog 853 行）。
- [ ] 提交 `docs(workspace): R07 backlog 登记与陈旧条目校正`。

## Task 8：全量验收与证据（R29）

- [ ] **门与测试**：`make gates` exit 0；平台全量 ≥3151/13；工具 ≥337；策略入口 tests；`check_reviews` 无 BAD。
- [ ] **对抗复验**：Task 2 注入 5/5 RED；arity/策略 lint 反例全拒。
- [ ] **数据**：`reconcile.py` 全绿；circ_mv 抽样 spec 有值。
- [ ] **证据落盘**：`governance/evidence/verification/R29/`（命令+输出+门结果）；R07 8 行在 `findings.md` 由团队回填 `fixed-claimed`，reviewer 复查置 `verified`。
- [ ] 提交 `docs(verification): R29 整改验收证据`。

## Self-Review（对 R07 报告覆盖）

- §2 八条 I → Task 1（MIG-I1/I2）/ Task 2（GATE-I3）/ Task 4（DATA-I4/I8 工作流登记）/ Task 3（CONTRACT-I5）/ Task 5（STRAT-I6、LINT-I7）/ Task 7（CA 工作流入 pending）；
- §3 M 与 backlog → Task 6/7；
- 架构"变干净五件" → Task 1（根因）/2（判据）/3（契约）/4（数据）/5（口子）；
- 未覆盖（有意）：Plan 2/3 本体实施与分钟 V2 属"另立计划"，本计划只登记与排期。

## 风险表

| 风险 | 处置 |
|---|---|
| 存量 158 文件批改引入新错误 | 脚本 dry-run + 门（G-LEGACY/G-INDEX/G-ANNOTATE）兜底；按树提交可回退 |
| untracked 扫描误报在途文件 | 只判"旧坐标模式"；在途档案按 Task 1 Step 3 纪律先修 |
| circ_mv 单位/口径错 | 单测锁口径（万元）+ 抽样对拍 `total_mv` 量级 |
| uv lock 改变环境 | 只生成不解旧 venv；重建验证单机试点 |
| 在途挖矿与整改抢文件 | 冻结窗口（与团队约）+ 完成后统一跑门 |
