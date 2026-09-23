# 最终测试只跑一次（Final-Test-Once）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development。步骤用 `- [ ]`。
> **Spec:** `knowledge/design/platform/specs/2026-09-24-final-test-once-discipline-design.md`

**Goal:** 把锁箱纪律简化为"数据两段 + 探索只准训练段 + 每版本最终测试一次 + 入库只看测试段结果"，
删掉配额/探索登记/相关开关，保留账本、流水线标记、自动补数据与结果记录。

**Tech Stack:** 既有平台（Python 3.13 / sqlite / Typer / Prefect 流水线）；无新依赖。

## Global Constraints

- 段定义：训练段 `< window_start`（`is_end`）；测试段 `[window_start, 数据日]`；窗口滚动/roll 不变。
- 探索（任何非最终测试的评估）窗口 `date.end > is_end` → 拒绝 `LOCKBOX_TEST_ONLY_FINAL`。
- 最终测试：每版本一次；同版本重复 → 拒绝（`LOCKBOX_FINAL_DUPLICATE`）；操作员逃生 `FACTORLAB_RE_FINAL=1`（审计留痕）。
- 版本指纹：spec 内容/参数/面板指纹/window_id（沿用 `candidate_fingerprint` 语义）。
- 最终测试必须经流水线（`FACTORLAB_PIPELINE=1`）或入库车道（`admit/ref add`）；host 直跑拒绝。
- 入库只看测试段冻结结果（`test_diagnostics.json`）：无最终测试登记或缺冻结件 → 拒绝。
- 删除：每窗配额、`--quota-final`、`FACTORLAB_LOCKBOX_ADMIN`、探索登记与 `lockbox_off` 留痕。
- 保留：账本 append-only + 防删改触发器、window state、流水线标记、裸跑警示、新鲜度门、ref-sync、manifest/config_path。
- 测试不得触碰真实台账（tmp）；每任务收尾跑聚焦测试；最终 R42 做真实验收。

---

### Task 1: core/store 语义重写（最终测试一次 + 探索拒 + 去配额）

**Files:** `platform/src/factorlab/core/lockbox.py`、`platform/src/factorlab/adapters/lockbox_store.py`、两处测试。

**Interfaces:**
- `LockboxError` 增 `LOCKBOX_TEST_ONLY_FINAL`；移除 `LOCKBOX_QUOTA_EXCEEDED` 的产生路径（类保留字符串兼容）。
- `guard_run(*, panel_start, panel_end, final_mode: bool, reason, spec_doc/artifact/command/tool, db_path, trading_days, data_end)`：
  1) `role=="is"` → `RunGuard({"role":"is"})`（不登记）
  2) `role!="is"` 且 `not final_mode` → `LOCKBOX_TEST_ONLY_FINAL`（hint：测试段只准最终测试；改走 `make xpipe` 或 `flab factor admit`）
  3) `final_mode`：需 `FACTORLAB_PIPELINE=1`（否则 `LOCKBOX_PIPELINE_REQUIRED`）→ 计算版本 fp →
     `final_exists` ? → 默认 `LOCKBOX_FINAL_DUPLICATE`（hint 改版本/操作员 `FACTORLAB_RE_FINAL=1`）:
     有 `FACTORLAB_RE_FINAL=1` 时允许再登记（reason 追加 `|re-final` 审计）；
     否则 `register_access(kind="final", ...)`（**无配额检查**）。
- `status()`：仅 `initialized/window_id/window_start/window_end/is_end/finals_total`（去 quota/探索计数）。
- `roll(..., quota_final)` 参数删除；state 表 `quota_final` 列保留（迁移兼容，不再读写语义）。

