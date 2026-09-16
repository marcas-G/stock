---
xname: max_effect_20d_extsum
formula: |
  _r = returns(close)
  signal = ts_sum(if_else(_r >= 0.095, _r, 0), 20)
tags: [mine_r12, lottery, magnitude, neutral]
params: {}
status: 观察中（vs 种子 extcnt：IC/t/IR 差异 <3%，中性）
created_ts: 2026-09-16
updated_ts: 2026-09-16
---

# max_effect_20d_extsum 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `max_effect_20d_extsum`（= `research/factor/volatility/extsum.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | **观察中（中性）**——vs 种子 extcnt 差异 <3%；幅度信息冗余 |
| 标签 | mine_r12, lottery, magnitude, neutral |
| 创建 | 2026-09-16（挖矿轮次 12，种子 `max_effect_20d_extcnt`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `max_effect_20d_extcnt` 只**计数**涨停日（有几天 ≥9.5%），
丢弃涨停日的**实际幅度**信息（9.5% vs 10.0% 涨停的差异）。

**核心假设**：涨停日的幅度携带额外信息——
- 9.5%（未完全封板）= 抛压大，反转更强
- 10.0%（封死）= 卖压被消化，反转弱一些

若假设成立，幅度加权计数（sum）应优于纯计数（count）。

**数学表达**：

```
_r      = returns(close)
signal  = sum(_r if _r >= 0.095 else 0, over 20)
```

**输入数据**：`close`（daily, qfq）

## 3. 参数与实现

### 处理链

```
universe: {exchanges: [SSE, SZSE]}
date: 2023-01-01 ~ 2026-07-31
process: winsorize(quantile=0.99) → standardize()
target: forward_return_5d
adjustment: qfq
```

### 实现（YAML 全文）

```yaml
name: max_effect_20d_extsum
category: custom
direction: -1
universe:
  rules: {exchanges: ["SSE", "SZSE"]}
date:
  start: "2023-01-01"
  end: "2026-07-31"
process:
  - winsorize(quantile=0.99)
  - standardize()
formula: |
  from polars_ta.prefix.wq import ts_sum
  _r = returns(close)
  signal = ts_sum(if_else(_r >= 0.095, _r, 0), 20)
```

## 4. 验证结果

> 数据快照自 `runs/platform/max_effect_20d_extsum/summary.json`（2026-09-16，
> `st_degrade: true`）。种子 `max_effect_20d_extcnt` 同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178 |
| 平均股票数 | ~5048 |
| 信号缺失率 | ~0.04 |

| 指标 | 值 |
|------|----|
| RankIC mean（方向调整后） | 0.0531 |
| IC std | 0.1225 |
| t 值 | 5.78 |
| IR | 0.434 |
| 近 26 周 mean / t | 0.0505 / -1.53 |
| PearsonIC mean | 0.0197（t=2.14） |

### 判定（对照种子同日重跑）

| 指标 | 种子 extcnt | 变异 extsum | Δ |
|------|-------------|-------------|---|
| RankIC mean | 0.05315 | 0.05311 | -0.08% |
| t | 5.894 | 5.784 | -1.9% |
| IR | 0.442 | 0.434 | -1.8% |
| PearsonIC | 0.0203 | 0.0197 | -3.0% |
| 近 26 周 t | -1.54 | -1.53 | ~0 |

- **主假设（幅度也携带信息）** **证伪**：sum 与 count 差异 <3%——
  涨停日的幅度信息（9.5%~10% 的变化）**不携带额外信息**。
- **合理解释**：A 股涨停是**硬边界**（10% 限制），涨停日内实际幅度变化
  极小（多数股票要么封板 10%，要么打板后炸板 <9.5%）；9.5% 阈值已经
  把"接近涨停"与"正常上涨"清晰分开，阈值内的小变化是**噪声**。
- **结论**：**观察中（中性）**——count 版更简单、t 略高；本变异作为
  对照档案，证明**幅度加权无价值**。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `max_effect_20d_extsum`（初始） | 挖矿轮12：count→sum（幅度加权） | 0.0531 | 5.78 | **观察中（中性）**（差异 <3%） |

## 6. 风险与备注

- **理论含义**：lottery 效应是**事件驱动**（是否涨停）而非**连续**（涨多少）
  ——涨停事件本身就是信息，涨停幅度是噪声。
- **对照价值**：与 Round 6（cumret_freq 失败）形成对照——
  - Round 6：把连续信号改成频率 → **失败**（连续信息被丢弃）
  - Round 12：把频率信号改成幅度加权 → **中性**（幅度信息冗余）
  - **结论**：lottery 家族里"频率"是正确的**原生度量**，连续化都不行。
- **未来研究**：
  - 阈值 9.5% 可能需要**分板**（主板 10%/创业板 20%/科创板 20%/ST 5%）——
    但 stock_st 表未灌入，暂无法测。
  - "封板 vs 炸板"可能才是信息所在（需 tick 数据区分）。
- 种子 [`max_effect_20d_extcnt.md`](max_effect_20d_extcnt.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
