---
xname: intraday_lunch_jump
formula: |
  _lunch_ret = day_max(if_else(minute_index == 120, close / im_delay(close, 1) - 1, None)) signal = _lunch_ret
tags: [minute, mine_m1, negative_result]
params: {}
status: 无效
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_lunch_jump 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_lunch_jump`（= `factor/intraday/lunch_jump.yaml`） |
| 方向 | `1` |
| 状态 | 无效 |

## 2. 逻辑

见 spec formula（分钟 bars_1m 折日因子，M1/M2 批探索）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_lunch_jump/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.00271 |
| t 值 | -0.87 |
| IR | -0.032 |
| 近 26 周 t | 0.86 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：午间边界单分钟跳幅无次日信息（|t|=0.9）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；复跑命令见 R09 报告 §3。

---
*档案规范见 `_template.md`。*
