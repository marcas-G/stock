---
xname: intraday_auction_conc_inter
formula: |
  _r = close / im_delay(close, 1) - 1 _prem = day_last(close) / day_max(if_else(minute_index == 237, close, None)) - 1 _conc = day_max(abs(_r)) / day_sum(abs(_r)) signal = if_else(_prem > 0, _prem * _conc, None)
tags: [minute, negative_result]
params: {}
status: 冗余
created_ts: 2026-09-16
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_auction_conc_inter 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_auction_conc_inter`（= `factor/intraday/auction_conc_inter.yaml`） |
| 方向 | `-1` |
| 状态 | 冗余 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_auction_conc_inter/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.02765 |
| t 值 | -7.45 |
| IR | -0.277 |
| 近 26 周 t | -4.91 |
| 期数 | 723（daily） |

**判定**：交互项（溢价×集中度）corr_max=0.911≥0.9、r2_lib=0.96——本质是 close_auction_premium 与 ret_concentration 的线性组合，被库完全解释 → 冗余（验证了库成员加性覆盖）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；旧周频口径快照（2026-09-16）已被本 daily 重跑取代。

---
*档案规范见 `_template.md`。*
