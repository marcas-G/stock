---
xname: intraday_opening_auction_premium
formula: |
  _auc = day_max(if_else(minute_index == 0, close, None)) _m1 = day_max(if_else(minute_index == 1, close, None)) signal = if_else(_m1 > 0, _auc / _m1 - 1, None)
tags: [minute, negative_result]
params: {}
status: 无效
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_opening_auction_premium 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_opening_auction_premium`（= `factor/intraday/opening_auction_premium.yaml`） |
| 方向 | `-1` |
| 状态 | 无效 |

## 2. 逻辑

开盘竞价价 vs 开盘后第 1 分钟收盘的溢价：t=-0.15 无信息。

## 4. 验证结果

> 快照自 `runs/platform/intraday_opening_auction_premium/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.00055 |
| t 值 | -0.15 |
| IR | -0.005 |
| 近 26 周 t | -0.74 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：开盘竞价价 vs 开盘后第 1 分钟收盘的溢价：t=-0.15 无信息。竞价信息在 open_gap（对昨收）里已有，对首分钟无增量。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
