# daily 全史体检——影响分析

- 范围：1990-12-19 .. 2026-09-17（8791 个交易日，18,230,232 行）
- raw sha256：`903ab1603b582c2500c6d3c26f417eccb6453b709dfa644e9e05bb2d6a82e323`
- dq_policy：`daily-v1`
- 问题总命中：1,310,927（按日期/字段/规则明细见 `issue_counts.csv`）

> 只审不改（§8）：本报告不触发任何重写/修复；修复动作留 M3 定向。

## 高影响项

### 复权因子负值（复权序列负价）（`ADJ_NEGATIVE`）
- rows: 8184
- affected_partitions: 879
- 影响：负因子整键在增量 clean 被行级隔离（不无痕改写），复权价序列断裂会污染 lookback 类因子与回测；canonical 行数相对 raw 减少
- 下一步：M3 定向往修：评估「置 NULL 不丢行」；核对 vendor 后复权价异常范围
- 状态：`measured`

### 退市股复权因子（adj sidecar 覆盖）（`delisted_adj`）
- delisted_codes: 331
- rows_delisted: 1316380
- rows_adj_missing: 1251010
- rows_adj_nonpositive: 12
- 影响：退市股复权链断流 → 退市前区间无法复权，回测幸存者偏差修正缺一段证据
- 下一步：M3 定向修复：delisted_adj_factor sidecar 覆盖率与断点核查
- 状态：`measured`

### 流通市值（circ_mv = close × float_shares）（`circ_mv`）
- float_shares_missing_rows: 1251010
- float_shares_zero_rows: 51
- float_shares_negative_rows: 0
- 影响：canonical circ_mv 由 float_shares 派生：缺失 → NULL（不伪造），零值 → 0 市值，均影响市值中性化/分组类因子
- 下一步：M3 定向修复：CH 侧 circ_mv 与 float_shares 异常值核查
- 状态：`proxy_measured`（M1 daily raw 无 circ_mv 列，以 float_shares 缺失/零值为代用指标）

### 跨频系统偏差（分钟 ↔ 日线）（`cross_frequency`）
- 影响：分钟聚合与日线不一致的存量偏差影响分钟/日频混用研究
- 下一步：M2 三角验证（分钟聚合 vs 内建日线 vs 腾讯日线）时实测
- 状态：`deferred_to_M2`

### 核心字段缺失（尤以退市股 amount/adj 无源）（`missing_value`）
- rows: 1251010
- affected_partitions: 8658
- amount_missing_rows: 1251010
- 影响：WARN 保留不填充；下游特征需自行处理 NaN（Feature 层职责）
- 下一步：M3 calibration：按字段/证券状态分级，评估是否需来源补齐
- 状态：`measured`

### VWAP=amount/volume 越 [low,high]（`vwap_out_of_range`）
- rows: 7906
- affected_partitions: 1492
- 影响：成交量额与价格区间矛盾 → 剔除该键（增量 clean 已隔离）
- 下一步：M3 定向核对：疑似单位/供应商口径问题（与 UNIT_SUSPECT 联动）
- 状态：`measured`

### 复权因子变化日与 CA 事件不符（`adj_factor_ca_mismatch`）
- rows: 43827
- affected_partitions: 4190
- 影响：因子事件日错配 → 复权口径漂移（WARN 保留，需 M3 核）
- 下一步：M3 定向核对 CA 事件源与 fq_factor 变化日
- 状态：`measured`

## 口径限制

- calendar/listing/limits 未接入（无权威来源）：TRADE_DATE_INVALID/LIST_* / LIMIT_BREACH 不计入本报告
- 分区桶按交易日；跨日规则经「重叠一日 + 停牌 carry 行」扫描保证块边界不漏检（与全表一次扫描 shift 语义对齐）
- 本报告只审不改：修复动作留 M3 定向（§8）
