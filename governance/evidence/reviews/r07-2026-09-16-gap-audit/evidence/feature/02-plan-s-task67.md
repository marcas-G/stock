# 02 Plan S Task 6/7 后置项

- 审计时间：2026-09-16｜只读
- 来源文档：
  - `knowledge/design/workspace/2026-09-16-strategy-decomposition/plan.md` Task 6/7、「未竟/后置」
  - 长期边界：`knowledge/dossiers/strategies/_l5-rules.md` §3（触发条件表）
  - `governance/evidence/verification/R28/strategy-first-example/SUMMARY.md`
- 实施状态（plan.md 头）：已完成 7e03acb/126def4（R28 验收）

## 实测矩阵

| ID | 事项 | 来源 | 现状实测 | 判定 |
|---|---|---|---|---|
| C1 | L5 `max_hold`（V1 研究侧近似） | plan Task 6；_l5-rules.md §1 | `research/tools/strategies/l5_rules.py` 实现；`test_l5_rules.py` 8 passed（实测复跑 0.39s）；R28 真 CH E2E | **已闭环** |
| C2 | `stop_loss/take_profit` 平台化 | plan Task 7-G3+；_l5-rules.md §3 | doc.py + apply_l5_rules 双层 NotImplementedError；触发条件未燃 | 条件未燃 |
| C3 | G1 regime 多输出 | plan Task 7-G1 | 平台 outputs 已支持；研究 0 使用；策略 regime 只声明 | 条件未燃 |
| C4 | G4 crash_bottom 收敛 M7/M8 | plan Task 7-G4 | 旧脚本仍双轨（见 06）；CH 三前置全缺 | 条件未燃 |
| C5 | `factorlab strategy run` CLI | plan Task 7 | 未做；研究侧入口已验收 | 条件未燃 |
| C6 | 策略 spec lint / direction 对齐 | plan「未竟/后置」第 2 条 | `factorlab lint --strategy` exit 2（No such option）；run_strategy 无 direction 校验（SignalMeta 无 direction） | 开放 |
| C7 | `universe_override` 运行链未消费 | R28 SUMMARY §5.5 | `app/strategy/run.py` 零引用（仅 dry-run 打印） | 开放 |

## 触发性判定（时间 2026-09-16）

| 触发条件 | 实测 | 燃否 |
|---|---|---|
| 首个需要段界显式化的策略上库前（G1） | 策略 YAML 仅 1 个（low_lottery），未需要段界 | 未燃 |
| V1 近似偏差在复盘中证实有实质影响（G3+） | 无复盘证据；max_hold 未被任何策略启用 | 未燃 |
| index_daily(000852.SH)/stock_st/duckdb 任一恢复（G4） | 三者全未恢复（表缺/0 行/0 文件） | 未燃 |
| 用户明确要求命令行一键（M8 CLI） | 无记录 | 未燃 |

## 用户影响

- C1 可用：研究入口 `run_strategy.py` 经 `target_transform` 注入 `max_hold`；
  换出为调仓日粒度（边界文档 `_l5-rules.md` 已写明非成交明细级）。
- C2 配置报错清晰（不会静默忽略）；旧脚本内的止损止盈语义与平台链不同源（见 06）。
- C6 风险：direction 与因子档案不一致（写反）无任何机器校验——回测结果方向性错误
  不会被拦住；这是"策略配置化"最容易被静默输入的坑。
- C7 风险：`universe_override` 设置后无效果也无提示（静默忽略），用户可能误以为在
  限定票池，实际全量 universe 随因子。

## 证据文件

- `transcripts/lint_strategy_flag.txt`（C6）
- 代码定位：`platform/src/factorlab/app/strategy/run.py:61-103`（无 universe_override/direction 校验）、
  `core/domain/frames.py:65-71`（SignalMeta 无 direction）、
  `research/tools/strategies/l5_rules.py:106-119`（C2 NotImplementedError）
- 测试复跑：`research/tools/strategies/tests/test_l5_rules.py` → 8 passed（本审计实测）
