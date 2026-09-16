# R02 平台计算正确性修复证据（engine）

R02 严格评审（`docs/reviews/r02-2026-09-15-strict-review/report.md`）中平台侧
4 个 finding 的复现、红绿证据与提交索引。评审 probe 在
`/tmp/opencode/reviewer-r02-newturf/`（probe1/3/6/7/11）。

| finding | 修复 | 测试 | 复现/证据 | commit |
|---|---|---|---|---|
| R02-C1 分钟未来门旁路（Critical） | `minute_gate` 解析 import alias + 折叠 Pow/IfExp/abs/int；`minute_ops` 运行时硬校验（k/window 为 int 且 >=1，拒 bool） | `tests/test_minute_gate.py::test_gate_direct_pow_ifexp_call_folds` / `::test_gate_resolves_import_aliases` / `::test_minute_scope_rejects_aliased_future_shift_e2e`；`tests/test_minute_ops.py::test_im_delay_runtime_rejects_non_positive_or_non_int` / `::test_im_window_runtime_rejects_lt_one_or_non_int` | `repro-before-C1-probe11.txt` / `repro-after-C1-probe11.txt`、`repro-before/after-C1-probe7-alias.txt`、`C1-red.txt` / `C1-green.txt` | `808c40b`（+证据 `1aabaa8`） |
| R02-I2 process 链无有限值门（Important） | `process_ops` 统一入口 `_finite`：NaN/±Inf → null（全部 7 处理器 + zscore 别名） | `tests/test_process.py::test_*_inf_treated_as_invalid*`、`::test_standardize_then_winsorize_chain_not_all_null` | `repro-before-I2-probe3-inf.txt` / `repro-after-I2-probe3-inf.txt`、`I2-red.txt` / `I2-green.txt` | `9dbe0ed` |
| R02-I3 BatchFlock throttle 看门狗失效（Important） | 无 future 且闸门关闭分支也记账 stall（超时抛 failed / requeue strikes 后放弃） | `tests/test_batch_flock.py::test_throttle_closed_without_futures_stalls_instead_of_hanging` / `::test_throttle_closed_requeue_gives_up_after_strikes` | `repro-after-I3-probe6-flock.txt`、`I3-red.txt`（timeout 124 挂死）/ `I3-green.txt` | `5088f77` |
| R02-I5 240 网格断言不查 index 范围/session_type（Important） | `minute.compute_minute_factor_panel`：组内 `minute_index` 0..239 断言；`session_type` 存在时须 ∈ {0,1,2} | `tests/test_minute_engine.py::test_minute_grid_index_range_enforced` / `::test_minute_grid_session_type_contract_enforced` | `repro-before-C1-I5-probe1.txt` / `repro-after-I5-probe1.txt`、`I5-red.txt` / `I5-green.txt` | `c9d463c` |

契约同步：`platform/docs/interface.md`（分钟参数双防线 / 网格范围与会话校验 /
process 非有限值门）→ `18d1e88`。

最终验证命令与输出见 `final-battery.txt`。
