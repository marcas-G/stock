---
xname: intraday_range_position
formula: |
  signal = (day_last(close) - day_min(low)) / (day_max(high) - day_min(low)) - 0.5
tags: [minute, mine_m1, negative_result]
params: {}
status: 冗余
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_range_position 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_range_position`（= `factor/intraday/range_position.yaml`） |
| 方向 | `-1` |
| 状态 | 冗余 |

## 2. 逻辑

见 spec formula（分钟 bars_1m 折日因子，M1/M2 批探索）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_range_position/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.01726 |
| t 值 | -3.52 |
| IR | -0.131 |
| 近 26 周 t | -2.48 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：retention=0.126<0.20 判冗余（收盘区间位置信息大部分被库吸收）；与 close_to_vwap ρ=0.80 互斥落选。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；复跑命令见 R09 报告 §3。

---
*档案规范见 `_template.md`。*
