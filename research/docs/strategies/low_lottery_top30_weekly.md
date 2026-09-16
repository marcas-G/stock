---
name: low_lottery_top30_weekly
spec: research/strategy/low_lottery_top30_weekly.yaml
window: "2025-03-01 ~ 2025-03-31"
status: verified
---

# low_lottery_top30_weekly 策略档案（Plan S 首例）

日期：2026-09-16 ｜ 数据：CH 事实库（`FACTORLAB_DATA_BACKEND=ch`）｜ 执行：M8 NEXT_OPEN

## 1. 假设（经济逻辑与预期方向）

- **市场行为假设**：A 股散户对"彩票型"标的（日内曾大幅摸高、高波动）有过度需求，
  推高当期价格、压低未来收益。因子 `max_effect_20d_high` 用 20 日内单日最大盘中摸高
  幅度（`ts_max(high/prev_close-1, 20)`）度量彩票体验峰值；因子档案 direction=-1
  （IC 0.0699, t=5.00, IR=0.375，见 `research/docs/factors/volatility/max_effect_20d_high.md`）。
- **策略预期**：做多**低分位**（低彩票暴露）Top-30 等权，周频轮动吃截面修正。
  direction=-1 与因子档案方向一致。
- **可交易性分工（G5）**：池 = 研究口径（因子 spec `exchanges: [SSE, SZSE]`，无 ST 过滤
  因 CH 缺 `stock_st`）；执行 = 成交口径（涨跌停/停牌/T+1/成本由 M8 拦截与计费）。
  两者互不重叠，不把执行约束混入信号。

## 2. 规格全文

> 机器可执行全文以 spec 为准：`research/strategy/low_lottery_top30_weekly.yaml`（索引有链接）。

| 层 | 配置 |
|---|---|
| L1 池 | 随因子（`universe_override: null`；SSE+SZSE，无 ST 口径——CH 缺 `stock_st`） |
| L2 regime | `signal_gate`（当前唯一合法语义；门控在因子公式内） |
| L3 信号 | `max_effect_20d_high`（direction=-1；qfq；`process: winsorize(0.99) → standardize`） |
| L4 组合 | top_k=30；equal_weight；gross_exposure=1.0；rebalance=weekly（ISO 周最后可用信号日） |
| L5 执行 | timing=NEXT_OPEN；initial_cash=10,000,000；cost_model：佣金 0.00025（最低 5 元）+ 印花税卖 0.0005 + 过户费 0.00001 + 滑点 5bps |
| L5 rules | 全 null（V1：`max_hold`/止损止盈未启用；见 Task 6 边界文档） |
| date | 2025-03-01 ~ 2025-03-31（回测评估窗口） |

## 3. 窗口筛选（CA Gate 记录）

CA Gate（R03-I8 / M8-06A §5.5）在持仓跨除权事件时 fail-closed。本次实测（R28 复验）：

| 窗口 | 结果 | 记录 |
|---|---|---|
| 2025-03-01 ~ 2025-03-31 | **干净（选定）** | 5 decision / 5 event，无拦截 |
| 2025-02-01 ~ 2025-02-28 | 干净 | 4 event，-0.81%（仅作筛选对照，不入结论） |
| 2025-04-01 ~ 2025-04-30 | **拦截** | `601328.SH@2025-04-18` 在 (2025-04-14, 2025-04-21] 除权 |
| 2025-01-01 ~ 2025-03-31（多月） | **拦截** | `600116.SH@2025-01-08` 在 (2025-01-06, 2025-01-13] 除权 |

- 与 `strategy-backtest-manual.md` 的实测结论一致：多月/长窗几乎必撞某只除权，逐月筛选后
  2025-03 干净可跑；原始输出 `docs/verification/R28/strategy-first-example/task5-ca-window-probe.txt`。
- 筛选过程口径：本策略链按 `doc.date` 先过滤 signal 再构造组合（partial ISO 周按 M7
  contract 计入，故 3/31 单日形成第 5 个 decision；demo 口径见 §4 对照）。

## 4. 结果

**首例真跑（2025-03，10M 初始资金；R28 原始输出）**：

| 指标 | 值 |
|---|---|
| decision 数 | 5（3/07、3/14、3/21、3/28、3/31——末周为 partial ISO 周） |
| 执行事件 | 5（3/10、3/17、3/24、3/31、4/01） |
| 成交笔数 | 176 |
| NAV | 9,992,407.37 → 10,214,807.42（**+2.2257%**） |
| 最大回撤 | -0.3140% |
| 费用合计 | 18,290.52 元（18.30 bps 初始资金） |
| trailing_unresolved | False |

**与手工 demo 对照（同窗口参数、防口径漂移）**：demo（`strategy-backtest-manual.md` §3，
1M 初始、decision 只取 3/7~3/28 四周）→ 4 event / 116 笔 / +2.49%。
用新入口同参数复跑（`date.end=2025-03-28`、cash=1M）→ **4 event / 116 笔 / +2.49%**，
且逐帧相等：`nav_series`/`final_state`/全部 artifact 的 fills 与 positions 完全一致
（`task5-parity-frame-equal.txt`）。差异仅来自：
1. 初始资金 10M vs 1M（手数取整/费用 bps 微差）；
2. 本链含 3/31 partial 周 decision（demo 用 `decision_range` 截断，不含 partial 周）
   → 多 1 个 event 与 +2.2257%（vs +2.49% 的四周口径）。
两条口径均已留证，不构成漂移。

- 原始输出：`task5-run-2025-03.txt`（首例）、`task5-run-metrics.txt`（指标）、
  `task5-parity-demo-window.txt` + `task5-parity-frame-equal.txt`（对照）、
  `task5-reference-demo.txt`（手工 demo 原文）。

## 5. 迭代历史

| 日期 | 变更 | 结果/结论 |
|---|---|---|
| 2026-09-16 | 首例建档（Plan S Task 5）：spec + 档案 + 索引；真跑 2025-03 | +2.23%（5 event，含 partial 周）；与 demo 逐帧对照通过 |

## 6. 风险与未决

- **窗口容量**：单月样本，收益/回撤不构成统计结论（年化/Sharpe 按执行日外推噪声大）。
- **ST 口径**：CH 缺 `stock_st`，池为无 ST 过滤口径；与有 ST 过滤的 run 不可混比
  （因子档案同款声明）。
- **partial 周**：窗口末 partial ISO 周会形成单日 decision（M7 deliberate contract），
  跨窗口拼接时注意边界重复计入。
- **L5 规则**：`max_hold`/止损止盈 V1 未启用（平台侧 NotImplementedError 防静默忽略）；
  触发条件登记见 Task 7（`docs/reviews/2026-09-16-strategy-decomposition/plan.md`）与
  `research/tools/strategies/l5_rules.py` 文档。
- **未决**：窗口内 3/31 决策只覆盖 4/1 一个执行间隔（周频语义在 partial 周的边界）；
  若需统一口径，用户可在 spec 里把 `date.end` 对齐到完整 ISO 周。
