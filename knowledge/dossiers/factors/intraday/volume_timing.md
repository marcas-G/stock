---
xname: intraday_volume_timing
formula: |
  _w = day_sum((minute_index + 1) * volume) _tot = day_sum(volume) signal = _w / _tot / 240
tags: [minute, mine_m1, negative_result]
params: {}
status: 观察
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_volume_timing 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_volume_timing`（= `factor/intraday/volume_timing.yaml`） |
| 方向 | `1` |
| 状态 | 观察 |

## 2. 逻辑

见 spec formula（分钟 bars_1m 折日因子，M1/M2 批探索）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_volume_timing/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.02161 |
| t 值 | 6.46 |
| IR | 0.240 |
| 近 26 周 t | 0.59 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：t=6.5 但 decile spread≈0（经济不平坦）；与 am_pm_vol ρ=-0.93 镜像冗余（规则 |ρ|≥0.9 只留一）——两只均落观察。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；复跑命令见 R09 报告 §3。

---
*档案规范见 `_template.md`。*
