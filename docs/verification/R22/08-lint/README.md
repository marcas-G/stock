# Task 8 — lint 接入完整静态管线（不触 DB）

**命令**
- 红：`before.txt`（5 failed：开放算子仍报未知、无 op_meta 指引、无输出名检查）
- 绿：`after_unit.txt`（`pytest tests/test_cli_lint_v2.py tests/test_cli_lint_params.py` → 20 passed）
- 真实 CLI：`after_cli.txt`
  - `factorlab lint ts_arg_max(volume, 60)` → `OK open_op_probe` exit=0
  - `factorlab lint totally_new(volume)` → `1:9: 未知算子 totally_new；… op_meta …` exit=1
- 既有门回归：`test_cli.py/test_catalog.py/test_doc_paths_exist.py/test_cli_op_registry.py/test_cli_list_show.py`
  → 50 passed；`make lint-factors` → 152 通过 / 0 失败

**实现**：新增 `compute.prepare_static(spec) -> (formula, pool)`——prepare_formula_pipeline
（参数替换/宏/保留名/def 内联/方法改写/薄封装/未来输入门/池公式归一与布尔门）→
stable_rank 与 vendor alias 改写 → `normalize_calls`（分类表解析，未知 → op_meta 指引）
→ `check_causality`（未来门全形态）→ `_require_declared_outputs`（outputs 声明检查）。
CLI `lint` 改为逐 variant 调 `prepare_static`（保留宏体语法预检与"未知参数/语法错误"
文案）；`compute_formula` 复用同一 `_require_declared_outputs`（单一实现）。

**测试名**：`test_lint_rejects_negative_shift`、`test_lint_accepts_open_library_op`、
`test_lint_accepts_uppercase_library_op`、`test_lint_rejects_unknown_operator_with_op_meta`、
`test_lint_rejects_outputs_mismatch`、`test_lint_rejects_future_in_pool_formula`、
`test_lint_accepts_params_template_open_op`。

**偏差**：计划测试的 `ts_quantile` 不存在（见 Task 2 证据），改用 `ts_arg_max` /
`BBANDS` 真实库函数；其余按计划。
