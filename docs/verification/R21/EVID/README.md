# R21 EVID 修复证据索引（R01-EVID-C1/C2/I1/I2/I4/I5/I6/I7/I8）

- 范围：只动文档/证据层；**未改**任何代码（`platform/src/**`、`research/tools/**` 零改动）、
  `docs/reviews/**` 零改动、`platform/docs/interface.md|catalog.md` 零改动。
- 口径：所有实测在 **R21 工作区**（含其他 7 个子系统的修复，未提交）执行，故基线数字与
  R01 评审时的 245/59 不同（新增了测试）；平台全量测试由 coordinator 负责，本目录不碰。
- 命令默认 cwd = `/data/students/gaolei/stock`（另有注明的除外）。

## 变更清单（按 finding）

| finding | 改动文件 | 证据 |
|---|---|---|
| C1 | `research/docs/factors/README.md`（新，总说明）；152 份档案 front matter +`snapshot:`；`_template.md`；`annotate_factor_archives.py`（新，幂等+`--check`） | `C1-not-reproducible.txt`（results/ 空、无 duckdb、CH 无 stock_st、152/152 依赖 exclude_st）；`C1-annotation.txt`（修复前 152 缺 → 写入 → 齐备）；`C1-index-gate.txt`；`C1-factor-lib-tests.txt` |
| C2 | `R12/r12_smoke.yaml`（新，复原 spec）；`R12/status.md` §6、`R13/status.md` §5（"R21 补录"节）；原始输出入 R12/R13 | `R12/r21-recheck-run.txt`（真 CH exit=0）；`R12/r21-recheck-layout.txt`（布局/权限 0664/无 tmp/list/show）；`R13/r21-recheck-web.txt` + `r21-recheck-web-{home,detail}.html`（serve+curl 双 200，IC/分层区块） |
| I1 | `research/README.md`（16/22/24/33/40/43/44 行） | `I1-I2-research-T2.txt`（268/10）；`I1-research-T1.txt`（116）；`I1-I2-per-dir-counts.txt`；`I1-I2-T2-with-skips.txt`（10 skip 明细） |
| I2 | `CLAUDE.md:74`、`research/CLAUDE.md:51`（183→191）；另同步 `research/README.md:16/24`、根/研究 CLAUDE 基线 | `I1-I2-per-dir-counts.txt`（lob_fact 191）；全仓同类扫描：`docs/workspace-p0p8.md:153` 为历史 S3 指针（保留不改成活文档） |
| I4 | 6 份 spec 状态→已实现；m4b 加勘误头 | `I4-spec-status.txt`（修复前后 + 范围外仍"待评审"清单） |
| I5 | `.gitignore`（+`quark_cookies*`、`.env*`） | `I5-gitignore.txt`（无跟踪凭据；`git check-ignore` 命中；platform/research 的 `.gitignore` 原本已 ignore `.env`） |
| I6 | `platform/README.md` §仓库纪律改为单仓单树 | `I1-I2-I5-I6-doc-diff.txt` |
| I7 | `docs/pending-items.md` #11（①②③✅/④❌）、#12①（460→68 + 口径） | `I7-pending-verify.txt`；`I7-dataiface-count.txt`（门 REPORT 68 处） |
| I8 | `R19/README.md`（勘误）；`R19/gen-02-migration-diff.py`（R21 补存）；`R19/02-migration-diff-r21-repro.txt` | 同左（运行输出：新侧摘要 + OUT_SCHEMA 自检） |

## 门结果（收尾复跑）

- `bash scripts/gates.sh` → **结构门全绿 + 数据接口门 ENFORCED 全绿**（`final-gates.txt`）。
- `cd platform && .venv/bin/python -m pytest -q tests/test_doc_paths_exist.py` → **2 passed**（`final-doc-paths-test.txt`）。
- `platform/.venv/bin/python -m pytest research/tools/factor_lib/tests -q` → **5 passed**（`final-factor-lib-and-smoke-lint.txt`）；
  `factorlab lint docs/verification/R12/r12_smoke.yaml` → `OK r12_smoke`。

## 未解决点 / 与任务描述的偏差（如实）

1. **C1 的"索引页顶注"未做**：`docs/index/factors.md` 是生成物，有 byte-equality 门，
   而生成器 `research/tools/factor_lib/build_index.py` 属"禁改的 research/tools 代码"——
   顶注只能经改生成器实现。改用 **factors/README.md 总说明 + 152 份 front matter `snapshot:` 字段**；
   索引门复跑 `索引一致 ✓`（front matter 不参与索引生成，故无需重生成）。
2. **C1 的"可复跑"本身不可恢复数据侧**：恢复条件已写入 `research/docs/factors/README.md`
   （重建平台库 `factorlab data rebuild` 或给 CH 灌 `stock_st`）。当前 `platform/results/` 仍为空
   （R21 补录的 C2 冒烟产物写在 `/tmp/opencode/`，未污染结果根）。
3. **C2 是"R21 重跑补录"，不是 R12/R13 当时的原始输出**（当时确未存档）——已在两个 status 的
   补录节首写明。
4. **根 CLAUDE.md 里平台全量基线 2570 未动**（差平台全量测试专由 coordinator 跑，避免越权/重复）。
5. **范围外观察**（未改）：`research/docs/superpowers/specs/2026-08-17-factorlab-factor-mine-skill-design.md`
   与 `2026-08-18-factorlab-correlation-and-classic-seeds-design.md` 仍写"待评审"，疑似同类漂移，
   建议 coordinator 决定是否同批处理。
