---
xname: reversal_10d_cumret_log
formula: |
  signal = ts_log_diff(close, 10)
tags: [mine_r2, reversal, log_return, geometric]
params: {}
status: 观察中（vs 种子 -5%，算术/几何差异边际）
created_ts: 2026-09-16
updated_ts: 2026-09-16
---

# reversal_10d_cumret_log 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `reversal_10d_cumret_log`（= `research/factor/reversal_10d/cumret_log.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | 观察中（vs 种子 -5%，边际差异） |
| 标签 | mine_r2, reversal, log_return, geometric |
| 创建 | 2026-09-16（挖矿轮次 2，种子 `reversal_10d_cumret`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `reversal_10d_cumret` 用 `ts_sum(returns(close), 10)`——**算术**日收益和
近似 10 日累积收益。数学上不精确：真累积收益是 Π(1+rᵢ)-1，算术和是一阶近似，对
高波动股票系统性偏差（Jensen 不等式）：`+10%` 后 `-10%` 的算术和是 0%、真累积是 -1%。

**核心逻辑**：改用**对数累积收益**——`log(close_t / close_{t-10}) = Σ log(1+rᵢ)`——
真正的乘法累积。

**数学表达**：

```
signal = log(close_t / close_{t-10})     # 10 日对数累积收益
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
name: reversal_10d_cumret_log
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
  from polars_ta.prefix.wq import ts_log_diff
  signal = ts_log_diff(close, 10)
```

## 4. 验证结果

> 数据快照自 `results/reversal_10d_cumret_log/summary.json`（2026-09-16，
> `st_degrade: true`）。种子同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 180 |
| 平均股票数 | 5040 |
| 信号缺失率 | 0.0445 |

| 指标 | 值 |
|------|----|
| RankIC mean（方向调整后） | 0.0371 |
| t 值 | 3.05 |
| IR | 0.227 |
| 近 26 周 mean / t | -0.0357 / -1.25 |
| PearsonIC mean | 0.0181（t=1.73） |

| 项 | 值 |
|----|----|
| spread | 0.00277 |
| D1 / D10 | 0.00335 / 0.00058 |

### 判定（对照种子同日重跑）

| 指标 | 种子（算术） | 变异（对数） | Δ |
|------|--------------|--------------|---|
| IC mean | 0.0391 | 0.0371 | -5.0% |
| t | 3.26 | 3.05 | -6.4% |
| IR | 0.2426 | 0.2272 | -6.4% |
| PearsonIC | 0.0189 | 0.0181 | -4.3% |
| 近 26 周 mean | +0.0316 | +0.0357 | +12.8% |

- **主假设（算术和偏差污染信号）不成立**：若假设成立，对数版应更好；实测对数版
  在**全期略差**（-5%）。种子公式虽数学不精确，但作为**排序信号**，算术和的偏差
  对截面排序影响很小——A 股日收益分布窄，`log(1+r) ≈ r - r²/2`，`r²` 项在
  cross-sectional standardize 之后被压掉。
- **近 26 周反向**：对数版近期表现略好（t 从 +1.14 → +1.25），暗示高波动期
  算术和偏差更大——但样本太小（26 周），不足以推翻主判定。
- 结论：**观察中**——负结论，种子公式的"数学不精确"在实践中无成本，保留种子。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `reversal_10d_cumret_log`（初始） | 挖矿轮次 2：`ts_sum(returns,10)` → `ts_log_diff(close,10)` | 0.0371 | 3.05 | 观察中（-5%，假设不成立） |

## 6. 风险与备注

- **理论意义**：算术 vs 几何累积的差异在 A 股日收益分布下（|r|<3% 常态）
  被 winsorize+standardize 抹平。数学精确性 ≠ 排序精确性。
- **保留意义**：负结论记录本身有价值——避免未来重跑相同"改进"。
- **近端观察**：若 A 股波动率长期抬升（如 mean|r| > 5%），对数版可能反超。
- 种子 [`cumret.md`](cumret.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
