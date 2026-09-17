---
xname: intraday_cross_count
formula: |
  _r = close / im_delay(close, 1) - 1 _s = sign(_r) _sl = im_delay(_s, 1) signal = day_sum(if_else(_s != _sl, 1, 0))
tags: [minute, mine_m5]
params: {}
status: 候选
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_cross_count 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_cross_count`（= `factor/intraday/cross_count.yaml`） |
| 方向 | `1` |
| 状态 | 候选 |

## 4. 验证结果

> 快照自 `runs/platform/intraday_cross_count/summary.json`。

| 指标 | 值 |
|------|----|
| RankIC mean | 0.02486 |
| t 值 | 7.48 |
| IR | 0.278 |
| 近 26 周 t | -0.08 |
| 期数 | 723（daily） |

**判定**：日内符号切换次数多（来回交易活跃）→次日延续（dir=+1 数据驱动，原反转假设反）。|t|=7.48，resIC t=+4.53、retention=0.69、max ρ=0.273 → 可加入。近 26 周 t≈0 列入跟踪。

## 6. 风险与备注

- 入库候选；近端与逐年定期复核。

---
*档案规范见 `_template.md`。*
