# 数据质量流水线 M1（daily 纵切）实施计划（Plan DQ-M1）

> **实施状态（2026-09-19 收口）**：**已完成并终审通过**（SDD 执行，21 提交，d331f02+；含 3 轮修复 + 终审修复波）——两阶段门/健康 artifact（双枚举）/读取 fail-closed（含 opt-in 通道）/clean staging 为 ingest 物理来源/全史体检只审不改；`make data-update`（daily）与 `verify` **rc=0**（explained delta 16,090 = quarantine）；拒绝矩阵 16/16；证据 `governance/evidence/verification/R32/`；执行台账（全部 Ruling 与 deferred 项）：`.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/progress.md`。次要残留（N3-N6 与 D1-D13）见台账；M2/M3 未开始。

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** daily 数据从下载到"research-ready"的全链带上质量证明：两阶段门、health artifact（双维度）、读取 fail-closed。

**Architecture:** `platform/tools/data_quality/`（规则/校验/聚合/systemic/audit/health）+ `pan_update` 阶段链接入；
读取侧 `adapters/read/health.py::require_dataset` 薄断言；落盘 `data/{staging,quarantine,health}/`。

**Tech Stack:** Python 3.13（平台 venv）/ polars / pyarrow / pytest；腾讯日线 HTTP（唯一外部抽样源）。

**Spec:** `knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md`（冻结 v2）

## Global Constraints

- **TDD**：先失败测试后实现；规则用**表驱动正反例**（每条规则至少一个 PASS 样例 + 一个命中样例）。
- **只 mock 真正的外部依赖**（腾讯 HTTP）：fake transport + **断言请求 URL/参数**；其余用真实 parquet/CH。
- **写入 fail-safe**：任何 FATAL / PRE-INGEST FAIL → 分区不入 canonical；**stage 标记不落**。
- **读取 fail-closed**：默认 `accept_quality=("PASS",)`；DEGRADED/LEGACY opt-in 必须写 manifest。
- **不无痕改历史**：M1 只"体检+打标"，不重写任何历史分区；修复留 M3 定向。
- **一次提交一棵树**；证据 `governance/evidence/verification/R32/`。
- **语义红线**：同 PK 不同 payload 一律 quarantine（禁止 source precedence）；分布突变只 WARN/SUSPECT。
- 运行位置：`platform/tools/**`（工具树），读取侧改动在 `platform/src/factorlab/adapters/read/`。

---

### Task 1：dq_policy 与规则模型

**Files:** Create `platform/tools/data_quality/{__init__,rules}.py`、`platform/tools/data_quality/dq_policy.daily-v1.yaml`；Test `platform/tools/data_quality/tests/test_rules.py`

**Interfaces (Produces):** `load_policy(path) -> DqPolicy`；`RuleResult(rule_id, level, key, detail)`；`SEVERITY={FATAL,ERROR,WARN,INFO}`；`POLICY_VERSION`。

- [ ] **Step 1: 失败测试**：policy 缺字段 → `ValueError`（点名缺失项）；旧版本号加载 → 兼容/拒绝规则明确；规则目录含 §4 ①-⑦ 的 rule_id 常量。
- [ ] **Step 2: 红→实现**（YAML 直读 + dataclass；阈值字段名与 spec §3.2 完全一致）。
- [ ] **Step 3: 提交** `feat(dq): dq_policy 与规则模型（Plan DQ-M1 T1）`

### Task 2：daily 行级校验器（表驱动）

**Files:** Create `platform/tools/data_quality/validators.py`；Test `tests/test_validators_daily.py`

**Consumes:** Task 1 的 level/rule_id。**Produces:** `validate_daily(df, calendar, listing, limits) -> list[RuleResult]`（纯函数）。

- [ ] **Step 1: 失败测试**（每规则：正例 + 反例；INFO 例外）：
  ① PK 重复（identical → 可 dedup 标记 / payload 冲突 → ERROR 且**不自动选**）；② 非法交易日/上市前/退市后/时间倒序；
  ③ 价格≤0、vol/amount<0、inf/-inf、NaN 原因登记；④ OHLC 三式 + VWAP 越界；
  ⑤ 复权因子变化日与 CA 不符、复权序列负价；⑥ 涨跌停与板块/ST 规则不符（首日例外 → INFO）。
