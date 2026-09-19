# 数据质量与清洗流水线 设计文档 v2（DQ Pipeline / Plan DQ）

日期：2026-09-18 ｜ 状态：**设计冻结 v2**（v1 经用户逐条评审，8 处收紧全部采纳）
范围：daily + minutes + fund_flow + financials；**tick/L2 后置**。
目标：**被特征因子层消费的数据，其"可用性"可证明、可追溯、可阻断**——写入链 fail-safe，
发布显式 health；读取侧 fail-closed。

## 0. 总原则（冻结）

1. **只修确定错误，不做统计美化**：winsorize/rank/zscore/neutralize/lookback/NaN 处理全属 Feature 层。
2. **Raw DQ 与 Feature preprocessing 分离**；raw 层不碰统计极端值。
3. **写入侧证明数据可用；读取侧只验证证明有效**（不重复跑检查）。
4. **读取侧默认 fail-closed**：默认只接受 `PASS`；`DEGRADED` 必须显式 opt-in + 强制质量声明。
5. **增量硬门 + 全史体检（只审不改）**；定向修复；不无痕重写。
6. **每日产出质量统计**（缺失率/重复/异常 OHLC/PK conflict/跨频不一致）。
7. **`canonical store 已写入` ≠ `research-ready`**：只有 `canonical + post-ingest audit 通过 +
   health artifact 已发布` 才构成 **published data_version**。读取层问的是"哪个 data_version
   已成功发布为 research-ready"，而不是"库里有没有 2026-09-18"。

## 1. 记录级严重度（Record Validation）

| 级别 | 例子 | 处置 | 阻断 |
|---|---|---|---|
| **FATAL** | schema 错、单位错、PK 大面积冲突、时间轴错位、整分区重复/缺失 | 整分区 quarantine | 是 |
| **ERROR** | 单行负价、OHLC 矛盾、单行 PK conflict | 行级 quarantine | 达阈值（见 §3） |
| **WARN** | 可解释极端值、跨源小偏差、可解释跳变、**分布突变（SUSPECT）** | 保留 + flag | 否 |
| **INFO** | 停牌、除权、ST 状态变化等正常特殊状态 | 保留 + 元数据 | 否 |

## 2. 聚合指标与系统性检测（先聚合、后判级）

**记录级问题 → 聚合指标 → 系统性检测 → 分区门**（不从 ERROR 直接猜 FATAL）：

```text
Record Issues → Aggregate Metrics → Systemic Detector → Partition Gate
```

**聚合指标**（每分区）：`fatal_count / error_count / warning_count / quarantine_count /
error_rate(=error_count/expected_count) / coverage(=actual/expected) / unresolved_partition_error`。

**系统性检测器**（可计算；阈值入 policy）：

| 维度 | 度量 | 规则（初值）|
|---|---|---|
| 字段集中 | 单一字段占全部 error 占比 `C_field` | `C_field > 0.80 且 error_count > N_field` → `SYSTEMATIC_FIELD_FAILURE` |
| 分组集中 | 某 group（板块/市场）error_rate vs 全市场 | `group_rate > market_rate × K 且 group_count > N_group` → `SYSTEMATIC_GROUP_FAILURE` |
| 时间集中 | 单日/单时段占错误比例 `C_time` | `C_time > 0.80 且 error_count > N_time` → `SYSTEMATIC_TEMPORAL_FAILURE` |

输出 `systematic_issue: bool`（+ 明细）；**由 Partition Gate 判 FAIL**。

**分布突变的正确用法**：`distribution shift` 只产生 **WARN/SUSPECT**，绝不直接 FAIL；
仅当**同时**出现下列证据时升级 `SYSTEMATIC_ISSUE`：单位变化 / 跨源不一致 / 跨频不一致 /
schema metadata 变化。**统计分布只负责发现异常，不负责裁定数据错误。**

## 3. 分区质量门（Partition Quality Gate）

**两个门**：`PRE-INGEST PARTITION GATE`（决定能不能入 canonical）与
`FINAL PARTITION GATE`（决定能不能发布 research-ready）。

### 3.1 判定定义（显式、互斥、可计算）

