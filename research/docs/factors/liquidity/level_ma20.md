---
xname: turnover_level_ma20
formula: |
  signal = ts_mean(turnover, 20)
tags: [mine_r4, turnover, smoothed, negative_result]
params: {}
status: 无效（vs 种子 -17% IC，-50% Pearson；瞬时信息不能平滑）
created_ts: 2026-09-16
updated_ts: 2026-09-16
---

# turnover_level_ma20 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `turnover_level_ma20`（= `research/factor/liquidity/level_ma20.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | **无效**（负结论——瞬时信息不能平滑） |
| 标签 | mine_r4, turnover, smoothed, negative_result |
| 创建 | 2026-09-16（挖矿轮次 4，种子 `turnover_level`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `turnover_level` 用**当日瞬时换手率**——`signal = turnover`。
直觉上，日频换手率噪声大（消息、随机波动、周末效应），作为"投机需求代理"
应做窗口平滑。若"真实换手水平"是结构性特征，20 日均应更稳定地捕捉该特征。

**核心逻辑**：改用 `signal = ts_mean(turnover, 20)`——20 日均换手率。

**数学表达**：

```
signal = mean(turnover_t, 20)
```

**输入数据**：`turnover`（daily_basic.turnover_rate）

## 3. 参数与实现

### 处理链

```
universe: {exclude_st: true, exchanges: [SSE, SZSE]}
date: 2023-01-01 ~ 2026-07-31
process: winsorize(quantile=0.99) → standardize()
target: forward_return_5d
adjustment: qfq
```

### 实现（YAML 全文）

```yaml
name: turnover_level_ma20
category: custom
direction: -1
universe:
  rules: {exclude_st: true, exchanges: ["SSE", "SZSE"]}
date:
  start: "2023-01-01"
  end: "2026-07-31"
process:
  - winsorize(quantile=0.99)
  - standardize()
formula: |
  from polars_ta.prefix.wq import ts_mean
  signal = ts_mean(turnover, 20)
```

## 4. 验证结果

> 数据快照自 `results/turnover_level_ma20/summary.json`（2026-09-16，
> `st_degrade: true`）。种子 `turnover_level` 同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178（种子 182；差异 = MA20 暖机 19 日） |
| 平均股票数 | 5038 |
| 信号缺失率 | 0.0436 |

| 指标 | 值 |
|------|----|
| RankIC mean（方向调整后） | 0.0601 |
| t 值 | 3.84 |
| IR | 0.288 |
| 近 26 周 mean / t | 0.0826 / 1.78 |
| PearsonIC mean | 0.0257（t=2.28） |

| 项 | 值 |
|----|----|
| spread | 0.00357 |
| D1 / D10 | 0.00354 / -0.00003 |

### 判定（对照种子同日重跑）

| 指标 | 种子（瞬时） | 变异（MA20） | Δ |
|------|--------------|--------------|---|
| RankIC mean | 0.0720 | 0.0601 | **-16.6%** |
| t | 5.05 | 3.84 | -24% |
| IR | 0.375 | 0.288 | -23% |
| **PearsonIC** | 0.0510 | 0.0257 | **-50%** |
| **PearsonIC t** | 4.90 | 2.28 | -54% |
| Spread | 0.0056 | 0.0036 | -36% |
| 近 26 周 t | 1.86 | 1.78 | ~ |

- **主假设（瞬时=噪声）** **强证伪**：平滑把 IC 砍掉 1/6、Pearson 砍掉一半。
  **瞬时换手是信号、不是噪声**——turnover 的信息**集中在事件日**（消息落地、
  突发炒作），平滑把这些"信息尖峰"抹掉，只留下"结构性水平"（后者弱得多）。
- **理论含义**：换手率的**时间敏感性**远大于横截面水平本身。做 turnover 家族
  的因子应**保瞬时**、不做窗口平滑（除非做比率型如 `turnover_accel`，那
  是"当期 vs 均值"的相对量，非平滑水平）。
- **档位区分度**：D10 mean_ret 从 -0.00371（种子）→ -0.00003（变异）——
  平滑把最投机那一档完全拉平，验证了"信息在尖峰里"。
- 结论：**无效**——负结论，种子公式的瞬时性不是缺陷而是特性。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `turnover_level_ma20`（初始） | 挖矿轮4：瞬时→MA20 平滑 | 0.0601 | 3.84 | **无效**（-17%/-50%，反证瞬时有效） |

## 6. 风险与备注

- **理论价值**：本因子作为**负结论档案**存在——避免未来再走"平滑换手率"的弯路。
- **对未来研究的启示**：
  - turnover 的信息结构是**尖峰驱动**（spike-driven），非结构性水平
  - 若做窗口化，应做**极值类**（如 `ts_max(turnover, N)`）而非均值类
  - `turnover_accel = mean_5/mean_20`（现有）之所以有效，是因为它保留"当期
    尖峰 / 常态水平"的比值结构
- 种子 [`level.md`](level.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
