# DQ 范围收窄（1996+ 非 BJ）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development 逐任务执行。
> 步骤用 `- [ ]` 跟踪。**TDD 铁律：先写失败测试（断言来自规格增补，不是实现），见红再实现。**
> 每任务收尾跑相关测试 + 提交（一次提交一棵树/一个主题）。

**Goal:** 研究范围冻结为 `trade_date >= 1996-01-01 ∧ 非 .BJ`，并完成 1996+ 全历史 health 发布，
使严格模式历史回测可跑通（#8 收窄、#25 验收）。

**Spec:** `knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md`（语义全权来源）
＋ `2026-09-18-data-quality-pipeline-design.md` §3.3/§5/§6/§7（除 scope 外不变）。

**技术栈:** Python 3.13（`platform/.venv`）、polars、pytest（`POLARS_MAX_THREADS` 不设置）、
CH 读路径 `FACTORLAB_DATA_BACKEND=ch`、重任务经 `governance/ops/heavy.sh`。

## Global Constraints（每任务隐含）

- 数据**不删除、不改写**范围外行；只改口径与发布物。
- 发布前先备份：`data/health/ashare_daily/` → `data/health/ashare_daily.bak-20260920/`（控制器执行）。
- DQ policy 版本升 `daily-v3`；health `dq_policy_version` 必须写 daily-v3。
- 测试单解释器：`platform/.venv/bin/python -m pytest`；tools 测试在仓根跑。
- 不加 `POLARS_MAX_THREADS`/heavy.sh 到 pytest（R36-CI-I1 教训）。
- 提交信息 `<type>(<scope>): …`；每任务只动自己的树。

## File Structure

- Create: `platform/src/factorlab/core/scope.py`（唯一事实源：常量+谓词）
- Create: `platform/tests/test_scope.py`
- Create: `platform/tools/data_quality/dq_policy.daily-v3.yaml`（v2 + `scope:` 块；默认政策切到 v3）
- Modify: `platform/tools/data_quality/{rules,pipeline,health}.py`（scope 过滤；`publish-history`）
- Modify: `platform/src/factorlab/adapters/read/health.py`（范围外拒绝）
- Modify: `platform/src/factorlab/adapters/read/universe.py`、`app/strategy/run.py`（BJ 默认排除）
- Modify: `knowledge/contracts/interface.md`、`knowledge/handbooks/factor-authoring-manual.md`
- Tests: `platform/tools/data_quality/tests/test_scope_ledger.py`、
  `platform/tools/data_quality/tests/test_publish_history.py`、
  `platform/tests/test_health_gate_scope.py`、`platform/tests/test_universe_exclude_bj.py`、
  `platform/tests/test_strategy_scope_filter.py`
- Evidence: `governance/evidence/verification/R37/**`

---

### Task 1: scope 单一事实源（core/scope.py + daily-v3 policy）

**Files:** Create `platform/src/factorlab/core/scope.py`、`platform/tests/test_scope.py`、
`platform/tools/data_quality/dq_policy.daily-v3.yaml`；Modify `platform/tools/data_quality/rules.py`（默认政策→v3）。

**Interfaces（Produces，后续任务依赖）:**
```python
# factorlab.core.scope
MIN_TRADE_DATE: datetime.date                      # 1996-01-01
EXCLUDED_CODE_SUFFIXES: tuple[str, ...]            # (".BJ",)
def is_in_scope_date(d: datetime.date) -> bool
def is_in_scope_code(ts_code: str) -> bool
def scope_expr(date_col: str = "trade_date", code_col: str = "code") -> pl.Expr
def filter_frame(df: pl.DataFrame, date_col: str = "trade_date", code_col: str = "code") -> pl.DataFrame
```

- [ ] 写失败测试 `platform/tests/test_scope.py`：边界（1995-12-29 False / 1996-01-01 True）；
  `"430047.BJ"` False、`"000001.SZ"` True；`filter_frame` 从混合帧剔除范围外行且不动行序；
  `scope_expr` 与 `filter_frame` 同真值（对拍）。
