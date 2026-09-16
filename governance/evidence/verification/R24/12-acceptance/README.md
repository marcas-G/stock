# R24 顶层目录重整（方案 A）验收证据（Task 12）

**日期**：2026-09-16 ｜ **分支**：`restructure/monorepo` ｜ **起始 HEAD**：`cbb81bc`
**迁移台账（旧→新全映射 + 逐 Task 提交 SHA）**：`governance/workspace/migration-r04.md`

## 1. 门与测试（实测，对照 Task 0 基线）

| 检查 | 结果 | 证据 |
|---|---|---|
| `make gates` | **红 1 项**：G-ANNOTATE 缺 snapshot 7 份（**挖矿在途**，非 R24 引入；其余全绿：G-COPY/G-BOUNDARY/G-LEGACY/G-PATHS/G-IMPORTS/G-INDEX×2/G-LINT 164+/G-VENV/G-TOPO/G-DATAIFACE） | `gates.txt` |
| 平台全量 | **3150 passed / 13 skipped**（816s）＝基线一致 | `platform-pytest.txt` |
| `make test-research` | **exit 0**：platform/tools **337 passed**；research/tools **58 passed / 2 skipped** | `test-research.txt` |
| `build_index.py --check` | 索引一致 ✓（重生成后；含挖矿在途条目，见 `build_index-regen.txt`） | `gen_op_catalog-check.txt` |
| `build_strategy_index.py --check` | 策略索引一致 ✓ | 同上 |
| `gen_op_catalog.py --check` | exit 0（生成物与生成器一致） | `gen_op_catalog-check.txt` |
| `annotate --check` | **红**：缺 snapshot 7 份（同上，挖矿在途） | `annotate-check.txt` |

**G-ANNOTATE 归因**：`liquidity/level_ma20`、`reversal_10d/cumret_log`、`reversal_20d/cumret_freq`、
`reversal_20d/overnight`、`reversal_rsi/reversal_14_ret`、`volatility/high_intraday`、
`volatility/low_vol_20d_park`——均为挖矿循环在途档案（Task 0 基线即红 4 份，随挖矿推进变化）。
**按要求未修改挖矿文件**。

## 2. 旧路径 grep（全库，Task 0.4 同一组模式）

原始输出：`old-path-final.txt`（2768 行）。命中分类：

| 类别 | 量级 | 判定 |
|---|---|---|
| `governance/evidence/**` 冻结证据与评审 | 2660 | ✅ 冻结正文（计划豁免） |
| `knowledge/design/**` 冻结设计 | 57 | ✅ 冻结正文（计划豁免） |
| `governance/workspace/**` 历史记录/映射 | 13 | ✅ 冻结历史 + 映射（workspace-p0p8/traceability-matrix/migration-r04） |
| `platform/tools/universe_stages/**`、`platform/scripts/**`、`platform/src/**`、`governance/ops/check_dataiface.py` | ~24 | ✅ 同名但不是搬迁对象的**树内自有 `scripts/` 子目录**（误报） |
| 映射表（`knowledge/README.md`、`knowledge/dossiers/factors/README.md`、各 README 节） | 4 | ✅ 旧→新映射表（计划允许） |
| 历史工具名引用（`converters/.../convert_minutes_to_parquet.py` 注 `detect_date_shift.py`，该工具未随仓） | 1 | ✅ 历史出处注记（非迁移对象） |

**结论**：搬迁对象的**活引用零残留**；全部命中为冻结文档、映射表、同名自有目录误报或历史出处注记。

## 3. data/ 零写

| 项 | Task 0 | Task 12 | 判定 |
|---|---|---|---|
| 容量 | 353G | 353G（`data-du-after.txt`） | ✅ |
| 文件清单 sha256（mtime+size+path，78120 文件） | `1ed59103…` | `1ed59103…`（`data-manifest-after.txt`） | ✅ **逐字节同一清单** |

## 4. 并发冲突与偏差（详见 migration-r04.md §3b）

- **C1**（Task 4）：`symrun_r30.md`/`max_effect_20d.md` 的挖矿在途内容随 rename 提交（`git mv` 语义）；
  不回滚（回滚会丢挖矿编辑），已登记。
- **C3**（Task 8）：根 `results/` 非空（挖矿在途写），未强搬/删除；`.gitignore` 过渡保留；
  新默认 `runs/platform` 已生效（冒烟 `runs/platform/r12_smoke` + `show` 通过）——见 `../08-runs/`。
- 其余 Task mtime 前后快照比对均"无并发写入"（Task 3/4/6）。

## 5. 计划遗漏补修（本 Task 落地）

- `annotate_factor_archives.py` ROOT 深度（Task 6 迁址后 `parents[4]`→`parents[5]`）——修复前门退化为
  "0 份 ✓"永真（**已修复并如实转红**）。
- `test_architecture.py` 契约单点判据（Task 8 发现，指向 `knowledge/contracts/`）。
- `test_quant_core_shim.py::test_contract_doc_present` 契约文档路径（Task 12 全量发现）。
- `platform/tools/ashare_ingest/{datapaths,validate_tick}.py` 对 `scripts/check_dataiface.py` 的引用
  → `governance/ops/`；`ddl.sql` adj 脚本出处注记更新。

## 6. 验收标准对照（计划 §验收标准）

1. `make gates` 全绿 → **除挖矿在途 G-ANNOTATE 外全绿**（归因明确；Task 0 起即红，非回归）。
2. 平台全量 ≥2969/13（实测基线 3150/13）；T2/T1 → **3150/13 与 337+58/2skip 全绿**。
3. `build_index` / `gen_op_catalog` / `annotate` `--check` → 前两者一致；annotate 红=挖矿在途。
4. 旧路径 grep → 仅冻结文档与映射表（§2）。
5. `data/` mtime/容量与基线一致；`runs/platform` 产物可经 `runs/README.md` 指回档案 → ✅。
6. `knowledge/README.md` 五类入口 + 契约/设计/档案/手册/索引；仓外 5 技能零旧路径（`../09-skills/`）。

## 7. 未竟项

- 根 `results/`（挖矿在途）清理：待挖矿停机后删除或并入 `runs/platform`。
- G-ANNOTATE 7 份 snapshot 标注：由挖矿循环按其节奏完成（非 R24 范围）。
- 方案 B（`parents[N]` 偏移）/ D4：本计划明确不做（后议）。
