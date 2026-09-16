# 05 分钟执行 V1 边界与 interface 同步缺口

- 审计时间：2026-09-16｜只读
- 来源：`knowledge/design/workspace/2026-09-15-minute-execution/design.md` §6（6.1-6.5）；
  `plan.md`「未竟/后置」；实施证据 `governance/evidence/verification/R22/minute-execution/SUMMARY.md`

## V1 实施状态（复核）

- Task 1-7 全完成（SUMMARY）：配置域 `NEXT_WINDOW` + `MinuteWindowSpec/SliceSpec/TriggerSpec`、
  纯窗口引擎、分钟读适配、窗口成交、`run_backtest` 集成 + `WINDOW_END_BASED`、
  持久化 v2（向后兼容）、CH E2E（20 只 × 2024-01，2 passed）。
- 测试在场：`test_minute_window.py`、`test_read_minute_window.py`、`test_window_fills.py`、
  `test_backtest_window_runtime.py`、`test_execution_store_window.py`、`test_minute_execution_e2e.py`。
- `ExecutionTiming.NEXT_WINDOW` 在 `core/domain/timing.py:43`；NEXT_OPEN 零差异（digest ZERO-DIFF）。

## 未竟/后置清单（design §6 与 plan 末）

| ID | 事项 | 设计登记 | 现状实测 | 判定 |
|---|---|---|---|---|
| F1 | 量能触发（V2） | design §6.3「预留 trigger.mode 扩展位」 | `TriggerSpec.mode: Literal["limit","vwap_offset"]`（spec.py:136）——无 volume 模式 | 开放（V2） |
| F2 | 分钟 NAV（V2） | design §6.4「分钟级 NAV 曲线不在范围」 | `MarksPolicy.WINDOW_END_BASED` = 窗口末分钟 close 的**日级** mark；无日内 NAV/回撤 | 开放（V2） |
| F3 | 盘中临停精确规则（另立） | design §6.5「V1 只按缺行跳过」 | 全仓 `临停` grep=0；读取层只做缺行过滤 | 开放（另立） |
| F4 | interface.md 未同步分钟执行（新观察） | design §3 声称 interface 为权威文档 | `knowledge/contracts/interface.md` 全文 0 处 `NEXT_WINDOW`/`minute_window`/`vwap_offset`；M8 段仍写「**NEXT_OPEN only**：v1 只支持 ExecutionTiming.NEXT_OPEN」（:2277） | 开放（文档缺口，非设计后置） |

## 用户影响

- F1-F3：不阻塞 V1 使用；量价异动类执行算法、日内风险视图、临停精确仿真暂缺，
  与 design 的 V1/V2 边界一致（合规）。
- F4：**读契约的读者/AI 会得出"平台不支持分钟执行"的结论**，与代码（timing.py 枚举、
  6 个测试文件、R22 CH E2E）和策略 Plan S 文档（YAML 支持 NEXT_WINDOW）矛盾；
  R28 首例虽用 NEXT_OPEN，但 YAML 契约已声明支持 `NEXT_WINDOW + minute_window`。
  建议：interface M8 段补「分钟窗口执行（R22）」小节或加显式指针到 minute-execution design。

## 证据文件

- `transcripts/ch_data_state.txt`（旁证：CH 分钟数据面存在，非本次重点）
- 代码定位：`platform/src/factorlab/core/domain/timing.py:35-44`、
  `core/execution/spec.py:125-170`、`app/backtest/backtest.py`（NEXT_WINDOW 分支）
- 文档对照：`knowledge/contracts/interface.md:2277`（NEXT_OPEN only） vs
  `knowledge/design/workspace/2026-09-16-strategy-decomposition/plan.md:72`（NEXT_WINDOW 支持）
