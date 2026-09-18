---
xname: intraday_dvr
formula: |
  _r = close / im_delay(close, 1) - 1 _d = if_else(_r < 0, _r * _r, 0) _u = if_else(_r > 0, _r * _r, 0) signal = if_else(day_sum(_u) > 0, day_sum(_d) / day_sum(_u), None)
tags: [minute, negative_result]
params: {}
status: 冗余
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_dvr 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_dvr`（= `factor/intraday/dvr.yaml`） |
| 方向 | `1` |
| 状态 | 冗余 |

## 2. 假设与文献出处

- 下行/上行半方差比（DVR；Kuanke 万赞分钟指标族；Bala-Subramanian 2015 downside volatility premium）

## 4. 验证结果

> 快照自 `runs/platform/intraday_dvr/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.03487 |
| t 值 | 7.78 |
| IR | 0.289 |
| 近 26 周 t | 1.16 |
| 期数 | 723（daily） |

**D10**：对库冗余（见判定）

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
