---
xname: intraday_conc_last_hour
formula: |
  _r = close / im_delay(close, 1) - 1 _late = day_sum(if_else(minute_index >= 210, abs(_r), 0)) _tot = day_sum(abs(_r)) signal = if_else(_tot > 0, _late / _tot, None)
tags: [minute, negative_result]
params: {}
status: 观察
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_conc_last_hour 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_conc_last_hour`（= `factor/intraday/conc_last_hour.yaml`） |
| 方向 | `1` |
| 状态 | 观察 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_conc_last_hour/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.02649 |
| t 值 | 7.34 |
| IR | 0.273 |
| 近 26 周 t | -1.34 |
| 期数 | 723（daily） |

**判定**：全期 t=7.34（原始正号，dir 已修 +1）但对 12 员库 resIC t=+2.78 显著、retention=0.32<0.5 → 落观察（净信息不足一半）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
