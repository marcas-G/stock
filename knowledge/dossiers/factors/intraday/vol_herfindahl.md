---
xname: intraday_vol_herfindahl
formula: |
  signal = day_sum(volume * volume) / (day_sum(volume) * day_sum(volume))
tags: [minute, mine_m1, negative_result]
params: {}
status: 无效
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_vol_herfindahl 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_vol_herfindahl`（= `factor/intraday/vol_herfindahl.yaml`） |
| 方向 | `1` |
| 状态 | 无效 |

## 2. 逻辑

见 spec formula（分钟 bars_1m 折日因子，M1/M2 批探索）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_vol_herfindahl/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.00120 |
| t 值 | 0.27 |
| IR | 0.010 |
| 近 26 周 t | -2.70 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：成交量 HHI 集中度无信号（t=0.3，近端 -2.7 不稳）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；复跑命令见 R09 报告 §3。

---
*档案规范见 `_template.md`。*
