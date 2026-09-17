---
xname: intraday_opening_drive
formula: |
  signal = day_sum(if_else(minute_index <= 30, close / im_delay(close, 1) - 1, 0))
tags: [minute, mine_m2, negative_result]
params: {}
status: 无效
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_opening_drive 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_opening_drive`（= `factor/intraday/opening_drive.yaml`） |
| 方向 | `-1` |
| 状态 | 无效 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_opening_drive/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.00068 |
| t 值 | -0.13 |
| IR | -0.005 |
| 近 26 周 t | 0.76 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：开盘 30 分钟冲高对次日无信息（t=-0.13）；且与库内 open_gap/auction_range 高共线（ρ=0.587）假设方向本身无独立价值。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
