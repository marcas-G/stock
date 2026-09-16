# Task 4 — 统一语义推断 pass（NodeInfo）

**命令**
- 测试：`cd platform && .venv/bin/python -m pytest tests/test_semantics.py -q`
- 红：`before.txt`（collection error：`semantics` 不存在）；绿：`after.txt`（17 passed）

**接口**：`NodeInfo(level, keys, order, lookback, forward, unbounded)`；
`infer(source: str | ast.AST, catalog, params=None) -> dict[id(ast.Node), NodeInfo]`；
`SemanticError(FactorDSLError)`（带 行:列）。

## 与 plan.md 的偏差（逐条理由）

1. **`infer` 接受已解析 AST**：计划测试 `info()` 里先 `infer(src)` 再自己
   `ast.parse(src)` 用 `id()` 查表——两次 parse 是两个对象，`id()` 必然失配
   （地址复用纯属偶然）。改为 `infer` 可直接吃 `ast.AST`（字符串路径不变），
   测试用同一棵树查询。
2. **嵌套窗口口径 = 求和（父窗 + 子回看）**：计划字面值 24（父+子-1），但旧
   `_ts_window_days`（预热来源）口径为 25；取 24 会让分块预热比现状少 1 天
   （零迁移风险，可能改变 chunked 结果）。取 25，`tests/test_semantics.py`
   断言同步；Task 6 的 `required_lookback >= 24` 仍成立。
3. **`gp_rank` 参数序按平台契约 `gp_rank(key, x)`**（platform_ops.py:51，
   掩码表 `gp_rank=(1,)` 即数据在 arg1），计划测试写成 `(x, key)` 已纠正为
   `gp_rank(industry, ts_mean(close, 20))`。
4. **`default_catalog()` 增加平台算子元数据 `PLATFORM_OP_META`**
   （`cs_stable_rank/cs_rank/cs_mean` mask(0,)、`gp_rank/gp_mean` mask(1,)）：
   计划测试要求 `gp_rank` 可推断，而 polars_ta 无 gp_ 函数、平台 gp 算子不在
   生成表——不加则 Task 4 测试（以及 Task 7 掩码）不成立。分类表仍是纯数据。
5. **窗口非常量保守放行**（不报 SemanticError）：plan §Task4 文字要求
   "arg:N 非常量 → SemanticError"，但既有门 `tests/test_partitions.py`
   （`ts_delay(close, n)` / `close[n]` 必须放行）与零迁移要求冲突。
   实现取保守放行（lookback 0）；负常量/可折叠负值仍进入 forward 由未来门拒绝。
6. **keyword 窗口**：`ts_delay(close, d=-1)`（既有 test_partitions 用例）与
   `.shift(n=-1)` 的窗口经 keyword 窗口名（d/n/window/window_size/period/
   length/timeperiod）常量折叠求值。

## 覆盖的推断形态（测试名）

`test_ts_window` / `test_nested_ts_lookback_additive` / `test_cs_of_ts_level_cs` /
`test_gp_keys` / `test_method_window` / `test_unbounded` / `test_unknown_operator_message` /
`test_denied_method_guidance` / `test_unknown_method_message` /
`test_elementwise_inherits_outermost_level` / `test_subscript_lookback_and_forward` /
`test_negative_window_sets_forward_with_const_folding` /
`test_forward_propagates_through_nesting` / `test_alias_import_resolved` /
`test_defined_function_calls_inherit_children` / `test_error_carries_location`。
