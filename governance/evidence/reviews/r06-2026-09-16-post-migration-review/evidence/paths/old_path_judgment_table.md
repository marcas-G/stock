# R06 活文件旧路径判定表（2026-09-16）

扫描命令模板（每条模式的原始输出见同目录 `grep_<pattern>.txt`）:

    cd /data/students/gaolei/stock
    rg -n --hidden \
      -g '!governance/evidence/verification/**' -g '!governance/evidence/reviews/r0*/**' \
      -g '!_archive/**' -g '!data/**' -g '!.git/**' -g '!**/__pycache__/**' \
      -e '<PATTERN>'

判定口径：真漏=活文件（会被执行/被用户读到）且路径已不存在或会误导落盘；
合法=历史记录/迁移映射/说明/冻结证据。

| 模式 | 命中数 | 判定 | 关键位置 |
|---|---|---|---|
| `research/docs` | 25 | 部分真漏 | 真漏：`knowledge/dossiers/factor-authoring-manual.md:7,73`；历史但易误导：`knowledge/design/workspace/2026-09-16-strategy-decomposition/{design.md:105,plan.md:46,175,186}`；合法映射/记录：`knowledge/README.md:4,12`、`knowledge/dossiers/factors/README.md:7-8`、`governance/ops/check_reviews.py:71-73`、`governance/workspace/migration-r04.md`、`governance/evidence/reviews/findings.md`、`README.md:74`、`research/docs/README.md` |
| `docs/verification` | 43 | 合法（历史/映射） | 冻结 spec `knowledge/design/platform/specs/2026-09-12-*.md` 等；历史 plan `knowledge/design/workspace/2026-09-15-*`；`check_reviews.py:59-61` 映射；`findings.md` 历史台账 |
| `docs/reviews` | 17 | 部分真漏 | 真漏（活动文档结构块）：`governance/evidence/reviews/README.md:9`；历史执行记录（Plan S 已实施）：`knowledge/design/workspace/2026-09-1{5,6}-*/{README,design,plan}.md`；合法映射/台账：`check_reviews.py:60`、`findings.md:25` |
| `platform/results` | 23 | 部分真漏 | 真漏（活手册指令+死快照引用）：`knowledge/dossiers/factor-authoring-manual.md:63`、`knowledge/dossiers/factors/volatility/max_effect_20d_high.md:83`（目标已不存在）；历史快照引用：`knowledge/dossiers/factors/**` 若干（`intraday_tail_amt_share.md:87`、`max_effect_20d_{zmax,extcnt}.md`）；合法：`platform/src/factorlab/config.py:7`（历史说明）、`research/tools/strategies/run_strategy.py:24`、`research/tools/strategies/tests/test_run_strategy_cli.py:276-284`、`check_reviews.py:104`、`findings.md` |
| `research/tools/` | 73 | 基本合法 | 合法：factor_lib/strategies 仍在 research（Makefile:32-33、gates.sh:67,70、knowledge/index/*.md:3、skill:126 等）；映射：`check_reviews.py:75`；历史台账：`findings.md`；冻结 notes。真漏：`knowledge/dossiers/factor-authoring-manual.md:94`（`research/tools/1m_features/` 已迁 platform/tools） |
| `docs/data-map` | 1 | 合法（冻结 spec 历史引用） | `knowledge/design/platform/specs/2026-08-15-factorlab-m3a-data-layer-design.md:5` |
| `docs/pending-items` | 4 | 合法（映射/冻结/历史） | `check_reviews.py:62`、两份冻结 spec、`findings.md:116` |
| `docs/directory-conventions` | 0 | — | — |
| `docs/index` | 10 | 部分真漏 | 真漏：`knowledge/dossiers/factor-authoring-manual.md:76,82`；历史 Plan S 执行记录：`.../strategy-decomposition/{design.md:107,116,plan.md:47,49,175}`；合法映射：`knowledge/dossiers/factors/README.md:8`、`migration-r04.md:55` |
| `docs/design` | 0 | — | — |
| `docs/contracts` | 0 | — | — |
| `docs/specs` | 0 | — | — |
| `quant-platform-main` | 12 | 合法（R17 删除记录/冻结/门自身） | `gates.sh:47`（门自匹配）、`governance/workspace/{workspace-p0p8,traceability-matrix,remote-cleanup-checklist,directory-conventions}.md`（均含 R17 删除记录）、`knowledge/design/platform/specs/2026-09-12-*.md:17,165`（冻结） |
| `projects/` | 41 | 合法（归档说明/映射/历史） | `CLAUDE.md:76`、`README.md:66`、`.gitignore:2` 均说明已归档；`governance/workspace/*` 映射/历史；`check_reviews.py:106,115` 豁免 |
| `emb/bin/python` | 6 | 部分真漏（T2 残留） | 真漏：`platform/tools/lob_fact/w5_closure.sh:7`（可执行 runbook 钉死 emb）；说明性：`governance/ops/reinstall_editable.sh:38-39`（"emb（T2）不装 quant_core"）；历史：`findings.md:110`；冻结：`platform/tools/lob_fact/notes/*` |
| `scripts/gates.sh` | 2 | 合法（历史） | `findings.md:112`（历史台账行）、`knowledge/design/workspace/2026-09-16-strategy-decomposition/plan.md:48`（Plan S 执行于 R24 前，当时坐标正确） |

## 附：门覆盖盲区说明

`gates.sh` 的 [G-LEGACY] 只匹配旧仓库名（quant-platform-main/research、projects/quant-platform），
**不检查** `research/docs`、`docs/reviews`、`docs/index`、`platform/results`、`research/tools/<已迁工具>`、
`emb/bin/python` 等迁移路径模式。因此本轮发现的多数"活文件旧路径"不会被常驻门拦住。
（check_reviews 只解析 `findings.md` 行内引用，不扫全仓。）
