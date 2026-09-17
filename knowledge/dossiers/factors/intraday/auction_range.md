---
xname: intraday_auction_range
formula: |
  signal = 开盘集合竞价(idx0) high/low - 1（low<=0 守卫）
tags: [minute, auction, reversal, mine_m1]
params: {}
status: 候选
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d v2；本快照产自分母守卫修正前，flip 重跑进行中，预期指标微变）
---

# intraday_auction_range 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_auction_range`（= `factor/intraday/auction_range.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | 候选（minute 参考库成员） |
| 创建/更新 | 2026-09-17 |

## 2. 逻辑

**动机**：开盘集合竞价撮合价带的振幅（high/low−1）度量开盘分歧度：竞价振幅大 =
多空对开盘价定价分歧剧烈。假设：分歧剧烈次日反转（direction=-1）。

**核心逻辑**：`signal = high@idx0/low@idx0 − 1`（开盘竞价 bar 振幅；实测该 bar
high≠low，中位振幅 0.82%，非退化）。

## 3. 参数与实现

无参数。处理链：universe bars1m_2023_2025；2023-01-01~2025-12-31；target forward_return_1d；
adjustment raw；evaluation daily（v2）。

```yaml
name: intraday_auction_range
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
  _hi = day_max(if_else(minute_index == 0, high, None))
  _lo = day_max(if_else(minute_index == 0, low, None))
  signal = if_else(_lo > 0, _hi / _lo - 1, None)
```

## 4. 验证结果

> 快照自 `runs/platform/intraday_auction_range/summary.json`（2026-09-17，守卫前 run）。

样本：723 逐日期，均 4846 只，有效率 99.84%。

| 指标 | 值 |
|------|----|
| RankIC mean（原始） | -0.0433 |
| t 值 | -8.64 |
| IR | -0.321 |
| 近 26 周 | -0.0303 / t=-1.34 |
| PearsonIC mean | -0.0143（同号） |

逐年 t：2023 **-7.2**、2024 **-4.0**、2025 **-5.0**。

分层：spread（×direction）=+0.001472，g0=0.001352，g9=-0.000120，不单调；月换手 0.841。

### 冗余/增量检查（D10）

vs minute 库（cap/ret_concentration）：max ρ=0.19，resIC t=**-4.85**，retention=0.88 → **可加入**。

### 判定

显著（|t|=8.64，逐年一致）→ **候选**，已入 minute 参考库。
动作：flip 重跑（守卫版）后刷新快照；跟踪近端。

## 5. 迭代历史

| 日期 | 变体 | 改动 | IC mean | t | 结论 |
|------|------|------|---------|---|------|
| 2026-09-17 | 守卫版 | low<=0 → None（源缺陷日防 inf） | -0.0433 | -8.64 | 显著入库 |

## 6. 风险与备注

- 审核（subagent）通过；一般问题：2024-11-13 minute0 low=0 源数据缺陷（已守卫，登记
  `pending-items.md` A1）。
- 竞价 bar 数据依赖开盘集合竞价快照质量。

---
*档案规范见 `_template.md`。*
