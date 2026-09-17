---
xname: intraday_ret_vv_ratio
formula: |
  _r = close / im_delay(close, 1) - 1 _m = day_mean(_r) _mean_abs = day_mean(abs(_r)) _m2 = day_mean(_r * _r) signal = _m2 / (_mean_abs * _mean_abs)
tags: [minute, negative_result, legacy]
params: {}
status: 冗余
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_ret_vv_ratio 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_ret_vv_ratio`（= `factor/intraday/ret_vv_ratio.yaml`，前会话遗留补档） |
| 方向 | `1`（原始 IC 为负 -0.00858，方向与数据相反，未修正——结论不依赖之） |
| 状态 | 冗余 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_ret_vv_ratio/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.00858 |
| t 值 | -2.21 |
| IR | -0.082 |
| 近 26 周 t | -2.56 |
| 期数 | 723（daily） |

**判定**：|t|=2.21 勉强达标，但对 17 员 minute 库 max ρ=0.579、r²_lib=0.652、resIC t=+0.38、retention=0.19 → **冗余**（收益-量交互信息已被 ret_concentration/amihud_minute 覆盖）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
