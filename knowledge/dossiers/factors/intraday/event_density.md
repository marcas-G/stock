---
xname: intraday_event_density
formula: |
  signal = #{ |r| > 2σ_within_day 的分钟数 }   # r=分钟收益，σ 日内标准差
tags: [minute, intraday_path, jump_events, mine_m1]
params: {}
status: 候选（近端跟踪）
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d v2）
---

# intraday_event_density 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_event_density`（= `factor/intraday/event_density.yaml`） |
| 类别 | custom |
| 方向 | `-1` |
| 状态 | 候选（minute 参考库成员；近端 t 走弱，跟踪） |
| 创建/更新 | 2026-09-17 |

## 2. 逻辑

**动机**：日内出现多次"超过日内 2σ"的剧烈分钟（事件冲击密集）→ 冲击方次日平仓/
对手方回归 → 反转。与 ret_concentration 的区别：本因子数**次数**（多波冲击），
ret_concentration 度量**单次占比**（最大跳幅集中度）。

**核心逻辑**：`signal = Σ 1{|r| > 2σ_day}`（σ_day 为日内分钟收益标准差，分母 239
因 minute 0 无收益）。

## 3. 参数与实现

阈值 2σ 固定。universe bars1m_2023_2025；2023-01-01~2025-12-31；target forward_return_1d；
adjustment raw；evaluation daily（v2）。

```yaml
name: intraday_event_density
category: custom
direction: -1
interface: bars_1m
adjustment: raw
universe:
  ref: bars1m_2023_2025
date:
  start: "2023-01-01"
  end: "2025-12-31"
formula: |
  _r = close / im_delay(close, 1) - 1
  _m = day_mean(_r)
  _var = day_sum((_r - _m) * (_r - _m)) / 239
  signal = day_sum(if_else(abs(_r) > 2 * sqrt(_var), 1, 0))
```

## 4. 验证结果

> 快照自 `runs/platform/intraday_event_density/summary.json`（2026-09-17）。

样本：723 逐日期，均 4846 只，有效率 99.84%。

| 指标 | 值 |
|------|----|
| RankIC mean（原始） | -0.0129 |
| t 值 | -4.32 |
| IR | -0.160 |
| 近 26 周 | +0.0122 / t=+1.19（**近期反向**） |
| PearsonIC mean | -0.0080 |

逐年 t：2023 -3.4、2024 -2.6、2025 -1.5（逐年减弱，2025 年已不显著）。

分层：spread=+0.000517，g0=0.001556，g9=0.001039，不单调；月换手 0.829。

### 冗余/增量检查（D10）

vs minute 库：max ρ=0.023，resIC t=-2.20，retention=0.90 → **可加入**。

### 判定

全期显著（|t|=4.32）但**逐年衰减、近 26 周反向**→ **候选（近端跟踪）**：若后续
复核 2025 全年转负则降级观察并移出参考库。

## 5. 迭代历史

| 日期 | 变体 | 改动 | IC mean | t | 结论 |
|------|------|------|---------|---|------|
| 2026-09-17 | daily v2 | 首跑 | -0.0129 | -4.32 | 全期显著，近端走弱，入库跟踪 |

## 6. 风险与备注

- 审核（subagent）通过，无问题清单。
- 逐年衰减模式（-3.4→-2.6→-1.5）是本因子最大风险；下次复核若转负即移除。

---
*档案规范见 `_template.md`。*
