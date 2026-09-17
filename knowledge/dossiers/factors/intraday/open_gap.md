---
xname: intraday_open_gap
formula: |
  signal = 开盘集合竞价(idx0) close / prev_close - 1   # 开盘跳空（close<=0 守卫）
tags: [minute, gap, reversal, mine_m1]
params: {}
status: 候选
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d v2；本快照产自守卫修正前，flip 重跑进行中）
---

# intraday_open_gap 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_open_gap`（= `factor/intraday/open_gap.yaml`） |
| 类别 | custom |
| 方向 | `1`（原 spec 误设 -1，与原始 IC 符号相反，2026-09-17 修正为 +1） |
| 状态 | 候选（minute 参考库成员） |
| 创建/更新 | 2026-09-17 |

## 2. 逻辑

**动机**：开盘集合竞价相对昨收的跳空幅度，度量隔夜信息强度。
假设：跳空方向**延续**（direction=+1，与多数日内反转族相反）——竞价高开承接
隔夜利好动量，次日续涨。原始 IC=+0.0199 印证正相关（原设 -1 方向记反已修正）。

**核心逻辑**：`signal = close@aution0 / prev_close - 1`。

## 3. 参数与实现

无参数。universe bars1m_2023_2025；2023-01-01~2025-12-31；target forward_return_1d；
adjustment raw（跨日 prev_close 由平台注入）；evaluation daily（v2）。

```yaml
name: intraday_open_gap
category: custom
direction: 1
interface: bars_1m
adjustment: raw
universe:
  ref: bars1m_2023_2025
date:
  start: "2023-01-01"
  end: "2025-12-31"
formula: |
  _auction_close = day_max(if_else(minute_index == 0, close, None))
  signal = if_else(_auction_close > 0, _auction_close / prev_close - 1, None)
```

## 4. 验证结果

> 快照自 `runs/platform/intraday_open_gap/summary.json`（2026-09-17，守卫前 run）。

样本：723 逐日期，均 4846 只，有效率 99.84%。

| 指标 | 值 |
|------|----|
| RankIC mean（原始） | +0.0199 |
| t 值 | +4.07 |
| IR | +0.151 |
| 近 26 周 | +0.0314 / t=+1.60 |
| PearsonIC mean | +0.0478（同号） |

逐年 t：2023 **+2.7**、2024 **+2.1**、2025 **+2.6**（三年稳定同号）。

分层：spread（×direction=+1）=-0.003145 → 实际 g9（高跳空）mean_ret=0.001654 > g0（低跳空）=-0.001491，
单调 True（跳空越高次日收益越高，延续效应）；月换手 0.878。

### 冗余/增量检查（D10）

vs minute 库：max ρ=0.034，resIC t=+2.40，retention=1.02 → **可加入**。

### 判定

显著（|t|=4.07，逐年稳定）→ **候选**，已入 minute 参考库（方向修正版）。
动作：flip 重跑（守卫版）刷新快照。

## 5. 迭代历史

| 日期 | 变体 | 改动 | IC mean | t | 结论 |
|------|------|------|---------|---|------|
| 2026-09-17 | dir=-1 初版 | 原始跑 | +0.0199 | +4.07 | 方向记反 |
| 2026-09-17 | dir=+1 + 守卫 | 方向修正 + auction_close<=0 → None | +0.0199 | +4.07 | 显著入库 |

## 6. 风险与备注

- 审核（subagent）通过；一般问题：2024-11-13 minute0 close=0 源缺陷（已守卫，登记
  `pending-items.md` A1）；**除权日伪跳空**（raw prev_close，实测除权日 signal 均值
  -3.9% vs 正常 -0.07%，登记 `pending-items.md` A2）——本因子延续效应结论建立在
  raw 口径，除权样本可能低估，列入数据风险。
- 与 auction_range（同用竞价 bar）相关低（ρ=0.015），独立信息。

---
*档案规范见 `_template.md`。*
