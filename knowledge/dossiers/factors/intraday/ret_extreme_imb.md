---
xname: intraday_ret_extreme_imb
formula: |
  _r = close / im_delay(close, 1) - 1 signal = day_max(_r) + day_min(_r)
tags: [minute, negative_result]
params: {}
status: 观察
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_ret_extreme_imb 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_ret_extreme_imb`（= `factor/intraday/ret_extreme_imb.yaml`） |
| 方向 | `-1` |
| 状态 | 观察 |

## 2. 逻辑

max涨+max跌（极端净偏多）反转假设：全期 |t| 不小但对库 corr_max=0.728（≥0.7 与 vol_asym 同族）、retention=0.37<0.5 → 落观察，信息被库吸收。

## 4. 验证结果

> 快照自 `runs/platform/intraday_ret_extreme_imb/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.03237 |
| t 值 | -11.10 |
| IR | -0.413 |
| 近 26 周 t | -0.88 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：max涨+max跌（极端净偏多）反转假设：全期 |t| 不小但对库 corr_max=0.728（≥0.7 与 vol_asym 同族）、retention=0.37<0.5 → 落观察，信息被库吸收。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
