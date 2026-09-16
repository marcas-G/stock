---
name: <策略名>
spec: research/strategy/<策略名>.yaml
window: "<start> ~ <end>"
status: draft
---

# <策略名> 策略档案

> 模板使用：复制为 `research/docs/strategies/<策略名>.md`（与 spec 同名）。
> front matter 的 `spec`/`window` 是索引门（G-INDEX）必填字段；`_` 前缀文件
> （如本模板）不参与配对。

## 1. 假设（经济逻辑与预期方向）

- 市场行为假设：…（为什么该信号在此策略下可交易；预期方向与因子 direction 对齐）
- 可交易性：池 = 研究口径（ST/次新在池），执行 = 成交口径（涨跌停/停牌由 M8 拦截）；
  两者互不重叠（G5 分工）。

## 2. 规格全文

> 以 spec YAML 为准（此处粘贴关键六层映射与参数；全文链接见索引）。

| 层 | 配置 |
|---|---|
| L1 池 | …（universe_override / 随因子） |
| L2 regime | signal_gate（当前唯一合法语义） |
| L3 信号 | `因子名`（direction=±1；因子档案链接） |
| L4 组合 | top_k=… / equal_weight / gross_exposure=… / rebalance=… |
| L5 执行 | timing=… / cost_model=… / rules=… |

## 3. 窗口筛选（CA Gate 记录）

- 候选窗口与拦截记录（R03-I8：30 只持仓 × 多月几乎必撞除权）：
  - 窗口 A：拦/过？事件 code + trade_date + 决策分段
  - 窗口 B：…（选定干净窗口的理由）
- 选定的干净窗口：`<start> ~ <end>`（decision 数 N）。

## 4. 结果

| 指标 | 值 |
|---|---|
| 决策数 | … |
| 执行事件 / 成交笔数 | … |
| NAV | initial → final（累计收益 …%） |
| 最大回撤 | … |
| 费用 | … |

- 原始输出：`docs/verification/R28/strategy-first-example/…`
- 对照：与 `strategy-backtest-manual.md` 手工 demo 同窗口参数逐项对照（防口径漂移）。

## 5. 迭代历史

| 日期 | 变更 | 结果/结论 |
|---|---|---|
| YYYY-MM-DD | 首例建档 | … |

## 6. 风险与未决

- 已知风险（数据口径/窗口/容量/成本假设）：
- 未决项（登记去向：`docs/pending-items.md` / Plan S Task 7 触发条件表）：
