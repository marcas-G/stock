---
xname: intraday_time_of_max_r
formula: |
  _r = close / im_delay(close, 1) - 1 _ar = abs(_r) _mx = day_max(_ar) _at = if_else(_ar >= _mx, minute_index, 0) signal = day_max(_at) / 239
tags: [minute, negative_result, mine_m10]
params: {}
status: 无效
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_time_of_max_r 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_time_of_max_r`（= `factor/intraday/time_of_max_r.yaml`） |
| 方向 | `-1` |
| 状态 | 无效 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_time_of_max_r/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.00186 |
| t 值 | -0.81 |
| IR | -0.030 |
| 近 26 周 t | -1.00 |
| 期数 | 723（daily） |

**判定**：最大分钟波动发生时刻（信息交易时点假设）：|t|=0.81。发生时刻本身无预测力；与 time_of_max_vol（观察）形成互证——时刻类假设在本数据集不成立。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
