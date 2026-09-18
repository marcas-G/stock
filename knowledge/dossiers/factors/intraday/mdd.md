---
xname: intraday_mdd
formula: |
  _cm = im_cummax(close) _dd = close / _cm - 1 signal = day_min(_dd)
tags: [minute, mine_m11, borderline]
params: {}
status: 观察
created_ts: 2026-09-18
updated_ts: 2026-09-18
snapshot: CH 运行快照（2026-09-18，daily/forward_return_1d，evaluation.version=2）
---

# intraday_mdd 因子档案

## 1. 元信息

| 项 | 值 |
|----|----|
| 名称 | `intraday_mdd`（= `factor/intraday/mdd.yaml`，需 `im_cummax` 算子） |
| 方向 | `1`（浅回撤→次日强，假设证实） |
| 状态 | 观察 |

## 2. 假设与文献出处

日内最大回撤（Chevalier-Lévy 2018 JF "What are you willing to lose"资本损失厌恶；中文行为金融研报系列"日内最大回撤因子"）：当日回撤深度反映抛压/风险，浅回撤股次日更强。

## 4. 验证结果

| 指标 | 值 |
|------|----|
| RankIC mean | 0.06249 |
| t 值 | 9.36 |
| IR | 0.348 |
| 近 26 周 t | 0.80 |
| 期数 | 723（daily） |

**D10**：resIC t=+3.61（有增量）、max ρ=0.796、r²_lib=0.76、retention=0.33 → 观察。补充检验：分年 resIC 三年同号（6.16/2.55/2.86），但对"17 员+jump_ratio+cn_spread"互验后 t 降至 2.52（被同族吸收最多）→ 维持观察，不入库。

## 6. 风险与备注

- im_cummax 算子为本档案配套新增（platform 提交 7297a57）。

---
*档案规范见 `_template.md`。*
