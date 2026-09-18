---
xname: intraday_autocorr_lag2
formula: |
  _r = close / im_delay(close, 1) - 1 _r2 = _r * _r _s2 = day_sum(_r2) signal = if_else(_s2 > 0, day_sum(_r * im_delay(_r, 2)) / _s2, None)
tags: [minute, negative_result, mine_m10]
params: {}
status: 无效
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_autocorr_lag2 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_autocorr_lag2`（= `factor/intraday/autocorr_lag2.yaml`） |
| 方向 | `-1` |
| 状态 | 无效 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_autocorr_lag2/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.00172 |
| t 值 | -0.94 |
| IR | -0.035 |
| 近 26 周 t | 0.20 |
| 期数 | 723（daily） |

**判定**：lag-2 收益自相关（Lo-MacKinlay 均值回归视野假设）：|t|=0.94 未达门槛。lag-1 信息已被 autocorr_micro 抓住，更高阶持久性在 A 股分钟尺度无独立预测力。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