```text
PASS
- fatal_count == 0
- error_rate <= pass.max_error_rate
- coverage >= pass.min_coverage
- systematic_issue == false
- unresolved_partition_error == false

DEGRADED
- fatal_count == 0
- pass.max_error_rate < error_rate <= fail.min_error_rate
  OR coverage 落在 degraded 区间
  OR 存在已隔离但不系统的质量问题（quarantine_count > 0 且 systematic_issue == false）

FAIL
- fatal_count > 0
  OR error_rate > fail.min_error_rate
  OR coverage < (1 - fail.max_missing_coverage)
  OR systematic_issue == true
```

> 说明：`quarantine_count > 0` 不再自动等于 DEGRADED——是否 PASS 由 `error_rate` 与
> `pass.max_error_rate` 比较决定（1/20000 的孤立坏行可 PASS；1/5000 落 DEGRADED）。

### 3.2 `dq_policy`（字段必须存在；数值先给初值，后续 calibration 只改值不改结构）

> **M1.5 daily-v2（2026-09-19 控制者裁定）**：早市 VWAP 容差、历史单位约定例外
> registry、ADJ 字段级处置入 policy；加载器对 daily-v1 **明确拒绝不映射**
> （v1 文件保留作历史）。结构变更如下（语义见 §4④/§4⑤）。

```yaml
dq_policy_version: daily-v2
partition_gate:
  pass:
    max_error_rate: 0.0001        # PASS 上界
    min_coverage: 0.999
  fail:
    min_error_rate: 0.01          # FAIL 下界（之间 = DEGRADED）
    max_missing_coverage: 0.01
systemic:
  field_share: 0.80
  field_count: 50
  group_ratio: 10.0
  group_count: 50
  time_share: 0.80
  time_count: 100
vwap:
  default_tol: 0.01
  pre_1995_tol: 0.02
  pre_1995_cutoff: "1995-01-01"
field_invalidity:
  null_on_nonpositive: ["adj_factor", "fq_factor"]
historic_unit_exceptions:                 # 不得泛化到未登记代码/年代
  - codes: ["000002.SZ", "000004.SZ"]
    before: "1994-01-01"                  # 开区间：trade_date < before
    factor: 5.0
    match_tol: 0.10
```

## 3.3 门控作用域（v2.1，2026-09-19 用户批准）

**背景**：I1 修复后 clean 改为全表，若门在全表口径判系统性，历史制度性残余（BJ 1,115 真坏 +
前 1996 残余 2,658 + 1996+ 55）会阻断每日新数据与默认读取——历史毛病不应拦住今天的数据。

**冻结语义**：
- **门（PRE-INGEST + FINAL）判定作用域 = 本轮更新增量**：`delta = {rows | trade_date > 上次成功发布的
  freshness.latest_trade_date}`；无发布历史时 delta = 全部行（首跑等价全表）。
- delta 上计算：`error_rate / coverage / completeness / systemic(field,group,time)` → 门结果
  （PASS/DEGRADED/FAIL）。**阈值与检测器逻辑不变；delta 内出现系统性照旧 FAIL 阻断**（不弱化）。
- **全表口径**改为披露项：写入 health 顶层新块 **`quality_backlog`**（`scope: full_table`、counts、
  error_rate、systemic_detail、top_classes），**不参与门判定**；health 其它键集沿用 §6 + 本块。
- **无新数据**（delta 空）：`health_status = PASS`（附 `note: no_new_data`），`quality_backlog` 照常披露。
- 读取门只看 `health_status` + `completeness.status` + `freshness`（不变）。
- 历史残余处置仍归 M3（BJ 用 minutes 重建；前 1996 残余逐码分类后处置）。

## 4. 规则目录（8 类；本期 1-7，tick 后置）

### ① 主键与重复
- 日线 `symbol+trade_date`；分钟 `symbol+timestamp+freq` 唯一（逐笔后置）。
- **完全相同记录 → 自动 dedup（确定性修复）**；
- **同主键不同 payload → 一律 quarantine，不属于 deterministic repair**。**禁止**在 cleaning 中
  塞入"source precedence/谁更新选谁"——如未来需要，另立 **Source Arbitration Policy**。
