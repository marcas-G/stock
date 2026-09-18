---
xname: intraday_kurtosis
formula: |
  _r = close / im_delay(close, 1) - 1 _r2 = _r * _r _r4 = _r2 * _r2 _s2 = day_sum(_r2) signal = if_else(_s2 > 0, day_sum(_r4) / (_s2 * _s2), None)
tags: [minute, negative_result]
params: {}
status: 冗余
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_kurtosis 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_kurtosis`（= `factor/intraday/kurtosis.yaml`） |
| 方向 | `-1` |
| 状态 | 冗余 |

## 2. 假设与文献出处

- 已实现峰度（Jia-Yang excess kurtosis premium，dir=-1 证实 t=-11.99）。但 ρ=0.944 撞库、resIC t=-1.07、retention=0.08 → 冗余

## 4. 验证结果

> 快照自 `runs/platform/intraday_kurtosis/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.04724 |
| t 值 | -11.99 |
| IR | -0.446 |
| 近 26 周 t | -1.13 |
| 期数 | 723（daily） |

**D10**：对库冗余（见判定）

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
