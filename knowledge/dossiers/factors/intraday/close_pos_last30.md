---
xname: intraday_close_pos_last30
formula: |
  _mx = day_max(if_else(minute_index >= 210, close, None)) _mn = day_min(if_else(minute_index >= 210, close, None)) signal = if_else(_mx - _mn > 0, (day_last(close) - _mn) / (_mx - _mn) - 0.5, None)
tags: [minute, mine_m4]
params: {}
status: 候选
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_close_pos_last30 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_close_pos_last30`（= `factor/intraday/close_pos_last30.yaml`） |
| 方向 | `-1` |
| 状态 | 候选 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_close_pos_last30/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.05645 |
| t 值 | -23.83 |
| IR | -0.886 |
| 近 26 周 t | -10.08 |
| 期数 | 723（daily） |

**判定**：全期 |t|=23.83（并列最强），近 26 周 t=-10.08 强不衰减。D10：对 12 员库 max ρ=0.632、resIC t=-7.21、retention=0.72 → 可加入。与 close_auction_premium（价溢价）相关 0.44——本因子是尾盘区间位置（近端微观结构），互补。

## 6. 风险与备注

- 入库候选；近端与逐年定期复核。

---
*档案规范见 `_template.md`。*
