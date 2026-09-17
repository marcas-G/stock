---
xname: intraday_rv_term_structure
formula: |
  _r = close / im_delay(close, 1) - 1 _r2 = _r * _r _h1 = day_sum(if_else(minute_index <= 120, _r2, 0)) _h2 = day_sum(if_else(minute_index > 120, _r2, 0)) signal = if_else(_h2 > 0, _h1 / _h2, None)
tags: [minute, negative_result]
params: {}
status: 冗余
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_rv_term_structure 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_rv_term_structure`（= `factor/intraday/rv_term_structure.yaml`） |
| 方向 | `-1` |
| 状态 | 冗余 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_rv_term_structure/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.02553 |
| t 值 | -6.12 |
| IR | -0.228 |
| 近 26 周 t | 1.22 |
| 期数 | 723（daily） |

**判定**：上午 RV/下午 RV：全期 t=-6.12 但近端反向（rec_t=+1.22）且 resIC t=-0.89、retention=0.11 → 冗余（期限结构信息被 am_pm/尾盘族吸收）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
