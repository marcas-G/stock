---
xname: intraday_close_vs_high
formula: |
  signal = day_last(close) / day_max(high) - 1
tags: [minute, mine_m3]
params: {}
status: 候选（近端跟踪）
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d，evaluation.version=2）
---

# intraday_close_vs_high 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_close_vs_high`（= `factor/intraday/close_vs_high.yaml`） |
| 方向 | `1` |
| 状态 | 候选（近端跟踪） |

## 2. 逻辑

收盘越贴近日内最高（值越接近 0）→ 次日越强（动量延续，dir=+1）。

## 4. 验证结果

> 快照自 `runs/platform/intraday_close_vs_high/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.03332 |
| t 值 | 5.56 |
| IR | 0.207 |
| 近 26 周 t | -0.17 |
| 期数 | 723（daily/forward_return_1d v2） |

**判定**：收盘越贴近日内最高（值越接近 0）→ 次日越强（动量延续，dir=+1）。近 26 周 t≈0 走弱，入库跟踪。D10：resIC t=+2.98、retention=0.61 → 可加入。

## 6. 风险与备注

- 入库候选；近端与逐年列入定期复核。

---
*档案规范见 `_template.md`。*
