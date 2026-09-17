---
xname: intraday_cont_last_ret
formula: |
  _c237 = day_max(if_else(minute_index == 237, close, None)) _c236 = day_max(if_else(minute_index == 236, close, None)) signal = if_else(_c236 > 0, _c237 / _c236 - 1, None)
tags: [minute, mine_m3]
params: {}
status: 候选
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_cont_last_ret 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_cont_last_ret`（= `factor/intraday/cont_last_ret.yaml`） |
| 方向 | `-1` |
| 状态 | 候选 |

## 2. 逻辑

连续竞价最后一分钟（idx236→237）的冲击收益→次日均值回归（dir=-1）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_cont_last_ret/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.00648 |
| t 值 | -10.09 |
| IR | -0.375 |
| 近 26 周 t | -1.97 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：连续竞价最后一分钟（idx236→237）的冲击收益→次日均值回归（dir=-1）。|t|=10.09，retention=3.29（残差信息比原始还强，与库几乎正交）。

## 6. 风险与备注

- 入库候选；近端与逐年列入定期复核。

---
*档案规范见 `_template.md`。*
