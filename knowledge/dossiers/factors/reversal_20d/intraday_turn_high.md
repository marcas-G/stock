---
xname: reversal_20d_intraday_turn_high
formula: |
  signal = ts_sum(high / open - 1, 20) * cs_rank(turnover)
tags: [mine_r11, reversal, intraday, high_spike, mixed]
params: {}
status: 观察中（信号强度 +32% 但波动率 +37%；t/IR 略降、近期走弱）
created_ts: 2026-09-16
updated_ts: 2026-09-16
---

# reversal_20d_intraday_turn_high 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `reversal_20d_intraday_turn_high`（= `research/factor/reversal_20d/intraday_turn_high.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | **观察中（混合）**——信号强度升 +32%，但波动率也升 +37% |
| 标签 | mine_r11, reversal, intraday, high_spike, mixed |
| 创建 | 2026-09-16（挖矿轮次 11，种子 `reversal_20d_intraday_turn`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `reversal_20d_intraday_turn` 用 `close/open - 1`（净方向）
作为"日内涨幅"——但**净方向丢失路径信息**。Round 7 结论：lottery 效应的
关键是**冲高幅度**（`high/open - 1`），而非收盘相对开盘的净方向。

具体例子：开 100 → 涨到 110 → 收 105（A：冲高 10%、净涨 5%）与
开 100 → 直接涨到 105 → 收 105（B：冲高 5%、净涨 5%）——种子视为相同，
但 A 有明显冲高（追高被套）、B 干净涨。

**核心逻辑**：把累计涨幅度量从 `close/open - 1` 换成 `high/open - 1`。

**数学表达**：

```
signal = sum(high_t / open_t - 1, 20) × cs_rank(turnover)
```

**输入数据**：`high/open`（daily）、`turnover`

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
name: reversal_20d_intraday_turn_high
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
  from polars_ta.prefix.wq import ts_sum, cs_rank
  signal = ts_sum(high / open - 1, 20) * cs_rank(turnover)
```

## 4. 验证结果

> 数据快照自 `runs/platform/reversal_20d_intraday_turn_high/summary.json`
> （2026-09-16，`st_degrade: true`）。种子同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178 |
| 平均股票数 | 5048 |
| 信号缺失率 | 0.0445 |

| 指标 | 值 |
|------|----|
| RankIC mean（方向调整后） | **0.0781** |
| IC std | 0.2107 |
| t 值 | 4.94 |
| IR | 0.371 |
| 近 26 周 mean / t | 0.0829 / **-1.69** |
| PearsonIC mean | 0.0297（t=2.23） |

| 项 | 值 |
|----|----|
| spread | 0.00434 |
| D1 / D10 | 0.00486 / 0.00052 |

### 判定（对照种子同日重跑）

| 指标 | 种子 | 变异 | Δ |
|------|------|------|---|
| RankIC mean | 0.0593 | **0.0781** | **+32%** ↑ |
| **IC std** | 0.1533 | 0.2107 | **+37%** ↑ |
| t | 5.16 | 4.94 | -4% |
| IR | 0.387 | 0.371 | -4% |
| PearsonIC | 0.0369 | 0.0297 | -20% |
| Spread | 0.0058 | 0.0043 | -26% |
| 近 26 周 t | -0.04 | **-1.69** | **大幅下降** |

- **主假设（冲高幅度 > 净方向）** **部分验证**：IC 强度 +32%——
  冲高幅度**确实是更强的信号**（Round 7 结论在组合结构里也成立）。
- **但波动率同升 +37%**：冲高幅度信号**噪声更大**（high 是盘中报价，
  含瞬时挂单/毛刺；close/open 是收盘价，噪声低）。
- **净效果 t/IR 略降**：信号强度提升被噪声抵消，稳定度未改善。
- **近端走弱**：近 26 周 t 从 -0.04 → -1.69——变异版**近期表现明显更差**。
  可能原因：近年 A 股冲高回落更常见（结构性变化），冲高信号的**噪声**
  超过**信号**（lottery 效应在近年可能减弱）。
- **spread/Pearson 都变差**：分层区分度 -26%、Pearson -20%——冲高版
  **截面排序质量整体下降**。
- **结论**：**观察中（混合）**——理论上冲高幅度更贴 lottery 机制，但
  **数据上不稳定**；种子仍是首选，本变异作对照。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `reversal_20d_intraday_turn_high`（初始） | 挖矿轮11：close/open→high/open | 0.0781 | 4.94 | **观察中（混合）**（+32% 但波动 +37%） |

## 6. 风险与备注

- **理论 vs 实践**：冲高幅度（high/open）**理论更贴 lottery 机制**，
  但**噪声也大得多**（盘中报价 vs 收盘价的信噪比差异）——与 Round 8
  （GK 波动率）教训一致：**理论精确 ≠ 信号质量**。
- **近期表现警示**：近 26 周 t 从 -0.04 → -1.69——如果 A 股 lottery 效应
  在近年减弱（散户占比下降），冲高信号的噪声会持续压制信号。
- **保留意义**：作为**对照档案**——证明"用盘中数据（high）替换收盘数据"
  在**单点因子**里有效（Round 7 lottery），但在**累计 × 组合**结构里
  噪声放大。
- **未来研究**：
  - 若要用 high/open 信号，应做**去噪**（如 ts_mean 平滑、winsorize 更严）
  - 或**分离建模**：净方向 close/open + 冲高幅度 high/close，各自独立系数
- 种子 [`intraday_turn.md`](intraday_turn.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
