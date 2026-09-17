---
xname: intraday_closing_push
formula: |
  signal = day_sum(if_else(minute_index >= 210, close / im_delay(close, 1) - 1, 0))
tags: [minute, mine_m3]
params: {}
status: 候选
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_closing_push 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_closing_push`（= `factor/intraday/closing_push.yaml`） |
| 方向 | `-1` |
| 状态 | 候选 |

## 2. 逻辑

尾盘最后 28 分钟（idx≥210）累计拉升=尾盘资金推动，次日反转。

## 4. 验证结果

> 快照自 `runs/platform/intraday_closing_push/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.05215 |
| t 值 | -14.74 |
| IR | -0.548 |
| 近 26 周 t | -6.72 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：尾盘最后 28 分钟（idx≥210）累计拉升=尾盘资金推动，次日反转。全期 |t|=14.74（第二强），近 26 周 t=-6.7 强且不衰减。D10：对 8 员库 max ρ=0.20、resIC t=-2.97、retention=0.96 → 可加入。

## 6. 风险与备注

- 入库候选；近端与逐年列入定期复核。

---
*档案规范见 `_template.md`。*
