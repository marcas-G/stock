# R06-2 门与脚本锚点核对（读代码 + 实跑）

## 与新版树一致的锚点（OK）
- `check_tool_layering.py:36` TOOL_ROOTS = [`platform/tools`, `research/tools`] —— 双根覆盖；
  `--selftest` 四类违规各命中 1 处、合法路径零误报（`make gates` 输出 44-45 行）。
  R1-R4 规则中的 lob_fact 路径按"相对子目录"判定，与工具所在树无关，迁移后语义不变。
- `check_dataiface.py:29` TOOL_ROOTS 双根；`G_READ_ALLOWED` 25 条登记全部指向 `platform/tools/<工具>`
  （已迁路径），白名单"不腐"校验启用（登记点消失也报红）；SKIP_PARTS 含 `/notes/`、`/.venv/`；
  ENFORCED 全绿、REPORT 70 处、`--selftest` 通过（`run_make_gates.txt:47-62`）。
- `check_imports.py:28` 扫描面 = `platform/` + `research/`；`--selftest` 用历史案例 RunContext 抓迁移遗漏；
  实跑 409 文件全解析（`run_make_gates.txt:22-24`）。
- `check_reviews.py:38` DEFAULT_LEDGER = `governance/evidence/reviews/findings.md`（新台账路径）；
  MAP_PREFIX 覆盖 R24 全部搬迁映射（58-78 行）；实跑 93 行 finding 全过 + `--selftest`（run_make_gates.txt:32-36）。
- `reinstall_editable.sh:18,21,26-28` 正向落位 platform/ 与 `platform/kernels/quant_core/` + 反向 `/projects/` 断言
  —— 与新版树一致。
- Makefile `gates`（26 行）→ `governance/ops/gates.sh`（新锚）；`reconcile`（37 行）→ `platform/tools/ch_ingest/reconcile.py`（新锚）。

## 与新版树不一致的锚点（M）
1. `gates.sh:21` `FROZEN='... :!research/tools/lob_fact/notes'` —— 变量全脚本零引用（死配置），且
   `research/tools/lob_fact/notes` 已随工具迁走（现 `platform/tools/lob_fact/notes`，第 21 行也已含之）。
2. `gates.sh:48` git pathspec `:!research/tools/lob_fact/notes` 同属已迁走的死路径（无害但误导）。
3. `gates.sh:74`、`Makefile:32-33` 使用系统 `python3`（anaconda 3.10.9）而非平台 venv 3.13；
   与"单解释器化（平台 venv）"声明不一致（实跑功能正常，`run_make_index.txt`）。
4. `reinstall_editable.sh:38-39` 尾注仍以 emb（T2）python 为指示（emb 环境实际仍存在，但研究树
   CLAUDE.md 声明"emb 已退役"；属残留说明）。
5. `gates.sh` G-LEGACY 判据只覆盖旧仓库名（quant-platform-main/research、projects/quant-platform），
   不覆盖本次 R24 迁移的路径族（research/docs、docs/reviews、platform/results、已迁工具路径）——
   故活文件旧路径可长期不被门拦截（见 old_path_judgment_table.md 附注）。

## 实跑结果（原始输出见 run_make_gates.txt）
- `FACTORLAB_MAX_MEMORY=8GB make gates` → **exit 2**（make recipe failed）；结构门 2 处红：
  - `[G-INDEX] 因子索引与生成器一致 ✗`：盘上 167 因子 vs 生成器 168（差 in-flight `turnover_accel_inflow`）
  - `[G-ANNOTATE] ✗ 缺 snapshot 标注：1 份 - knowledge/dossiers/factors/reversal_20d/netflow_vol.md`
- 其余全绿：G-COPY/G-BOUNDARY/G-LEGACY/G-PATHS/G-IMPORTS/G-LINT（168 通过 0 失败）/G-VENV/G-TOPO/
  G-CONTRACT/G-MARK/G-READ/数据接口门 ENFORCED。
