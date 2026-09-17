---
xname: intraday_tail_amt_share
formula: |
  signal = day_sum(if_else(minute_index >= 210, amount, 0)) / day_sum(amount)
tags: [minute, negative_result]
params: {}
status: 观察
created_ts: 2026-09-16
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_tail_amt_share 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_tail_amt_share`（= `factor/intraday/intraday_tail_amt_share.yaml`） |
| 方向 | `1` |
| 状态 | 观察 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_tail_amt_share/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.01129 |
| t 值 | 4.11 |
| IR | 0.153 |
| 近 26 周 t | -1.07 |
| 期数 | 723（daily） |

**判定**：全期 t=4.11 但 D10 对 12 员库 corr_max=0.41、retention=0.36<0.5 → 落观察（尾盘金额占比信息被 closing_push/cont_last_ret 等吸收）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；旧周频口径快照（2026-09-16）已被本 daily 重跑取代。

---
*档案规范见 `_template.md`。*
