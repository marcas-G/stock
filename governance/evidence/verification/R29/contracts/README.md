# R29/contracts —— R07 Task 3 残余 + Task 7 证据（契约指针/计数对齐/backlog）

- 任务：Plan G（`governance/evidence/reviews/r07-2026-09-16-gap-audit/improvement-plan.md`）
  Task 3 残余（契约同步/计数对齐）+ Task 7 残余（pending-items 增补/校正）
- 开工 HEAD：`881a077`；日期：2026-09-16
- 纪律：挖矿在途（research/factor/**、knowledge/dossiers/**）与 reviewer 在途
  （reviews/** findings/README/report）未触碰；R21 为**追加注记**（历史正文不改写）

## 证据清单

| # | 文件 | 内容 |
|---|---|---|
| 01 | `01-next-window-test-red.txt` | 防漂移断言**先红**：interface.md 仍含 `NEXT_OPEN only`（2 处） |
| 02 | `02-next-window-test-green.txt` | 措辞同步后**转绿**（3 passed） |
| 03 | `03-catalog-face-red.txt` | catalog 分类面入口测试**先红**（KeyError: classification_face） |
| 04 | `04-catalog-face-green.txt` | 生成器 + 重生成后**转绿**（test_catalog.py 28 passed） |
| 05 | `05-gen-op-catalog-check.txt` | `gen_op_catalog.py --check` 绿（未动生成表） |
| 06 | `06-counts-measured.txt` | 实测：G-CONTRACT REPORT **70** 处；CH **13 表**（index_daily 0）；daily_fact **18,124,805** 行/5,861 码；pyproject 直接依赖 **19**/venv **72**；factor yaml 170 tracked/196 on-disk、15 族；分类面 **528** 条 |
| 07 | `07-ch-skill.diff` | 仓外 `~/.claude/skills/factorlab-ch-pipeline/SKILL.md` 行数/归档路径更新 diff |
| 08 | `08-interface-diff.txt` | interface.md 工作树 diff + 平台侧改动 stat（注：该轮工作树另有并发 agent 的 ST/index_daily 裁决 hunk，非本任务） |
| 09 | `09-relevant-tests.txt` | `pytest test_doc_paths_exist test_catalog test_architecture test_cli_op_registry` → 48 passed |
| 10 | `10-gates.txt` | `bash governance/ops/gates.sh` 全量输出 |
| 11 | `11-gates-attribution.txt` | 唯一红 G-INDEX 归因：并发挖矿在途（开工前已红），非本任务 |
| 12 | `12-doc-diffs.txt` | catalog/data-map/pending-items/R21/check_dataiface/gates.sh diff |

## 结论与残余

- 防漂移：`platform/tests/test_doc_paths_exist.py::test_interface_next_window_contract_not_drifted`
  纳入常规测试集（`platform && pytest -q`）。
- catalog：`knowledge/contracts/catalog.md` 现含「分类表全集（含未注册库函数）」入口
  （`factorlab op list --catalog`，528 条）+ 生成器指针；生成源为
  `platform/src/factorlab/adapters/catalog.py` 的 `operators.classification_face`
  （计数实时取 `effective_catalog()` 与 CLI 同源，防陈旧）。
- 计数（2026-09-16 实测）：`460 处`（grep 口径）→ **70 处**（门口径）；data-map
  A5/B1/C2/A9 按实测校正；R21 补 circ_mv 已派生 / index_daily / stock_st 现状注记。
- backlog：pending-items #9/#11④/#12①/#18 校正；新增 #28（CA Gate 连续回测 ✅ +
  fail-closed 残余）/ #29（circ_mv 派生 ✅）；#23 补触发状态；#24 核对一致（未改）。
- 残余：G-INDEX 红（挖矿在途，见 11）；R07 报告中 Plan 2/3 本体、分钟 V2 属另立计划，
  本任务只登记/核对；`governance/evidence/verification/**` 冻结面仅 R21 追加注记。