- [ ] Step1 失败测试（`platform/tests/test_lockbox_guard.py` 重写要点）：
  · IS 放行零登记；· 碰箱非 final → `LOCKBOX_TEST_ONLY_FINAL` 零登记；
  · 碰箱 final 无 marker → `LOCKBOX_PIPELINE_REQUIRED`；· 有 marker → 1 行 final；
  · 同版本再 final → `LOCKBOX_FINAL_DUPLICATE` 行数不变；· `FACTORLAB_RE_FINAL=1` → 新增 1 行且 reason 含 `re-final`；
  · 改 spec 内容（新版本）→ 可登记。
- [ ] Step2 见红 → Step3 实现上述 → Step4 聚焦绿（guard/state/registry/window/cli）→ Step5 提交
  `feat(lockbox): 最终测试一次语义重写（去探索登记/配额）（R42-T1）`。

---

### Task 2: CLI / 参数面清理（去 `--lockbox*`、`--quota-final`、admin）

**Files:** `platform/src/factorlab/surfaces/cli/main.py`、`platform/src/factorlab/research/factor.py`（run 透传）、`platform/src/factorlab/research/strategy.py`、测试。

**Interfaces:**
- `execute_run(..., final_mode: bool=False)`（替代 `lockbox_intent/reason`）；`final_mode=True` 仅由流水线子进程
  （marker）或入库车道内部使用；`--lockbox/--lockbox-reason` 从 `run`/`compose`/`strategy run`/registry 全部移除。
- `lockbox roll` 去 `--quota-final` 与 admin 校验；`lockbox status` 输出新字段。
- 顶层/研究门面错误映射保持：`LockboxError → envelope`。

- [ ] Step1 测试：参数面消失（`describe` 无 lockbox 键、`--help` 无 `--lockbox`）；`roll --quota-final` → 未知参数；
  `final_mode=True`+marker → 登记；host `final_mode=False` 碰箱 → TEST_ONLY_FINAL。
- [ ] Step2 红 → Step3 实现（含 `_lockbox_guard_for_execute` 改写）→ Step4 聚焦绿 → Step5 提交
  `refactor(lockbox): CLI 参数面收敛（去探索/配额/管理员开关）（R42-T2）`。

---

### Task 3: 流水线接线（config=版本；一次；replay 短路；去 off 留痕）

**Files:** `research/tools/xscore/pipeline/xlib.py`、`flows.py`、测试。

**Interfaces:**
- `xlib.lockbox_register`：strict——`final_exists` 命中时：
  - 若 `out/manifest.json` 与产物已存在（replay）→ 返回既有 ctx（不登记、不报错，打印"复用最终测试"）；
  - 否则 → `LOCKBOX_FINAL_DUPLICATE`（提示改版本/`FACTORLAB_RE_FINAL=1`）。
- `lockbox_off` 留痕字段删除（保留 `FACTORLAB_LOCKBOX=off` 短路用于 CI/测试，语义=不登记 unknown）。
- marker：flow 进程 `FACTORLAB_PIPELINE=1`（保留）；无其他开关。

- [ ] Step1 测试（fake-prefect）：首跑登记 1 行；同 config 二跑（产物在）→ 零新增、无异常、日志含复用；
  产物被删后二跑 → DUPLICATE；`FACTORLAB_RE_FINAL=1` → 新增行；off → unknown/[] 且无 `lockbox_off` 键。
- [ ] Step2 红 → Step3 实现 → Step4 绿 → Step5 提交 `feat(pipeline): 最终测试一次接线（R42-T3）`。

---

### Task 4: 入库只看测试段冻结结果（admit/ref add）

**Files:** `platform/src/factorlab/research/factor.py`（`_lockbox_final_gate` 重写 → `_final_test_gate`）、
`platform/src/factorlab/app/analysis/cross_section.py`（增 `date_start` 过滤参数，供测试段诊断）、测试。

**Interfaces:**
- 新增冻结件：`<results_dir>/<name>_5y/test_diagnostics.json`
  `{version_fingerprint, window_id, window_start, date_start, date_end, corr_max, r2_lib, resic_t, resic_mean, n_weeks, created_at}`。
