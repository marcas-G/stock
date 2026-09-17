---
xname: intraday_abs_auction_premium
formula: |
  _cont = day_max(if_else(minute_index == 237, close, None)) signal = if_else(_cont > 0, abs(day_last(close) / _cont - 1), None)
tags: [minute, mine_m4]
params: {}
status: 候选（方向数据驱动）
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_abs_auction_premium 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_abs_auction_premium`（= `factor/intraday/abs_auction_premium.yaml`） |
| 方向 | `1` |
| 状态 | 候选（方向数据驱动） |

## 4. 验证结果

> 快照自 `runs/platform/intraday_abs_auction_premium/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.00455 |
| t 值 | 3.21 |
| IR | 0.119 |
| 近 26 周 t | -0.55 |
| 期数 | 723（daily） |

**判定**：原假设"双向竞价扰动→次日反转"被数据否定：原始 IC=+0.0046（正）、t=+3.21，全期显著为正——大扰动次日**更强**（注意力/价格发现解释）。方向修正 dir=+1 入库。D10：resIC t=+4.88、retention=1.55、max ρ=0.110 → 可加入。

## 6. 风险与备注

- 入库候选；近端与逐年定期复核。

---
*档案规范见 `_template.md`。*