- （tick）sequence 重复/倒序/断档。

### ② 时间与交易日历
- 时间戳可解析、时区统一；`trade_date` 合法交易日；上市前无行情、退市后无正常行情；
- 分钟/逐笔符合数据源 session；时间倒序/跨日错位/上午落下午 → 检查；
- **供应商 timestamp 语义**（事件/接收/bar 结束）确认并落契约，不猜；
- 停牌日 bar/零成交语义按数据源验证并落契约。

### ③ 基础数值合法性
- 价格>0（除格式明确允许占位值）；`volume≥0`、`amount≥0`；禁 inf/-inf；
- 核心字段 NaN → **记录原因，不 fillna(0)**；单位全历史一致（股/手、元/千元）；
- 全市场 volume ~100× 突增 → SUSPECT（配合 §2 升级条件）。

### ④ OHLC 与内部一致性
- `Low ≤ Open,Close ≤ High`、`Low ≤ High`；
- `volume>0 且 amount>0` → `VWAP=Amount/Volume` 落在当期价格范围附近；
  **v2 容差带**：`trade_date >= vwap.pre_1995_cutoff`（1995-01-01）用
  `default_tol=1%`；`trade_date < cutoff` 用 `pre_1995_tol=2%`（覆盖上界
  `0.02/1.02≈1.96pp`；R33 证据：1991-04-20 / 1991-06-01 / 1993-07-03 整日与
  1991-04-13 边际行属规则误判，恢复）；
- **v2 历史制度例外 registry**：命中 `historic_unit_exceptions`（code 集合 ×
  era，`trade_date < before`）且 `vwap/factor` 落在 `[low,high]×(1±match_tol)`
  的行 → 降级 `HISTORIC_UNIT_EXCEPTION`（WARN + flag，**行保留入 canonical**）；
  未登记代码/年代、或背离代码约定（真坏行）仍为 `VWAP_OUT_OF_RANGE` ERROR；
- 日线 ↔ 分钟聚合：`high≈max / low≈min / volume≈sum / amount≈sum`（容差，不要求浮点相等）。

