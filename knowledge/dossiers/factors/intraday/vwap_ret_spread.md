---
xname: intraday_vwap_ret_spread
formula: |
  _r = close / im_delay(close, 1) - 1 _vwm = day_sum(_r * volume) / day_sum(volume) _ewm = day_mean(_r) signal = if_else(day_sum(volume) > 0, _vwm - _ewm, None)
tags: [minute, negative_result, mine_m6]
params: {}
status: 冗余
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_vwap_ret_spread 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_vwap_ret_spread`（= `factor/intraday/vwap_ret_spread.yaml`） |
| 方向 | `1` |
| 状态 | 冗余 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_vwap_ret_spread/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.02361 |
| t 值 | -5.79 |
| IR | -0.215 |
| 近 26 周 t | -0.62 |
| 期数 | 723（daily） |

**判定**：量权-等权分钟收益差：对库 max ρ=0.727（≥0.7 与 vol_asym/autocorr 同族）、resIC t=+0.71 → 冗余。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
