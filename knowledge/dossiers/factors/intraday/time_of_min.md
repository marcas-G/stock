---
xname: intraday_time_of_min
formula: |
  _at = if_else(close <= day_min(close), minute_index, 0) signal = day_max(_at) / 239
tags: [minute, negative_result]
params: {}
status: 观察
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_time_of_min 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_time_of_min`（= `factor/intraday/time_of_min.yaml`） |
| 方向 | `-1` |
| 状态 | 观察 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_time_of_min/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.01634 |
| t 值 | 4.04 |
| IR | 0.150 |
| 近 26 周 t | 0.36 |
| 期数 | 723（daily） |

**判定**：全期 t=4.04（原始正号）resIC t=+2.79 显著但 retention=0.447<0.5 → 观察。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
