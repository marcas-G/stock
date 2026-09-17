---
xname: intraday_max_vol_ratio
formula: |
  signal = day_max(volume) / day_sum(volume)
tags: [minute, negative_result]
params: {}
status: 无效
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_max_vol_ratio 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_max_vol_ratio`（= `factor/intraday/max_vol_ratio.yaml`） |
| 方向 | `-1` |
| 状态 | 无效 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_max_vol_ratio/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.00629 |
| t 值 | -1.86 |
| IR | -0.069 |
| 近 26 周 t | -3.13 |
| 期数 | 723（daily） |

**判定**：最大单分钟量占比：t=-1.86 未达门槛（近端 t=-3.13 但全期不足）→ 无效。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
