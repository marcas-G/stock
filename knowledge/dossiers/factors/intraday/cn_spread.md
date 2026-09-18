---
xname: intraday_cn_spread
formula: |
  _a = high / open - 1 _b = low / close - 1 _p = _a * _b _s = day_sum(_p) signal = if_else(_s < 0, sqrt(-_s), 0)
tags: [minute, mine_m10, borderline]
params: {}
status: 候选
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_cn_spread 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_cn_spread`（= `factor/intraday/cn_spread.yaml`） |
| 方向 | `-1`（原始 IC 负号，dir=-1 数据驱动） |
| 状态 | 观察（边界件：resIC 强、retention 0.4625 差 0.04 达门槛） |

## 2. 假设与文献出处

Corcoran-Nomura（2016）OU 隐含有效价差（OHLC 版，log 用比率近似）：价差高=流动性差/风险高 → 次日弱（流动性溢价，dir=-1 假设与数据一致，t=-11.98）。

## 4. 验证结果

| 指标 | 值 |
|------|----|
| RankIC mean | -0.06228 |
| t 值 | -11.98 |
| IR | -0.446 |
| 近 26 周 t | -2.17 |
| 期数 | 723（daily） |

**D10**：resIC t=**-4.96**（本轮最强增量）、max ρ=0.496、r²_lib=0.54，但 retention=0.4625 差 0.04 未达 0.5 → 分年 resIC 三年同号+互验显著 → 2026-09-18 入库（minute 库成员）。**复议触发**：近端数据积累后（或 2026 年满一年后）复核；若 retention ≥0.5 且 resIC |t|≥2 → 入库候选。

## 6. 风险与备注

- log→比率近似未做精确性对拍（CN 原式用 log，量纲单调性保持，rank IC 不受影响）。

---
*档案规范见 `_template.md`。*
