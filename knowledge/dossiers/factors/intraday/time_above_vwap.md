---
xname: intraday_time_above_vwap
formula: |
  _vwap = day_sum(close * volume) / day_sum(volume) _n = day_sum(if_else(close > _vwap, 1, 0)) signal = if_else(day_sum(volume) > 0, _n / 239, None)
tags: [minute, negative_result, mine_m6]
params: {}
status: 冗余
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_time_above_vwap 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_time_above_vwap`（= `factor/intraday/time_above_vwap.yaml`） |
| 方向 | `1` |
| 状态 | 冗余 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_time_above_vwap/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.02983 |
| t 值 | 7.85 |
| IR | 0.292 |
| 近 26 周 t | 2.52 |
| 期数 | 723（daily） |

**判定**：全期 t=7.85 但 resIC t=-0.40、retention=-0.21——VWAP 上方时间信息被尾盘族完全吸收（r2_lib 仅 0.19 但残差反向，信息为库内成员组合）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
