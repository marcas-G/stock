# 07 因子评估后置项现状

- 审计时间：2026-09-16｜只读
- 来源：`knowledge/contracts/interface.md` :358（target）、:359-373 + :746-753（M2 多输出）、
  :1638-1641（v2 loader）；`governance/workspace/pending-items.md`（无评估相关未决行）

## 实测

| ID | 事项 | 现状 | 判定 |
|---|---|---|---|
| H1 | `target=forward_return_20d` | 契约支持（`forward_return_5d | forward_return_20d`）；`app/evaluate.py` 用 `spec.target`（:59/:63/:76/:80 逐输出）；测试 `test_eval_rust_ic.py:141-152`（target 字段 + 20d 数值）、`test_web.py:249-271`（Web 详情页 20d IC 曲线，R01-EVAL-I1 修复）；lint 20d spec 实测 OK | **已闭环** |
| H2 | 多输出评估（M2） | 一趟 compute 全输出 + per-output process/eval/summary；`test_outputs_multi.py` 端到端（per-output parquet/summary，无 signal.parquet）；lint `outputs: [momentum, gap]` 实测 OK | **已闭环（评估侧）** |
| H3 | 多输出 per-output loader（v2 目录） | `load_signal_artifact` 对 `MULTI_ARTIFACT_FORMAT_VERSION=2` 报 `unsupported artifact format version 2——per-output loader 在后续里程碑提供`（parquet_artifacts.py:431）；无 per-output 读取实现 | **开放**（interface :1638-1641 已登记） |
| H4 | regime 多输出在策略链消费 | strategy 链只读 v1 `signal.parquet`；G1 挂起（见 02-C3）；H3 是其前置 | 条件未燃 |

## 备注（避免误报）

- 研究侧 0 个因子 spec 实际使用 `target: forward_return_20d` 或 `outputs:`（grep research/factor = 0）——
  这是**使用现状**而非登记未做项：能力与测试都在，用户随时可用。
- `factorlab resic` 对多输出 panel 报错退出（interface :85 明示"错误路径 Exit 1"）——
  属**明确的拒绝语义**，不是静默缺口；与 H3（loader）是不同层面的限制。

## 用户影响

- H1/H2 可用：20d 目标评估与多输出因子评估/落盘都真实可用；
- H3：多输出因子的产物目前只能通过 `panel.parquet`（legacy view）或 `result.signals[...]`
  （同进程）消费；跨进程/下游（策略链、工具）读取 v2 目录会被明确拒绝——
  即"多输出因子可算、可评估，但不可被策略链消费"。这是 G1（regime 多输出策略）的技术前置。

## 证据文件

- `transcripts/lint_target20d.txt`、`lint_multi_output.txt`
- `specs/eval_target20d.yaml`、`specs/eval_multi_output.yaml`
- 代码定位：`platform/src/factorlab/adapters/parquet_artifacts.py:424-433`（v2 拒绝 +
  per-output loader 承诺）、`app/evaluate.py`（target/per-output）、
  `core/engine/compute.py`（M2 共享 pass）
