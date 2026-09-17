# R30 评估指标 v2 — 批 1：Task 0 + Task 5（口径批）

- 计划：`knowledge/design/platform/plans/2026-09-16-factorlab-eval-metrics-v2.md`（执行顺序第 1 项）
- 设计：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md` §2 D1=B/D6
- 环境：`platform/.venv`（Python 3.13）；基线 HEAD 见 `00-baseline-head.txt`
- 提交：
  - Task 0：`e530732 docs(contracts): 评估口径 v2 统一（spread 正=好 + 迁移说明）`
  - Task 5：本提交（`feat(eval): spread 口径 v2（正=好）+ 版本字段`；hash 见 `git log -1`）

## 交付内容

| 任务 | 内容 |
|---|---|
| Task 0 | interface spread 节重写为 v2（`(g9−g0)×direction`，正=好）+ v1/v2 迁移对照（历史不重算）；playbook §4.1 spread 行同步；新增防漂移测试 `platform/tests/test_eval_docs.py`（含"旧公式仅许出现在迁移注记行"负向守卫） |
| Task 5 | kernel `evaluate_factor`：spread 改 `(g9−g0)×direction`、结果新增 `version: 2`；桥接/装配透传；`factorlab list/show` 按 version 渲染（v2 正值提示；含历史 v1 产物时加注记"未按 v2 重算"） |

## 证据索引

| 文件 | 内容 | 命令（按原样执行） |
|---|---|---|
| `00-baseline-tests.txt` | 改动前相关测试基线：129 passed | `cd platform && .venv/bin/python -m pytest tests/test_quant_core_shim.py tests/test_eval_rust_ic.py tests/test_cli_list_show.py tests/test_cli_run.py tests/test_layered.py tests/test_web.py tests/test_doc_paths_exist.py tests/test_evaluate_notes.py tests/test_eval_alignment.py -q` |
| `00-baseline-gates.txt` | 改动前 `make gates`：唯一失败 = G-INDEX（挖矿在途因子未入索引，**预存**）；余全绿 | `make gates` |
| `01-task0-red.txt` | 文档测试先红：playbook 行缺 v2 公式 / interface 缺 v2+迁移（2 failed） | `cd platform && .venv/bin/python -m pytest tests/test_eval_docs.py -q` |
| `02-task0-green.txt` | 文档改后绿：6 passed（含既有 `test_doc_paths_exist`） | 同上 + `tests/test_doc_paths_exist.py` |
| `02-task0-docs-diff.txt` | Task 0 文档 diff（interface + playbook） | `git diff -- knowledge/contracts/interface.md knowledge/handbooks/factor-mining-playbook.md` |
| `03-task0-gates.txt` | Task 0 后 `make gates`：与基线一致（仅预存 G-INDEX） | `make gates` |
| `04-task5-red.txt` | Task 5 测试先红：12 failed（spread 符号/version/CLI 渲染/legacy 结构锁） | `cd platform && .venv/bin/python -m pytest tests/test_eval_spread_sign.py tests/test_quant_core_shim.py tests/test_cli_list_show.py tests/test_cli_run.py::test_run_legacy_single_output_eval_structure_lock -q` |
| `05-task5-green.txt` | 实现后绿：38 passed | 同 `04` |
| `06-task5-related-green.txt` | 相关面回归：140 passed（eval/CLI/web/layered/对齐/文档路径全绿） | 见文件首行命令 |
| `07-task5-mutation.txt` | 突变检验：v1 公式回退 → 2 failed；去 `version` → 3 failed；还原 sha256 一致且 5 passed | `platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-task0-5/mutation.py` |
| `08-task5-gates.txt` | Task 5 后 `make gates`：与基线一致（仅预存 G-INDEX） | `make gates` |
| `09-v1-annotation-pending.md` | **历史 v1 档案注记待办清单**（46 份档案 + 28 个无匹配档案的运行；`M`/`??`=挖矿在途禁动） | `platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-task0-5/pending_annotations.py` |
| `10-task5-code-diff.txt` | Task 5 代码 diff（kernel/CLI/tests） | `git diff -- platform/...` |

脚本：`mutation.py`（两突变 + 还原逐字节断言）、`pending_annotations.py`（只读清单生成）。

## 口径要点（验收锚）

- v2：`decile_returns.spread.ret = (g9−g0)×direction`，**正值 = 表现与声明方向一致**；
  手算锚：signal=0..19、fwd=signal×0.001 → g0=0.0005、g9=0.0185、spread=+0.018（`05`）。
- `evaluation.version=2`：kernel 恒有（空面板也有）；单输出在顶层、多输出逐输出；`publish_run`
  落 `summary.json`；`list/show` 按 version 分支渲染。
- **历史 summary 不重算**：无 `version` 键 ≡ v1（`(g0−g9)×direction`，负=自洽），list/show
  显式注记；档案注记待办见 `09`（挖矿在途，未动任何档案）。

## 未竟 / 移交

- G-INDEX 预存红：`research/factor/` 挖矿在途新增 spec 未入索引（baseline 即红；本批未触碰）。
  协调者在挖矿收尾后跑 `build_index.py` 收口。
- 历史 v1 档案注记（`09` 清单）：`knowledge/dossiers/factors/**` 挖矿在途（M/??），本批按约束
  未动；建议在挖矿批次收尾后由协调者统一注记「spread 为 v1 口径（负=自洽）」。
