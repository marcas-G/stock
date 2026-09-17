---
xname: intraday_closing_auction_intensity
formula: |
  _ca = day_sum(if_else(minute_index >= 238, volume, 0)) signal = _ca / day_sum(volume)
tags: [minute, mine_m3]
params: {}
status: 候选
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_closing_auction_intensity 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_closing_auction_intensity`（= `factor/intraday/closing_auction_intensity.yaml`） |
| 方向 | `-1` |
| 状态 | 候选 |

## 2. 逻辑

收盘集合竞价（idx≥238）成交量占全天比例高=竞价承接失衡剧烈→次日反转（dir=-1）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_closing_auction_intensity/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | -0.01227 |
| t 值 | -5.08 |
| IR | -0.189 |
| 近 26 周 t | -3.97 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：收盘集合竞价（idx≥238）成交量占全天比例高=竞价承接失衡剧烈→次日反转（dir=-1）。近端 t=-3.97 强。D10：resIC t=-2.03、retention=2.32 → 可加入。与 close_auction_premium（价）互补（量维度），ρ=0.02。

## 6. 风险与备注

- 入库候选；近端与逐年列入定期复核。

---
*档案规范见 `_template.md`。*
