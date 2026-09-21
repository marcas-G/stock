# R38 / 01-markers —— Task 1 验收证据（pytest markers 取代硬编码 deselect）

- 任务：`knowledge/design/platform/plans/2026-09-21-cicd-streamlining.md` Task 1
- 规格：`knowledge/design/platform/specs/2026-09-21-cicd-streamlining-addendum.md` §3/§4（marker 名冻结）
- 日期：2026-09-21 ｜ 执行环境：宿主 repo 树，`platform/.venv`（Python 3.13，pytest 9.1.1）
- 对照实验两腿同 env（"无 CH" 口径 + 线程限制）：
  `FACTORLAB_DATA_BACKEND=duckdb FACTORLAB_CH_HOST=127.0.0.1 FACTORLAB_CH_PORT=1 POLARS_MAX_THREADS=1 OMP_NUM_THREADS=1`

## 1. 等价性脚本（硬验收）

```
$ bash governance/ops/tests/test_markers_equivalence.sh
→ exit 0
  P1 platform/tests : old(--deselect)=3893  new(markers)=3893
  P2 platform/tools : old(--deselect)=914   new(markers)=914
  R  research/tools : old(--deselect)=100   new(markers)=100
  另：7 个被标 node 在 fast 下均不出现；deep 下 data_on_disk/host_root 被收集、
      tick_paused/inflight/known_red 仍排除。
```

原始输出：`equivalence.txt`（含 `--collect-only` 行数对照与逐 node 出现/排除断言）。

## 2. 全量通过数对照（old --deselect vs new -m fast）

| 腿 | old 命令 | new 命令 | 结果 |
|---|---|---|---|
| platform/tests | `cd platform && .venv/bin/python -m pytest -q --deselect tests/test_factio.py::test_paths_roots_exist_or_skip` | `cd platform && .venv/bin/python -m pytest -q -m "<fast>"` | 两腿均 **2804 passed / 1089 skipped / 1 deselected / 0 failed**（old 216.04s；new 127.67s） |
| platform/tools | 仓根 `platform/.venv/bin/python -m pytest platform/tools -q --deselect tools/lob_fact/tests/test_config_paths.py::test_root_exists_on_disk --deselect tools/ch_ingest/tests/test_ch_data_semantics.py::test_reconcile_daily_covers_derived_tables --deselect tools/pan_update/tests/test_config.py::test_pan_update_and_ingest_share_data_roots --deselect tools/lob_fact/tests/test_run_lob_batch.py::test_main_cli_e2e_mini_month` | 仓根 `platform/.venv/bin/python -m pytest platform/tools -q -m "<fast>"` | 两腿均 **914 passed / 7 deselected / 0 failed**（old 388.76s；new 254.67s） |
| research/tools | 仓根 `platform/.venv/bin/python -m pytest research/tools -q --deselect tools/factor_lib/tests/test_index.py::test_every_yaml_has_mirror_doc_and_name_matches --deselect tools/strategies/tests/test_run_strategy_cli.py::test_real_run_clean_window_2025_03 --deselect tools/strategies/tests/test_run_strategy_cli.py::test_real_run_max_hold_excludes_stale_and_renormalizes` | 仓根 `platform/.venv/bin/python -m pytest research/tools -q -m "<fast>"` | 两腿均 **1 failed / 99 passed / 3 deselected**（见 §5 预存红，与 markers 无关） |

fast 表达式（冻结）：
`not needs_ch and not data_on_disk and not host_root and not tick_paused and not inflight and not known_red`

原始输出：`platform-fast-{old,new}.txt`、`tools-fast-{old,new}.txt`、`research-fast-{old,new}.txt`。

## 3. deep 表达式（冻结）

`not tick_paused and not inflight and not known_red`

```
$ cd platform && .venv/bin/python -m pytest -q -m "not tick_paused and not inflight and not known_red" --collect-only 2>&1 | tail -1
3894 tests collected in 6.30s          # 含 tests/test_factio.py::test_paths_roots_exist_or_skip（data_on_disk）
$ platform/.venv/bin/python -m pytest platform/tools -q -m "<deep>" --collect-only | tail -1
920/921 tests collected (1 deselected) # 排除 test_main_cli_e2e_mini_month（tick_paused）
$ platform/.venv/bin/python -m pytest research/tools -q -m "<deep>" --collect-only | tail -1
100/103 tests collected (3 deselected) # 排除 inflight 1 + known_red 2
```

`data_on_disk`/`host_root` 被 deep 收集的 7 个 node 与 `needs_ch=0` 说明：`markers-inventory.txt`。
原始输出：`deep-collect.txt`。

## 4. 常驻门

```
$ make gates   # rc=0 全绿（G-REF 门槛按 enforcement=report 提示不计失败）
```

原始输出：`gates.txt`。

## 5. 标注清单（原因 / issue 关联）

| nodeid | marker | 原因 / 关联 |
|---|---|---|
| `platform/tests/test_factio.py::test_paths_roots_exist_or_skip` | `data_on_disk` | tick_fact/daily_fact 在盘断言；CI checkout 无大数据必红 |
| `platform/tools/lob_fact/tests/test_config_paths.py::test_root_exists_on_disk[4 参数]` | `data_on_disk` | 四根在盘断言；skip 守卫只看 STOCK_ROOT 存在性 |
| `platform/tools/ch_ingest/tests/test_ch_data_semantics.py::test_reconcile_daily_covers_derived_tables` | `data_on_disk` | 真跑 reconcile 子进程读 DATA_ROOT，无 skip 守卫 |
| `platform/tools/pan_update/tests/test_config.py::test_pan_update_and_ingest_share_data_roots` | `host_root` | 工作区==宿主根恒等式；checkout 语义不成立 |
| `platform/tools/lob_fact/tests/test_run_lob_batch.py::test_main_cli_e2e_mini_month` | `tick_paused` | tick 全线暂停；GitHub issue **#15**（PLAN-LOB-PAUSED；恢复时删标记） |
| `research/tools/factor_lib/tests/test_index.py::test_every_yaml_has_mirror_doc_and_name_matches` | `inflight` | 挖矿在途档案时效门（owner=挖矿流程，补齐即删；非 issue 制） |
| `research/tools/strategies/tests/test_run_strategy_cli.py::test_real_run_clean_window_2025_03` | `known_red` | DQ health 对 2025-03 历史分区 UNKNOWN 拒单；GitHub issue **#25**；删标记条件=按新 data_version 复跑通过 |
| `research/tools/strategies/tests/test_run_strategy_cli.py::test_real_run_max_hold_excludes_stale_and_renormalizes` | `known_red` | 同上（issue #25） |

`needs_ch` 已声明但当前 **0 个用例**：CH 集成测试保留既有 `integration` 标记 + 不可达 skip
守卫，旧 fast 亦未 deselect 它们，无 fast 排除缺口；留待 Task 2 deep CH 腿/后续按需标注。

## 6. 遗留问题（非本任务引入）

- `research/tools/strategies/tests/test_run_strategy_cli.py::test_results_dir_default_follows_platform_settings`
  在宿主树预存红：`f22b764`（产物归位研究区）后 `settings.results_dir` 研究区优先为
  `<QUANTRESEARCH_ROOT>/results/platform`（`.parent.name == "results"`），而用例断言
  `"runs"`。CI（无研究产物区）走 `runs/platform` 回退仍绿；该用例不在 Task 1 排除集内，
  **未加 marker**，建议由产物归位主题单独修/更新断言（Task 5 deep 收口前需处置）。
- 本目录证据由 Task 1 执行时采集；commit SHA 见提交记录。
