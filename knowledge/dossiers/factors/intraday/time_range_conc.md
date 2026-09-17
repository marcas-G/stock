---
xname: intraday_time_range_conc
formula: |
  _late_hi = day_max(if_else(minute_index >= 210, close, None)) _late_lo = day_min(if_else(minute_index >= 210, close, None)) _hi = day_max(close) _lo = day_min(close) signal = if_else(_hi - _lo > 0, (_late_hi - _late_lo) / (_hi - _lo), None)
tags: [minute, negative_result]
params: {}
status: 冗余
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_time_range_conc 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_time_range_conc`（= `factor/intraday/time_range_conc.yaml`） |
| 方向 | `-1` |
| 状态 | 冗余 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_time_range_conc/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.00951 |
| t 值 | 2.76 |
| IR | 0.103 |
| 近 26 周 t | -0.78 |
| 期数 | 723（daily） |

**判定**：全期 t=2.76 边缘，resIC t=-2.16 但 retention=-0.60 → 冗余（尾盘振幅占比信息被尾盘族吸收）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
