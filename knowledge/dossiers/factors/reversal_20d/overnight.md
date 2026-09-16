---
xname: reversal_20d_overnight
formula: |
  signal = ts_sum(open / ts_delay(close, 1) - 1, 20)
tags: [mine_r5, reversal, overnight, momentum_discovery, sign_flip]
params: {}
status: 无效（direction=-1 假设） / **强发现**（隔夜是动量，反号 t=+4.35）
created_ts: 2026-09-16
updated_ts: 2026-09-16
snapshot: 历史快照（验证数字不可复跑；R21 标注，见 ../README.md）
---

# reversal_20d_overnight 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `reversal_20d_overnight`（= `research/factor/reversal_20d/overnight.yaml`） |
| 类别 | custom |
| 方向 | `-1`（假设隔夜也反转，实测反号） |
| 状态 | **无效**（按 direction=-1 判定）——**但揭示强发现**：隔夜收益呈**动量** |
| 标签 | mine_r5, reversal, overnight, momentum_discovery, sign_flip |
| 创建 | 2026-09-16（挖矿轮次 5，种子 `reversal_20d_intraday`） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `reversal_20d_intraday` 用**日内累计收益**
（`ts_sum(close/open - 1, 20)`）作为反转信号，隐含假设"隔夜成分（open vs
prev_close）无信息或反号"——但**从未实证验证**隔夜的行为。

**核心逻辑**：把公式改为**纯隔夜累计收益**，检验隔夜是否同样反转。

**数学表达**：

```
overnight_ret_t = open_t / close_{t-1} - 1
signal          = sum(overnight_ret, 20)
```

**输入数据**：`open`、`close`（qfq 复权）

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
name: reversal_20d_overnight
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
  from polars_ta.prefix.wq import ts_sum, ts_delay
  signal = ts_sum(open / ts_delay(close, 1) - 1, 20)
```

## 4. 验证结果

> 数据快照自 `runs/platform/reversal_20d_overnight/summary.json`（2026-09-16，
> `st_degrade: true`）。种子同日重跑对比。

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178 |
| 平均股票数 | 5040 |
| 信号缺失率 | 0.0445 |

**关键：raw IC 反号**

| 指标 | Raw（本方向） | Adj（按 direction=-1） |
|------|---------------|------------------------|
| RankIC mean | **+0.0242** | -0.0242（负 → 假设错）|
| t | **+4.35** | -4.35 |
| IR | +0.326 | -0.326 |
| Pearson IC | +0.0114 | -0.0114 |
| Pearson t | +2.40 | -2.40 |
| Spread | +0.0023 | -0.0023 |
| 近 26 周 mean | +0.0339 | -0.0339 |
| 近 26 周 t | **+2.51** | -2.51 |

### 判定（对照种子同日重跑）

| 因子 | Raw IC | t | IR | Pearson raw |
|------|--------|---|----|-------------|
| 种子 `reversal_20d_intraday` | **-0.0577** | -5.12 | -0.384 | -0.0284 |
| 变异 `reversal_20d_overnight` | **+0.0242** | **+4.35** | +0.326 | +0.0114 |

- **主假设（隔夜也反转）** **强证伪**：raw IC **反号**——隔夜收益与未来 5 日收益**正相关**。
- **反向结论：隔夜是动量、日内是反转**——两个成分**符号相反**！
- 种子的**隐含假设"隔夜无信号"被更强地证实**：不仅不是同样反转，还**反号**。
- **近 26 周对照**：种子 t=-0.29（近期失效），变异 t=**+2.51**（近期强动量）。
  → 隔夜动量是**近期主导信号**，与反转家族**负相关**。
- **理论支撑**：与 Bogoev & Orlov (2013)、Park & Song (2019) 发现一致——
  隔夜 vs 日内收益有**不同风险溢价**与不同行为机制。

**结论**：按 direction=-1 **无效**——但**发现"隔夜动量"这一独立强信号**（|t|=4.35、
|IR|=0.326、近 26 周 t=+2.51）。该发现应作为**新种子**（下轮挖 momentum 家族）
或独立因子族（当前未加，避免同轮多因子）。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | raw IC | t | 结论 |
|------|-----------|------|--------|---|------|
| 2026-09-16 | `reversal_20d_overnight`（初始） | 挖矿轮5：日内→隔夜分解 | +0.0242 | +4.35 | **无效**（假设反转）——发现**隔夜动量** |

## 6. 风险与备注

- **本档案的双重角色**：
  1. **负结论档案**：证明"隔夜也反转"不成立，种子公式的日内选择是对的
  2. **新维度发现档案**：揭示"隔夜动量"作为独立强信号（|t|=4.35、IR=0.326）
- **建议后续动作**：
  - **短期**：把本因子的 direction 改为 +1、加入 momentum 族作为独立因子
    （需要新档案；本轮不添加避免同轮多因子）
  - **中期**：验证 overnight_ret × reversal 分解在组合层面的增益
- **保留意义**：档案本身按 direction=-1 判定无效，但 raw IC 数据揭示了
  一个可能被低估的**动量维度**——种子档案 [`intraday.md`](intraday.md) 应加
  一条脚注指向本档案。
- 种子 [`intraday.md`](intraday.md)。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