- [ ] 跑测试见红：`platform/.venv/bin/python -m pytest tests/test_scope.py -q` → FAIL（模块不存在）。
- [ ] 实现 `core/scope.py`（常量+谓词，纯函数，无 I/O）。
- [ ] `dq_policy.daily-v3.yaml` = v2 全文 + `scope: {min_trade_date: "1996-01-01", exclude_code_suffixes: [".BJ"]}`
  + 头注释说明裁定源；`rules.py` `POLICY_VERSION="daily-v3"`、`DEFAULT_POLICY_PATH` 指 v3；
  加载校验：v3 缺 scope 块/与 `core.scope` 常量不一致 → ValueError（测试锁定一致性）。
- [ ] 全量跑 tools/DQ 相关测试（政策版本引用处同步改），提交：
  `feat(core): scope 单一事实源（1996+ 非 BJ）与 daily-v3 政策`。

### Task 2: DQ 账本 scope 化 + `publish-history` 批量发布

**Files:** Modify `platform/tools/data_quality/{pipeline,health}.py`；
Tests `platform/tools/data_quality/tests/{test_scope_ledger,test_publish_history}.py`。

**Interfaces（Consumes: T1；Produces: T3/验收依赖）:**
```python
# pipeline.clean(...) 内部：raw 先 filter_frame 再 validate/repair；账本全部 scoped
# health.publish_history(*, dataset_id="ashare_daily", date_from, date_to=None,
#                        run_tag, root=None, resume=True) -> dict  # 报告：published/skipped/degraded/failed
# health.derive_partition_status(*, partition_quarantine: int, partition_expected: int,
#                                partition_has_error: bool, policy) -> str  # PASS/DEGRADED/FAIL
```

- [ ] 写失败测试（合成帧，不碰 CH）：
  - `test_scope_ledger.py`：混合帧（含 BJ/前 96/在范围内坏行）→ BJ 与前 96 不进
    quarantine、不进 expected/actual；在范围内坏行照旧 quarantine；`quality_backlog`
    分母 = scoped。
  - `test_publish_history.py`：注入假分区读取器（依赖注入或 monkeypatch `_read_canonical`），
    断言：每个在范围分区写出 `<partition>.json`；状态推导（0 隔离→PASS；1 隔离→DEGRADED；
    分区 audit ERROR→FAIL）；已 PASS 跳过（幂等）；`--force` 重发；summary.json 重建；
    范围外分区不写。系统性未过 → 整批拒绝（fail fast，不留半成品）。
- [ ] 见红后实现：pipeline scope 过滤；`health.publish_history` + CLI `publish-history
  --from YYYY-MM-DD [--to] --run-tag <tag> [--force]`；分区审查复用 `audit`/`_read_canonical`
  （scoped 版）；原子写 + summary 重建。
- [ ] `platform/.venv/bin/python -m pytest platform/tools/data_quality -q` 全绿；提交：
  `feat(dq): 账本 scope 化 + publish-history 批量历史发布（daily-v3）`。

### Task 3: 读取门范围外拒绝 + universe/策略 BJ 默认排除 + 文档

**Files:** Modify `adapters/read/health.py`、`adapters/read/universe.py`、`app/strategy/run.py`、
`knowledge/contracts/interface.md`、`knowledge/handbooks/factor-authoring-manual.md`；
Tests `platform/tests/test_health_gate_scope.py`、`test_universe_exclude_bj.py`、`test_strategy_scope_filter.py`。

**Interfaces（Consumes: T1）:**
```python
# require_dataset：partition < scope.MIN_TRADE_DATE → DatasetQualityError(
#   status="OUT_OF_SCOPE", guidance="早于数据集范围 1996-01-01（用户裁定 2026-09-20）")
# universe：rules 增 "exclude_bj"（默认 True；False 才纳入）；.BJ 一律不出现在 universe 帧
# strategy.run：组合前对信号帧应用 scope.filter_frame（已有产物兼容）
```

