---
name: cx_demo_score_weighted
spec: research/strategy/cx_demo_score_weighted.yaml
window: "2026-09-01 ~ 2026-09-16"
status: draft
---

# cx_demo_score_weighted 策略档案（Plan CX-C4 T3 示例）

日期：2026-09-19 ｜ 数据：CH 事实库（`FACTORLAB_DATA_BACKEND=ch`）｜ 执行：M8 NEXT_OPEN

## 1. 假设（经济逻辑与预期方向）

- **市场行为假设**：合成分 `composites/cx_demo = 0.5·z(mom5) − 0.5·z(vol5)`（成员
  `cx_demo_x1` = 5 日动量、`cx_demo_x2` = 5 日已实现波动，均截面标准化）刻画
  "强势且低波动"的截面排序；score_weighted 在其上做 long-only 倾斜（分数越高权重越大，
  负分不持有）——预期与等权相比更集中到高分名字。
- **可交易性分工（G5）**：池 = 合成面板覆盖的 15 只主板（成分因子显式 codes 池）；
  执行 = 成交口径（涨跌停/停牌/T+1/成本由 M8 拦截与计费），两者互不重叠。
- **信号来源**：composite（Plan CX-C4 §19.1：策略 `signal: composites/<name>`，
  `signal_kind` 自动识别，M7 不感知来源）。

## 2. 规格全文

> 机器可执行全文以 spec 为准：`research/strategy/cx_demo_score_weighted.yaml`。

| 层 | 配置 |
|---|---|
| L1 池 | `universe_override: null`（随 composite 面板 15 只主板 codes） |
| L2 regime | `signal_gate`（唯一合法语义） |
| L3 信号 | `composites/cx_demo`（合成；成员 `cx_demo_x1/x2`，证据 fixtures：`governance/evidence/verification/R34/c4/fixtures/`） |
| L4 组合 | top_k=5；**score_weighted**；gross_exposure=1.0；rebalance=daily |
| L5 执行 | timing=NEXT_OPEN；initial_cash=10,000,000；佣金 0.00025（最低 5 元）+ 印花税卖 0.0005 + 过户费 0.00001 + 滑点 5bps |
| L5 rules | 全 null（V1 边界） |
| date | 2026-09-01 ~ 2026-09-16（12 个决策日；末日 09-16 执行 09-17） |

## 3. 窗口筛选（CA Gate + 读取门记录）

- CA Gate：窗口内 15 只 codes 的 `adj_event` 为空（CH 查询留证于 R34/c4），
  真跑无 `ExecutionDataQualityError`；执行侧涨跌停/停牌由 M8 处理。
- 窗口终点 2026-09-16 的读取门（Plan DQ-M1）为 UNKNOWN/LEGACY（completeness
  UNKNOWN，不可 opt-in）→ CLI `flab strategy run` 在过闸后被 `DATA` 拒绝
  （`cli_strategy_run_gate.txt`）；唯一 COMPLETE 分区 2026-09-17 无下一开放日
  （trade_cal 止于 09-17，末日决策无法解析执行日）。故本项目 E2E 按 T3 任务授权走
  研究脚本 `run_strategy(..., dataset=None)`（与既有 oos2026 运行同口径），
  执行数据仍为真 CH。

## 4. 结果

**R34/C4 真跑（12 决策；`run_e2e_output.txt` / `e2e_metrics.json`）**：

| 指标 | 值 |
|---|---|
| 决策数 / 执行事件 | 12 / 12 |
| 目标持仓行 | 60（12 日 × 5，逐日 sparse） |
| 成交笔数 | 77 |
| NAV | 9,992,409.30 → 9,425,050.40（**-5.6779%**） |
| 最大回撤 | -5.6981% |
| 费用合计 | 65,678.86 元 |
| 平均单边换手 | 0.3946 |

- 手工对照（同窗口同参数）：`cx_demo_equal_weight`（68 笔 / -5.0689% / 换手 0.3250），
  差异来自加权口径（见对照档案）。
- target 权重 = composite 分数独立复算逐值一致（12 日全部；抽日 09-01 表见
  `run_e2e_output.txt` [3]）。
- 原始输出：`governance/evidence/verification/R34/c4/`。

## 5. 迭代历史

| 日期 | 变更 | 结果/结论 |
|---|---|---|
| 2026-09-19 | 首建（Plan CX-C4 T3）：composite 引用 + score_weighted 真跑 | 12 决策 / 77 笔 / -5.68%；手算逐值一致 |

## 6. 风险与未决

- **样本量**：12 个交易日的演示窗口，收益/回撤不构成统计结论（R34/C4 为链路验收）。
- **成员因子**：`cx_demo_x1/x2` 为证据 fixtures（不在 `research/factor/` 因子库索引内，
  无因子档案）；重跑命令见 `governance/evidence/verification/R34/c4/acceptance.md`。
- **读取门**：历史分区（<2026-09-17）为 LEGACY_UNVERIFIED，CLI 入口须待 DQ 管道补齐
  health/completeness 后才能直跑（Plan DQ-M1 范围）。
- **single-name cap**：score_weighted 本期不做（spec §19.2），极端分数会形成集中权重。
