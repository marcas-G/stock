---
xname: intraday_realized_skew
formula: |
  _r = close / im_delay(close, 1) - 1 _r2 = _r * _r _r3 = _r * _r * _r _s2 = day_sum(_r2) signal = if_else(_s2 > 0, day_sum(_r3) / (_s2 * sqrt(_s2)), None)
tags: [minute, negative_result]
params: {}
status: 冗余
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_realized_skew 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_realized_skew`（= `factor/intraday/realized_skew.yaml`） |
| 方向 | `-1` |
| 状态 | 冗余 |

## 2. 假设与文献出处

- 已实现偏度（Fang-Goyal-Swinkels 2022 JFQA realized skewness premium

## 4. 验证结果

> 快照自 `runs/platform/intraday_realized_skew/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.03345 |
| t 值 | -8.89 |
| IR | -0.331 |
| 近 26 周 t | -0.74 |
| 期数 | 723（daily） |

**D10**：对库冗余（见判定）

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
