---
xname: max_effect_20d_zmax
formula: |
  signal = ts_max((returns(close) - ts_mean(returns(close), 20)) / ts_std_dev(returns(close), 20), 20)
tags: [mine_r8, max_effect, lottery, vol_normalized, H2, watch]
params: {}
status: 观察中（IC 0.0301；近 26 周失效）
created_ts: 2026-09-16
updated_ts: 2026-09-16
snapshot: CH 运行快照（2026-09-16，无 exclude_st 版；results/ 未随仓存档；复跑命令见 §3）
---

# max_effect_20d_zmax 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `max_effect_20d_zmax`（= `factor/volatility/max_effect_20d_zmax.yaml`） |
| 类别 | custom |
| 方向 | `-1`（信号高 → 预期收益低） |
| 状态 | 观察中（IC 0.0301；近 26 周失效） |
| 标签 | mine_r8, max_effect, lottery, vol_normalized, H2, watch |
| 创建 | 2026-09-16（挖因子轮 1，种子 `max_effect_20d`，变异 H2） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子的 max 是绝对幅度，高波动股的 max 系统性更大（隐含假设 H2：max 估计
稳健、与股票自身波动无关）。用收益 z 分数（相对自身 20 日均值/波动）再取 max，
剥离波动率混淆，得到"相对自己的异常暴涨峰值"。

**核心逻辑**：把"绝对最大涨幅"换成"**标准化后的异常度峰值**"——同一只股票窗口内
最反常的一天相对其常态有多极端；方向仍为负。

**数学表达**：

```
_r = returns(close)
_z = (_r - ts_mean(_r, 20)) / ts_std_dev(_r, 20)
signal = ts_max(_z, 20)
```

**输入数据**：`close`（qfq 复权视图）

## 3. 参数与实现

### 参数表

无参数（标准化窗口与 max 窗口均为 20，写死在公式中）。

### 处理链

```
universe: {exchanges: [SSE, SZSE]}   # CH 运行版：无 exclude_st
date: 2023-01-01 ~ 2026-07-31
process: winsorize(quantile=0.99) → standardize()
target: forward_return_5d；adjustment: qfq
```

### 实现（YAML 全文）

```yaml
name: max_effect_20d_zmax
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
  from polars_ta.prefix.wq import ts_max, ts_mean, ts_std_dev
  _r = returns(close)
  _z = (_r - ts_mean(_r, 20)) / ts_std_dev(_r, 20)
  signal = ts_max(_z, 20)
```

复跑：`cd platform && FACTORLAB_DATA_BACKEND=ch .venv/bin/factorlab run ../research/factor/volatility/max_effect_20d_zmax.yaml`

## 4. 验证结果

> 数据快照自 `runs/platform/max_effect_20d_zmax/summary.json`（2026-09-16 运行；无 exclude_st 版）。

### 样本

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 174（少 4 周：部分股票标准化分母为 0 产生缺失） |
| 平均股票数 | 5033.6 |
| 复权 | qfq |
| 信号缺失率 | 0.0561（高于种子 3.35%） |

### IC（方向调整后取绝对值）

| 指标 | 值 |
|------|----|
| RankIC mean | 0.0301 |
| t 值 | 5.01 |
| IR | 0.380 |
| 近 26 周 mean | 0.0034（t=0.25，**失效**） |
| PearsonIC mean | 0.0111（t=2.29） |

### 分层（十分位等权）

| 项 | 值 |
|----|----|
| spread（D1−D10） | ±0.002027 |
| 单调性 | False |
| D1 mean_ret | 0.002764 |
| D10 mean_ret | 0.000737 |

### 判定

- 对照 playbook：IC 0.0301（低于 0.05 门槛）虽 |t|=5.01 显著（低波动收益与 IC 比值），
  但**近 26 周 mean≈0（t=0.25）已失效** → **观察中（不推荐）**；
- 同环境对照：种子 IC 0.0673 → 波动率标准化把幅度**腰斩**（0.0301），说明 MAX 效应
  主要活在"绝对幅度"里而非"相对异常度"；H2 未被支持。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | `max_effect_20d_zmax`（初始） | 挖因子轮 1：H2 绝对幅度 → 波动率标准化异常度 | 0.0301 | 5.01 | 观察中：幅度腰斩 + 近 26 周失效 |

## 6. 风险与备注

- 标准化在波动接近 0 时不稳定（缺失率上升至 5.61%）；
- 结论为负向证据：**MAX 效应不来自"相对异常度"**，支持种子的绝对幅度口径——这是本轮
  有价值的证伪（见种子档案 §5）；
- CH 运行版缺 exclude_st。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
