# R03-I7 证据 — 陈旧尾部 bar 守卫（`has_trade` 便捷列）+ 布尔连接门

**Finding**：分钟 bar 238/239 常为 `amount=0` 的陈旧 OHLC；无守卫时"触高时间"类因子
截面被系统性后移（002721.SZ 2024-02-08：239/239 → 229/239）；funnel 文档示例用
`AND` 写法不可用（`&` 被 AST 门拒）。

**修法**（提交 `fix(platform): R03-I7`）：

1. `compute_minute_factor_panel` 按公式引用派生 `has_trade`（= `amount > 0`，逐分钟
   序列；不进折日输出/用户列）；引擎 `_bars_needed_cols` 引用时增读 `amount`。
2. AST 门：`and`/`or`/`not` 前置拒绝（expr_codegen 执行期必崩 TypeError），
   给出嵌套 `if_else` / `has_trade` 可用写法；`&` 维持拒绝（实测优先级陷阱
   `a > 0 & b > 0` 解析为链式比较静默错值——见下"& 结论"）。
3. 文档：1m-funnel spec（R10/B6.5）、interface.md（分钟注入列 + 池公式示例）、
   catalog（错误手册两条修法）、主设计文档 `&` 示例 → 可用写法。

**`&` 白名单结论：不放行（维持 AST 门拒绝）**。实测：expr_codegen 对 BitAnd 支持
正常（`(a>0) & (b>0)` codegen OK），但无括号写法 `a > 0 & b > 0` 被 Python 解析为
链式比较 `a > (0 & b) > 0`，codegen 后静默全 false（不报错）——静默错值是 AST 门
要防的风险；`and`/`or`/`not` 则根本无法求值。分钟守卫由 `has_trade` + 嵌套
`if_else` 完整覆盖，无需放宽节点白名单（开放算子 Plan 2/3 未涉及 AST 节点面）。

## 文件与命令

| 文件 | 内容 / 命令 |
|---|---|
| `probe_i7_has_trade_real_ch.py` / `.txt` | 真 CH 探针：`cd platform && .venv/bin/python ../docs/verification/R22/R03/minute/probe_i7_has_trade_real_ch.py` → 无守卫 239/239、守卫 229/239；`amount>0`≡`volume>0` |
| `real_ch_engine_run.txt` | 真 CH CLI 引擎链：`FACTORLAB_DATA_BACKEND=ch factorlab run i7_run_spec.yaml --no-backtest` → signal=0.958159（229/239）、无守卫 1.0（239/239）、产物列无 `has_trade` |
| `tdd_red.txt` | 隔离 worktree（HEAD 47fe5be 源码 + 新测试）→ 4 failed（3 has_trade + 1 布尔连接）；1 passed 是正向控制 |
| `tdd_green.txt` | 同 worktree + 本提交内容 → 143 passed（required 6 文件） |
| `lint_factors_159.txt` | 主工作树 `make lint-factors` → **159 通过 / 0 失败**（含在途 mining specs） |

隔离 worktree 回归（同 `PYTHONPATH=<wt>/platform/src`）：`test_run_factor` /
`test_e2e_free_form` / `test_platform_ops` / `test_minute_ops` /
`test_minute_coverage` / `test_cli_lint_v2` / `test_cli_lint_params` /
`test_cli_run` / `test_universe_aware_formula` → **176 passed, 2 skipped**。

## 测试锚点（新增）

- `tests/test_ast_gate.py::test_rejects_boolean_connectors_with_if_else_guidance`
- `tests/test_ast_gate.py::test_boolean_connector_rejection_does_not_block_comparisons`
- `tests/test_minute_engine.py::test_minute_has_trade_guard_excludes_stale_tail_bars`
- `tests/test_minute_engine.py::test_minute_has_trade_derivation_requires_amount_column`
- `tests/test_minute_engine.py::test_minute_has_trade_engine_guard_reads_amount_and_no_output_leak`

## `has_trade` 定义（写进 docstring 与 interface.md）

`has_trade = amount > 0`：该分钟有真实成交。真 CH 实测（002721.SZ 2024-02-08）
零成交行 `amount=0 且 volume=0`（140/240 行），`amount>0` 与 `volume>0` 0 行不一致。
逐分钟序列、按公式引用派生（无引用零行为变化）、只进公式作用域，不进折日输出/
用户列（`compute_formula` 输出 `select [date, code, *outputs]` 兜底）。
