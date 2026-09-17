---
xname: intraday_close_to_vwap
formula: |
  _vwap = day_sum(close * volume) / day_sum(volume) signal = day_last(close) / _vwap - 1
tags: [minute, mine_m1, negative_result]
params: {}
status: 观察
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_close_to_vwap 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_close_to_vwap`（= `factor/intraday/close_to_vwap.yaml`） |
| 方向 | `-1` |
| 状态 | 观察 |

## 2. 逻辑

见 spec formula（分钟 bars_1m 折日因子，M1/M2 批探索）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_close_to_vwap/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.02712 |
| t 值 | -4.87 |
| IR | -0.181 |
| 近 26 周 t | -2.77 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：|t|=4.92 方向已修 -1；对库 resIC t=-0.30（收盘溢价信息已被 close_auction_premium 解释，retention 0.55）；与 range_position ρ=0.80 互斥落选。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；复跑命令见 R09 报告 §3。

---
*档案规范见 `_template.md`。*
