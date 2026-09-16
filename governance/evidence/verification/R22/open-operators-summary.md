# R22 开放算子底座（Plan 1）实施证据总览

**实施范围**：`docs/reviews/2026-09-15-open-operators/plan.md` Task 1–9（TDD 逐任务）。
> R24 路径映射（2026-09-16）：`docs/reviews/2026-09-15-open-operators/` → `knowledge/design/workspace/2026-09-15-open-operators/`（本文正文为历史冻结，保留当时路径）。
**证据根**：本目录。基线（改动前）在 `00-baseline/`；其余按任务 `0N-*/`。
**全门结果**：见 `10-regression/gates.txt`（`make gates` exit 0）、
`10-regression/full_pytest.txt`（**2871 passed / 13 skipped**，967s）、
`10-regression/lint_factors.txt`（**152/152**）。

## 任务索引

| # | 内容 | commit（platform） | 证据 |
|---|---|---|---|
| 基线 | 6 代表 spec 改动前 ch 跑 + 数据面说明 | `72340c8`(docs) | `00-baseline/` |
| 1 | OpMeta 分类模型与查询 API | `e1e8e0b` | `01-opmeta/` |
| 2 | polars_ta 全量分类表（381 条，签名感知 + --check） | `df6851f` | `02-ta-catalog/` |
| 3 | polars 方法/访问器分类表（版本锁；88/39/96） | `95ffbae` | `03-polars-methods/` |
| 4 | 统一语义推断 pass（NodeInfo/infer/SemanticError） | `3e3309b` | `04-semantics/` |
| 5 | 未来门统一（全形态 + 行:列） | `555f93c` | `05-future-gate/` |
| 6 | 窗口/分块统一（required_lookback/unbounded_ops） | `28a6334` | `06-chunk/` |
| 7 | 拆除注册闸门 + 分区绑定规范化（核心交付） | `c7b3d66` | `07-open-gate/` |
| 8 | lint 接入完整静态管线 | `3df4f45` | `08-lint/` |
| 9 | 152 零迁移回归 + 全门 | `daec29b` | `10-regression/` |

## 实测数字

- **分类表规模**：`default_catalog` = **512** 条（polars_ta 380 / polars 方法 127 /
  平台 5；partition：ts 255 / el 223 / cs 32 / gp 2）；`effective_catalog`（+注册面）
  = **528**（另有 im 7 / day 6）。**开放面新增可用库函数 = 346**（注册清单 55 之外）。
- **零迁移**：6 代表 spec 改动前后逐值对比，`ic.{mean,t_stat,ir}` 全部 |Δ| = 0，
  `n_weeks` 相等（`10-regression/ic_delta.tsv`）。
- **未来门**：12 种未来形态全部拒绝（含方法窗/常量折叠/嵌套），4 种合法对照放行；
  真实 CLI 复验见 `10-regression/future_gate_cli.txt`。

## 与 plan.md 的偏差（全部已在各任务 README 记录）

1. **`ts_quantile` 在 polars_ta 0.5.17 不存在**（三库 vars 实测）→ Task 2/6/7/8
   相关用例改用真实未注册库函数（`ts_arg_max`/`ts_corr`/`ts_weighted_mean`/`BBANDS`）。
2. **窗口判定改签名扫描**：计划用"第 2 个必需参数为 int"，但库函数窗口参数几乎都带
   默认值且可位于 arg:2/3 → 改为扫描位置参数第一个窗口名/`int` 注解（保留 float
   正式窗口名 timeperiod 特判、单字母 n/d 阈值不误判）。
3. **嵌套 lookback 取求和口径**（25，而非计划字面 24）：与旧 `_ts_window_days`
   一致（预热只多不少，零迁移安全）。
4. **`_ts_window_days` 保留旧前缀回退腿**：插件 `ts_*` 算子与 `wq.ts_sum` 模块限定
   名的窗口提取是既有测试契约，推断腿不能覆盖 → `max(前缀回退, 推断)`。
5. **`infer` 接受 AST**（计划测试的两棵树 `id()` 失配）、变量引用链解析、
   `strict_unknown` 兼容模式（未知算子归 validate 管）。
6. **`default_catalog` 增加平台元数据**（cs_stable_rank/gp_rank/gp_mean 等）；
   **注册面 overlay**（registry 修订号缓存）供插件/分钟算子进入有效分类表。
7. 计划 render 漏写 `ROWS` 收尾 `]`（生成文件语法错误）→ 已补。
8. Baseline 6 spec 因 CH 数据面限制（无 stock_st、`pb`/`circ_mv` 全 null、
   `index_daily` 空）替换 2 个等价代表：`value/bp` → `reversal_20d/wcorr`，
   `crash_bottom_leader` → `liquidity/accel`；6 份 spec 的 `exclude_st` 在证据副本中
   移除（formula/date/params 逐字不变），详见 `00-baseline/README.md`。

## 未解决 / 存疑点（Plan 2/3 范围）

1. **方法窗体端到端**：`ast_gate.ALLOWED_EXPR_METHODS` 仍只放行 7 个元素级方法，
   `close.rolling_mean(5)` 在 compute/lint 报"禁止属性调用"（不是"未知算子"）。
   分类表（39 TS/88 EL）与 infer 已就绪，开放 AST 方法门需与 expr_codegen 属性调用
   重写同批落地（Plan 2/3）。
2. **通达信 REF 模式族窗口未知**（`ts_早晨之星`/`ts_四串阳`/`ts_单日放量` 等，
   签名无可推断窗口 → window=None）：整段跑正确，分块时块边界 REF 预热不足；
   归 Plan 2 conformance（算子档案/窗口声明）。
3. **keyword-only 窗口算子**（`ts_resid`/`ts_pred`）保守记 unbounded（分块 fail fast
   而非静默欠预热）；Plan 2 的 op_meta/算子档案可声明更精确窗口。
4. **依赖 scipy 的算子**（如 `ts_partial_corr`）冒烟通过但运行时报 ImportError；
   conformance 套件（Plan 2）应把它标为不可用或补依赖。
5. **`factorlab op list` 仍是注册面视图（55）**；设计 §4.3 的 "op list ≥400" 与
   G8 文档同源（catalog 生成）属 Plan 2。
6. `cumulative_eval`/`ts_OBV` 等 unbounded 判定为静态近似（分类表人工覆盖），
   Plan 2 conformance 的截断重放可进一步验证。
