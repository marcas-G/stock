# Plan DQ-M1.5 T2 重跑与验收证据（daily-v2 字段级语义 + 早市容差 + trade_cal）

- 日期：2026-09-19 ｜ 分支：`restructure/monorepo`
- 裁定：控制者 2026-09-19（见 `knowledge/design/platform/plans/2026-09-18-data-quality-pipeline-m15.md`）
- raw 快照：`data/fact/daily_fact/daily_fact.parquet`，sha256
  `903ab1603b582c2500c6d3c26f417eccb6453b709dfa644e9e05bb2d6a82e323`（与 R32/R33 全程一致，未变）
- 重跑 run_tag：`20260919b`（v1 基线 `20260919` 保留不动）
- 红线：只跑 daily 链；Feature 层不动；读取门 `--accept-quality` 语义不动；重任务均过
  `heavy.sh`（FACTORLAB_MAX_MEMORY=8GB、oom_score_adj=700）；测试 `POLARS_MAX_THREADS=1`。

## 1. 前后关键数字表

| 指标 | v1（20260919，基线） | v2（20260919b，本次） | Δ | 说明 |
|---|---|---|---|---|
| raw 行数 | 18,230,232 | 18,230,232 | 0 | raw 全程未变 |
| canonical/clean 行数 | 18,214,142 | **18,223,564** | **+9,422** | 补偿 8,184 + VWAP 恢复 1,238 |
| quarantine 行数 | 16,090 | **6,668** | −9,422 | 子集关系（v2 无新增隔离） |
| `ADJ_NEGATIVE`（v1 ERROR） | 8,184 | — | — | v1 整行隔离 |
| `ADJ_NULLED`（v2 WARN + 字段置 NULL） | — | **8,235** | — | 8,184 负值（回 canonical）+ 51 零值（原已入 canonical，本次置 NULL/flag） |
| `HISTORIC_UNIT_EXCEPTION`（v2 WARN） | — | **1,160** | — | registry（code×era）表级全部命中；RCA 内 19 行 + RCA 外 **1,141 行** |
| `VWAP_OUT_OF_RANGE`（ERROR） | 7,906 | **6,668** | −1,238 | 1,160 registry + 78 pre-1995 容差恢复 |
| `ADJ_FACTOR_CA_MISMATCH`（WARN） | 43,827 | 43,827 | 0 | 不变 |
| `MISSING_VALUE`（WARN） | 1,251,010 | 1,251,010 | 0 | 不变 |
| warning_count | 1,294,837 | 1,304,232 | +9,395 | +8,235 ADJ_NULLED +1,160 registry |
| PRE-INGEST decision | DEGRADED | **FAIL** | — | 见 §5（残余 VWAP 类系统性集中） |
| `trade_cal` 行数（CH） | 8,773 | **8,791** | **+18** | raw 日期域全量；全隔离日不再消失 |
| `stk_limit` 行数（CH） | 17,950,797 | **17,958,979** | +8,182 | ingest 后按链步重派生 |
| verify（reconcile daily） | rc=0（旧口径） | **rc=0（新口径）** | — | 见 §4 |
| health（2026-09-17） | DEGRADED/VERIFIED（daily-v1） | **FAIL/VERIFIED（daily-v2）** | — | 见 §5 |

canonical 增量分解（精确、无重叠）：**9,422 = 8,184（adj 字段级）+ 1,160（registry）+ 78（pre-1995 容差恢复）**。
其中 VWAP 恢复 = 1,238 = 1,160 + 78；RCA 18 日内 23 行（19 registry + 4 容差），**RCA 之外 1,215 行**
（registry 1,141 + tol 74，逐行清单 `extra-recovered-rows.csv`，计数 `recovery-analysis.json`）。

## 2. 18 日 RCA 决议逐条核对（与 Task 1 报告对齐）

- **恢复 3 个整日**：1991-04-20 / 1991-06-01 / 1993-07-03（pre-1995 tol=2% 带内）✅
- **2 个混合日各恢复 1 行**：1991-04-13 `000001.SZ`（MARGIN，容差带内）、
  1991-05-04 `000002.SZ`（registry，factor 5）✅
