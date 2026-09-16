---
xname: low_vol_20d_park_gk
formula: |
  _hl = log(high / low)
  _co = log(close / open)
  signal = -ts_mean(0.5 * _hl * _hl - 0.386294 * _co * _co, 20)
tags: [mine_r8, low_vol, garman_klass, neutral]
params: {}
status: 观察中（vs 种子 ±1%，GK 效率优势未转化为信号改善）
created_ts: 2026-09-16
updated_ts: 2026-09-16
---

# low_vol_20d_park_gk 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `low_vol_20d_park_gk`（= `research/factor/volatility/low_vol_20d_park_gk.yaml`） |
| 类别 | custom |
| 方向 | `1` |
| 状态 | 观察中（**中性**——GK ≈ Parkinson，理论效率优势未转化） |
| 标签 | mine_r8, low_vol, garman_klass, neutral |
| 创建 | 2026-09-16（挖矿轮次 8，种子 `low_vol_20d_park`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `low_vol_20d_park`（Round 3 升级版）用 Parkinson 极差波动率
（只用 H/L）。Garman & Klass (1996) 证明**用 OHLC 四点**的方差估计
`σ²_GK = 0.5·ln(H/L)² − (2·ln2 − 1)·ln(C/O)²`
效率是 Parkinson 的 2×——`ln(C/O)²` 项扣除"close 相对 open 的方向性"，
剩余更纯粹的**无方向日内波动**。若效率优势能传导到信号，GK 应显著优于 Parkinson。

**核心逻辑**：改用 Garman-Klass 波动率取负。

**数学表达**：

```
_hl     = log(high_t / low_t)
_co     = log(close_t / open_t)
signal  = -mean(0.5·_hl² - 0.386·_co², 20)
```

**输入数据**：`high/low/close/open`（daily，qfq 复权）

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
name: low_vol_20d_park_gk
category: custom
direction: 1
universe:
  rules: {exclude_st: true, exchanges: ["SSE", "SZSE"]}
date:
  start: "2023-01-01"
  end: "2026-07-31"
process:
  - winsorize(quantile=0.99)
  - standardize()
formula: |
  from polars_ta.prefix.wq import ts_mean, log
  _hl = log(high / low)
  _co = log(close / open)
  signal = -ts_mean(0.5 * _hl * _hl - 0.386294 * _co * _co, 20)
```

## 4. 验证结果

> 数据快照自 `runs/platform/low_vol_20d_park_gk/summary.json`（2026-09-16，
> `st_degrade: true`）。种子 `low_vol_20d_park`（Round 3）同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178 |
| 平均股票数 | 5038 |
| 信号缺失率 | 0.0445 |

| 指标 | 值 |
|------|----|
| RankIC mean（方向调整后） | 0.0734 |
| t 值 | 4.46 |
| IR | 0.334 |
| 近 26 周 mean / t | 0.0838 / 1.69 |
| PearsonIC mean | 0.0254（t=1.90） |

| 项 | 值 |
|----|----|
| spread | 0.00330 |
| D1 / D10 | 0.00474 / 0.00144 |

### 判定（对照种子同日重跑）

| 指标 | 种子（Parkinson） | 变异（GK） | Δ |
|------|-------------------|-----------|---|
| RankIC mean | 0.0738 | 0.0734 | **-0.5%** |
| t | 4.43 | 4.46 | +0.5% |
| IR | 0.332 | 0.334 | +0.5% |
| PearsonIC | 0.0256 | 0.0254 | -0.9% |
| Spread | 0.00361 | 0.00330 | -8.6% |
| 近 26 周 t | 1.69 | 1.69 | 0 |

- **主假设（GK 效率优势应转化为信号提升）** **未验证**：所有指标差异 < 1%（spread
  略降）。GK 理论上更精确，但对**低波动溢价异象**的信号质量没有实质改善。
- **理论含义**：GK 的 `ln(C/O)²` 项在 A 股上：
  - **多数情况**：close 相对 open 的偏移**已被 H/L 极差捕获**（因为 A 股
    日内 close 常接近 H 或 L，方向性与极差高度相关）——所以 GK 的第二项
    "扣除方向"信息在截面波动率排序上冗余
  - **对信号**：更精确的波动率估计 ≠ 更好的截面排序；波动率的**排序**信息
    Parkinson 已足够
- **spread 略降**：GK 两端档位区分度略弱（-9%），说明扣除方向后**尾部**
  （极高波动股）的度量差异更大，反而稀释了排序。
- 结论：**观察中（中性）**——保留作对照，Parkinson 仍是波动率族首选（更简洁）。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `low_vol_20d_park_gk`（初始） | 挖矿轮8：Parkinson→Garman-Klass | 0.0734 | 4.46 | **观察中（中性）**（±1%） |

## 6. 风险与备注

- **理论 vs 实践**：GK 效率优势是"**方差估计**"层面的（MSE 更小），但因子
  质量看的是"**截面排序**"——两者不一定同向。这是本档案的核心教训。
- **对未来研究**：更复杂的波动率度量（Yang-Zhang 等）可能同样不改善截面信号；
  建议波动率族**止步于 Parkinson**（Round 3 升级），不再深挖。
- **保留意义**：作为负结论档案——避免未来投入 GK/YZ 等"更精确"的波动率度量。
- 种子 [`low_vol_20d_park.md`](low_vol_20d_park.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