### ⑤ 收益跳变与复权/公司行为
- 禁止"|return|>x 直接删"；先查除权除息/送转/拆并/配股；
- **raw 与复权分开存**；复权因子变化日与 CA 日期一致；复权序列负价/断点/**lookahead 回写**检查；
- **v2 字段级语义（ADJ_NULLED）**：`adj_factor`/`fq_factor` ≤ 0 → 该**字段**置
  NULL + flag `ADJ_NULLED`（WARN，计数入 health quality/rules），OHLCV 不动、
  **整行保留**（不再因单个 adj 字段坏掉整行 quarantine；`adj=NULL` 与既有缺失
  语义一致，下游按缺失消费）。

### ⑥ 证券状态与市场规则一致性
- **ST 必须 PIT**；上市/退市/停牌/复牌与行情匹配；
- 涨跌停由**日期+板块+ST+特殊阶段**决定，不固定 10%；越界先报警；
- 首日上市/恢复上市/特殊证券允许例外；目标：识别"不可能的状态组合"。

### ⑦ 跨源/跨频率验证
- **腾讯行情抽样**：daily raw OHLC 抽样对拍（免费；格式见腾讯日线接口）；
- **内建分钟↔日线对拍**（同 ④）；
- 系统性同向偏差优先于单票异常。

### ⑧ 逐笔/L2（后置）

## 5. 流水线（写入链，两阶段门）

```text
FETCH → RAW STAGING → STRUCTURAL VALIDATION → RECORD VALIDATION
→ DETERMINISTIC REPAIR → ROW QUARANTINE → CLEAN STAGING
→ PRE-INGEST PARTITION GATE → CANONICAL INGEST
→ POST-INGEST AUDIT（completeness / PK / reconciliation / cross-source sampling /
                     distribution drift detector → SUSPECT / systemic issue detector）
→ FINAL PARTITION GATE → HEALTH ARTIFACT → PUBLISH RESEARCH-READY
```

- **ingest 前后职责不同，缺一不可**：前=防脏数据入库；后=确认最终数据集整体可信。
- **确定性修复清单（穷尽）**：完全重复行去重；时间解析/时区规范化；schema/类型强制；
  其余（含 PK payload 冲突）一律 quarantine。
- 落点：`data/staging/<dataset>/<partition>/`、`data/quarantine/<dataset>/<partition>/`（+索引）、
  `data/health/<dataset>/<partition>.json`（+数据集汇总）。
- 单写者/幂等沿用 flock + 阶段标记 + `_SUCCESS`。

## 6. Health Artifact（发布契约，双维度）

```json
{
  "dataset_id": "ashare_daily",
  "partition": "2026-09-18",

  "data_version": "v20260919_01",
  "dq_policy_version": "daily-v2",
  "repair_policy_version": "daily-v2",

  "health_status": "PASS | DEGRADED | FAIL | UNKNOWN",
  "verification_state": "VERIFIED | LEGACY_UNVERIFIED | KNOWN_ISSUE",
  "note": null,

  "completeness": {
    "status": "COMPLETE | INCOMPLETE | UNKNOWN",
    "expected_count": 5231,
    "actual_count": 5230,
    "coverage": 0.9998
  },

  "quality": {
    "fatal_count": 0, "error_count": 1, "warning_count": 4, "quarantine_count": 1,
    "error_rate": 0.00019, "systematic_issue": false,
    "systemic_detail": null
  },

  "quality_backlog": {
    "scope": "full_table",
    "expected_count": 18230232, "actual_count": 18226404,
    "fatal_count": 0, "error_count": 3828, "warning_count": 1307072,
    "quarantine_count": 3828, "error_rate": 0.00021,
    "systemic_detail": "字段集中：…（历史残余，不参与门判定）",
    "top_classes": [{"rule_id": "VWAP_OUT_OF_RANGE", "count": 3828}]
  },

  "freshness": {"latest_trade_date": "2026-09-18"},

  "rules": {"OHLC_INVALID": 0, "PK_CONFLICT": 1, "UNIT_SUSPECT": 0},

  "validated_at": "...",
  "raw_lineage": {"source_version": "...", "raw_sha256": "..."}
}
```

> **门作用域注记（2026-09-19 M1.5c §3.3，用户批准）**：`quality.*` 与
> `completeness.*` 为 **delta**（`trade_date > 上次成功发布 freshness`，首跑=全量）口径，
> 门的 PASS/DEGRADED/FAIL 只由 delta 决定；`rules` 同为 delta 命中。全表口径移到顶层
> **`quality_backlog`**（`scope: full_table`，键集 = `scope/expected_count/actual_count/
> fatal_count/error_count/warning_count/quarantine_count/error_rate/systemic_detail/
> top_classes`），**只披露、不参与判定**；无新数据（delta 空）→ `health_status=PASS` +
> `note: "no_new_data"`，backlog 照常披露。读取门三腿（health_status + completeness.status +
> freshness）不变。
>
> **scope 注记（2026-09-19 修复轮 1 F5；M1.5c 起由 delta 口径取代）**：M1 曾以
> **full_table** 作为 `quality.*`/`completeness.*` 判定口径（expected = raw 全表、actual =
> clean 行数、差额须被 quarantine + dedup 完全解释）；M1.5c 后该职责整体由
> `quality_backlog` 承接，判定口径改为 delta（见上）。分区级 PK / 腾讯抽样 / 漂移仍按
> `partition` 审查。
>
> **repair_policy_version 注记（2026-09-19 M1.5 T2）**：修复/清洗语义版本留痕，与
> `dq_policy_version` 并列（缺省同值）；语义变更（字段级处置/容差/例外）必须 bump
> 此版本，健康档案可据此判断消费的数据是哪一版修复规则的产物。

**两个枚举是不同维度，不得互相映射**：
- `health_status`：该 partition 按某版 policy 检查后的质量（PASS/DEGRADED/FAIL/UNKNOWN）；
- `verification_state`：该数据经历的治理状态（VERIFIED/LEGACY_UNVERIFIED/KNOWN_ISSUE）。
- 示例：`verification_state=LEGACY_UNVERIFIED, health_status=UNKNOWN`；
  或 `KNOWN_ISSUE, DEGRADED`。

## 7. 读取门（Research-ready contract）

```text
Readable = HealthValid AND FreshEnough AND Complete
```

| health_status | 默认 | 显式 opt-in |
|---|---|---|
| PASS | ✅ | — |
| DEGRADED | ❌ | `accept_quality=["PASS","DEGRADED"]` |
| FAIL | ❌ | 不可 opt-in |
| UNKNOWN | ❌ | 仅 LEGACY 存量：任务显式声明（过渡期条款） |

**薄 API（Data Access 层统一）**：

```python
require_dataset(
    dataset="ashare_daily",
    as_of="2026-09-18",
    accept_quality=("PASS",),      # 默认 fail-closed
    max_staleness="1d",
)
```

- `completeness.status` 独立检查（不靠 coverage 猜）；
- DEGRADED opt-in 自动写 Experiment Manifest：`dataset_quality / quality_issues /
  affected_partitions / dq_policy_version / override_reason`；
- 场景强制：正式 OOS / 策略验收 / Production Replay / 上线前复核 / 基准实验 → **PASS-only**；
  探索性研究可显式放宽；
- factor/backtest summary 追加：`dataset_version / quality_status / quarantined_rows /
  coverage / cleaning_policy_version`。

## 8. 历史数据治理

- **增量**：完整链硬门。
- **全史体检（只审不改）**：输出问题清单 + 影响分析（按日期/股票/数据集：schema/PK、缺失率、
  OHLC、volume/amount、上市退市/停牌冲突、CA/adj、分钟↔日线偏差、外部抽样、系统性口径断点）。
- **三状态**：`VERIFIED / LEGACY_UNVERIFIED / KNOWN_ISSUE`（与 health 双维度，见 §6）。
- **定向往修**：circ_mv、退市 adj、跨频系统偏差等；**不首轮全史重跑**。
- **修复留痕**：`source_version / repair_policy_version / repair_reason / old_hash / new_hash /
  affected_partitions`；health artifact 同步记录 `repair_policy_version`（§6，M1.5 T2）。下游
  `FeatureFrame/Alpha/Backtest Artifact` 失效或标记重算。
- **过渡条款**：存量无 health 分区 → `verification_state=LEGACY_UNVERIFIED, health_status=UNKNOWN`；
  读取默认拒；任务可显式声明接受（自动写 manifest + override_reason）。M1 先跑 **daily 全史体检**打标。

## 9. 模块落点

```
platform/tools/data_quality/
    rules.py        # 规则目录 + severity + dq_policy 加载/版本
    validators.py   # 行级/字段级校验（纯函数、表驱动）
    aggregate.py    # 聚合指标 + systemic detector
    audit.py        # post-ingest 审计（completeness/PK/reconcile/抽样/drift→SUSPECT）
    health.py       # health artifact 读写 + 两阶段 gate
platform/tools/pan_update/
    stages.py       # 阶段链接入 clean / audit / publish_health
platform/src/factorlab/adapters/read/health.py
    require_dataset()
```

## 10. 里程碑与验收

| 期 | 内容 | 验收 |
|---|---|---|
| **M1** | daily 纵切：规则①-⑦（daily 子集）+ 两阶段门 + health（双维度）+ 读取断言 + **daily↔腾讯 raw OHLC 抽样** + **daily 全史体检打标** | 真 `make data-update` 出 health；规则表驱动正反例全绿；注入 FATAL → PRE-INGEST FAIL 不入 canonical；DEGRADED 默认被拒、opt-in 后 manifest 字段齐；LEGACY_UNVERIFIED 默认拒 |
| **M2** | minutes：分钟规则 + **minutes→daily 内部对拍**（三角验证：分钟聚合 vs 内建日线 vs 腾讯日线） | 容差内一致；系统偏差 → FINAL FAIL；分钟 health 发布 |
| **M3** | fund_flow/financials + 历史三状态全量标注 + 定向修复（circ_mv/退市 adj/跨频偏差） | 全史体检报告（问题清单+影响分析）；修复留痕与下游作废生效 |

## 11. 非目标

- tick/L2 微观结构；Feature 层统计处理；历史自动重写；跨源仲裁（第二源只做抽样对拍）。