- **11 日历史制度例外（标记）**：全行回 canonical 且仅 WARN + `HISTORIC_UNIT_EXCEPTION` ✅
- **4 条真坏行保持隔离**（0 恢复）：`000002.SZ|1991-04-13`、`000001.SZ|1991-05-04`、
  `000004.SZ|1991-11-17`、`000012.SZ|1993-08-21` ✅
- 机械核对（`analyze_recovery.py`）：18 日中 14 日整日恢复（3 恢复 + 11 例外），
  04-13/05-04 各 1 行，11-17/08-21 为 0；v2 隔离集合 ⊂ v1 隔离集合（无新增）✅

## 3. 命令与日志（全部可复现）

```bash
# 1) v2 全表 clean（policy daily-v2 默认）
FACTORLAB_MAX_MEMORY=8GB governance/ops/heavy.sh platform/.venv/bin/python \
  platform/tools/data_quality/pipeline.py clean \
  --raw data/fact/daily_fact/daily_fact.parquet --root data \
  --run-tag 20260919b --partition latest            # rc=1（decision=FAIL，见 §5）
# 2) canonical ingest（裁定 5/7：--source staging + --calendar-source raw）
FACTORLAB_MAX_MEMORY=8GB governance/ops/heavy.sh platform/.venv/bin/python \
  platform/tools/ch_ingest/ingest_daily.py \
  --source data/staging/ashare_daily/20260919b/daily_fact.parquet \
  --calendar-source data/fact/daily_fact/daily_fact.parquet
# 3) 链步重派生（ingest 消费新 daily 后）
FACTORLAB_MAX_MEMORY=8GB governance/ops/heavy.sh platform/.venv/bin/python \
  platform/tools/ch_ingest/derive_stk_limit.py
# 4) verify（reconcile daily；trade_cal 期望=raw 日期域）
platform/.venv/bin/python platform/tools/ch_ingest/reconcile.py daily \
  --source data/staging/ashare_daily/20260919b/daily_fact.parquet
# 5) health 发布（记录 repair_policy_version）
FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/python \
  platform/tools/data_quality/health.py publish --partition latest \
  --run-tag 20260919b --no-sample
# 6) 差异分析（只读） + 额外恢复行清单
platform/.venv/bin/python governance/evidence/verification/R33/t2-rerun/analyze_recovery.py \
  --old data/quarantine/ashare_daily/20260919/rows.parquet \
  --new data/quarantine/ashare_daily/20260919b/rows.parquet \
  --rca governance/evidence/verification/R33/rca-18-days.json \
  --out governance/evidence/verification/R33/t2-rerun/recovery-analysis.json
```

日志：`clean.log` / `ingest.log` / `derive_stk_limit.log` / `reconcile.log` / `health.log` /
`analyze.log`（同目录）。

## 4. verify（rc=0）与 explained delta

`reconcile.py daily --source data/staging/ashare_daily/20260919b/daily_fact.parquet` → **rc=0「全库一致」**：

```text
daily        CH=18,223,564 源=18,223,564 一致（explained delta: raw 18,230,232
             − clean 18,223,564 = 6,668 = quarantine 6,668 + deduped 0）
adj_factor   CH=18,223,564 源=18,223,564 一致
daily_basic  CH=18,223,564 源=18,223,564 一致
trade_cal    CH=8,791 源=8,791 一致（日期域=raw daily，含 clean 全隔离日）
stock_basic  CH=5,879 源=5,879 一致
stk_limit    CH=17,958,979 期望=17,958,979 一致
adj_detail/adj_event/delisted_adj 全部一致
```

## 5. 阻断性发现：PRE-INGEST / FINAL = FAIL（系统性检测）

- v2 把 ADJ 行从 ERROR 降为 WARN 后，**剩余 error 全部是 VWAP 历史/现代残余类**：
  `<=1996` 5,514（SH 4,805 / SZ 709）、`1997-2019` 24、`>=2020` 1,130（BJ 1,115）——
  `SYSTEMATIC_FIELD_FAILURE`（单一字段 amount 100%）+ `SYSTEMATIC_GROUP_FAILURE`
  （BJ error_rate 4.13e-3 > 全市场 3.66e-4 × 10，ratio≈11.3）双双命中 → **FAIL**。
