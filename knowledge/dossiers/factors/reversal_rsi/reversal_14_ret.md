---
xname: rsi_reversal_14_ret
formula: |
  _d = returns(close)
  signal = ts_mean((_d + abs(_d)) / 2, 14) / (ts_mean((abs(_d) - _d) / 2, 14) + 1e-6)
tags: [mine_r1, rsi, returns, scale_invariance]
params: {}
status: 观察中（vs 种子 +4%，尺度不变假设证伪）
created_ts: 2026-09-16
updated_ts: 2026-09-16
snapshot: 历史快照（验证数字不可复跑；R21 标注，见 ../README.md）
---

# rsi_reversal_14_ret 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `rsi_reversal_14_ret`（= `research/factor/reversal_rsi/reversal_14_ret.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | 观察中（vs 种子小幅改善，主假设证伪） |
| 标签 | mine_r1, rsi, returns, scale_invariance |
| 创建 | 2026-09-16（挖矿轮次 1，种子 `rsi_reversal_14`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `rsi_reversal_14` 的 `_d = ts_delta(close, 1)` 用绝对价差，
隐含"绝对价差跨股票可比"。若成立，则高价股与低价股同幅度价格变化被吞进信号，
应改为百分比收益 `_d = returns(close)` 去除尺度偏差。

**核心逻辑**：把 RSI 分子的日变化从绝对价差换成百分比收益，其他结构不变。

**数学表达**：

```
_d = returns(close)                    # 百分比日收益
signal = mean(max(_d, 0), 14) / (mean(max(-_d, 0), 14) + 1e-6)
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
name: rsi_reversal_14_ret
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
  _d = returns(close)
  signal = ts_mean((_d + abs(_d)) / 2, 14) / (ts_mean((abs(_d) - _d) / 2, 14) + 1e-6)
```

## 4. 验证结果

> 数据快照自 `runs/platform/rsi_reversal_14_ret/summary.json`（2026-09-16，
> `st_degrade: true` = 无 ST 口径）。种子 `rsi_reversal_14` 本环境重跑同日对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 179 |
| 平均股票数 | 5039 |
| 信号缺失率 | 0.0265 |

| 指标 | 值 |
|------|----|
| RankIC mean（方向调整后） | 0.0389 |
| t 值 | 3.74 |
| IR | 0.279 |
| 近 26 周 mean / t | 0.0025 / 0.09 |
| PearsonIC mean | 0.0138（t=1.67） |

| 项 | 值 |
|----|----|
| spread | 0.00253 |
| D1 / D10 | 0.00271 / 0.00018 |

### 判定（对照种子同日重跑）

| 指标 | 种子 | 变异 | Δ |
|------|------|------|---|
| IC mean | 0.0376 | 0.0389 | +3.4% |
| t | 3.59 | 3.74 | +4.2% |
| IR | 0.268 | 0.279 | +4.1% |
| PearsonIC | 0.0130 | 0.0138 | +6.1% |

- **主假设（尺度偏差）证伪**：种子比值 `Σmax(Δ,0)/Σmax(-Δ,0)` 对价格水平 **本就
  线性齐次**（每只股票内 P 在分子分母约掉）。实测差异（+4%）来自**乘性 vs 加性**
  的路径不对称（如 +10%→-10% 使 ts_delta 完全对称但 returns 净负），与尺度无关。
- **近 26 周双双接近零**（种子 t=0.00，变异 t=0.09）——两版本近期均无信号。
- 结论：**观察中/边际改善**——变异保留，但不作为主选；种子仍是该族主因子。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `rsi_reversal_14_ret`（初始） | 挖矿轮次 1：`ts_delta(close,1)` → `returns(close)` | 0.0389 | 3.74 | 观察中（+4%） |

## 6. 风险与备注

- **理论证伪**：比值型 RSI 对价格水平尺度不变；"绝对价差不可比"直觉在比值结构
  下不成立。若目标是去除尺度偏差，应改**分子/分母结构**（如 gain_count/loss_count
  频率型），不是改度量。
- **保留意义**：+4% 改善虽边际，但方向稳定（PearsonIC 同向 +6%）——若做 RSI 族
  ensemble，可作种子替代。
- 缺失率 0.0265（比种子 0.0654 更低——`returns()` 首行 NaN 少一天，但差异不显著）。
- 种子 [`reversal_14.md`](reversal_14.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
