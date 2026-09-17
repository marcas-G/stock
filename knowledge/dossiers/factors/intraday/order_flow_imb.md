---
xname: intraday_order_flow_imb
formula: |
  _r = close / im_delay(close, 1) - 1 signal = day_sum(sign(_r) * volume) / day_sum(volume)
tags: [minute, mine_m1, negative_result]
params: {}
status: 无效
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_order_flow_imb 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_order_flow_imb`（= `factor/intraday/order_flow_imb.yaml`） |
| 方向 | `1` |
| 状态 | 无效 |

## 2. 逻辑

见 spec formula（分钟 bars_1m 折日因子，M1/M2 批探索）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_order_flow_imb/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.00393 |
| t 值 | -0.86 |
| IR | -0.032 |
| 近 26 周 t | -1.09 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：成交量方向失衡（主动买卖差占比）在 1 日尺度无预测力（|t|=0.9）；假设证伪。

## 6. 风险与备注

- 负结果归档（负结果入库纪律）；复跑命令见 R09 报告 §3。

---
*档案规范见 `_template.md`。*
