---
xname: intraday_ret_concentration
formula: |
  signal = max|r| / sum|r|   # _r = 分钟收益 close/im_delay(close,1)-1
tags: [minute, intraday_path, reversal, jump, mine_m1]
params: {}
status: 候选
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2；产物 runs/platform/intraday_ret_concentration/）
---

# intraday_ret_concentration 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_ret_concentration`（= `factor/intraday/ret_concentration.yaml`） |
| 类别 | custom |
| 方向 | `-1`（集中度越高次日越跌） |
| 状态 | 候选（minute 参考库成员） |
| 标签 | minute, intraday_path, reversal, jump |
| 创建 | 2026-09-17 |
| 最近更新 | 2026-09-17 |

## 2. 逻辑

**动机**：全天价格路径是"均匀演化"还是"少数几分钟剧烈跳变"？跳变式路径由事件/冲击
驱动，冲击方（流动性提供者/对手盘）次日有回归动机。假设：日内涨跌越集中在极少数
"跳幅分钟"→ 次日反转（direction=-1）。与 MAX 效应（日频）同族但取数在**分钟级**。

**核心逻辑**：`signal = max|r| / Σ|r|`（r 为分钟收益）。占比高 = 全天波动集中在
一两个分钟；占比低 ≈ 波动均匀分布。

**数学表达**：

```
_r  = close_t / close_{t-1} - 1        （日内逐分钟，im_delay）
S   = max_t |_r_t| / Σ_t |_r_t|
```

**输入数据**：`bars_1m`（close），adjustment=raw。

## 3. 参数与实现

### 参数表

无参数（全天窗口）。

### 处理链

```
universe: bars1m_2023_2025
date: 2023-01-01 ~ 2025-12-31
process: 无
target: forward_return_1d（D11 因子侧固定 1 日）
adjustment: raw
evaluation: daily（D9 逐日口径）
```

### 实现（YAML 全文）

```yaml
name: intraday_ret_concentration
category: custom
direction: -1
interface: bars_1m
adjustment: raw
universe:
  ref: bars1m_2023_2025
date:
  start: "2023-01-01"
  end: "2025-12-31"
formula: |
  _r = close / im_delay(close, 1) - 1
  _max = day_max(abs(_r))
  _sum = day_sum(abs(_r))
  signal = _max / _sum
```

## 4. 验证结果

> 快照自 `runs/platform/intraday_ret_concentration/summary.json`（2026-09-17，
> daily/forward_return_1d，evaluation.version=2）。

### 样本

| 项 | 值 |
|----|----|
| 区间 | 2023-01-01 ~ 2025-12-31（723 交易日） |
| 期数（逐日） | 723 |
| 平均股票数 | 4836 |
| 复权 | raw |
| 信号有效率 | 99.62% |

### IC

| 指标 | 值 |
|------|----|
| RankIC mean（原始） | -0.0382 |
| t 值 | -11.78 |
| IR | -0.438 |
| 近 26 周 mean | -0.0191 |
| 近 26 周 t | -1.60 |
| PearsonIC mean | +0.0247 |

> **rank 与 pearson 符号相反说明**：信号右尾极重（集中度的分布严重右偏），
> Pearson 被极端大值观测主导、方向失真；rank-IC 对尾稳健，单调负关系成立——以 rank 为准。

逐年稳定性（逐日 Spearman 分组 t）：2023 **-5.5**、2024 **-6.9**、2025 **-7.3**——三年一致。

### 分层（十分位，spread=(g9−g0)×direction，正=好）

| 项 | 值 |
|----|----|
| spread | +0.000228 |
| 单调性 | 否（低集中度端收益最高） |
| g0 mean_ret | 0.000927 |
| g9 mean_ret | 0.000699 |
| 月换手 | 0.848 |

### 冗余/增量检查（D10）

- vs minute 库种子 `intraday_close_auction_premium`：rank ρ = **0.033**，
  resIC t = **-4.70**（被解释 R²=0.0025）——信息几乎零重叠且净增量显著 → **可加入**。
- vs 同批其余候选 max|ρ|≤0.20（与 path_efficiency Pearson 0.51 稍高，rank 仅 0.17，
  path_efficiency 本身不显著未入库）。

### 判定

对照 playbook §4.1：**显著**（|t|=11.8，|IR|=0.44，逐年一致；近 26 周 t=-1.6 略弱于
全期但仍同号）。→ **候选**，已入 minute 参考库。

- 下一个动作：把近 26 周衰减列入跟踪；研究"集中度"的开盘/尾盘分段变体。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-17 | daily v2 | D9 逐日 + forward_1d 首跑 | -0.0382 | -11.78 | 显著，入库 |

## 6. 风险与备注

- **失效风险**：近 26 周 t=-1.6 弱于全期（-11.8），需持续跟踪；若涨跌停/熔断制度变化，
  跳变分钟的定义受影响。
- **数据风险**：依赖分钟 close 连续可比（raw 价，除权日分钟收益内部无影响，因 im_delay
  在日内滞后）。
- Pearson/rank 分歧源于信号重尾，组合使用时**必须用 rank 或截尾**。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `knowledge/handbooks/factor-mining-playbook.md`。*
