# Task 7 — 拆除注册闸门 + 分区绑定规范化（核心交付）

**命令**
- 单元：`cd platform && .venv/bin/python -m pytest tests/test_open_ops_e2e.py -q`
  → 红 `before.txt`（ImportError: normalize_calls）；绿 `after_unit.txt`（8 passed）
- 计划 Step 4：`pytest tests/test_open_ops_e2e.py tests/test_run_factor.py tests/test_platform_ops.py -q`
  → 绿 `after_plan_step4.txt`（99 passed）
- 广域回归：universe_masking/plugin/minute/outputs/attributes/catalog/stable_rank/free_form
  → 145 passed, 2 skipped
- `make lint-factors` → 152 通过 / 0 失败

**接口**：`normalize_calls(source, catalog, params=None) -> (source, import块)`；
`registration.effective_catalog()`（生成表 + 平台元数据 + **注册面全集**，registry
revision 缓存失效）；`universe_masking.apply_universe_masking(source, mask, catalog=None)`
（给定 catalog 时 mask_args 按分类表，缺省时保留旧 registry 静态表路径）。

## 实现要点

1. **闸门拆除**：`compute_formula` 不再调 `validate_partition_calls`（registry 白名单），
   改为 `normalize_calls`（semantics.infer strict）——380 条 polars_ta + polars 方法 +
   平台/插件/分钟注册面全部放行；未知 → `未知算子 <name>；... op_meta`（保留"未知算子"
   字样，CLI lint 测试契约）。
2. **canonical 改名通道**：`meta.canonical != 调用名` → AST 改写 + import 块返回；
   Plan 1 生成表 canonical 均= 原名，通道预留给 Plan 2 算子档案。生成代码的 import
   解析由 expr_codegen 的 `from polars_ta.prefix.{wq,ta,tdx} import *` 与
   `extra_codes`（平台/插件）承担。
3. **mask_args 分类表化**：cs/gp 数据参数位置查 `OpMeta.mask_args`（cs_resid 等
   override 保证存量 (0,1) 语义）；未声明 → fail fast（消息保留 canonical 名）。
4. **注册面 overlay**：`registry` 加修订号；`effective_catalog()` 按修订号浅拷贝
   生成表并补注册面条目（ts/ta 窗口按命名契约 arg:1，cs/gp 掩码默认 (0,)/(1,)，
   im/day 不声明窗口）——插件与分钟算子通过正式校验，测试重置后不残留。
5. `check_causality(formula, catalog)` 接替 reject_future_shifts（strict，未知已在
   归一化门拒绝）。

## 证据（e2e 测试名）

`test_open_ts_op_computes`（`ts_arg_max`——此前必报"未知算子"，现真实算出 UInt 索引）、
`test_open_uppercase_op_computes`（`BBANDS`→struct upperband 有值）、
`test_unknown_op_guides_op_meta`、`test_normalize_calls_returns_source_and_imports`、
`test_normalize_calls_rejects_unknown`、`test_new_cs_op_masked_via_catalog`
（`cs_minmax` 截面 minmax：A=0/B=1/C 非成员 null——证明 mask 与真实截面计算）、
`test_registry_ops_join_effective_catalog`、`test_catalog_copy_is_isolated`。

## 已知边界（不阻塞 Plan 1 验收）

- **方法窗体端到端**仍被 `ast_gate.ALLOWED_EXPR_METHODS`（7 个元素级方法）挡在
  compute_formula 之外；Task 4 的方法推断经 `infer` 单测覆盖。开放 AST 方法门
  （分类表 EL/TS 白名单放行）属 Plan 2/3（涉及 ast_gate 与 expr_codegen 属性调用
  重写），本轮不扩大改动面。
- `ts_partial_corr` 等依赖 scipy 的算子进入分类表（冒烟通过）但运行时缺依赖会报
  ImportError——属算子 conformance（Plan 2）范围。
