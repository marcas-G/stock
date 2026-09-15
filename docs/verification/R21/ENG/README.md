# R21 ENG 子系统证据 README（R01-ENG-C1/C2/I1-I5）

- 范围：平台引擎侧 R21 修复（commit `ea2ebfe`）；本目录是**原始输出归档**，已有文件只读不改。
- 默认 cwd = 仓库根；各表命令与归档输出中的 `cd platform && .venv/bin/python -m pytest ...` 同形。
- **占位符说明（R02-I9(b) 补）**：`pytest-before.txt` / `pytest-after.txt` 的命令行含占位符
  `<R21 ENG new tests>`（写档时未把真实 `-k` 表达式存入），不可按原样复现。原始输出保持原样；
  下面给出**逐 finding 的真实测试节点 ID**与文末**重建的整组 `-k`**。
- 计数口径：归档时（19:53/19:55）整组选中 **38** 个用例；重建表达式比它多
  `test_cumulative_ops_used_resolves_import_alias`（该用例在原始输出捕获后才加入
  `ea2ebfe`），故今天整组复跑为 **39 passed**（deselected 数随测试面增长而变化：
  归档当时 98、R02 复跑时 106）。复跑验证见
  `docs/verification/R22/R02/evidence/i9-rerun-eng.txt`。

## 逐 finding 精确复跑

### R01-ENG-C1（负下标 = 未来函数）

```bash
cd platform && .venv/bin/python -m pytest -q \
  tests/test_partitions.py::test_rejects_negative_subscript \
  tests/test_partitions.py::test_rejects_negative_subscript_via_top_level_const \
  tests/test_partitions.py::test_allows_forward_and_zero_and_unknown_subscript \
  tests/test_run_factor.py::test_run_factor_rejects_negative_subscript \
  tests/test_run_factor.py::test_run_factor_accepts_positive_subscript
```

证据：`C1-probe-before.txt`、`C1-probe-after.txt`（`probe_eng.py C1`）、
`C1-pool-probe-after.txt`（`probe_c1_pool.py`，池公式路径补充）；probe 前后对照见 `pytest-before.txt`。

### R01-ENG-C2（名常量递归折叠：Not/Unary/BinOp + import 别名）

```bash
cd platform && .venv/bin/python -m pytest -q \
  tests/test_partitions.py::test_rejects_negative_shift_via_name_inside_unary_and_binop \
  tests/test_partitions.py::test_rejects_negative_shift_via_alias_and_name_const \
  tests/test_partitions.py::test_allows_positive_folded_shift_via_name_const \
  tests/test_run_factor.py::test_run_factor_rejects_negative_shift_via_named_const \
  tests/test_run_factor.py::test_run_factor_accepts_positive_named_const_shift
```

证据：`C2-probe-before.txt`、`C2-probe-after.txt`。

### R01-ENG-I1（分块 × 累计算子 fail fast）

```bash
cd platform && .venv/bin/python -m pytest -q \
  tests/test_run_factor.py::test_chunk_days_rejects_cumulative_operator \
  tests/test_run_factor.py::test_chunk_days_rejects_vwap_macro_expansion \
  tests/test_run_factor.py::test_chunk_days_rejects_cumulative_in_pool_formula \
  tests/test_run_factor.py::test_chunk_days_allows_window_operator_control \
  tests/test_run_factor.py::test_cumulative_ops_used_resolves_import_alias
```

证据：`I1-probe-before.txt`、`I1-probe-after.txt`。

### R01-ENG-I2（插件 AST 扫描封 `from os import ...` 形态）

```bash
cd platform && .venv/bin/python -m pytest -q \
  tests/test_ops.py::test_plugin_importfrom_bypass_rejected \
  tests/test_ops.py::test_plugin_importfrom_side_effect_blocked_before_import
```

证据：`I2-probe-before.txt`、`I2-probe-after.txt`（marker 文件未创建 = import 前拦截）。

### R01-ENG-I3（插件同名冲突前移到 import/副作用之前）

```bash
cd platform && .venv/bin/python -m pytest -q \
  tests/test_ops.py::test_builtin_override_rejected_before_import_without_force
```

证据：`I3-probe-before.txt`、`I3-probe-after.txt`（字面量装饰器形态：
`ts_mean` 版本保持 0.1.0 而非 9.9.9）。
R02-I7 延续：**import 别名 / 命名常量 / 运行期拼接名**形态在此门之后补齐
（前两者前移、后者 import 后快照回滚），证据见 `docs/verification/R22/R02/plugins/`。

### R01-ENG-I4（`cs_resid` canonical + `cs_regression_resid` 别名改写）

```bash
cd platform && .venv/bin/python -m pytest -q \
  tests/test_polars_ta_wrappers.py::test_cs_resid_registered_canonical_with_regression_alias \
  tests/test_polars_ta_wrappers.py::test_cs_resid_and_alias_compute_real_values
```

证据：`I4-probe-before.txt`、`I4-probe-after.txt`、`catalog-drift-note.txt`
（该 note 是修复中间态：`platform/docs/catalog.md` 的再生成实际已随 `ea2ebfe` 提交，
`tests/test_catalog.py` 全绿见 `required-suites-after.txt`）。

