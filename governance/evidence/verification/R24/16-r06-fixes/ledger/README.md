# R06 台账门加固证据（R06-LEDGER-I1/I2/I3）

- 轮次：R24 迁移后复查 R06 修复（2026-09-16）
- 改动：`governance/ops/check_reviews.py`（G-REVIEWS 门）
- 测试：`test_check_reviews_r06.py`（TDD：先失败后通过；18 用例）

## 文件索引

| 文件 | 内容 |
|---|---|
| `test_check_reviews_r06.py` | TDD 套件（m5/m7 对抗、迁移映射、before/after 简写、URL/注释排除、豁免行锚定、闭环计数/告警、selftest） |
| `01-tdd-red.txt` | **RED**：实现前 6 个新行为用例失败 |
| `02-tdd-green.txt` | **GREEN**：实现后 18 passed |
| `03-real-ledger-gate.txt` | 真实台账（101 行 = 93 + R06 8）门输出：绿 + 闭环摘要 |
| `04-closure.txt` | `--closure`：复查列 1/101、fixed-claimed 未复查 92、12 条报告/复查列-台账状态告警 |
| `05-selftest.txt` | `--selftest`：10 类负向场景（含裸文本死路径 / 无证据 token） |
| `06-9-scenario-adversarial.txt` | 9 场景对抗表重跑摘要：baseline GREEN、m1–m9 全 RED |
| `gate_adversarial_rerun.py` | 对抗驱动（副本写 /tmp，只读真实台账） |
| `adversarial/gate-adv-*.txt` | 每场景门输出 + exit code（m5/m7 修复前 GREEN → 现 RED） |
| `07-make-gates.txt` | `make gates` 全量输出（G-REVIEWS 绿；G-INDEX 挖矿在途红，如实归因） |
| `closure.md` | R06-LEDGER-I1 闭环机制 + R02 判定来源 + 职责归属（reviewer 回填，团队不代填） |

## 判据要点（修复后）

1. **裸文本路径也校验**（I2）：反引号 span 与裸文本同解析器（全仓索引 + 后缀/
   轮次相对/`before/after` 简写 + 迁移映射）；URL、HTML 注释、全角冒号排除；
   `path:line`/`::test`/`#锚点` 支持。真实台账 9 处历史不精确引用按
   (行 ID, token) 精确豁免（`PLAIN_EXEMPTIONS`，逐条理由，命中打印）。
2. **fixed-claimed 证据 token**（I3）：`[0-9a-f]{7,40}` SHA 或存在的路径，否则 RED；
   现台账 93 行全部满足（无需豁免）。
3. **闭环**（I1）：汇总行常驻覆盖率/未复查计数/告警数；`--closure` 逐轮清单；
   报告判定（表格单元格 `| **reopened** |` 或 `verified（…）`）与台账状态不一致 → WARN（非致命）。
