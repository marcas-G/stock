---
xname: reversal_20d_netflow_vol
formula: |
  signal = ts_sum(sign(returns(close)) * volume, 20)
tags: [mine_r9, netflow, volume, marginal]
params: {}
status: 观察中（vs 种子 amount 版边际优势 -21% IC；两版本截面相关 0.919）
created_ts: 2026-09-16
updated_ts: 2026-09-16
snapshot: 历史快照（验证数字不可复跑；R21 标注，见 ../README.md）
---

# reversal_20d_netflow_vol 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `reversal_20d_netflow_vol`（= `research/factor/reversal_20d/netflow_vol.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | 观察中（**边际**）——volume 与 amount 近乎等效（截面 ρ=0.919） |
| 标签 | mine_r9, netflow, volume, marginal |
| 创建 | 2026-09-16（挖矿轮次 9，种子 `reversal_20d_netflow`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `reversal_20d_netflow` 用 `amount × sign(returns(close))`
（成交额 × 前收方向）——隐含"成交额是资金流的正确度量"。检验：改用
`volume × sign(...)`（成交量）是否等价？若 amount 含**价格水平偏差**
（高价股 amount 天然大），volume 应更"纯净"。

**核心逻辑**：只改单位（amount→元 / volume→股），方向基准锁定为
`sign(returns(close))`（与种子完全一致）。

**数学表达**：

```
signal = sum(sign(returns(close)) × volume, 20)   # 股 × ±1
```

**输入数据**：`close`、`volume`

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
name: reversal_20d_netflow_vol
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
  signal = ts_sum(sign(returns(close)) * volume, 20)
```

## 4. 验证结果

> 数据快照自 `runs/platform/reversal_20d_netflow_vol/summary.json`
> （2026-09-16，`st_degrade: true`）。种子 `reversal_20d_netflow` 同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178 |
| 平均股票数 | 5038 |
| 信号缺失率 | 0.0445 |

| 指标 | 值 |
|------|----|
| RankIC mean（方向调整后） | 0.0327 |
| t 值 | 4.21 |
| IR | 0.316 |
| 近 26 周 mean / t | 0.0021 / 0.11 |
| PearsonIC mean | 0.0141（t=2.30） |

| 项 | 值 |
|----|----|
| spread | 0.00189 |
| D1 / D10 | 0.00145 / -0.00044 |

### 判定（对照种子同日重跑）

| 指标 | 种子（amount） | 变异（volume） | Δ |
|------|----------------|----------------|---|
| RankIC mean | 0.0416 | 0.0327 | **-21%** |
| t | -5.07 | -4.21 | -17% |
| IR | -0.380 | -0.316 | -17% |
| PearsonIC | -0.0173 | -0.0141 | -18% |
| Spread | -0.0026 | -0.0019 | -27% |

**两信号截面秩相关 ρ ≈ 0.919**（近乎同一信号）。
配对差 `ic_amount − ic_volume` mean ≈ −0.0089，配对 t ≈ −2.23（p≈0.03）——
**边际**差异。

- **主假设（volume 去除价格水平偏差后应更强）** **未验证**：volume 版 t=4.21
  **本身也显著**（t>2），不是"弱"信号——两者截面相关 0.919 说明它们是
  **近似同一信号**，差异是**边际**而非量级。
- **amount 优势是边际的**：配对差 p≈0.03 勉强显著，IC 降 21%——不足以
  "强证伪"量纲无关假设，只能说 **amount 边际更好**。
- **理论含义**：
  - amount ≈ close × volume，close 是"当日均价"，因此 amount 是 volume 的
    **按价格加权**版本——高价股 amount 权重更大
  - 若"资金流"的经济学含义是"**美元/元 流量**"（市场冲击），amount 是对的
  - 若"资金流"的经济学含义是"**份额交换**"，volume 才对
  - 实测：两者截面 ρ=0.919 意味着**两种解读给出近似结论**；amount 略优暗示
    经济含义更接近"美元流量"（高价股的交易确实更值得关注）
- **两版本近等效**：截面 ρ=0.919 说明**做 ensemble 意义不大**（高度共线）；
  若要 ensemble，应做**增量回归**（`fwd_ret ~ vol_signal + amount_signal`）
  看两者系数是否各自显著——本档案未做（超出单因子挖因子范围）
- 结论：**观察中（边际）**——保留作对照，种子仍是 netflow 族首选。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `reversal_20d_netflow_vol`（初始） | 挖矿轮9：amount→volume | 0.0327 | 4.21 | **观察中（边际）**：ρ=0.919 近等效 |

## 6. 风险与备注

- **审核教训**：本轮初稿犯了**双重变异错误**（同时改单位+方向基准），
  被独立审核员抓到并要求修正——档案记录此教训：
  - **单变量实验原则**：变异只能改**一个正交维度**；其他维度锁定
  - 变异记录的**每一处改动**都要能归因到一个假设
- **未来研究**：
  - 若要严格判定"美元 vs 份额哪个是正确计价"，应做**双变量回归**
  - 若 A 股市场结构变化（高价股占比变化），两者关系可能漂移
- 种子 [`netflow.md`](netflow.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
