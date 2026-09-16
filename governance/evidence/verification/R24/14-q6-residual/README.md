# R24 / 14-q6-residual —— R04-Q6 残余（死符号/数字/断链/僵尸清理）证据（2026-09-16）

承接 R04 报告 §3（tidy 分报告定稿版：`governance/evidence/reviews/r04-efficiency-2026-09-16/report.md`）。

## 1. 真死符号：报告 10 项中删 9、留 1（`platform_head`）+ `SZ_POOL/SH_POOL` 双删（逐符号 grep 零引用后处置）

| 符号 | 位置 | 处置 | 依据 |
|---|---|---|---|
| `bars_columns` | `platform/src/factorlab/adapters/bars_read.py` | **删**（连同 `_TABLE_COLS`/`BARS_1M_COLS` import） | `git grep` 0 引用 |
| `lob_columns` | `platform/src/factorlab/adapters/lob_read.py` | **删** | 0 引用（`_TABLE_COLS` 仍被读函数使用，保留） |
| `BARS_1M_REQUIRED` | `platform/tools/ashare_ingest/contracts.py` | **删** | 0 引用；其内联列序副本在 `readers/minute.py query_1m` 内，随该函数一并删除 |
| `delisted_codes()` | `platform/tools/ashare_ingest/datapaths.py` | **删** | 0 调用（消费方 `ingest_daily.py:37` 直拼 `delisted_codes.parquet`；G-TOPO 禁跨工具 import，函数无处可接） |
| `ensure_output_dirs` | `platform/tools/universe_stages/universe_paths.py` | **删** | 0 引用（`out_dir()` 自带 mkdir） |
| `done_files` | `platform/tools/quark_download/quark_download_v2.py:221` | **删**（局部赋值未读） | 0 引用 |
| `entry_weeks` | `research/tools/strategies/strategy_crash_bottom.py:111` | **删**（元组槽位未读） | 0 引用 |
| `first_trade_dates_by_month` | `platform/tools/universe_stages/readers/daily.py` | **删** | 0 引用 |
| `query_1m` | `platform/tools/universe_stages/readers/minute.py` | **删**（连带消除第二份 schema 字面量，模块 docstring 已说明 `query_5m`/`month_files` 为在用面） | 0 引用 |
| `SZ_POOL` / `SH_POOL` | `platform/tools/lob_fact/core/config.py` | **删** | 0 引用、无文档提及（早于 `CALIB_DAYS` 的校准池草稿；不影响 `pins.sha256` 金样——pins 只哈希 fixtures 文件） |
| `_env.platform_head` | `platform/tools/_env.py` | **留 + 注释**（R04-Q6 确认意图） | REQ-Q-011 文档性 API（`research/README.md` 指引 + 设计 spec 引用）；当前 0 调用者，若决定不接线应连同文档引用一并移除 |

- 删除后引用复查原始输出：`dead-symbols-after-grep.txt`（全部 0 refs；`platform_head` 仅定义+文档引用）。
- 周边测试：`targeted-tests-after.txt`（123 passed / 2 skipped：lob_read、ashare_ingest、universe_stages、
  quark_download、strategies、lob_fact fixtures）；完整 `platform/tools` 337 passed（`test-research-after.txt`）。

## 2. 数字对齐（活文档改为「现测快照 + 证据指针」，不写死易漂数字）

| 文档 | 旧 | 新 |
|---|---|---|
| `CLAUDE.md` | 平台基线 3099/13（R23/R27）；tools 337+35 | 最近基线 **3150/13** @R24，指针 `R24/00-baseline/platform-pytest.txt`；tools **337 + 58passed/2skip**，指针 `R24/12-acceptance/test-research.txt`；lob_fact 191→192 |
| `README.md` | `data/ 353G`（2 处写死）；测试节无指针 | data 体量权威指向 `data-map.md`（353G 标注为 2026-09-16 快照）；测试节加基线指针 |
| `AGENTS.md` | 全库 lint「当前 164+」 | `make lint-factors` 现测（2026-09-16 快照 167） |
| `Makefile` help | test-platform 指针 R27；gates 未列台账 | 指针 R24/00-baseline；gates 描述加「台账口径」；新增 `clean` 条目 |
| `research/README.md` | 152 spec/14 族；tools 337+35；`tools/_env.py`；design 链接缺 `../` | 167 spec/15 族（现测快照）；60 collected=58/2skip（分目录 337 明细保留）；`../platform/tools/_env.py`；`../knowledge/design/research/` |
| `research/CLAUDE.md` | 工具经 `tools/_env.py` | `../platform/tools/_env.py` |
| `CLAUDE.md` 结构句 | 「三棵树 platform/research/docs」/根 `docs/` | platform/research/knowledge + governance（R24 后真实坐标） |

现测命令与输出：`doc-align-verify.txt`（lint 167、族 15、platform/tools 337）。

## 3. 断链（tidy §3 清单逐条核实，冻结历史不动）

| 引用 | 现状 |
|---|---|
| `findings.md:51` fills 旧坐标 | **本轮修**（见 13-reviews-gate，位置列改正） |
| `pending-items.md:17` lob 批算路径 | 已修（现 `platform/tools/lob_fact/pipeline/run_lob_batch.py`，test -e ✓） |
| `interface.md` 旧 `artifacts.py` | 已修（R21/R24 后正文指向 `parquet_artifacts/strategy_artifacts/results_fs` 退役说明） |
| lob/resic/mining 三份 spec | 已修（R04/R27 勘误行在，正文不改；grep 证据见 `doc-align-verify.txt`） |
| `timed_m6.md:54` `tools/strategy_crash_bottom.py` | **本轮修**：`research/tools/strategies/strategy_crash_bottom.py` + R04-Q6 校正说明 |

## 4. 僵尸清理（仅安全项）

- `make clean` 扩到 `.pytest_cache`（原只清 `__pycache__`）；**未动**：`_staging`、`1m_features/output`、
  platform 旧 results 备份、`state.json.legacy-*`、根 `results/`（挖矿在途）。
- 清理量：`__pycache__` **894 dirs / 157,440 KB**（其中 `.venv` 内 814 dirs；仓库工作区 80 dirs / 13,236 KB）
  + `.pytest_cache` **4 dirs / 488 KB** → 后均 0。原始输出 `clean-before.txt` / `clean-after.txt`。

## 5. 门与测试结果（如实归因）

- `make test-research`：`platform/tools` **337 passed**；`research/tools` 2 failed（`test_index_matches_generator`、
  `test_every_yaml_has_mirror_doc_and_name_matches`）——**挖矿在途**：`research/factor/reversal_20d/netflow_vol.yaml`
  已在盘、档案 `knowledge/dossiers/factors/reversal_20d/netflow_vol.md` 未落（原子循环未闭合），与 R27 先例同因。
  原始输出 `test-research-after.txt`。
- `make gates`：**仅 G-INDEX 红**（同上在途 yaml 使索引成子集），G-LINT/G-ANNOTATE/G-REVIEWS/两自检全绿；
  数据接口门 ENFORCED 全绿。原始输出 `13-reviews-gate/gates-full.txt`。
