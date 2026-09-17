---
xname: intraday_gap_fill_speed
formula: |
  _fill = day_min(if_else(close >= prev_close, minute_index, None)) signal = if_else(_fill >= 0, _fill / 239, 1)
tags: [minute, negative_result, mine_m6]
params: {}
status: 观察
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_gap_fill_speed 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_gap_fill_speed`（= `factor/intraday/gap_fill_speed.yaml`） |
| 方向 | `-1` |
| 状态 | 观察 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_gap_fill_speed/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.01616 |
| t 值 | -3.75 |
| IR | -0.139 |
| 近 26 周 t | -2.00 |
| 期数 | 723（daily） |

**判定**：全期 t=-3.75、近 26 周 t=-2.00 同号，resIC t=-1.80 不足、retention=0.28 → 观察（与 gap_fill_ratio 同族互证：回补慢→次日弱，但净信息被库吸收）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
