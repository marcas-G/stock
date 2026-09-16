# 06 策略 L4/L5 双轨现状

- 审计时间：2026-09-16｜只读
- 来源：`knowledge/design/workspace/2026-09-16-strategy-decomposition/design.md` §3（现状实现对照）、
  §4-G4；`plan.md` Task 7-G4；`knowledge/dossiers/strategies/_l5-rules.md` §3；
  `research/tools/strategies/README.md`

## 实测

| 项 | 现状 | 证据 |
|---|---|---|
| 旧脚本自实现 L4/L5 | 是：`strategy_crash_bottom.py:67 strategy_backtest()`（k/等权/周频/cost_bps/stop_loss/take_profit/max_hold/rebalance_weeks）、`:265 strategy_long_backtest()`；`strategy_wait_crash.py:82 wait_crash_backtest()`（stop_loss/take_profit/成本） | grep + 代码阅读 |
| 新 YAML 链 | Plan S 首例 1 个：`research/strategy/low_lottery_top30_weekly.yaml`（R28 验收，5 决策/176 笔/NAV +2.2257%） | R28 SUMMARY |
| crash_bottom 是否已转 YAML | 否；档案（`crash_bottom_leader_strategy.md`）标注"历史快照，当前数据状态下不可复现"并列出恢复条件 | dossier |
| 双轨的登记触发条件 | G4：「`index_daily`(000852.SH)/`stock_st`/duckdb 任一恢复后」收敛 M7/M8 | `_l5-rules.md` §3 |
| 触发条件实测 | `stock_st` 表缺（CH code 60）；`index_daily` 0 行（000852.SH 0 行）；`data/` 下 0 个 duckdb | `transcripts/ch_data_state.txt` |

## 判定

- **双轨仍存在（开放）**，但按登记属于 **条件未燃**：三个数据前置全未恢复，
  crash_bottom 收敛 M7/M8 无法进行（design §4-G4 也写明"收敛前明确脚本为参考实现"）。
- 旧脚本使用的 L5 规则语义（盘中/逐日止损止盈、段界清仓、长线分批）在平台链中
  仍不存在（见 02-C2）——即使数据恢复，收敛还需要 L5 规则平台的投入。
- 新 YAML 链（L4=M7、L5=M8+研究侧 `max_hold`）已被首例证明可用；
  但两轨的成本/止损口径不同源（旧脚本 cost_bps 统一口径 vs M8 成本模型），
  旧策略结论不可与新链直接对比。

## 用户影响

- 旧策略（crash_bottom/wait_crash）只能继续用旧脚本或其结论快照；
  新策略用 YAML 链。用户面对两套"策略"入口，需按 README 辨别；
- 若未来数据恢复后直接重跑旧脚本，其结论与 M7/M8 行为可能有漂移（design §4-G4
  点名的 R01-STRAT-C2 段界成本 bug 即自实现产物）。

## 证据文件

- `transcripts/ch_data_state.txt`（G4 三前置实测）
- 文件：`research/tools/strategies/strategy_crash_bottom.py`、`strategy_wait_crash.py`、
  `README.md`；`knowledge/dossiers/strategies/crash_bottom_leader_strategy.md`
- 对照：`governance/evidence/verification/R28/strategy-first-example/SUMMARY.md`（新链验收）
