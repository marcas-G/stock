# 坏台账 fixture（R04-Q7 负向证据；2026-09-16）
# 预期红：重复 ID、非法状态、死引用、fixed-claimed 空修复说明、统计≠实计（五类各 1 处）
- 轮次：**R99**
- 统计：**1 Critical / 0 Important**（故意与实计 2C/1I 不符）
| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填） | 复查 |
|---|---|---|---|---|---|---|
| R99-ENG-C1 | C | 死引用 + 空修复说明 | `platform/src/factorlab/core/definitely_missing.py:1` | fixed-claimed |  | |
| R99-ENG-C1 | C | 重复 ID + 非法状态 | `platform/src/factorlab/core/spec.py` | done | commit x（状态非法、ID 重复） | |
| R99-ENG-I1 | I | 对照：完全合法行 | `platform/src/factorlab/core/spec.py:1` | open | | |
