---
xname: intraday_close_auction_premium
formula: |
  signal = day_last(close) / close@minute_index==237 - 1   # 收盘集合竞价价 / 连续竞价末价 - 1
tags: [minute, auction, reversal, mine_m1]
params: {}
status: 候选
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2；产物 runs/platform/intraday_close_auction_premium/）
---

# intraday_close_auction_premium 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_close_auction_premium`（= `factor/intraday/close_auction_premium.yaml`） |
| 类别 | custom |
| 方向 | `-1`（溢价越高次日越跌；原 spec 误设 1，2026-09-17 修正） |
| 状态 | 候选（minute 参考库种子） |
| 标签 | minute, auction, reversal |
| 创建 | 2026-09-17 |
| 最近更新 | 2026-09-17 |

## 2. 逻辑

**动机**：收盘集合竞价（14:57–15:00，minute_index 238–239）集中承接全天失衡的委托，
竞价成交价相对连续竞价末分钟（idx 237）的溢价度量了竞价阶段的**价格冲击**。
假设：竞价溢价高（买方把收盘价顶上连续竞价水平）→ 透支次日买盘 → 次日回落（direction=-1）。
属"日内冲击→次日反转"微观结构族。

**核心逻辑**：`signal = day_last(close)/close@237 − 1`，即收盘竞价价对连续末价的百分比溢价。

**输入数据**：`bars_1m`（close，minute_index），adjustment=raw。

## 3. 参数与实现

### 参数表

无参数（窗口固定：连续末分钟 idx=237、收盘竞价末分钟 day_last）。

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
name: intraday_close_auction_premium
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
  _cont = day_max(if_else(minute_index == 237, close, None))
  signal = day_last(close) / _cont - 1
```

## 4. 验证结果

> 快照自 `runs/platform/intraday_close_auction_premium/summary.json`（2026-09-17，
> daily/forward_return_1d，evaluation.version=2）。

### 样本

| 项 | 值 |
|----|----|
| 区间 | 2023-01-01 ~ 2025-12-31（723 交易日） |
| 期数（逐日） | 723 |
| 平均股票数 | 4846 |
| 复权 | raw |
| 信号有效率 | 99.84% |

### IC

| 指标 | 值 |
|------|----|
| RankIC mean（原始） | -0.0358 |
| t 值 | -22.67 |
| IR | -0.843 |
| 近 26 周 mean | -0.0428 |
| 近 26 周 t | -6.68 |
| PearsonIC mean | -0.0281（与 rank 同号） |

逐年稳定性（逐日 Spearman 分组 t）：2023 **-23.7**、2024 **-8.3**、2025 **-11.5**，
IC<0 天数占比 70–93%——三年方向一致，非单段行情驱动。

### 分层（十分位，spread=(g9−g0)×direction，正=好）

| 项 | 值 |
|----|----|
| spread | +0.002234 |
| 单调性 | 否（极端两端分化，g0 最高） |
| g0 mean_ret | 0.002035 |
| g9 mean_ret | -0.000199 |
| 月换手 | 0.854 |

### 冗余/增量检查（D10）

- minute 参考库首员（种子）；对同批全部分钟候选 max|ρ|≤0.03——高度独立。
- 与 `intraday_close_ret` **ρ=1.000**（同公式重复件，待删，保留本名）。

### 判定

对照 playbook §4.1：**显著**（|t|=22.7 ≫ 2，|IR|=0.84，近三年逐年稳定，近 26 周
t=-6.7 无衰减）。→ **候选**，已作 minute scale 种子入参考库。

- 下一个动作：样本外滚动复验；研究竞价溢价的**开盘侧镜像**（open_gap 族）与本因子的正交性。

## 5. 迭代历史

| 日期 | 变体/版本 | 改动 | IC mean | t | 结论 |
|------|-----------|------|---------|---|------|
| 2026-09-17 | dir=1 初跑（周频） | 首轮分钟面探索 | -0.0358 | -4.74（5d 非重叠） | 有效但方向记反 |
| 2026-09-17 | daily v2 重跑 | D9 逐日 + forward_1d | -0.0358 | -22.7 | 显著，逐日口径更强 |
| 2026-09-17 | **dir 修正 -1** | 方向纠正 | -0.0358 | -22.67 | 候选入库（种子） |

## 6. 风险与备注

- **失效风险**：竞价机制变更（如收盘竞价规则调整）直接改变信号结构；溢价效应对
  流动性环境敏感。
- **数据风险**：依赖分钟数据完整覆盖（uncovered drop 处理）；停牌日无连续末价。
- 与 `close_ret` 为同式重复件（ρ=1），后者待删除；`auction_range`/`open_gap` 为
  竞价族旁支（未显著）。
- t=-22.7 属强信号，警惕与既有收盘类策略重叠（组合前查重）。

---
*档案规范见 `_template.md`；因子挖掘方法论见 `knowledge/handbooks/factor-mining-playbook.md`。*
