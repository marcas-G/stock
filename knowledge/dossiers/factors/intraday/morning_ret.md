---
xname: intraday_morning_ret
formula: |
  _am_ret = day_sum(if_else(minute_index <= 120, close / im_delay(close, 1) - 1, 0)) signal = _am_ret
tags: [minute, mine_m1, negative_result]
params: {}
status: 无效
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_morning_ret 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_morning_ret`（= `factor/intraday/morning_ret.yaml`） |
| 方向 | `-1` |
| 状态 | 无效 |

## 2. 逻辑

见 spec formula（分钟 bars_1m 折日因子，M1/M2 批探索）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_morning_ret/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.00686 |
| t 值 | -1.20 |
| IR | -0.044 |
| 近 26 周 t | 0.12 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：上午累计收益次日均值回归假设不成立（|t|=1.2）；不再迭代。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；复跑命令见 R09 报告 §3。

---
*档案规范见 `_template.md`。*
