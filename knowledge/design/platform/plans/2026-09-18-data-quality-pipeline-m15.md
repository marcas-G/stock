# 数据质量流水线 M1.5（历史 hardening）实施计划（Plan DQ-M1.5）

> **实施状态（2026-09-19 收口）**：**acceptance ①②③ 全部 PASS**（审查独立复核）——18 日 RCA（恢复 3/保持隔离 4/历史例外 11）；ADJ 字段级（adj=NULL+flag，8,184 行回补）；1.25M 分群（SOURCE_LIMITATION 1,146,706 / EXPECTED 104,304 / TRUE_ERROR 收窄为"在市码 0"）。canonical +9,422（18,223,564）、quarantine 16,090→3,828、trade_cal 8,791、verify rc=0。**残余 FAIL（BJ 1,115 真坏 + 前 1996 残余 2,658 + 1996+ 55）按用户裁定挂 M3 数据修复；日更链暂时阻断（clean 步 FAIL）待解**。提交：386361a..a657dd7；证据 `R33/`；台账 `.superpowers/sdd/2026-09-18-data-quality-pipeline-m15/progress.md`。

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在 M1（能发现/阻断）之后，修最明显的规则误判、冻结字段级处置语义、把 1.25M 历史命中做
root-cause 分群；**不做批量历史修复**（留给 M3）。

**Spec:** `knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md`（冻结 v2）
+ 本计划 §Acceptance（用户 2026-09-19 裁定，逐字）。
**前序**：Plan DQ-M1 已实施终审（21 提交；证据 R32；台账 `.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/progress.md`）。

## Acceptance（用户 2026-09-19，逐字）

```text
① 18 个全隔离日：
   每个都有 root cause
   → 恢复 / 保持隔离 / 标记历史制度例外
② ADJ_NEGATIVE：
   冻结 field-level invalidity 语义
   → 不再因单个 adj 字段坏掉整行
③ 1.25M 命中：
   完成分类统计 + 影响范围 + 修复候选清单
   → 不要求本阶段全部修完
```

节奏：**M1 → M1.5（本计划）→ M2（minutes/calibration）→ M3（系统化历史修复与重建）**。

## Global Constraints

- **只审优先**：Task 1/3 纯只读分析（不写 canonical/不重写 parquet）；只有 Task 2 允许规则/repair 变更
  与 canonical 重跑（授权范围）。
- **重任务纪律**：先查可用内存 + `FACTORLAB_MAX_MEMORY=8GB`；与团队 R09 bench 错峰；测试用
  `POLARS_MAX_THREADS=1` 规避旧存线程敏感。
- **留痕**：语义变更必须 bump `dq_policy_version`（daily-v1 → daily-v2）并在 health `raw_lineage`
  之外记录 `repair_policy_version`；下游产物按 M3 作废链处理（本阶段不批量作废）。
- 不改 Feature 层处理；不弱化 M1 门的 fail-closed 语义（读取门 `--accept-quality` 语义不动）。
- 一次提交一棵树/主题；证据落 `governance/evidence/verification/R33/`。

## Task 1：18 个全隔离日 RCA（只读）

**Files:** `platform/tools/data_quality/rca_full_quarantine_days.py`（新，只读脚本）；
报告 `governance/evidence/verification/R33/rca-18-days.md`；Test `tests/test_rca_days.py`（解析/分类纯函数）

- [x] 逐日（1991-01-19..1993-08-21 共 18 日）取 raw 行（1-2 行/日），复算 `VWAP=amount/volume` 与
  当期 OHLC 关系，核对早期市场制度（1996 前无涨跌停/T+0、面值/单位、volume 单位股/手）；
- [x] 每日结论三选一并给证据：`恢复（规则误判）` / `保持隔离（数据确坏）` / `历史制度例外（标记）`；
  汇总为"规则修正候选"（如早市 VWAP 容差/单位例外）——**Task 1 不改规则**。

## Task 2：ADJ_NEGATIVE 字段级语义冻结 + 定向重跑

**Files:** `data_quality/{rules,repair,validators}.py`（adj 字段级处置）、`dq_policy.daily-v2.yaml`；测试；证据

- [x] **语义**：`adj_factor<=0` 不再整行 quarantine → **该字段置 NULL + quality flag**（计数
  `ADJ_NULLED`），OHLCV 正常则行保留；`adj=NULL` 的行为与既有缺失语义一致（下游可判）。
