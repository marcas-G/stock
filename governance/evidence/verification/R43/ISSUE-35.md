# Issue #35 盘查记录：`industry_lag_beta_1m` 缺少可复现 spec

盘查日期：2026-09-25。结论：**找到可信的上游算法来源，但现有 FactorLab 分钟 DSL/engine 无法原生表达它；#35 仍阻塞，不能用回填面板代替可复现 spec 或五年产物。**

## 找到的算法来源

`$QUANTRESEARCH_ROOT/scratch/20260923_m28_factors.py` 包含 M28 原始算法，`scratch/20260923_m28_panels.py` 包含 IS/OOS 面板构造。对 `industry_lag_beta_1m`，算法使用 `fundamentals.sw_industry` 当前行业映射：

1. 对每只股票、每个交易日计算连续分钟收益；每日有效分钟少于 180 时整日输出缺失。
2. 按行业和分钟计算 leave-one-out（LOO）收益均值，要求剔除自身后的同分钟有效成员数大于 3。
3. 将 LOO 均值滞后一根分钟，与该股票当分钟收益配对；有效配对数至少 100 时，计算
   `sum(r_t * industry_loo_mean_{t-1}) / sum(industry_loo_mean_{t-1} ** 2)`。

仓外 `$QUANTRESEARCH_ROOT/REPORT.md` §5k 记载该特征被 M28 接受为新信息通道，并报告 IS/OOS 统计。该结果说明有真实研究来源，但不能替代平台 spec 的实现和复算。源算法使用的当前行业映射也不是 PIT 历史行业；移植时应将这一限制写进 spec/档案。

## 为什么当前 DSL 不能承载原算法

- [`compute_minute_factor_panel`](../../../../platform/src/factorlab/core/engine/minute.py) 只接收分钟 bars 和按 `(date, code)` 注入的日级列；当前属性供给面不提供 `fundamentals.sw_industry`。
- [`minute_gate.py`](../../../../platform/src/factorlab/core/engine/minute_gate.py) 明确禁止 bars_1m 公式调用 `gp_*` 跨截面算子。
- 现有 `gp_mean(key, x)` 只按日和 key 分组；它没有 `(date, minute_index, industry)` 分组、剔除自身及有效成员计数语义。
- 公式 `def` 会先内联，受限 AST 不允许用 `.over`、`group_by` 等 Python/Polars 操作绕开分钟门。

因此，现有 `im_*` 与 `day_*` 能表达的单股票分钟序列/折日部分不足以表达 M28 的行业 LOO 分钟序列和配对回归。直接把算法写成 YAML 会缺少所需的数据列和跨股票运算，不构成有效修复。

## 当前产物核查

- 未找到 `$QUANTRESEARCH_ROOT/factor/**/industry_lag_beta_1m.yaml`。
- 未找到 `$QUANTRESEARCH_ROOT/experiments/r37_5y/industry_lag_beta_1m_5y.yaml` 或对应五年面板。
- 现有 `$QUANTRESEARCH_ROOT/results/platform/industry_lag_beta_1m/summary.json` 标记来源为 M28 挖矿侧 backfill，窗口为 2023-01-01 至 2025-12-31，3,492,672 行、4,829 只股票；它没有 FactorLab spec 身份或 evaluation 元数据，不能作为 ref-sync 所需的 `_5y` 结果。
- `$QUANTRESEARCH_ROOT/scratch/20260923_m28_panels.py` 生成的 NPZ 是研究检验缓存，也不是 FactorLab runnable spec。

## 建议的处理路径

先为分钟跨截面能力写独立设计与验收：提供 PIT 行业键到 bars_1m 的受控注入，并增加按 `(date, minute_index, industry)` 分组、LOO 均值/计数与分钟滞后配对所需的算子或引擎装配层。随后以 M28 脚本为参照，用小型合成面板逐值对拍，覆盖行业成员数、缺失分钟、股票自身剔除、100/180 样本门槛和折日结果。能力验收后再迁移为 spec、完成 lint 与研究流水线五年运行，使用隔离输出目录，不覆盖现有 backfill。

本轮没有改研究区 spec/缓存/产物，没有运行 CH 或流水线，也没有改 GitHub 状态。Issue #35 保持 open，等待引擎能力设计与实现。
