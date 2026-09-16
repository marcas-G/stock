---
xname: reversal_20d_cumret_freq
formula: |
  signal = ts_sum(sign(returns(close)), 20)
tags: [mine_r6, reversal, frequency, negative_result]
params: {}
status: 无效（vs 种子 -76% IC，-53% t；幅度驱动反转，频率弱得多）
created_ts: 2026-09-16
updated_ts: 2026-09-16
snapshot: 历史快照（验证数字不可复跑；R21 标注，见 ../README.md）
---

# reversal_20d_cumret_freq 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `reversal_20d_cumret_freq`（= `research/factor/reversal_20d/cumret_freq.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | **无效**（负结论）——幅度驱动反转，频率信号弱得多 |
| 标签 | mine_r6, reversal, frequency, negative_result |
| 创建 | 2026-09-16（挖矿轮次 6，种子 `reversal_20d_cumret`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `reversal_20d_cumret` 用 `ts_sum(returns, 20)`——**幅度加权**
的累计收益。隐含相信"反转由幅度驱动"，从未验证"频率驱动"（多少天涨/跌）
是否才是触发机制。行为金融里散户注意力被**事件**抓住，可能"多少天大涨"
比"总幅度多大"更相关。

**核心逻辑**：完全丢弃幅度信息，只计涨跌频率——
`signal = ts_sum(sign(returns(close)), 20)` = "过去 20 日上涨天数 − 下跌天数"。

**数学表达**：

```
signal = count_up_days - count_down_days   over past 20 days   （范围 −20..+20）
```

**输入数据**：`close`

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
name: reversal_20d_cumret_freq
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
  from polars_ta.prefix.wq import ts_sum, sign
  signal = ts_sum(sign(returns(close)), 20)
```

## 4. 验证结果

> 数据快照自 `results/reversal_20d_cumret_freq/summary.json`（2026-09-16，
> `st_degrade: true`）。种子同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178 |
| 平均股票数 | 5038 |
| 信号缺失率 | 0.0445 |

| 指标 | 值 |
|------|----|
| RankIC mean（方向调整后） | 0.0162 |
| t 值 | 1.91 |
| IR | 0.143 |
| 近 26 周 mean / t | -0.0083 / -0.34 |
| PearsonIC mean | 0.0050（t=0.68） |

| 项 | 值 |
|----|----|
| spread | 0.00128 |
| D1 / D10 | 0.00193 / 0.00065 |

### 判定（对照种子同日重跑）

| 指标 | 种子（幅度） | 变异（频率） | Δ |
|------|--------------|--------------|---|
| RankIC mean | 0.0504 | 0.0162 | **-76%** |
| t | 4.09 | 1.91 | -53% |
| IR | 0.307 | 0.143 | -53% |
| PearsonIC | 0.0247 | 0.0050 | -80% |
| Spread | 0.0047 | 0.0013 | -73% |
| 近 26 周 t | +0.23 | -0.34 | ~ |

- **主假设（频率驱动反转）** **强证伪**：频率版损失 76% IC、53% t。
  种子假设"幅度驱动"验证成立——幅度加权**远比**频率计数更能捕捉反转。
- **符号一致**：两者 raw IC 同为负，说明频率信号**方向相同**但**强度极弱**——
  "多少天涨"确实与反转相关，但强度远低于"涨多少"。
- **理论含义**：反转机制里，**幅度分布的形状**（尤其尾部大涨大跌）比"事件
  频次"更重要——大额波动触发资金平仓/反手，小额波动即使频繁也不触发。
- **winsorize+standardize 局限**：本变异信号是整数（-20..+20），winsorize(0.99)
  近似空操作；但这不影响判定（信号强度差距远超处理链差异）。
- 结论：**无效**——负结论，种子公式的幅度选择是对的。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `reversal_20d_cumret_freq`（初始） | 挖矿轮6：幅度→频率（sign） | 0.0162 | 1.91 | **无效**（-76%，反证幅度驱动） |

## 6. 风险与备注

- **理论价值**：档案作为**负结论档案**——避免未来再走"频率替代幅度"的弯路。
- **对未来研究的启示**：
  - 反转的核心是**幅度分布的尾部**，不是事件频次
  - 若做"事件驱动"因子，应关注**极端幅度事件**（如 `ts_count(|r| > k*σ, N)`），
    不是简单涨跌计数
  - `max_effect_20d_extcnt`（现有）用 `returns >= 0.095` 计涨停日数——
    是"极端事件"频率，不是"涨跌"频率，可能更强
- 种子 [`cumret.md`](cumret.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
