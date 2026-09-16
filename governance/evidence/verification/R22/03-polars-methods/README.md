# Task 3 — polars 方法/访问器分类表（版本锁）

**命令**
- 生成/校验：`.venv/bin/python scripts/gen_op_catalog.py [--check]`
- 测试：`cd platform && .venv/bin/python -m pytest tests/test_op_classification.py -q`
- 红：`before.txt`（4 failed：产物模块/指引函数不存在）；绿：`after.txt`（18 passed）

**分桶**（polars 1.44.1，`dir(pl.Expr)` 公开名 223 个）：
- EL（逐元素/访问器命名空间）= 88
- TS（窗口/位移/累计）= 39（`shift/diff/pct_change`→`arg:0`；`rolling_*`→`arg:0`；
  `rolling_*_by`→`arg:1`；`cum_*`/`ewm_*`/`cumulative_eval`/`peak_max/peak_min`/
  `forward_fill`→unbounded）
- DENIED（上下文歧义/聚合/选行/未来/任意回调）= 96（全部带指引文本）

## 与 plan.md 的偏差

1. 计划的版本锁测试直接对三组做集合运算：`EL | TS | DENIED`——要求三者都是 set。
   产物因此导出 `EL_METHODS/TS_METHODS/DENIED_METHODS` 三个 **frozenset**，窗口信息
   单独放 `TS_WINDOWS` dict、指引放 `DENIED_GUIDANCE` dict（测试改用 `set(...)` 包裹，
   断言语义不变）。
2. 拒绝桶 96 > Spike 2 的"35 需人工判定"：Spike 的 35 只统计了分组/排序族；本实现把
   **整列聚合**（`max/mean/std/sum/...`）、**选行**（`head/tail/slice/gather/get`）、
   **结构操作**（`explode/implode/reshape`）、**未来/顺序敏感**（`backward_fill/
   interpolate/reverse/rle`）、**任意回调**（`map_elements/map_batches/pipe`）一并
   拒绝——它们在 expr_codegen 的 per-asset `.over()` 包装下语义随上下文变化，属于
   设计 §4.2"语义不明确 → 报错并指引"的同一族。开放面由函数形式（380 条 polars_ta）
   承担；Plan 3 的 `by=` 分组会替换聚合族。
3. `closed` 的窗口判定：`rolling`（按时间周期）无法静态解析 → 拒绝而非 ts/None，
   避免分块欠预热；`cum_*` 与 `ewm_*` 判 unbounded（累计算子与分块互斥，见 Task 6）。
