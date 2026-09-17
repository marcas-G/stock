---
xname: intraday_amihud_minute
formula: |
  _r = close / im_delay(close, 1) - 1 _imp = if_else(amount > 0, abs(_r) / amount, None) signal = day_mean(_imp)
tags: [minute, mine_m5]
params: {}
status: 候选
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_amihud_minute 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_amihud_minute`（= `factor/intraday/amihud_minute.yaml`） |
| 方向 | `1` |
| 状态 | 候选 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_amihud_minute/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.05472 |
| t 值 | 9.52 |
| IR | 0.354 |
| 近 26 周 t | -0.27 |
| 期数 | 723（daily） |

**判定**：微观 Amihud 冲击（分钟 |收益|/成交额 均值）→次日延续（dir=+1 数据驱动：高冲击=知情交易主导）。|t|=9.52，resIC t=+5.64、retention=0.67、max ρ=0.574 → 可加入。冻结 bar（amount=0）逐行守卫防 NaN 传播（polars mean 遇 NaN 传染，首轮死信号教训）。

## 6. 风险与备注

- 入库候选；近端与逐年定期复核。

---
*档案规范见 `_template.md`。*
