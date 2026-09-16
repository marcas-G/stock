# 01 open-operators Plan 2/3 后置项

- 审计时间：2026-09-16｜只读
- 来源文档：
  - `knowledge/design/workspace/2026-09-15-open-operators/design.md` §5（算子生命周期）、§5.2（op_meta）、§5.3（算子档案）、§5.4（conformance）、§6（截面表达 by=）、§9-G6/G7/G8/G9
  - `.../plan.md` Self-Review 表（「§5 算子生命周期 → Plan 2」「§6 截面 → Plan 3」）与末尾「Execution Handoff」（Plan 1 完成后写 Plan 2/3）
  - `governance/evidence/verification/R22/open-operators-summary.md` §「未解决/存疑点（Plan 2/3 范围）」6 条
- 状态标记：Plan 1 = 已验收（R22，512 分类面/528 effective；证据 R22/10-regression）

## 实测矩阵

| ID | 事项 | 来源 | 现状实测 | 判定 |
|---|---|---|---|---|
| A1 | `op_meta` 黑盒声明 | design §5.2/§5.4 | lint 非空 → exit 1「op_meta 暂未支持（Plan 2）」（spec.py:136-140） | 开放 |
| A2 | conformance 套件 | design §5.4/§9-G9 | `grep -r conformance platform/src` = 0 | 开放 |
| A3 | 算子档案 `research/ops/` | design §5.3 | 目录不存在；`op doc` 注明「算子档案/字段访问归 Plan 2」 | 开放 |
| A4 | 插件元数据要求 | design §5.4 方式③ | `op add` 仅 path/--force；窗口固定 arg:1 | 开放 |
| A5 | 方法窗体端到端 | R22 §未解决 1 | `close.rolling_mean(5)` lint exit 1「禁止属性调用」（白名单仅 7 元素方法） | 开放 |
| A6 | struct 字段访问 `.upperband` | R05-I1 残余 | 属性调用被 AST 门拒（与 A5 同门） | 开放 |
| A7 | catalog 完整同源 | R22 §未解决 5 | `catalog dump` registry_inventory=55；`catalog.md` 无分类面条目 | 开放（同 E2） |
| A8 | REF 族窗口未知 | R22 §未解决 2 | `ts_早晨之星/ts_四串阳/ts_单日放量` window=None | 开放 |
| A9 | keyword-only 窗口保守 unbounded | R22 §未解决 3 | `ts_resid/ts_pred` unbounded（分块 fail fast） | 开放 |
| A10 | scipy 依赖算子可见不可运行 | R22 §未解决 4 | `ts_partial_corr` lint OK、compute → ImportError（venv 无 scipy） | 开放 |
| A11 | unbounded 静态近似待重放复核 | R22 §未解决 6 | 无 conformance 截断重放 | 开放 |
| B1 | Plan 3：`by=` + agg/rank/clip/cut/dist/proj/mask | design §6 | `rank(close, by=date)` exit 1 未知算子；engine 无 by= 实现 | 开放 |
| B2 | Plan 3：数据可用性检查 | design §9-G7 | 无输入声明/覆盖检查 | 开放 |

## 触发性判定

- **口径已燃**：Plan 1 已验收（R22），交接条件"Plan 1 完成后：写 Plan 2/3"已满足，
  但 `knowledge/design/platform/plans/` 无 Plan 2/3 文档、代码无落地痕迹。
- 与 Plan S/minutes 不同，这两项没有"等数据/等复盘"的挂起理由，属**应排期未排期**。

## 用户影响（对"写任意因子"目标）

1. 截面/分组表达仍只能是 cs_/gp_ 有限名单组合；"通用分组 + 少量原语"（design §6 路线）
   未兑现——新想法的表达成本高（if_else 嵌套、白名单限制）。
2. 黑盒/外部函数无 `op_meta` 通道；未知算子只能改写为 `def` 组合。
3. 分类面混有"可见但不可运行"（A10）/"可见但不可安全分块"（A8/A9）算子，且
   `lint` 不揭示——用户只能在运行期撞错。

## 证据文件

- `transcripts/lint_plan2_opmeta.txt`（A1）、`lint_plan2_method.txt`（A5）、
  `lint_plan3_by.txt`（B1）、`lint_plan3_cut.txt`（B1 对照：polars_ta el cut）、
  `probe_lint_vs_run.txt`（A10）、`catalog_dump_summary.txt`+`op_doc_ts_cum_count.txt`（A3/A7）
- 代码定位：`platform/src/factorlab/core/spec.py:136`、`core/ops/registration.py:38-55`、
  `scripts/gen_op_catalog.py:51-104`（MANUAL_OVERRIDES=已知近似清单）、
  `core/engine/semantics.py:236`（方法档案 Plan 2 文案）