- [x] TDD：单测（坏 adj 行保留、adj=NULL、flag 计数、raw OHLCV 不变）；策略版本 bump（daily-v2）；
- [x] **定向重跑**：full-table clean → ingest `--source` → verify；断言 canonical 恢复 8,184 行（adj NULL）、
  quarantine 数下降、explained delta 更新、verify rc=0；
- [x] 18 日修复（若 Task 1 判定"规则误判"）并入本任务重跑。

> T2 落地（2026-09-19）：daily-v2 policy（早市 VWAP 2%、registry、ADJ 字段级）；重跑
> canonical +9,422（8,184 adj + 1,160 registry + 78 tol）、quarantine 16,090→6,668、
> trade_cal 8,773→8,791（raw 日期域）、verify rc=0；证据
> `governance/evidence/verification/R33/t2-rerun/`（含 FAIL 系统性发现与额外恢复行清单）。
> **T2b（残余 VWAP RCA）**：registry 追加 14 条前 1996 单位 regime（+2,840 回 canonical，
> quarantine 6,668→3,828，verify rc=0）；BJ 1,115 经 bars_1m 三角验证为**真坏**
> （daily amount/volume 损坏）→ 保持隔离，M3 用 minutes 重建；门仍 FAIL（检测器未调参）。
> 证据 `R33/residual-vwap-rca.md` + `R33/t2b-rerun/`。

## Task 3：1.25M 命中 root-cause 分群（只读）

**Files:** `platform/tools/data_quality/classify_missing.py`（新，只读）；报告 `R33/missing-classification.md`；
Test `tests/test_classify_missing.py`（分类纯函数）

- [x] 分群：`EXPECTED_MISSING / SOURCE_LIMITATION / TRUE_ERROR / LEGACY_SCHEMA`（规则可配置），
  按字段（amount/adj/float_shares）、退市状态、市场、年份分布统计；
- [x] 影响范围：哪些研究/因子消费这些字段（引用现状）+ 每类修复候选（可否 backfill、源、成本、优先级）；
- [x] **不修数据**。

## Task 4：验收与证据

- [x] ① 18 日：每有 root cause 与结论（恢复/保持/例外）✅
- [x] ② ADJ：field-level 语义已冻结（单测 + 重跑 + verify）✅
- [x] ③ 1.25M：分类统计 + 影响范围 + 修复候选清单 ✅
- [x] 回归：全量测试（POLARS_MAX_THREADS=1）+ 门（G-* 中与本计划相关项）；证据 `R33/`；台账登记。

## 风险

| 风险 | 处置 |
|---|---|
| 早期数据制度资料不足 | 以证据分级（充分/推断/未知），未知者保持隔离并登记 |
| adj 置 NULL 影响下游复权 | 与既有缺失语义一致；档案/接口注明 dq_policy v2；下游按缺失处理 |
| 重跑影响在读实验 | 重跑仅 daily 表；读取门默认拒 DEGRADED/UNKNOWN，影响可审计 |

## Task 5（M1.5c，用户 2026-09-19 批准）：门控作用域修订

**Spec 修订**：设计 §3.3（门判 delta；全表→`quality_backlog` 披露；无新数据→PASS(note)；阈值/检测器不动）。
**Files**：`data_quality/{aggregate,pipeline,health}.py`（delta 计算与 backlog 组装）、health 键集与测试、`R33/m15c/**`。
**DoD**：
- [x] delta=新增 trade_date 行（首跑=全量）；门在新口径下判定；
- [x] health 增 `quality_backlog`（full_table 披露，不参与判定）；键集断言同步；
- [x] 无新数据 → PASS + `note: no_new_data`；
- [x] 重跑 daily 链：clean→ingest→verify，`make data-update`（daily）恢复 rc=0；health=PASS/DEGRADED；
      （注：`make data-update` 全链 rc 仍受网盘 sync 412/403 外部阻断；daily 链
      build+publish+verify rc=0，health=PASS/no_new_data，见 R33/m15c/）
- [x] delta 内注入系统性反例 → 仍 FAIL（不弱化证明）；
- [x] 证据 `R33/m15c/`（命令+输出+注入对照）。

> **M1.5c 收口（2026-09-19）**：门控作用域=更新增量 + `quality_backlog` 披露已实现（f1c4eed/3726845）；
> `make data-update`（daily 链）**rc=0**，health=PASS(no_new_data)/backlog 3,828，默认读取恢复；注入反例证明未弱化。
> 3 条非阻断小项（delta dedup 边界/§3.3 措辞/水位 ISO 校验）转 M2；残余历史修复（BJ 1,115 + 前1996 2,658 + 1996+ 55）转 M3。