- 该 FAIL 是 **v1 已存在、被 ADJ/VWAP 五五开掩盖的系统性问题**；本次语义修复使其显性化。
  FAIL 的 clean 仍写出候选 parquet 但**无 `_SUCCESS`**（fail-closed 未弱化）。
- 为完成控制者裁定的定向重跑（clean → ingest → verify），第 2/3/4 步以**显式操作者方式**消费
  staged parquet；canonical 不含任何本应隔离的行（6,668 全在 quarantine），增量 9,422 行为
  裁定准予恢复者。
- health 已按实数发布 `2026-09-17.json`：`health_status=FAIL`、`dq_policy_version=daily-v2`、
  `repair_policy_version=daily-v2`、`completeness=COMPLETE`（coverage 0.99963）、
  `error_rate=0.000366`、raw sha 未变。**读取门 §7 对 FAIL 不可 opt-in** → 默认阻断，
  交控制者裁定（系统性口径 vs M2/M3 对残余类的 RCA/修复）。

## 6. 额外恢复行（不得静默）——逐行清单

- `extra-recovered-rows.csv`（1,215 行 = registry 1,141 + tol 74）：
  - **registry_extra（1,141）**：`000002.SZ`/`000004.SZ` 且 `trade_date<1994-01-01` 的
    ratio≈5 单位约定行（RCA 18 日之外，表级 registry 语义的必然覆盖；裁定 2 的
    code 集合 × era 形式）；
  - **tol_extra（74）**：pre-1995 tol 1%→2% 在 18 日之外恢复的行（如 600653.SH 1991-03-01
    vwap 37.81 vs close 38.5，−1.8%；600601.SH 1991-04-01 +1.63%）。
- ADJ 补偿 8,184 行不逐行列表（规则类处置；计数与 v1 的 `ADJ_NEGATIVE=8,184` 精确相等）。

## 7. 测试

`POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest platform/tools/data_quality/tests
platform/tools/ch_ingest/tests platform/tools/pan_update/tests -q` → **517 passed**
（v1 基线 492；新增 25 例：policy-v2 结构/registry 校验、早市容差边界、registry 正反例、
字段级置 NULL/隔离行不动/幂等、pipeline 计数与 policy 贯通、health repair_policy_version、
trade_cal 日历源、reconcile raw 口径、pan_update 接线）。

## 8. 遗留担心（交控制者）

1. **FAIL 阻断链**（最高优先）：`make data-update` daily 链会在 clean 步 FAIL（systemic），
   不会自动 ingest；本轮 canonical 更新是裁定定向重跑的操作者显式执行。读取门现为 FAIL
   （不可 opt-in）。需裁定：系统性检测口径（field 归因/分组阈值 vs 全表 scope）或
   M2/M3 对残余 VWAP 类（BJ 1,115 + SH 6006xx 等 5,514 + 零星）的 RCA/修复。
2. **registry 表级范围**：裁定写的是「14 行 / 11 日（k=5 落带）」，但 registry 形式
   （code 集合 × era）在表级自然命中 1,160 行（RCA 内 19 + 外 1,141），全部为已登记
   代码/年代且经 ±10% 代码约定 guard（真坏行不洗白）。若控制者只想要 19 行，需要
   改为行级 registry——与「不得泛化」并不冲突，但会留下另外 1,141 行 000002/000004
   历史行继续隔离。请复核 §6 清单。
3. **tol 额外恢复 74 行**：多为 1991-1993 SH 早期代码 1-2% 偏离（金额取整类），清单已列；
   若认为其中存在真坏行，可据清单回退（policy 收窄）。
4. **health FAIL 的下游影响**：旧 artifact（daily-v1/DEGRADED）已被 v2/FAIL 替换；
   按 M3 作废链处理；在裁定前，DEGRADED opt-in 通道对 2026-09-17 不再可用。
5. **stock_basic.list_date** 仍按 clean 幸存行派生（裁定只要求 trade_cal）；当前 5,879 码
   计数未变，但语义上仍有「仅存于全隔离日的代码不入 stock_basic」的残余风险（本数据集无实例）。
6. R33/R32 的审计报告（`missing-classification.*`、historical-audit）仍是 daily-v1 口径数字；
   M3 应以 v2 重跑体检并比对。
