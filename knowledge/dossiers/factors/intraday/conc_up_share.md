---
xname: intraday_conc_up_share
formula: |
  _r = close / im_delay(close, 1) - 1 _up = day_sum(if_else(_r > 0, abs(_r), 0)) _tot = day_sum(abs(_r)) signal = if_else(_tot > 0, _up / _tot, None)
tags: [minute, negative_result]
params: {}
status: 冗余
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_conc_up_share 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_conc_up_share`（= `factor/intraday/conc_up_share.yaml`） |
| 方向 | `-1` |
| 状态 | 冗余 |

## 2. 逻辑

上行波动占比：与 vol_asym ρ=0.768 同族、retention=0.15<0.2 → 冗余。

## 4. 验证结果

> 快照自 `runs/platform/intraday_conc_up_share/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.02084 |
| t 值 | -3.70 |
| IR | -0.138 |
| 近 26 周 t | -1.78 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：上行波动占比：与 vol_asym ρ=0.768 同族、retention=0.15<0.2 → 冗余。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
