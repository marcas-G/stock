---
name: cx_demo_equal_weight
spec: research/strategy/cx_demo_equal_weight.yaml
window: "2026-09-01 ~ 2026-09-16"
status: draft
---

# cx_demo_equal_weight 策略档案（Plan CX-C4 T3 对照）

日期：2026-09-19 ｜ 数据：CH 事实库（`FACTORLAB_DATA_BACKEND=ch`）｜ 执行：M8 NEXT_OPEN

`cx_demo_score_weighted` 的同参对照（唯一差异：`weighting: equal_weight`），
用于验证 score_weighted 分派与结果差异（Plan CX-C4 T2/T3）。

## 1. 假设（经济逻辑与预期方向）

- 同一 composite 信号（`composites/cx_demo`）与选择集（Top-5），等权持有——
  作为 score_weighted 的对照臂：若加权口径未生效，两者 Nav/成交/换手应完全相同。
- 可交易性分工同 score_weighted 档案。

## 2. 规格全文

> 机器可执行全文以 spec 为准：`research/strategy/cx_demo_equal_weight.yaml`。

| 层 | 配置 |
|---|---|
| L1 池 | `universe_override: null`（随 composite 面板） |
| L2 regime | `signal_gate` |
| L3 信号 | `composites/cx_demo` |
| L4 组合 | top_k=5；**equal_weight**；gross_exposure=1.0；rebalance=daily |
| L5 执行 | timing=NEXT_OPEN；initial_cash=10,000,000；成本同 score_weighted |
| date | 2026-09-01 ~ 2026-09-16 |

## 3. 窗口筛选

同 score_weighted 档案（同窗口、同读取门口径）。

## 4. 结果（对照）

| 指标 | score_weighted | equal_weight |
|---|---|---|
| 决策数 | 12 | 12 |
| 成交笔数 | 77 | 68 |
| 末 NAV | 9,425,050.40 | 9,485,896.89 |
| 区间收益 | -5.6779% | -5.0689% |
| 最大回撤 | -5.6981% | -5.1151% |
| 费用合计 | 65,678.86 | 54,000.91 |
| 平均单边换手 | 0.3946 | 0.3250 |

- 差异证明 weighting 分派真实生效（同信号/同选择集/同 window/top_k/成本）。
- 原始输出：`governance/evidence/verification/R34/c4/run_e2e_output.txt` [2]。

## 5. 迭代历史

| 日期 | 变更 | 结果/结论 |
|---|---|---|
| 2026-09-19 | 首建（Plan CX-C4 T3）：score_weighted 同参对照 | 12 决策 / 68 笔 / -5.07%；与 score_weighted 指标分化 |

## 6. 风险与未决

- 同 score_weighted：12 日演示窗口，不构成统计结论。
