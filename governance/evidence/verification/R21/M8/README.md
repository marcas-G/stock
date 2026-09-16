# R21 M8（M7/M8 组合与回测）修复证据索引

范围：R01-M8-I1..I7（7 个 Important；I1 评审建议升 C）。
基线：`dd01bd9`（评审基线）；本目录所有 `before-*` 为修复前输出，`after-*` 为修复后输出。
复现环境：`cd platform && .venv/bin/python ...`。M7 侧的 BEFORE 用
`pytest -o pythonpath=/tmp/opencode/r21-m7/src`（HEAD 版 `strategy_artifacts.py` /
`execution_store.py` / `market_open.py` 沙箱）复现旧行为。

## Finding → 修复 → 测试 → 证据

| Finding | 修复（文件） | 测试（platform/tests/） | before / after 证据 |
|---|---|---|---|
| I1 artifact 覆盖写崩溃窗口（M7+M8） | `adapters/strategy_artifacts.py`（旧 manifest 先 rename `.stale` tombstone + 每文件 sha256 + load 复核）; `adapters/execution_store.py`（同 + nav_series↔artifacts/final_state/event 覆盖交叉校验） | `test_strategy_artifacts.py::test_failed_overwrite_never_hybrid_loadable` / `test_content_hash_mismatch_fails` / `test_missing_content_hash_field_fails` / `test_manifest_atomic_failure_no_partial`；`test_backtest_persistence.py::test_failed_overwrite_never_hybrid_loadable` / `test_file_content_hash_mismatch_fails` / `test_nav_series_artifact_cross_check_fails` / `test_final_state_cross_check_fails` / `test_extra_event_index_row_fails` | `I1-before/after-probe3-strategy-hybrid.txt`、`I1-before/after-probe4-exec-hybrid.txt`、`I1-before-test-red-m7.txt` / `I1-after-test-green-m7.txt`、`I1-m8-before-test-red.txt` / `I1-m8-after-test-green.txt`、`I1-after-probe5-empty-tamper.txt` |
| I2 `FillBatch.order_quantity` 恒等 filled | `app/backtest/fills.py`（order_quantity=委托量；filled=funding 缩量后成交） | `test_backtest_fills.py::test_partial_buy_order_quantity_auditable` / `test_partial_buy_fill_batch_validator_accepts_filled_lt_order` | `I2-before-test-red.txt` / `I2-after-test-green.txt` |
| I3 空 adj_event 窗口 Null dtype | `adapters/read/market_open.py`（无条件 cast） | `test_backtest_ca_gate.py::test_loader_existing_table_empty_window_typed` / `test_loader_existing_table_empty_codes_typed` | `I3-before-test-red.txt` / `I3-after-test-green.txt`、`I3-before/after-probe1-empty-adj.txt` |
| I4 decision_range 外 trailing 拖垮 run | `app/backtest/backtest.py`（range 内 scoped target/schedule） | `test_backtest_runtime.py::test_decision_range_ignores_out_of_range_trailing` / `test_in_range_trailing_decision_still_fails` | `I4-I5-before-test-red.txt` / `I4-I5-after-test-green.txt` |
| I5 尾部未决 fail 全 run（§6.3 矛盾） | `core/domain/backtest.py`（`trailing_unresolved`）、`app/backtest/overnight.py`（`TrailingUnresolvedError`）、`app/backtest/backtest.py`（最后 event 合法终止、保留 artifacts） | `test_backtest_runtime.py::test_trailing_unresolved_legal_termination_preserves_results`；`test_backtest_persistence.py::test_trailing_result_roundtrip` | 同上 + `I1-I6-I7-before-test-red.txt` |
| I6 manifest columns/created_at/runtime_version/日期范围/artifact_count 严格性 | `adapters/execution_store.py`（`_strict_nonneg_int`、columns/sha256/date-range/created_at/runtime_version 校验） | `test_backtest_persistence.py::test_load_rejects_bool_artifact_count` / `test_load_rejects_manifest_columns_mismatch` / `test_load_rejects_invalid_created_at` / `test_load_rejects_invalid_runtime_version` / `test_load_rejects_date_range_mismatch` / `test_load_rejects_missing_date_range` | `I6-before-test-red.txt` / `I6-after-test-green.txt` |
| I7 execution_timing 不持久化、load 硬编码 NEXT_OPEN | `adapters/execution_store.py`（write 持久化 + load 读取校验；legacy/next_close 显式拒绝） | `test_backtest_persistence.py::test_manifest_persists_execution_timing` / `test_load_rejects_missing_execution_timing` / `test_load_rejects_next_close_execution_timing` / `test_save_rejects_next_close_artifacts` | `I7-before-test-red.txt` / `I7-after-test-green.txt` |

## 回归面（修复后）

- `after-regression-m7-m8.txt`：452 passed（backtest_*/execution_signal_chain/
  canonical_artifact_handoff/strategy_artifacts）。
- `after-regression-adapters-arch.txt`：568 passed（atomicio/market_open/
  open_*/execution_*/architecture/ast_gate）。

## 说明

- probe3/4/5、probe1 直接跑 `/tmp/opencode/reviewer-m8/` 原脚本（未改语义）；
  probe1 的 scratch duckdb 已备份于 `/tmp/opencode/r21-m7/emptyadj.duckdb.bak`。
- 未 commit（评审修复任务要求）；`docs/` 未改动（建议见交付总结）。
