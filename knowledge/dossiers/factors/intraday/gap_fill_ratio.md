---
xname: intraday_gap_fill_ratio
formula: |
  _auc = day_max(if_else(minute_index == 0, close, None)) _den = _auc / prev_close - 1 signal = if_else(abs(_den) > 0.001, (day_last(close) / _auc - 1) / _den, None)
tags: [minute, negative_result]
params: {}
status: 观察
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_gap_fill_ratio 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_gap_fill_ratio`（= `factor/intraday/gap_fill_ratio.yaml`） |
| 方向 | `-1` |
| 状态 | 观察 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_gap_fill_ratio/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.01056 |
| t 值 | 3.40 |
| IR | 0.126 |
| 近 26 周 t | -0.37 |
| 期数 | 723（daily） |

**判定**：全期 t=3.40（原始正号）但对 14 员库 resIC t=+1.50<2（残差不显著）→ 观察：回补比例的净信息不足。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
