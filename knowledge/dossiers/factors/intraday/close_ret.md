---
xname: intraday_close_ret
formula: |
  _last = day_last(close) _cont_close = day_max(if_else(minute_index == 237, close, None)) signal = _last / _cont_close - 1
tags: [minute, mine_m1, negative_result]
params: {}
status: 已废弃
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_close_ret 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_close_ret`（= `factor/intraday/close_ret.yaml`） |
| 方向 | `-1` |
| 状态 | 已废弃 |

## 2. 逻辑

见 spec formula（分钟 bars_1m 折日因子，M1/M2 批探索）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_close_ret/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.03578 |
| t 值 | -22.67 |
| IR | -0.843 |
| 近 26 周 t | -6.68 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：与 intraday_close_auction_premium 同式 ρ=1.000 完全重复，待删除（D7 垃圾不保存）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；复跑命令见 R09 报告 §3。

---
*档案规范见 `_template.md`。*
