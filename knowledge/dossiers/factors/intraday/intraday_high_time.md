---
xname: intraday_high_time
formula: |
  _h = if_else(volume > 0, if_else(amount > 0, high, None), None) _at = if_else(_h >= day_max(_h), minute_index, 0) signal = day_max(_at) / 239
tags: [minute, negative_result]
params: {}
status: 观察中
created_ts: 2026-09-16
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_high_time 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_high_time`（= `factor/intraday/intraday_high_time.yaml`） |
| 方向 | `-1` |
| 状态 | 观察中 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_high_time/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.02414 |
| t 值 | 1.60 |
| IR | 0.149 |
| 近 26 周 t | -1.12 |
| 期数 | 116（daily） |

**判定**：重跑 daily（bars1m_2024h1 窗，116 期）t=1.60 不显著；日内高点时刻对次日无预测力（旧周频 t=-0.39 同结论）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；旧周频口径快照（2026-09-16）已被本 daily 重跑取代。

---
*档案规范见 `_template.md`。*