- [ ] **Step 2: 红→实现**（polars 向量化；输出稳定排序，便于分区聚合）。
- [ ] **Step 3: 提交** `feat(dq): daily 行级校验器（Plan DQ-M1 T2）`

### Task 3：确定性修复 + 行级隔离

**Files:** Create `platform/tools/data_quality/repair.py`；Test `tests/test_repair.py`

**Produces:** `repair(df, results) -> (clean_df, quarantined_df, repair_log)`；`write_quarantine(dataset, partition, rows, index)`。

- [ ] **Step 1: 失败测试**：完全相同行 dedup（保留策略：全列一致才删）；时间解析/时区规范化幂等；
  **PK payload 冲突 → 两行都进 quarantine**（断言 clean 中无该键）；quarantine 落盘结构 + 索引字段（rule_id/reason/count）。
- [ ] **Step 2: 红→实现**。
- [ ] **Step 3: 提交** `feat(dq): 确定性修复与行级隔离（Plan DQ-M1 T3）`

### Task 4：聚合指标 + 系统性检测 + PRE-INGEST 门

**Files:** Create `platform/tools/data_quality/aggregate.py`；Test `tests/test_partition_gate.py`

**Produces:** `aggregate(results, expected_count) -> Metrics`；`detect_systemic(metrics, rows, policy) -> SystemicDetail|None`；
`decide_pre_ingest(metrics, systemic, policy) -> "PASS"|"DEGRADED"|"FAIL"`。

- [ ] **Step 1: 失败测试**（边界值）：`error_rate` 恰等于 `pass.max_error_rate` → PASS（≤ 语义）；
  恰等于 `fail.min_error_rate` → DEGRADED（≤ 语义）；`coverage` 边界同款；
  `fatal_count>0` → FAIL；field 集中 81% 且 count>N → FAIL（SYSTEMATIC_FIELD_FAILURE）；
  group_rate > 10× 且 count>N → FAIL；孤立 1/20000 ERROR → **PASS**（不再见 quarantine 即 DEGRADED）。
- [ ] **Step 2: 红→实现**（判定顺序与 spec §3.1 逐字一致）。
- [ ] **Step 3: 提交** `feat(dq): 聚合/系统性检测/PRE-INGEST 门（Plan DQ-M1 T4）`

### Task 5：clean staging + pan_update 接入（daily）

**Files:** Create `platform/tools/data_quality/pipeline.py`（`run_clean_stage(...)`）；Modify `platform/tools/pan_update/stages.py`（daily 链插入 clean）；Test `tests/test_pipeline_stage.py`

- [ ] **Step 1: 失败测试**：伪 raw parquet → clean staging 落盘（`data/staging/ashare_daily/<partition>/`）；
  PRE-INGEST FAIL 时：**不产生 staging 完成标记、不触发 ingest**（spy 断言 ingest 未被调用）。
- [ ] **Step 2: 红→实现**（CLI/阶段链最小接线，`--dry-run` 不落盘）。
- [ ] **Step 3: 提交** `feat(dq): clean staging 与 pan_update 接线（Plan DQ-M1 T5）`

### Task 6：post-ingest audit + FINAL 门 + health 发布

**Files:** Create `platform/tools/data_quality/audit.py`、`health.py`；Modify `pan_update/stages.py`（audit/publish_health）；Test `tests/test_audit_health.py`

**Produces:** `audit_post_ingest(...) -> AuditMetrics`（completeness/PK/reconcile/腾讯抽样/漂移→SUSPECT）；
`publish_health(...)` → `data/health/ashare_daily/<partition>.json`（**字段与 spec §6 完全一致**，含双枚举）。

