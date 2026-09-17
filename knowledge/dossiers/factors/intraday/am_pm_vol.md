---
xname: intraday_am_pm_vol
formula: |
  _am = day_sum(if_else(minute_index <= 120, volume, 0)) signal = _am / day_sum(volume)
tags: [minute, mine_m1, negative_result]
params: {}
status: 观察
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_am_pm_vol 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_am_pm_vol`（= `factor/intraday/am_pm_vol.yaml`） |
| 方向 | `-1` |
| 状态 | 观察 |

## 2. 逻辑

见 spec formula（分钟 bars_1m 折日因子，M1/M2 批探索）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_am_pm_vol/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.01899 |
| t 值 | -6.18 |
| IR | -0.230 |
| 近 26 周 t | -0.03 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：|t|=6.2 但近 26 周 t=0.0 近期消失；与 volume_timing ρ=-0.93 镜像冗余；方向已修 -1。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；复跑命令见 R09 报告 §3。

---
*档案规范见 `_template.md`。*
