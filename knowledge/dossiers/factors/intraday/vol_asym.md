---
xname: intraday_vol_asym
formula: |
  signal = Σ(r²|r>0) / Σ(r²|r<0)   # 上行波动/下行波动（_dn<=0 守卫）
tags: [minute, intraday_path, asymmetry, mine_m1]
params: {}
status: 候选
created_ts: 2026-09-17
updated_ts: 2026-09-17
snapshot: CH 运行快照（2026-09-17，daily/forward_return_1d v2；本快照产自守卫修正前，flip 重跑进行中）
---

# intraday_vol_asym 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_vol_asym`（= `factor/intraday/vol_asym.yaml`） |
| 类别 | custom |
| 方向 | `-1`（原 spec 误设 1，与原始 IC 符号相反，2026-09-17 修正） |
| 状态 | 候选（minute 参考库成员） |
| 创建/更新 | 2026-09-17 |

## 2. 逻辑

**动机**：日内上涨分钟波动与下跌分钟波动之比刻画"日内赚钱效应"。
假设：日内上行波动占比过高（追涨拥挤）→ 次日反转（direction=-1）。

**核心逻辑**：`signal = Σ_up r² / Σ_dn r²`（分钟收益平方按符号分拆求和）。

## 3. 参数与实现

无参数。universe bars1m_2023_2025；2023-01-01~2025-12-31；target forward_return_1d；
adjustment raw；evaluation daily（v2）。

```yaml
name: intraday_vol_asym
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
  _up = day_sum(if_else(_r > 0, _r * _r, 0))
  _dn = day_sum(if_else(_r < 0, _r * _r, 0))
  signal = if_else(_dn > 0, _up / _dn, None)
```

## 4. 验证结果

> 快照自 `runs/platform/intraday_vol_asym/summary.json`（2026-09-17，守卫前 run）。

样本：723 逐日期，均 4833 只，有效率 99.56%。

| 指标 | 值 |
|------|----|
| RankIC mean（原始） | -0.0352 |
| t 值 | -7.86 |
| IR | -0.292 |
| 近 26 周 | -0.0199 / t=-1.19 |
| PearsonIC mean | +0.0226（与 rank 异号：重尾+比值分布失真，以 rank 为准） |

逐年 t：2023 **-4.9**、2024 **-2.3**、2025 **-6.9**。

分层：spread（×direction）=+0.000647，g0=-0.000055，g9=0.000593，不单调；月换手 0.883。

### 冗余/增量检查（D10）

vs minute 库：max ρ=0.294（vs ret_concentration），resIC t=**-2.84**，retention=0.77 → **可加入**。
与 `vol_price_corr` ρ=0.765（≥0.7 不共存），后者落观察、未入库。

### 判定

显著（|t|=7.86，逐年同号）→ **候选**，已入 minute 参考库（方向修正版）。
动作：flip 重跑（_dn 守卫版）刷新快照；与 ret_concentration 的 0.29 相关列入组合关注。

## 5. 迭代历史

| 日期 | 变体 | 改动 | IC mean | t | 结论 |
|------|------|------|---------|---|------|
| 2026-09-17 | dir=1 初版 | 原始跑 | -0.0352 | -7.86 | 方向记反 |
| 2026-09-17 | dir=-1 + 守卫 | 方向修正 + _dn<=0 → None | -0.0352 | -7.86 | 显著入库 |

## 6. 风险与备注

- 审核（subagent）通过；一般问题：涨停/封板日 `_dn=0` → inf（2,177 行）、全平日 0/0
  nan（7,740 行），守卫版消除。
- rank/Pearson 异号：比值信号重尾，下游必须 rank/截尾。

---
*档案规范见 `_template.md`。*
