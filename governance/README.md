# governance —— 治理与证据

> R24 顶层目录重整（方案 A）：工作区级的**治理脚本、约定台账与证据**收敛到这里。
> 迁移台账：`workspace/migration-r04.md`。

## 三节

| 目录 | 内容 | 原位置 |
|---|---|---|
| `ops/` | 门与运维脚本：`gates.sh`（唯一门入口，`make gates`）、`check_imports.py`、`check_tool_layering.py`、`check_dataiface.py`、`reinstall_editable.sh` | `scripts/` |
| `workspace/` | 工作区约定与台账：`directory-conventions.md`（结构唯一权威）、`data-map.md`（数据唯一权威）、`pending-items.md`、`archive-policy.md`、`traceability-matrix.md` 等 | 根 `docs/` |
| `evidence/` | 可复现证据：`verification/<轮次>/`（命令 + 原始输出 + 门结果）、`reviews/`（评审台账：报告 / findings / 设计评审） | `governance/evidence/verification/`、`governance/evidence/reviews/` |

## 纪律

- 每轮结构/行为改动必须留可复现证据（命令 + 原始输出）到 `evidence/verification/<轮次>/`。
- 历史证据与评审**正文不改**（记录当时事实）；路径变化只加旧→新映射表。
- `data/` 零写；`data/` 的 mtime/容量基线存于 `evidence/verification/`。
