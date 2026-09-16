---
xname: reversal_20d_skew_extreme_w10
formula: |
  _skew = ts_skewness(returns(close), 10)
  _w = sign(sign(cs_rank(turnover) - 0.8) + 1) / 2
  signal = _skew * _w
tags: [mine_r16, skew, window, negative]
params: {}
status: 无效（vs 种子 t -40%、IC -44%）——lottery 是 20 日结构性非短期现象
created_ts: 2026-09-16
updated_ts: 2026-09-16
---

# reversal_20d_skew_extreme_w10 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `reversal_20d_skew_extreme_w10`（= `research/factor/reversal_20d/skew_extreme_w10.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | **无效**——t -40%、IC -44% |
| 标签 | mine_r16, skew, window, negative |
| 创建 | 2026-09-16（挖矿轮次 16，种子 `reversal_20d_skew_extreme`） |

## 2. 逻辑

**动机**：种子的偏度窗口 20 从未扫过。若 lottery 是**短期**现象
（散户近期追涨），短窗口更强；若是**长期**结构性特征，长窗口更稳。

**数学表达**：

```
_skew   = ts_skewness(returns(close), 10)         # 偏度窗口缩到 10
_w      = 1{cs_rank(turnover) > 0.8}
signal  = _skew × _w
```

## 3. 实现（YAML 全文）

```yaml
name: reversal_20d_skew_extreme_w10
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
  from polars_ta.prefix.wq import ts_skewness, cs_rank, sign
  _skew = ts_skewness(returns(close), 10)
  _w = (sign(sign(cs_rank(turnover) - 0.8) + 1) / 2)
  signal = _skew * _w
```

## 4. 验证结果

> 数据快照自 `runs/platform/reversal_20d_skew_extreme_w10/summary.json`
> （2026-09-16，`st_degrade: true`）。种子同日重跑对比。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.0204 |
| IC std | 0.0618 |
| t 值 | -4.43 |
| IR | -0.330 |
| 近 26 周 mean / t | -0.0117 / -0.80 |
| PearsonIC | -0.0016 (t=-0.32) |

### 判定（对照种子）

| 指标 | 种子 w20 | 变异 w10 | Δ |
|------|----------|----------|---|
| raw IC | -0.0366 | -0.0204 | **-44%** |
| t | -7.38 | -4.43 | **-40%** |
| IR | -0.553 | -0.330 | -40% |
| PearsonIC | -0.0129 | -0.0016 | **-88%** |
| 近 26 周 t | -1.32 | -0.80 | 更差 |

- **主假设（lottery 是短期现象）** **证伪**：mean IC 真实下降 44%（非
  仅 std 收缩的机械膨胀）——10 日窗口**丢失 lottery 信息**。
- **结论**：lottery 偏好是**结构性特征**（股票自身偏度稳定），
  20 日窗口捕捉的是股票的**内在 lottery 属性**，而非近期散户行为。
- **结论**：**无效**——种子 w20 是最优窗口；本变异作对照档案。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `reversal_20d_skew_extreme_w10`（初始） | 挖矿轮16：偏度窗口 20→10 | -0.0204 | -4.43 | **无效**（-40% t） |

## 6. 风险与备注

- **理论含义**：lottery 不是短期散户追涨（散户追涨是日内/短期现象，
  已被 Round 7 max_effect 高/open 捕捉），而是**股票自身的收益分布
  结构性偏度**——某些股票天生有 lottery-like 回报分布（科技/题材股）。
- **与 Round 7 对照**：max_effect 用 9.5% 阈值捕捉"涨停事件"（短期），
  skew 用 20 日分布捕捉"结构性 lottery"（长期）——**两种 lottery 机制
  独立存在**。
- **窗口谱结论**：偏度窗口 20 最优（10 太短、信息不足）；30+ 未测但
  预期与 20 接近（结构性特征稳定）。
- 种子 [`skew_extreme.md`](skew_extreme.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
