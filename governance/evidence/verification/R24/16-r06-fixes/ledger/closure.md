# R06-LEDGER-I1 台账闭环：现状、机制与职责归属（2026-09-16）

## 结论（为什么开发团队不回填状态列）

- **R02 复查结论有来源可查**：`governance/evidence/reviews/r02-2026-09-15-strict-review/report.md`
  §1「例外（reopened / partial，需继续修）」表，7 条判定：
  `R01-DATA-C1`（reopened）、`R01-TOOLS-I9` / `R01-TOOLS-I3` / `R01-ENG-I3` / `R01-TOOLS-I5` /
  `R01-EVAL-I7/I8`（partial）；聚合口径「56 行 verified、7 行 reopened/partial」见
  `findings.md:8`（R02 复查注记，2026-09-15）。
- **状态/复查列回填属 reviewer 职责**（`README.md` §流程 3：reviewer 实际重跑后
  通过 → `verified`、未通过 → `reopened`，并写「复查」列）。开发团队**不代填**
  reviewer 判定——I1 的根因正是历次「团队只写修复说明、复查列从未回填」；
  代填会让「唯一状态源」变成自证。
- **等待 reviewer 在 R06 复查时逐行回填**。本轮把缺口做成机器可见（见下），
  不再依赖人工记忆。

## 门机制（`governance/ops/check_reviews.py`，R06-LEDGER-I1 修复）

1. **汇总行常驻**（`make gates` 可见）：
   `闭环：复查列 X/N、fixed-claimed 未复查 Y（逐轮）、报告/复查列-台账状态告警 Z`；
2. **`--closure`**：逐轮未复查行清单（含 ID 样例）+ 全部告警原文 + 职责说明；
3. **告警（非致命）口径**：
   - 各轮 `r*/report.md`：行内同时出现 finding ID 与**判定**（独立单元格
     `| **reopened** |` 或判定词+括注 `verified（…）`），台账状态未回填 → WARN；
     问题描述里引用字眼（如「R02 声称 56 verified」）不触发（避免误报）；
   - 台账「复查」列出现 verified/reopened/partial 而状态列未回填 → WARN。
   - 告警不改变 exit code（回填需要人；门只负责暴露）。

## 当前告警实跑（2026-09-16；原文见 `04-closure.txt`）

12 条 = R05-C1 复查列（1）+ R02 report §1 例外表（10）+ R05 report 结论（1）。
R02 的 7 条判定中 `R01-EVAL-I7/I8` 同一行、`R02-C2`/`R02-I6a`/`R02-I6b`/`R02-I7` 为联动新
finding，均只有「fixed-claimed」状态——与 R06-LEDGER-I1 描述一致。

## 什么算闭环（给 reviewer 的回填清单）

- `R01-DATA-C1` → `reopened`（R02）→ 已由 `da8bba0+f55befa` 修复，复查通过后置
  `verified`（需 reviewer 重跑确认）；
- `R01-TOOLS-I9`、`R01-TOOLS-I3`、`R01-ENG-I3`、`R01-TOOLS-I5`、`R01-EVAL-I7/I8` →
  当时 partial；后续 R02/R03 修复已回填修复说明，待本轮复查定状态；
- `R05-C1` 复查列已写「verified（2026-09-16 对抗性复查）」而状态列仍 `fixed-claimed`
  —— reviewer 直接回填状态即可。

## 证据

- `04-closure.txt`：`--closure` 原始输出（12 告警全文）；
- `03-real-ledger-gate.txt`：门汇总行（含覆盖率/告警计数）；
- `07-make-gates.txt`：`make gates` 中 G-REVIEWS 段（门回绿）。
