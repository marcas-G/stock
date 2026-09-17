---
xname: intraday_vol_skew
formula: |
  _c = volume - day_mean(volume) _s3 = day_sum(_c * _c * _c) _s2 = day_sum(_c * _c) signal = if_else(_s2 > 0, _s3 / sqrt(_s2 * _s2 * _s2), None)
tags: [minute, mine_m8]
params: {}
status: 候选
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_vol_skew 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_vol_skew`（= `factor/intraday/vol_skew.yaml`） |
| 方向 | `-1` |
| 状态 | 候选 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_vol_skew/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.01569 |
| t 值 | -6.40 |
| IR | -0.238 |
| 近 26 周 t | -3.37 |
| 期数 | 723（daily） |

**判定**：分钟成交量分布偏度（偶发巨量分钟的不对称）→次日反转：|t|=6.40 且近 26 周 t=-3.37 强；对库 max ρ=0.238、resIC t=-3.27、retention=1.03 → 可加入（第 17 员）。

## 6. 风险与备注

- 入库候选；近端与逐年定期复核。

---
*档案规范见 `_template.md`。*