- `_final_test_gate(spec_doc, spec_path, *, reason, command)`：
  1) role IS → 放行（纯训练段入库不需要最终测试？——**否**：入库必须看测试段 → IS-only 因子不可入库，报 `LOCKBOX_TEST_ONLY_FINAL`）
  2) 无 final 登记 → **执行最终测试**：`flab factor run <spec>`（全窗，经流水线标记语义）→ 计算 `incremental_diagnostics` 的
     **测试段切片**（`date >= window_start`）→ 写 `test_diagnostics.json` → `register_access(kind=final, reason=...)`。
  3) 有 final 登记 → 校验冻结件存在且 `version_fingerprint/window_id` 一致 → 否则 `LOCKBOX_FINAL_REQUIRED`（提示重建）。
- `admit/ref add` 的入库判决改用冻结件里的测试段 `corr_max/r2_lib/resic_t`（`_admit_verdict` 不变，喂测试段数）。

- [ ] Step1 测试（沙箱）：无 final → admit 触发最终测试并落冻结件+登记；二次 admit 只读冻结件（零新登记）；
  冻结件缺失/版本不符 → 拒绝；测试段指标与冻结件一致（注入 fake diagnostics）。
- [ ] Step2 红 → Step3 实现 → Step4 绿 → Step5 提交 `feat(lockbox): 入库只看测试段冻结结果（R42-T4）`。

---

### Task 5: 残留清理（配额/探索/文档引用）

**Files:** 全仓 grep `QUOTA|quota_final|FACTORLAB_LOCKBOX_ADMIN|exploration|lockbox_off`；
`governance/ops/*`、`platform/tests/*`、`research/tools/xscore/tests/*`、R41 证据附注。

- [ ] Step1 列出残留清单（grep 输出贴报告）；逐项删除/改写（历史数据行保留，代码路径清除）。
- [ ] Step2 聚焦全测：lockbox/guard/store/cli/compose/strategy/admit + xscore + governance 全绿。
- [ ] Step3 提交 `chore(lockbox): 配额/探索残留清理（R42-T5）`。

---

### Task 6: 文档同步

**Files:** `knowledge/contracts/interface.md` §10 重写；`quantresearch/CONVENTIONS.md` §4；
`quantresearch/knowledge/pipeline-usage.md`；`.claude/skills/factor-mine` §7/§8；`playbook §4.1`；pipeline README。

- [ ] 按 spec §3–§5 更新：两段/探索拒/每版本一次/入库只看测试段/无配额；删除已不存在的 CLI 参数与错误码；
  明确 `flab factor admit` 会执行最终测试并冻结果。
- [ ] `make gates` / `research_tidy` / `check_lockbox --root` 全绿后提交 `docs(lockbox): R42 契约/手册/技能同步`。

---

### Task 7: R42 验收与证据

- [ ] 沙箱矩阵：探索碰测试段被拒；同版本二次最终测试拒；新版本可测；admit 触发最终测试（冻结件+登记）；
  二次 admit 只读；无配额路径（grep/`--help`）。
- [ ] 真实宿主（可选，若不 roll 则用现有窗口）：`flab factor admit <已有 IS-only 因子>` → 预期 `LOCKBOX_TEST_ONLY_FINAL`
  （证明入库必须测试段）；对一个真实 spec 走 pipeline final 一次并 replay 验证。
- [ ] 证据 `governance/evidence/verification/R42/README.md`（命令+输出+结论）；推送 + CI 全绿。

---

## Self-Review

- Spec §3 四条 → T1/T2/T3/T4；§4 砍项 → T1/T2/T5；§5 处置表 → 逐行映射到 T1–T6；§6 验收 → T7。
- 风险：admit 内执行最终测试会真跑测试段（每次新版本 1 次，符合设计）；pipeline replay 判定以"产物存在"为据，
  产物被外部删除时的报错已定义（DUPLICATE）。
- 未决（实现中遇阻再裁定）：最终测试的"评估指标"是否只落测试段（本计划：评估全窗运行、**指标只取测试段切片**并冻结）。
