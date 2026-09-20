# DQ 范围收窄规格增补（2026-09-20，用户裁定）

> 裁定源：`governance/workspace/pending-items.md` #24（2026-09-20 用户）；issue #8（DQ-M3 收窄）、
> issue #25（发布链按新范围执行）。本增补冻结语义，实现按此执行；与
> `2026-09-18-data-quality-pipeline-design.md` 冲突处以本增补为准（仅 scope 相关）。

## 1. 数据集范围（scope）

```
scope := trade_date >= 1996-01-01  AND  NOT code.endswith(".BJ")
```

- 实算（`daily_fact` 全表）：总 18,230,232 行；scope 内 **17,787,921 行（97.57%）**；
  范围外：BJ 270,183 + 前 1996 172,128（无重叠）。
- 范围外数据**保留在盘、不删除、不改写**；仅不纳入审计/发布/研究。
- 单一事实源：`platform/src/factorlab/core/scope.py`（常量 + 谓词）。
  DQ policy `dq_policy.daily-v3.yaml` 增加 `scope:` 块**显式登记同一口径**（供审计与
  版本追踪）；两处一致性由测试锁定（不许漂移）。
- 语义后续变更（扩范围/改截止）必须新政策版本 + 新裁定记录，不得静默改常量。

## 2. DQ 账本与发布（tools/data_quality）

- **账本口径 = scope 内**：validators 前先过滤 scope；`expected/actual/quarantine/dedup`
  与 `quality_backlog` 全部按 scope 计算；范围外行不进 quarantine 证据、不进 staging。
- **`publish-history`（新）**：一次命令覆盖 `[--from, --to]` 的**逐分区发布**：
  - 全范围账本跑一次（scoped clean/audit），作为公共 ledger；
  - 逐分区：读该分区 canonical（scoped）→ 分区级 PK/重复/抽样/漂移审查 →
    推导状态 → 原子写 `data/health/<dataset>/<partition>.json` + 重建 `summary.json`；
  - **幂等可续**：已 PASS 分区跳过（`--force` 重发）；进度日志逐分区；
  - 与每日链的关系：日更链 `publish` 不变（只发最新分区），`publish-history` 是历史回填工具。
- **分区状态推导**（冻结）：
  - 分区级 audit 有 ERROR（PK 重复等）→ `FAIL`；
  - 分区内 scope 内 quarantine > 0，或 error_rate ∈ (pass.max_error_rate, fail.min_error_rate]
    → `DEGRADED`；
  - 其余 → `PASS`；
  - 全表计数/规则 top-N 只进 `quality_backlog` 披露，**不参与分区状态**（M1.5c 精神）。
- **系统性检查**：`publish-history` 开工前在 scoped 全范围上跑一次
  `aggregate.detect_systemic`；未过 → 拒绝发布（要求先处置残余，或由新裁定降级治理）。

## 3. 读取门（adapters/read/health.py）

- `partition < scope.MIN_TRADE_DATE` → `DatasetQualityError`，文案显式：早于数据集范围
  （1996-01-01，用户裁定 2026-09-20），不属 UNKNOWN/LEGACY 过渡条款。
- scope 内逻辑不变（PASS / DEGRADED opt-in / UNKNOWN LEGACY 显式过渡 / FAIL 拒绝）。

## 4. 研究默认口径（universe / 策略）

- universe 解析默认**排除 `.BJ`**（`exclude_bj` 默认 true；显式 false 才纳入），
  日频/分钟/复合成员解析同口径。
- 策略组合前对信号帧应用同一 scope 谓词（历史产物兼容：不重算信号也能保证不交易 BJ）。
- 文档同步：`knowledge/contracts/interface.md`（universe、读取门）、
  `knowledge/handbooks/factor-authoring-manual.md`（数据范围一节）。

## 5. 与验收的关系

1. 1996+ 范围内 health 全发布（分布 PASS/DEGRADED 由第 2 节推导）；
2. 严格模式（无 opt-in）`flab strategy run` 在 2023-01→2026-07（信号覆盖域）的干净窗口跑通；
3. DQ-M3 残余：BJ 1,115 与前 1996 2,658 **出范围免修**；scope 内残余降为 55 行，
   其中可确证的现代单位 bug 行按 bars_1m 复核后修复/登记，其余保持隔离披露。
