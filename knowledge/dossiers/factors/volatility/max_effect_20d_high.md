---
xname: max_effect_20d_high
formula: |
  signal = ts_max(high / ts_delay(close, 1) - 1, 20)
tags: [mine_r8, max_effect, lottery, intraday_spike, H1, candidate]
params: {}
status: 候选（强：IC 0.0699, t=5.00, IR=0.375）
created_ts: 2026-09-16
updated_ts: 2026-09-16
snapshot: CH 运行快照（2026-09-16，无 exclude_st 版；results/ 未随仓存档；复跑命令见 §3）
---

# max_effect_20d_high 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `max_effect_20d_high`（= `factor/volatility/max_effect_20d_high.yaml`） |
| 类别 | custom |
| 方向 | `-1`（信号高 → 预期收益低） |
| 状态 | 候选（强：IC 0.0699, t=5.00, IR=0.375） |
| 标签 | mine_r8, max_effect, lottery, intraday_spike, H1, candidate |
| 创建 | 2026-09-16（挖因子轮 1，种子 `max_effect_20d`，变异 H1） |
| 最近更新 | 2026-09-16 |

## 2. 逻辑

**动机**：种子 `max_effect_20d` 用**收盘对收盘**收益的最大值代表"彩票性"（隐含假设 H1：
散户评估彩票看日终涨跌幅）。但前景理论中彩票价值来自"曾经可能赚到的最大值"——
A 股涨停/冲高发生在**盘中**，"摸高后回落"恰把追高者套住、暴露彩票需求；收盘收益低估
此信息。

**核心逻辑**：把"单日最大收盘涨幅"换成"单日最大**盘中摸高幅度**"（最高价 / 昨收 - 1）：
捕捉窗口内曾被市场报价过的最大涨幅（彩票体验峰值）。方向仍为负（摸得越高 → 未来越弱）。

**数学表达**：

```
signal = ts_max(high / ts_delay(close, 1) - 1, 20)
```

**输入数据**：`high`、`close`（qfq 复权视图）

## 3. 参数与实现

### 参数表

无参数（窗口/口径写死在公式中；与种子同构）。

### 处理链

```
universe: {exchanges: [SSE, SZSE]}   # CH 运行版：无 exclude_st（CH 缺 stock_st 表）
date: 2023-01-01 ~ 2026-07-31
process: winsorize(quantile=0.99) → standardize()
target: forward_return_5d；adjustment: qfq
```

### 实现（YAML 全文）

```yaml
name: max_effect_20d_high
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
  from polars_ta.prefix.wq import ts_max, ts_delay
  signal = ts_max(high / ts_delay(close, 1) - 1, 20)
```

复跑：`cd platform && FACTORLAB_DATA_BACKEND=ch .venv/bin/factorlab run ../research/factor/volatility/max_effect_20d_high.yaml`

## 4. 验证结果

> 数据快照自 `runs/platform/max_effect_20d_high/summary.json`（2026-09-16 运行；无 exclude_st 版）。

### 样本

| 项 | 值 |
|----|----|
| 区间 | 2023-01-03 ~ 2026-07-31 |
| 周数（有效） | 178 |
| 平均股票数 | 5037.9 |
| 复权 | qfq |
| 信号缺失率 | 0.0335 |

### IC（方向调整后取绝对值）

| 指标 | 值 |
|------|----|
| RankIC mean | 0.0699 |
| t 值 | 5.00 |
| IR | 0.375 |
| 近 26 周 mean | 0.0753 |
| 近 26 周 t | 1.72 |
| PearsonIC mean | 0.0151（t=1.30） |

### 分层（十分位等权）

| 项 | 值 |
|----|----|
| spread（D1−D10 周均收益，direction 口径） | ±0.001363 |
| 单调性 | False |
| D1 mean_ret | 0.002486 |
| D10 mean_ret | 0.001123 |

### 判定

- 对照 playbook：RankIC 0.0699（>0.05 优秀）、|t|=5.00、IR 0.375（>0.3 优秀）、近 26 周 t=1.72 未失效 → **候选（强）**；
- 同环境对照：种子 `max_effect_20d`（CH 影子跑）IC 0.0673 / spread ±0.001162 → 本变体 IC **+3.7%**、spread **+17%**，换手相当（monthly 0.776 vs 0.790）。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-16 | 衍生：`max_effect_20d_high_intraday` | 挖矿轮7：摸高含跳空→纯日内（high/open），见 [`high_intraday.md`](high_intraday.md) | 0.0720 | 5.56 | **候选（升级）**：t +11%, spread +111% |
| 2026-09-16 | `max_effect_20d_high`（初始） | 挖因子轮 1：H1 收盘收益 max → 盘中摸高 max | 0.0699 | 5.00 | 候选（强）：较种子 IC +3.7%、spread +17% |

## 6. 风险与备注

- **口径风险**：`high/昨收-1` 在涨停日被封板截断（主板 ≤10%），跨板阈值不一致（创业板/科创 20%），
  横截面里不同板块的"摸高上限"不同；
- **实现风险**：high 为盘中报价，可能含瞬时挂单/数据毛刺（winsorize 已削尾）；
- **缺失率** 3.35%（同种子）；
- 与种子高度相关（同一 max 结构），组合价值需相关性检验；与 `max_effect_5d` 的关系未测；
- CH 运行版缺 exclude_st（含 ST 股），平台库恢复后应复跑对照。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `docs/factor-mining-playbook.md`。*
