---
xname: intraday_time_of_max_vol
formula: |
  _at = if_else(volume >= day_max(volume), minute_index, 0) signal = day_max(_at) / 239
tags: [minute, negative_result]
params: {}
status: 观察
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_time_of_max_vol 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_time_of_max_vol`（= `factor/intraday/time_of_max_vol.yaml`） |
| 方向 | `1` |
| 状态 | 观察 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_time_of_max_vol/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.01688 |
| t 值 | 6.81 |
| IR | 0.253 |
| 近 26 周 t | 1.12 |
| 期数 | 723（daily） |

**判定**：最大成交量分钟位置：全期 t=6.81（原始正号，dir 已修 +1）但 resIC t=+1.45<2、retention=0.15 → 观察。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
