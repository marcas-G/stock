# R22 分钟级执行（M8 扩展）证据索引

命令与原始输出按任务存放；本文件只做索引与结论（详细输出见同目录 txt）。

## 任务与提交

| Task | 内容 | commit | 证据 |
|---|---|---|---|
| 0 | 基线：NEXT_OPEN digest 探针 + M8 回归（BEFORE） | — | `task0-baseline/probe_next_open_BEFORE.txt`、`task0-baseline/m8_tests_BEFORE.txt`（323 passed） |
| 1 | 配置域（NEXT_WINDOW + MinuteWindowSpec/SliceSpec/TriggerSpec + fail-fast 校验） | `7172ce5` | `task1/red.txt` → `task1/green.txt`（31 passed） |
| 2 | 纯分钟窗口引擎（VWAP/触发/参与率/封板/兜底） | `471b503` | `task2/red.txt` → `task2/green.txt`（20 passed） |
| 3 | 分钟读取适配（CH only + 契约校验） | `49e23ac` | `task3/red.txt` → `task3/green.txt`（11 passed） |
| 4 | 窗口成交（明细/未成交/成本/现金约束） | `4d36e9b` | `task4/red.txt` → `task4/green.txt`（16 + 既有 79 passed） |
| 5 | run_backtest NEXT_WINDOW + WINDOW_END_BASED | `5683d29` | `task5/red.txt` → `task5/m8_regression_AFTER.txt`（307 passed）、`task5/extra_regression_AFTER.txt`（122 passed） |
| 6 | 持久化扩展（v2 窗口元数据 + 明细，v1 向后兼容） | `8f54dfa` | `task6/red.txt` → `task6/green.txt`（37 passed） |
| 7 | CH 端到端 + 全门 | （见下） | `task7/ch_e2e.txt`、`task7/ch_chain_probe.txt`、`task7/gates.txt`、`task7/full_pytest.txt` |

## NEXT_OPEN 零差异（硬门）

- digest 探针（确定性合成 4 事件：orders/assessment/fills/positions sha256 + NAV + cash）：
  `task0-baseline/probe_next_open_BEFORE.txt` vs `task5/probe_next_open_AFTER.txt` →
  `task5/probe_diff.txt` = **ZERO-DIFF**。
- 既有 M8 全量回归：`task0-baseline/m8_tests_BEFORE.txt`（323 passed，含 marks/端到端链）→
  `task5/m8_regression_AFTER.txt`（307 passed，Task5 指定 7 文件）+ `task5/extra_regression_AFTER.txt`（122 passed）。
- `NEXT_CLOSE` 显式拒绝未动（fills/orders/overnight/state/accounting/fillability/execution_store）；
  `tests/test_backtest_runtime.py::test_caller_explicit_marks_not_implemented` 等全绿。

## CH 端到端（Task 7）

- `task7/ch_e2e.txt`：`pytest tests/test_minute_execution_e2e.py` → 2 passed（20 只 × 2024-01；
  真实一字涨停日 BUY 不成交）。抽中样本 000009.SZ @ 2024-01-29（event 18），
  SQL 逐分钟 `amount/volume` 与 window_fills 明细价对拍 delta=0.0，汇总加权价与
  `FillBatch.reference_price` 对拍 delta=0.0，滑点 1bp 关系成立。
- `task7/ch_chain_probe.txt`：全链 `run_factor → construct_target_portfolio → run_backtest(NEXT_WINDOW)`
  （20 只 × 21 决策，20 个事件有成交；NAV 10,000,000 → 10,150,838.97）+ 持久化 v2 round-trip 逐值一致。

## 全门（Task 7 Step 3）

- `make gates` → 结构门全绿、数据接口 ENFORCED 全绿（`task7/gates.txt`）。
- `cd platform && .venv/bin/python -m pytest -q` → **2862 passed, 13 skipped**（≥ 基线 2696/13，无回退；`task7/full_pytest.txt`）。
