---
xname: intraday_afternoon_ret
formula: |
  signal = day_sum(if_else(minute_index > 120, close / im_delay(close, 1) - 1, 0))
tags: [minute, mine_m2, negative_result]
params: {}
status: 冗余
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_afternoon_ret 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_afternoon_ret`（= `factor/intraday/afternoon_ret.yaml`） |
| 方向 | `-1` |
| 状态 | 冗余 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_afternoon_ret/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.03507 |
| t 值 | -6.94 |
| IR | -0.258 |
| 近 26 周 t | -4.10 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：全期 |t|=6.94 且近 26 周 t=-4.10 强，但对 8 员库 resIC t=-0.29、retention=-1.80（净信息被库吸收甚至反号）——**强共线冗余**：下午收益反转已被竞价溢价/集中度等成员覆盖。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