- [ ] 写失败测试：1995-12-29 分区读取 → OUT_OF_SCOPE 错误（文案含 1996-01-01）；1996-01-02
  正常走原逻辑；universe 帧无 `.BJ`（默认）且 `exclude_bj: false` 时保留；策略信号帧含 BJ 行
  → 组合产物不含 BJ。
- [ ] 见红后实现；`platform/.venv/bin/python -m pytest -q`（平台全量）绿。
- [ ] 文档同步（interface.md 两节 + 手册数据范围节，给出裁定引用与命令示例）；提交：
  `feat(read): 读取门范围外拒绝 + universe/策略默认排除 BJ；docs 同步`。

### Task 4: 55 行残余处置（RCA → 处置 → 系统性过门）

**Files:** `governance/evidence/verification/R37/residual-55/**`（RCA 证据）；Modify
`platform/tools/data_quality/{rules.py,dq_policy.daily-v3.yaml,repair.py}`（如需）与测试。

- [ ] RCA（只读）：从 quarantine 证据提取 55 行；19 行现代单位 bug 用 `bars_1m` 三角复核
  （对照同码正常日量/额比 ≈1）；36 行零星定性复核。产出 `R37/residual-55/rca.md` + 原始输出。
- [ ] 处置规则（TDD）：可确证的 19 行按 field-level 修复（单位换算，逐行证据）或登记
  registry（同 T2b 模式，附 n_match/dominance）；36 行保持隔离（披露）。
- [ ] **系统性必须过**：scoped 全范围 `detect_systemic` 输出 field_count < 50 且无
  time/group 系统性（证据存档）。
- [ ] 若 19 行修复需回写 canonical：走 `clean → ingest --source → verify`，断言
  canonical 行数变化、quarantine 55→36、`reconcile` rc=0（证据 `R37/rerun/`）。
- [ ] 提交：`fix(dq): 55 行残余定向处置（19 行单位 bug 确证修复/登记；系统性过门）`。

### Task 5: 全历史发布 + 严格模式回测验收（控制器主导）

- [ ] 备份 `data/health/ashare_daily/` → `.bak-20260920`。
- [ ] `heavy.sh platform/.venv/bin/python platform/tools/data_quality/health.py publish-history
  --from 1996-01-01 --run-tag scope20260920`（可续；记录耗时与分布：PASS/DEGRADED/FAIL 计数）。
- [ ] 抽查：2025-03 分区 PASS；`summary.json` 不含 UNKNOWN（范围外分区不写或原样保留并说明）。
- [ ] 严格模式（无 opt-in）：
  `flab strategy run "$QUANTRESEARCH_ROOT/strategy/low_lottery_top30_weekly.yaml" --out-dir runs/platform/strategies/low_lottery_scope_20260920 --pretty` → `ok:true`；
  或信号域内挑一个全 PASS 窗口（若 2025-03 有 DEGRADED 分区，先按 T4 处置或换窗并记录原因）。
- [ ] 验收测试：平台全量 + `make test-research` + `make gates` + 自托管 workflow dispatch 绿；
  证据 `R37/acceptance/**`（命令+原始输出）。
- [ ] 收口：#8/#25 评论验收结果（通过则关闭 #8 的修复部分/#25）；`findings.md` 如需新增
  finding 行（本次为计划执行，不开 finding）；STATUS/pending 更新；提交
  `docs(evidence): R37 范围收窄验收（发布分布 + 严格模式回测）`。

## Self-Review

- Scope 覆盖：规格增补 §1→T1，§2→T2/T4，§3→T3，§4→T3，§5→T5。无缺项。
- 无占位符：每任务有文件、接口、测试断言、命令。T4 的 RCA 结论依赖实测（19 行可证则修，
  不可证则登记），但两种路径的门槛（系统性过）与证据要求已冻结。
- 类型一致：`filter_frame/scope_expr`（T1）在 T2/T3 复用；`publish_history`/`derive_partition_status`
  （T2）在 T5 复用；`exclude_bj`（T3）与 policy `scope` 块（T1）一致。
