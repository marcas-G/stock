---
xname: turnover_accel_inflow
formula: |
  _r = ts_mean(turnover, 5) / ts_mean(turnover, 20)
  signal = if_else(_r > 1, _r - 1, 0)
tags: [mine_r10, turnover, accel, asymmetric, candidate]
params: {}
status: 候选（vs 种子 t +31%、IR +31%；spread/Pearson 因并列退化不作证据）
created_ts: 2026-09-16
updated_ts: 2026-09-16
snapshot: 历史快照（验证数字不可复跑；R21 标注，见 ../README.md）
---

# turnover_accel_inflow 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `turnover_accel_inflow`（= `research/factor/liquidity/accel_inflow.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | **候选（升级）**——t/IR +31%；但 spread/Pearson 因并列退化不作证据 |
| 标签 | mine_r10, turnover, accel, asymmetric, candidate |
| 创建 | 2026-09-16（挖矿轮次 10，种子 `turnover_accel`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `turnover_accel` 把"资金涌入（ratio>1）"和"资金退潮（ratio<1）"
**对称**处理——同一个公式，`ratio > 1` 时信号高（做空），`ratio < 1` 时信号低
（做多）。但**涌入与退潮的机制不同**：
- **涌入** = 散户追涨 → 短期过热 → 反转（种子已捕获）
- **退潮** = 聪明钱离场（负向）或洗盘完成（正向），方向**不确定**

若退潮信号方向不确定，种子把两类机制混进同一个信号里会**稀释**涌入部分的强度。

**核心逻辑**：**只保留涌入部分**——退潮侧置 0（无信号）。

**数学表达**：

```
_r      = MA(turnover, 5) / MA(turnover, 20)
signal  = _r > 1 ? _r - 1 : 0        # 非负截断，退潮置 0
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
name: turnover_accel_inflow
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
  _r = ts_mean(turnover, 5) / ts_mean(turnover, 20)
  signal = if_else(_r > 1, _r - 1, 0)
```

## 4. 验证结果

> 数据快照自 `runs/platform/turnover_accel_inflow/summary.json`（2026-09-16，
> `st_degrade: true`）。种子 `turnover_accel` 同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178 |
| 平均股票数 | 5048 |
| 信号缺失率 | 0.0211 |

| 指标 | 值 |
|------|----|
| **RankIC mean**（方向调整后） | **0.0422** |
| **t 值** | **6.95** |
| **IR** | **0.521** |
| 近 26 周 mean / t | -0.0069 / -0.41 |
| PearsonIC mean † | 0.0294（t=5.26） |

† **Pearson IC 与 spread 在本因子不可用**（详见 §5 判定）

### 判定（对照种子同日重跑）

| 指标 | 种子 | 变异 | Δ |
|------|------|------|---|
| **RankIC mean** | 0.0367 | 0.0422 | +15% |
| **t** | 5.30 | **6.95** | **+31%** |
| **IR** | 0.397 | **0.521** | **+31%** |
| PearsonIC † | 0.0267 | 0.0294 | (+10%，不可信) |
| Spread † | 0.0047 | 0.0570 | (+11×，**伪信号**) |

- **主假设（涌入/退潮非对称）** **验证成立**：仅用涌入部分后 t/IR +31%
  （rank-based，不受并列影响）——退潮部分**确实是噪声**。
- **spread 与 Pearson IC 在本因子不可用**（审核员实证）：
  - `signal = max(ratio-1, 0)` 让退潮侧 signal=0，每周 40~75% 截面**并列**
  - quant_core 的 average-rank 分位把并列整块坍缩进单一档位，某档膨胀到
    3.5×正常规模（实测某档成员最多）
  - G0 mean_ret = +0.0554（5.5%/周）**不是分层效应**，是分组退化伪信号
    （G0 只在 5/178 周末出现，每周仅 1~884 只成员）
  - Pearson IC 因重并列衰减，解释力弱
  - 平台退化护栏 `degenerate_decile_groups` 只标"全期空组(NaN)"，
    "全期近空但有限"漏检 → summary notes=None，档案必须显式注明
- **仅引用 rank-based 证据**：IC/t/IR（基于逐周截面秩相关）改善真实
- **结论**：**候选（升级）**——非对称处理显著提升信号质量；spread 与 Pearson
  证据须撤下

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `turnover_accel_inflow`（初始） | 挖矿轮10：对称→非对称（仅保留涌入） | 0.0422 | 6.95 | **候选（升级）**：t/IR +31% |

## 6. 风险与备注

- **评估口径警示**：本档案的 spread/decile_returns/Pearson IC **不可用**
  （signal=0 大量并列导致 quant_core 分组退化）。仅 rank IC/t/IR 可信。
- **平台缺口**：`degenerate_decile_groups` 应扩展检测"近空但有值"的档位
  （成员数 < 正常规模 20% 时标记退化）——建议进 pending-items。
- **理论含义**：turnover 家族里"退潮"信号是噪声——若要利用退潮信息，
  应做**双向分离建模**（inflow_signal + outflow_signal 各自独立），
  而不是用一个对称公式。
- **实盘含义**：非对称处理让因子**只在涌入时发出信号**（多数股票多数时间
  signal=0）——交易频率降低、信号更精准；但信号缺失时的中性持仓需要单独设计。
- 种子 [`accel.md`](accel.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
