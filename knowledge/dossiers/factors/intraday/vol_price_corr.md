---
xname: intraday_vol_price_corr
formula: |
  _r = close / im_delay(close, 1) - 1 _m_r = day_mean(_r) _m_v = day_mean(volume) _num = day_sum((_r - _m_r) * (volume - _m_v)) _den_r = day_sum((_r - _m_r) * (_r - _m_r)) _den_v = day_sum((volume - _m_v) * (volume - _m_v)) signal = _num / sqrt(_den_r * _den_v)
tags: [minute, mine_m1, negative_result]
params: {}
status: 观察
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_vol_price_corr 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_vol_price_corr`（= `factor/intraday/vol_price_corr.yaml`） |
| 方向 | `-1` |
| 状态 | 观察 |

## 2. 逻辑

见 spec formula（分钟 bars_1m 折日因子，M1/M2 批探索）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_vol_price_corr/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.02071 |
| t 值 | -4.89 |
| IR | -0.182 |
| 近 26 周 t | -0.43 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：|t|=4.9 但对库 resIC t=-0.78（被解释，retention 0.455）；与 vol_asym ρ=0.765 同族落选。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；复跑命令见 R09 报告 §3。

---
*档案规范见 `_template.md`。*
