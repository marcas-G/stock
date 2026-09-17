---
xname: intraday_open_minute_mom
formula: |
  _open_ret = day_max(if_else(minute_index == 1, close, None)) / day_max(if_else(minute_index == 0, close, None)) - 1 signal = _open_ret
tags: [minute, mine_m1, negative_result]
params: {}
status: 无效
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_open_minute_mom 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_open_minute_mom`（= `factor/intraday/open_minute_mom.yaml`） |
| 方向 | `1` |
| 状态 | 无效 |

## 2. 逻辑

见 spec formula（分钟 bars_1m 折日因子，M1/M2 批探索）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_open_minute_mom/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.00056 |
| t 值 | 0.15 |
| IR | 0.006 |
| 近 26 周 t | 0.75 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：开盘第 1 分钟动量无信号（t=0.1）。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；复跑命令见 R09 报告 §3。

---
*档案规范见 `_template.md`。*
