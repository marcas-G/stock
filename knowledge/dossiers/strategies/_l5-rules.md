# L5 规则层 V1 边界（研究侧近似）

日期：2026-09-16 ｜ 实施：Plan S Task 6 ｜ 代码：`research/tools/strategies/l5_rules.py`

> 注：Plan S Task 6 Step 5 原要求更新 `knowledge/design/workspace/2026-09-16-strategy-decomposition/design.md`
> §4-G3（R24 前旧址 `governance/evidence/reviews/2026-09-16-strategy-decomposition/`）；按本轮任务约束 **设计评审目录只读**，边界登记改落本文件（作为代码
> docstring 的长期伴随文档），触发条件与 plan Task 7 表一致。

## 1. V1 实现：`max_hold`（调仓日粒度近似）

| 项 | V1 语义 |
|---|---|
| 计数 | 交易日历按 `(entry, decision]` 数连续持有交易日；严格 `> max_hold` 才换出（恰 N 日保留） |
| 换出时点 | **调仓日**（不是触发日）——非成交明细级，不逐日盯市、不模拟盘中触发/部分成交/T+1 |
| 换出动作 | 从该调仓日目标中删除；剩余持仓按 `gross_exposure` 再归一（`TargetPortfolio` gross invariant 无现金缺口表达）；**不引入替补候选**（规则层不消费 signal） |
| 全部换出 | 该 decision_date 显式 all-cash（0 rows，日期保留） |
| 中断 | 未入选/空仓日 → 连续持有中断，再次入选重新起算 |
| 注入链 | 研究 CLI 经平台 `run_strategy(target_transform=…)` 钩子注入（M7 组合后、落盘/回测前）；规则全 null → 不注入（零行为变化） |

**与因子/组合层的分工**：本规则只改目标权重（L5 路径依赖），不回头改信号（L3）或
选股（L4）；替补轮动属 L4 语义，若需要应扩展 M7 组合器而不是在本层打补丁。

## 2. `stop_loss` / `take_profit`：V1 明确不实现

- `StrategyDoc` 加载与 `apply_l5_rules` 双层显式 `NotImplementedError`（不允许
  静默忽略配置）；
- 平台化所需的语义：连续盯市（收盘/盘中）触发 + 成交明细级回放（触发后卖出价、
  部分成交、T+1 可卖约束）；M8 v1 无这些语义。

## 3. 平台化触发条件（登记，不在本轮实施）

| # | 事项 | 触发条件 |
|---|---|---|
| G1 | regime 多输出（`outputs: [signal, regime]`） | 首个需要段界显式化的策略上库前 |
| G3+ | stop_loss/take_profit 平台化（连续/日内） | V1 近似的偏差在复盘中证实有实质影响 |
| G4 | crash_bottom 收敛 M7/M8 | `index_daily`(000852.SH)/`stock_st`/duckdb 任一恢复后 |
| — | M8 无 CLI 的入口策略（要不要 `factorlab strategy run`） | 用户明确要求命令行一键；否则维持研究侧薄入口 |

## 4. 验证

- 单测：`research/tools/strategies/tests/test_l5_rules.py`（逐值 frame/边界/数据依赖/
  NotImplementedError）；
- 端到端：`test_run_strategy_cli.py::test_real_run_max_hold_excludes_stale_and_renormalizes`
  （真 CH + 真信号：连续持有 3 期的 code 在 age=10 时被换出且剩余再归一）；
- 证据：`governance/evidence/verification/R28/strategy-first-example/task6-*.txt`。
