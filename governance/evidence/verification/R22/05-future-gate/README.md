# Task 5 — 未来函数门统一到语义推断（全形态）

**命令**
- 测试：`cd platform && .venv/bin/python -m pytest tests/test_future_gate_v2.py tests/test_partitions.py tests/test_m6_semantic_guards.py -q`
- 红：`before.txt`（collection error：`check_causality` 不存在）；绿：`after.txt`（151 passed）

**实现**：`engine/partitions.py` 新增 `check_causality(source, catalog=None, params=None, *,
strict_unknown=True)`——单源调用 `semantics.infer`，`forward > 0` 且由本节点引入
（嵌套只报最内层）→ `FactorDSLError("... 不允许负位移（lookback 只能取过去）", 行:列)`。
`reject_future_shifts(source)` 保留为兼容入口（`strict_unknown=False`）。

## 测试覆盖（12 拒绝 + 4 合法，逐条）

拒绝：`ts_delay(close,-1)`、`_n=3;-ts_delay(close,-_n)`、`ts_delay(close,1-3)`、
`close[-1]`、`close[-_n]`、`close.shift(-1)`、`close.diff(-2)`、
`close.rolling_mean(-5)`、`ts_rank(close,-20)`、`ts_delta(close,-_n)`、
`close.pct_change(-1)`、`close[-2] + open`
（后两条为本轮补足：方法窗体与复合表达式）。
合法：`close[-0]`、`close[1]`、`close.shift(1)`、`_n=3;ts_delay(close,_n)`。
另：行列定位（单行/第二行）、嵌套最内层报错、未知算子两种入口语义。

## 与 plan.md 的偏差

1. **常量折叠单源放 semantics.py**：计划说"复用 partitions.py 的
   `_fold_consts/_top_level_consts`，传入 semantics"，但 Task 5 后 partitions 反向
   依赖 semantics（check_causality → infer），反向 import 会循环。折叠函数随推断
   层单源（partitions 旧副本删除），行为逐条同旧实现（同名同算法）。
2. **`check_causality` 的 `strict_unknown`**：未知算子/方法在旧入口（reject_future_shifts）
   放行（既有分工：未知归 validate 管；`test_partitions` 的 `ts_delay(close, n)` /
   `close[n]` / 插件算子用例必须继续通过），但**未知算子包裹的负位移仍被抓住**
   （旧门独立遍历的等价行为，测试锁定）。
3. **float 负窗口**：旧门接受 `-1.0`（`shift < 0` 判定）；窗口求值对 float 常量折叠
   同样放行并由 `int(-w)` 进 forward（`test_partitions.py::test_rejects_float_negative_delay` 通过）。
4. `test_error_location_on_later_line` 用直接负窗（`ts_delay(close,-2)`）而非变量间接：
   旧门与推断层都不穿透非顶层常量的变量（`_x = close+1`），写成变量间接属测试笔误。
