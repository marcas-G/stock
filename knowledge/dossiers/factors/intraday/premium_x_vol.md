---
xname: intraday_premium_x_vol
formula: |
  _cont = day_max(if_else(minute_index == 237, close, None)) _prem = if_else(_cont > 0, day_last(close) / _cont - 1, None) _int = day_sum(if_else(minute_index >= 238, volume, 0)) signal = _prem * _int
tags: [minute, negative_result]
params: {}
status: 冗余
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_premium_x_vol 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_premium_x_vol`（= `factor/intraday/premium_x_vol.yaml`） |
| 方向 | `-1` |
| 状态 | 冗余 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_premium_x_vol/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.03455 |
| t 值 | -22.08 |
| IR | -0.821 |
| 近 26 周 t | -6.30 |
| 期数 | 723（daily） |

**判定**：溢价×竞价量：对 close_auction_premium ρ=0.969≥0.9、r2_lib=0.94 → 冗余（量缩放假溢价信号，无增量）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）。

---
*档案规范见 `_template.md`。*
