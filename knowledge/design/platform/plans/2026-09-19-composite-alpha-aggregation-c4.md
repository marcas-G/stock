# Composite 聚合层 C4（Portfolio 接入 + 权重扩容）实施计划

> **实施状态（2026-09-19 收口）**：**C4 已完成并终审通过**（5 commits，7039932..8f2d447；170+479 tests；E2E 独立复算一致）——策略 `signal: composites/<name>` 全链打通、`score_weighted` 上线、入口预检按 kind 分派；证据 `governance/evidence/verification/R34/c4/`；台账 `.superpowers/sdd/2026-09-19-composite-alpha-aggregation-c4/progress.md`。C4b（top_k_buffered / market_cap_weighted / artifact frequency 显式）与 C2/C3 待启。

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 策略直接引用合成分数（`composites/<name>`）跑通"分数→选股→加权→调仓→回测"；权重参数面补 `score_weighted`。
**Spec:** `.../2026-09-19-composite-alpha-aggregation-design.md` §13/§17/§19（C4 决议逐字）。
**前序:** CX-C1 已交付（7 commits；R34）。

## Global Constraints
- 不改 `StrategySpec.signal_name` 契约（basename）；factor 行为零回归；composite 包成 SignalArtifact 复用 M7/M8。
- score_weighted：`s=signal×direction`、Top-K 后 `s'=max(s,0)` 归一、Σs'==0→all-cash；long-only。
- TDD 先红后绿；`POLARS_MAX_THREADS=1`；精确 git add；证据 `R34/c4/`。

### Task 1：策略引用 composite（T1，A 实现者）
**Files:** `core/strategy/doc.py`、`app/strategy/run.py`、tests（`test_strategy_doc.py`、`test_run_strategy.py`）
- [ ] `signal: composites/<name>` 自动识别（+显式 `signal_kind` 覆盖）；`StrategyDoc.signal_kind`（default factor）；
- [ ] 加载分派：factor→`load_signal_artifact(results_dir/name)`；composite→`runs/platform/composites/<name>/`（复用 `app/composite/artifact.read_composite_artifact`），包 `SignalArtifact(frame, meta)`；
- [ ] 失败清晰：kind 不符/产物缺失/非 composite 目录 → 点名报错；
- [ ] 测试：composite 引用成功（合成产物）；factor 路径零回归；prefix/kind 冲突处理。
- [ ] 提交 `feat(strategy): 策略引用 composite 信号（Plan CX-C4 T1）`

### Task 2：score_weighted（T2，B 实现者）
**Files:** `core/strategy/spec.py`（WeightingSpec Literal + 校验）、`core/strategy/constructor.py`、tests（`test_strategy_spec.py`、`test_portfolio_constructor.py`）
- [ ] `WeightingSpec.method: Literal["equal_weight","score_weighted"]`；
- [ ] constructor：`s=signal×direction`，Top-K 后 `s'=max(s,0)`，`w=gross×s'/Σs'`；**Σs'==0→all-cash**；
- [ ] 测试（手算）：归一逐值；全负/全零→all-cash（0 rows 但 decision_date 在）；与 equal_weight 的差异用例；方向翻转对称；
- [ ] 提交 `feat(strategy): score_weighted 加权（Plan CX-C4 T2）`

### Task 3：E2E（T3，串联）
- [ ] 示例策略 `research/strategy/cx_demo_score_weighted.yaml`（signal: composites/cx_demo；portfolio: score_weighted）；
- [ ] 真跑（CH 小窗）：run_strategy → NAV/持仓产物；`equal_weight` vs `score_weighted` 对照表；
- [ ] 证据 `governance/evidence/verification/R34/c4/`；提交 `docs(verification): R34 C4 端到端验收`

### Task 4：验收
- [ ] factor 策略零回归（既有 e2e）；composite 引用全链；score_weighted 手算一致；all-cash 边界；门相关项绿。
