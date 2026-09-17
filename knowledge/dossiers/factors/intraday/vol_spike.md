---
xname: intraday_vol_spike
formula: |
  signal = #{ volume > 3·mean_day 的分钟数 }
tags: [minute, volume_event, mine_m1]
params: {}
status: 候选
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d v2）
---

# intraday_vol_spike 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_vol_spike`（= `factor/intraday/vol_spike.yaml`） |
| 类别 | custom |
| 方向 | `1`（放量事件多→次日延续，与原始 IC 同号） |
| 状态 | 候选（minute 参考库成员） |
| 创建/更新 | 2026-09-17 |

## 2. 逻辑

**动机**：日内出现多次成交量>3×日均的"放量分钟"=注意力/知情交易集中。
假设：放量事件多→动量延续（direction=+1，与反转族相反，原始 IC=+0.0175 印证）。

**核心逻辑**：`signal = Σ 1{volume > 3·mean_day(volume)}`。

## 3. 参数与实现

```yaml
name: intraday_vol_spike
category: custom
direction: 1
interface: bars_1m
adjustment: raw
universe:
  ref: bars1m_2023_2025
date:
  start: "2023-01-01"
  end: "2025-12-31"
formula: |
  _m = day_mean(volume)
  signal = day_sum(if_else(volume > 3 * _m, 1, 0))
```

## 4. 验证结果

> 快照自 `runs/platform/intraday_vol_spike/summary.json`（2026-09-17）。

| 指标 | 值 |
|------|----|
| RankIC mean | +0.0175 |
| t 值 | +4.57 |
| IR | +0.17 |
| 近 26 周 | ≈-0.004 / t=-1.8（近端走弱） |

### 冗余/增量检查（D10）

vs minute 库 6 员：max ρ=0.071（独立），resIC t=**+2.96**，retention=0.77 → **可加入**。

### 判定

显著（|t|=4.57）→ **候选**，已入 minute 参考库。
动作：近端 t 走弱，列入跟踪；研究阈值 3σ→2σ/4σ 敏感性。

## 5. 迭代历史

| 日期 | 变体 | IC mean | t | 结论 |
|------|------|---------|---|------|
| 2026-09-17 | daily v2 首跑 | +0.0175 | +4.57 | 显著入库 |

## 6. 风险与备注

- 与 vol_herfindahl（成交集中度）逻辑相邻但构造不同；后者无信号。
- 近端 t=-1.8，若转负即降级。

---
*档案规范见 `_template.md`。*