### R01-ENG-I5（`factorlab lint` 接引擎同序语义门）

```bash
cd platform && .venv/bin/python -m pytest -q \
  tests/test_cli_lint_params.py::test_lint_rejects_unknown_operator \
  tests/test_cli_lint_params.py::test_lint_rejects_negative_shift_via_named_const \
  tests/test_cli_lint_params.py::test_lint_rejects_future_subscript \
  tests/test_cli_lint_params.py::test_lint_rejects_future_shift_in_pool_formula \
  tests/test_cli_lint_params.py::test_lint_accepts_positive_subscript \
  tests/test_cli_lint_params.py::test_lint_rejects_review_probe_specs
```

证据：`I5-probe-before.txt`、`I5-probe-after.txt`、`lint-factors-after.txt`
（`make lint-factors` → 152 通过 / 0 失败）。

## 整组复跑（重建 `-k`，替代占位符）

```bash
cd platform && .venv/bin/python -m pytest -q \
  tests/test_partitions.py tests/test_polars_ta_wrappers.py tests/test_cli_lint_params.py \
  tests/test_ops.py tests/test_run_factor.py \
  -k 'test_allows_forward_and_zero_and_unknown_subscript or test_allows_positive_folded_shift_via_name_const or test_builtin_override_rejected_before_import_without_force or test_chunk_days_allows_window_operator_control or test_chunk_days_rejects_cumulative_in_pool_formula or test_chunk_days_rejects_cumulative_operator or test_chunk_days_rejects_vwap_macro_expansion or test_cs_resid_and_alias_compute_real_values or test_cs_resid_registered_canonical_with_regression_alias or test_cumulative_ops_used_resolves_import_alias or test_lint_accepts_positive_subscript or test_lint_rejects_future_shift_in_pool_formula or test_lint_rejects_future_subscript or test_lint_rejects_negative_shift_via_named_const or test_lint_rejects_review_probe_specs or test_lint_rejects_unknown_operator or test_plugin_importfrom_bypass_rejected or test_plugin_importfrom_side_effect_blocked_before_import or test_rejects_negative_shift_via_alias_and_name_const or test_rejects_negative_shift_via_name_inside_unary_and_binop or test_rejects_negative_subscript or test_rejects_negative_subscript_via_top_level_const or test_run_factor_accepts_positive_named_const_shift or test_run_factor_accepts_positive_subscript or test_run_factor_rejects_negative_shift_via_named_const or test_run_factor_rejects_negative_subscript'
```

期望：**39 passed**（归档时 38 selected、deselected 98；deselected 数随测试面增长，见文首占位符说明）。

## 必跑套件（R21 归档）

```bash
cd platform && .venv/bin/python -m pytest -q \
  tests/test_ast_gate.py tests/test_compute.py tests/test_cli_lint_params.py \
  tests/test_cli_op_registry.py tests/test_cli.py tests/test_partitions.py \
  tests/test_ops.py tests/test_plugin_run_path.py tests/test_plugin_partition_prefix.py \
  tests/test_polars_ta_wrappers.py tests/test_pool_formula.py tests/test_platform_ops.py \
  tests/test_universe_aware_formula.py tests/test_minute_gate.py \
  tests/test_m6_semantic_guards.py tests/test_outputs_multi.py tests/test_run_factor.py \
  tests/test_chunk_label_exactness.py tests/test_suspension_chunk_invariance.py \
  tests/test_qfq_chunk_invariance.py
```

原始输出：`required-suites-after.txt`（403 passed, 1 warning；含已知 polars `pivot(columns=...)`
DeprecationWarning，非本轮引入）。

## 文件清单

| 文件 | 说明 |
|---|---|
| `probe_eng.py` | 各 finding 复现/验证 probe（`C1`/`C2`/`I1`...`I5`/`all`）；同脚本前后各跑一次 |
| `probe_c1_pool.py` | C1 池公式路径补充 probe（`universe.formula` 同链） |
| `C1-probe-before/after.txt`、`C1-pool-probe-after.txt` | C1 原始输出 |
| `C2-probe-before/after.txt` | C2 原始输出 |
| `I1..I5-probe-before/after.txt` | I1-I5 原始输出 |
| `pytest-before.txt` / `pytest-after.txt` | 新增测试整组红→绿（27 failed/11 passed → 38 passed；命令占位符见文首） |
| `required-suites-after.txt` | 必跑套件全绿原始输出 |
| `lint-factors-after.txt` | `make lint-factors`（152/152） |
| `catalog-drift-note.txt` | I4 中间态记录（catalog.md 由 `ea2ebfe` 已同步） |

## 未解决点 / 偏差（如实）

1. `pytest-before/after.txt` 的命令占位符不可原样复现——本 README 用真实节点 ID 重建；
   原始文件不改，重建的整组复跑为 39 passed（+1 用例，见文首）。
2. `catalog-drift-note.txt` 记录的"catalog.md 未改"只是写档时中间态；`ea2ebfe` 已同步，
   `tests/test_catalog.py` 全绿（`required-suites-after.txt`）。
3. 本目录不含 R02 对插件的延续修复证据（别名/动态注册）——见
   `docs/verification/R22/R02/plugins/`。
