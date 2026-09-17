---
xname: intraday_autocorr_micro
formula: |
  signal = Σ(r_t·r_{t-1}) / Σ(r_t²)   # 分钟收益 lag1 自相关（未归一）
tags: [minute, intraday_path, micro_trend, reversal, mine_m1]
params: {}
status: 候选（近端跟踪）
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d v2；方向修正重跑在 flip2 队列，本快照产出自同 panel）
---

# intraday_autocorr_micro 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_autocorr_micro`（= `factor/intraday/autocorr_micro.yaml`） |
| 类别 | custom |
| 方向 | `-1`（原 spec 误设 1，2026-09-17 修正） |
| 状态 | 候选（minute 参考库成员；近端反向，重点跟踪） |
| 创建/更新 | 2026-09-17 |

## 2. 逻辑

**动机**：分钟收益 lag1 自相关度量**日内微观趋势强度**（连续分钟同向走）。
假设：日内微观趋势强 = 单边消耗流动性/情绪推动，次日更可能均值回归
（direction=-1）。与 ret_concentration 相关 0.364（同族但不同构造）。

**核心逻辑**：`signal = Σ(r_t·r_{t-1})/Σ(r_t²)`（日内，im_delay(…,1)）。

## 3. 参数与实现

```yaml
name: intraday_autocorr_micro
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
  _rl = im_delay(_r, 1)
  _num = day_sum(_r * _rl)
  _den = day_sum(_r * _r)
  signal = _num / _den
```

## 4. 验证结果

> 快照自 `runs/platform/intraday_autocorr_micro/summary.json`（2026-09-17）。

样本：723 逐日期，均 ~4840 只。

| 指标 | 值 |
|------|----|
| RankIC mean（原始） | -0.0378 |
| t 值 | -7.97 |
| IR | -0.30 |
| 近 26 周 | ≈+0.005 / t=+1.0（**近期反向**） |
| PearsonIC mean | 见 summary（重尾以 rank 为准） |

### 冗余/增量检查（D10）

vs minute 库 6 员：max ρ=0.364（vs ret_concentration），resIC t=**-3.11**，
retention=0.59 → **可加入**。

### 判定

全期显著（|t|=7.97）但**近 26 周转反**→ **候选（重点跟踪）**：与 event_density 同
为近期走弱型；若 2025 年复核转负则移出参考库。
动作：flip2 重跑刷新方向修正快照；每月复核近端。

## 5. 迭代历史

| 日期 | 变体 | 改动 | IC mean | t | 结论 |
|------|------|------|---------|---|------|
| 2026-09-17 | daily v2 首跑 | dir=1 | -0.0378 | -7.97 | 显著，方向记反修正 |
| 2026-09-17 | dir=-1 | 方向修正入库 | -0.0378 | -7.97 | 候选（近端跟踪） |

## 6. 风险与备注

- 近 26 周 t=+1.0（与全期反号）是本因子最大风险信号。
- 产品级公式（R09-PERF-I1 慢形态），复跑成本高（~15 min）。

---
*档案规范见 `_template.md`。*