- [ ] **Step 1: 失败测试**：
  completeness（expected vs actual + `status` 独立）；腾讯抽样 fake（断言请求 URL/参数；偏差阈值 → SUSPECT 不 FAIL）；
  漂移检测仅产出 SUSPECT；FINAL 门复用 Task 4 判定 + `systematic_issue`；
  health JSON 键集断言（缺 `verification_state` 或 `completeness.status` → 测试失败）。
- [ ] **Step 2: 红→实现**（发布原子写；`raw_lineage.sha256` 取自 raw 文件）。
- [ ] **Step 3: 提交** `feat(dq): post-ingest audit 与 health 发布（Plan DQ-M1 T6）`

### Task 7：读取门 require_dataset + 产物记录

**Files:** Create `platform/src/factorlab/adapters/read/health.py`；Modify `app/run.py`、`app/backtest/backtest.py`（读取前断言）、`app/evaluate.py` 或 summary 组装处（追加字段）；Test `platform/tests/test_require_dataset.py`

- [ ] **Step 1: 失败测试**（拒绝矩阵）：PASS→过；DEGRADED 默认拒、opt-in 后过且 manifest 写入（断言五个字段）；
  FAIL/UNKNOWN→拒；`max_staleness` 独立生效（数据新但 DQ PASS 陈旧 → 拒）；`completeness.status != COMPLETE` → 拒；
  **禁止行为**：因子 run 不得自己重算 OHLC（spy 断言 validator 未被调用）。
- [ ] **Step 2: 红→实现**（只读 health JSON；异常文案含 dataset/partition/status/指引）。
- [ ] **Step 3: 提交** `feat(read): require_dataset 读取门与产物记录（Plan DQ-M1 T7）`

### Task 8：daily 全史体检（只审不改）+ 存量打标

**Files:** Create `platform/tools/data_quality/historical_audit.py`；报告落 `governance/evidence/verification/R32/historical-audit/`

- [ ] **Step 1: 失败测试**（小样本）：扫描给定分区范围 → 汇总问题清单（按日期/字段/规则计数）+ 影响分析骨架；
  **禁止行为断言**：审计过程不得写 canonical / 不得改 parquet（只写 health/verification 标记与报告）。
- [ ] **Step 2: 红→实现**；对存量分区写 `verification_state=LEGACY_UNVERIFIED, health_status=UNKNOWN`。
- [ ] **Step 3: 真跑 daily 全史**：出问题清单 + 影响分析（顶层：高影响问题清单，如 circ_mv/退市 adj/跨频）。
- [ ] **Step 4: 提交** `feat(dq): daily 全史体检与存量打标（Plan DQ-M1 T8）`

### Task 9：M1 端到端验收

- [ ] 真实 `make data-update`（daily 类别）：staging/quarantine/health 全产出；`verify` 绿；
- [ ] 反例注入：构造 FATAL 分区 → PRE-INGEST FAIL、canonical 未写入、stage 标记未落；
  构造 DEGRADED 分区 → 默认读取被拒、opt-in 通过且 manifest 完整；
- [ ] 读取拒绝矩阵表（PASS/DEGRADED/FAIL/UNKNOWN × freshness × completeness）；
- [ ] 证据 `governance/evidence/verification/R32/`；台账（reviews）登记执行状态。

## Self-Review（对 spec v2）

§1 严重度→T2；§2 聚合/systemic→T4；§3 门定义与 policy→T1/T4；§4 规则①-⑦→T2；
§5 流水线与两阶段门→T3/T5/T6；§6 health 双维度→T6；§7 读取门→T7；§8 历史治理→T8；
§10 M1 验收→T9。M2/M3 不在本计划。

## 风险

| 风险 | 处置 |
|---|---|
| 腾讯接口变更/限流 | 抽样量小 + 失败降级为 `SUSPECT`（不 FAIL）+ 明确报错；后续可换源 |
| 阈值初值误伤（DEGRADED 偏多）| 阈值集中在 policy；M1 真跑后按分布 calibration（只改值不改结构） |
| 读取门上线致存量停摆 | T8 先打标；LEGACY 可显式 opt-in（过渡条款） |
| clean staging 占盘 | 按分区保留 + 定期清理策略（M1 先不删，M3 定保留窗口） |
