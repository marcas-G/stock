# 评审台账（findings）—— 唯一状态源

- 轮次：**R01**（2026-09-15 严格 review，HEAD `dd01bd9`）
- 统计：**11 Critical / 37 Important**（Minor 只存于 `r01-2026-09-15-strict-review/report.md`）
- 状态词表与流程见 `README.md`；证据文件在 `r01-2026-09-15-strict-review/evidence/`
- `实测` = 本轮 coordinator 亲自复现；`probe` = 评审 probe 脚本复现（脚本已存档）

> **R02 复查（2026-09-15 第二轮）**：R21 已提交（`9a5c3d3`）并回填 63 行 fixed-claimed；本轮复核 56 行 verified、
> 7 行 reopened/partial，并新增 **2 Critical + 9 Important**（R02-C1 分钟门旁路、R02-C2 staleness 短窗/分块洞 等）。
> 详见文末「R02 复查」节与 `r02-2026-09-15-strict-review/report.md`。
>
> **R02/R03 修复回填（2026-09-16，开发团队）**：R02 新增 2C+9I 全部 `fixed-claimed`（C1 808c40b / C2 da8bba0+f55befa / I1 b766092 / I2 9dbe0ed / I3 5088f77 / I4 d973e0e / I5 620940a / I6a dc8c2b6 / I6b fb62c08+0d09bf5 / I7 7c8e26a / I8 3cd1b1f / I9 a9bd28c 等）；R01 七条 reopened/partial 的闭环注记见各原行与例外表。R03 五条（I1 79d256a / I2 136c30d / I3 0455502 / M1 ba59430 / M2 55e754c）同批回填。平台全量 2969 passed/13 skipped；R22 零迁移 6 代表 spec |Δ|=0；证据 `docs/verification/R22/R02|R03/`。

> **R21 修复回填（2026-09-15，开发团队）**：台账 63 行全部 `fixed-claimed`（含 EVID C1/C2；
> 行数口径为 13C + 50I，与首行"11/37"的差异源于 EVID 两项 Critical 未计入统计行——按
> append-only 约定不改写统计行）。每行修复说明含 commit + 复跑命令 + 原始输出路径；
> 证据总目录 `docs/verification/R21/`（187 文件，按子系统分目录），基线复核见
> `docs/verification/R21/README.md`。R21 提交：平台 ea2ebfe / fc2858c / 0687914 / 87958b2 /
> 3af7275 / 08432ab；研究 5187030 / b192235 / 0bb1b2f / 0d21e7e；工作区 e3d2454（+本行提交）。

## ENG —— 平台引擎 / DSL

| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填：commit+命令+输出） | 复查（reviewer） |
|---|---|---|---|---|---|---|
| R01-ENG-C1 | C | 未来函数旁路#1：`signal = close[-1]` 过 lint，E2E 实测 signal(t)=close(t+1)（pool 公式同样）【实测】 | `core/factor/ast_gate.py:30`；`core/engine/partitions.py:142-153` | verified | ea2ebfe；复现：platform/.venv/bin/factorlab lint docs/reviews/r01-2026-09-15-strict-review/evidence/engine-dsl/future_subscript.yaml（修复后 exit≠0）；E2E：.venv/bin/python evidence/engine-dsl/probe_run_factor.py（signal(t)=close(t+1) → REJECT）；测试 tests/test_partitions.py::test_rejects_negative_subscript、test_run_factor.py::test_run_factor_rejects_negative_subscript；证据 docs/verification/R21/ENG/C1-probe-before/after.txt、C1-pool-probe-after.txt（池公式同封） | verified（R02 复查 §1） |
| R01-ENG-C2 | C | 未来函数旁路#2：`_n=3; ts_delay(close,-_n)` 过 lint，E2E 实测 shift(-3)；`-1` 字面量反而被拒【实测】 | `core/engine/partitions.py:62-94,136-140` | verified | ea2ebfe；lint future_const.yaml 修复后 exit≠0；E2E probe_run_factor（shift(-3) → REJECT）；测试 tests/test_partitions.py::test_rejects_negative_shift_via_name_inside_unary_and_binop；证据 R21/ENG/C2-probe-before/after.txt | verified（R02 复查 §1） |
| R01-ENG-I1 | I | `--chunk-days` 对累计算子（`ts_cum_sum`/`vwap`）违反文档"逐 cell 一致"承诺 | `core/engine/compute.py:220-262`；`app/run.py:379-383` | verified | ea2ebfe；测试：cd platform && .venv/bin/python -m pytest -q tests/test_run_factor.py -k chunk_days（拒 ts_cum_sum/vwap/池公式，控制 ts_mean 仍可分块）；证据 R21/ENG/I1-probe-before/after.txt | verified（R02 复查 §1） |
| R01-ENG-I2 | I | 插件"AST 安全扫描"可被 `from os import system` 绕过（实测扫描 PASS 并写入标记） | `adapters/plugins.py:46-49` | verified | ea2ebfe；测试 tests/test_ops.py::test_plugin_importfrom_bypass_rejected（4 形态）+ ::test_plugin_importfrom_side_effect_blocked_before_import；证据 R21/ENG/I2-probe-before/after.txt（marker 未写入、注册表零污染） | verified（R02 复查 §1） |
| R01-ENG-I3 | I | 插件可静默覆盖内建算子（import 先于 `--force` 冲突检查；`ts_mean` 被替换实测） | `adapters/plugins.py:99-111` | verified | ea2ebfe；测试 tests/test_ops.py::test_builtin_override_rejected_before_import_without_force（marker 不存在 + ts_mean 版本未变）；证据 R21/ENG/I3-probe-before/after.txt；【R02 partial 已闭环】7c8e26a：别名/命名常量前置拒绝 + 动态注册快照回滚，见 R22/R02/plugins/ | verified（R02 §1 原判 reopened/partial → R22 批次闭环，证据见左列） |
| R01-ENG-I4 | I | `cs_regression_resid` 注册但调用即 `NameError`；`cs_resid` 未注册（注册表/catalog/interface 三处不一致） | `core/ops/polars_ta_wrappers.py:27-29`；`platform/docs/catalog.md:64` | verified | ea2ebfe；测试 tests/test_polars_ta_wrappers.py::test_cs_resid_and_alias_compute_real_values（真 compute_formula 数值，硬编码桩必败）；catalog 重生成后 tests/test_catalog.py 27 passed；证据 R21/ENG/I4-probe-before/after.txt、catalog-drift-note.txt | verified（R02 复查 §1） |
| R01-ENG-I5 | I | `factorlab lint` 不跑任何语义门（未来函数/未知算子 spec 照样 exit 0）【实测】 | `surfaces/cli/main.py:47-77` | verified | ea2ebfe；三 probe（future_subscript/future_const/neg_shift_lint）lint exit≠0；make lint-factors 152/152；测试 tests/test_cli_lint_params.py::test_lint_rejects_review_probe_specs；证据 R21/ENG/I5-probe-before/after.txt、lint-factors-after.txt | verified（R02 复查 §1） |

## DATA —— 平台数据面（读路径 / PIT）

| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填） | 复查 |
|---|---|---|---|---|---|---|
| R01-DATA-C1 | C | 退市股永不退市：`stock_basic` 无 delist 字段，600005.SH 最后成交 2017-02-13 仍 `is_listed=True` 并 forward-fill 死价格入 2026 截面；`FULL_HISTORY_PIT_GATE` 仅文档【实测】 | `adapters/read/universe.py:596-598,621-636` | verified | 平台 fc2858c（staleness.py + run_factor 接线）+ 数据 5187030（stock_basic.delist_date 灌入，sidecar 331 codes）；复现：platform/.venv/bin/python /tmp/opencode/reviewer-data/probe_delisted.py → 600005.SH @2026-08-14 is_listed=False；测试 tests/test_pit_staleness.py（11，含双腿）；证据 R21/DATA/C1.md、probe_delisted_before.txt、probe_stale_gate_prod_after.txt、R21/TOOLS-A/after/probe_delisted.txt；【R02 reopened 已闭环】da8bba0 + f55befa：窗口无关判定（全历史 last close + 有界回看）+ 退市 code 不误杀 seed；240 日/30d 分块回归见 R22/R02/data/c2-* | verified（R02 §1 原判 reopened/partial → R22 批次闭环，证据见左列） |
| R01-DATA-C2 | C | `pre_close/pct_chg` 为 raw 前收而非 catalog 承诺的"除权参考价"：300842.SZ 2024-04-10 `pre_close=70.9, pct_chg=-28.1%`【实测】；`stk_limit` 同源派生 | `research/tools/ch_ingest/ingest_daily.py:4,52-54`；`platform/docs/catalog.md` pre_close 行 | verified | 5187030；ingest_daily 按除权事件列（div_cash/div_bonus/div_transfer/rights_num/rights_price）算除权参考价 half-up，change/pct_chg 随动；300842.SZ 2024-04-10 pre_close=50.07、pct=+1.8175%（raw 为 70.9/-28.1%）；2025 全 4,925 事件日公式 0 违规；stk_limit 随之重派生（probe2：2025 事件日越带宽 297→0）；测试 research/tools/ch_ingest/tests/test_ingest_daily_derive.py（现金+送转/只派现/只送转/配股/half-up/无事件/退市无事件列）；CH 真读 after/factor_signal.txt；证据 docs/verification/R21/TOOLS-A/ | verified（R02 复查 §1） |
| R01-DATA-I3 | I | `pit_qfq` 复权视图分块依赖（full≠chunk，实测 `[6.67,7.33,8,9]` vs `[10,11,8,9]`），违反 asof 全局固定 | `adapters/read/adjust.py:133-143`；`app/run.py:194-196` | verified | fc2858c；测试 tests/test_adjust.py::test_view_pit_qfq_global_base_full_equals_chunked；复跑 /tmp/opencode/reviewer-data/probe_pit_chunk.py（after full==chunk）；证据 R21/DATA/I3.md、probe_pit_chunk_before/after.txt | verified（R02 复查 §1） |
| R01-DATA-I4 | I | CH `delist_date` 为 Date 类型时 `resolve_universe_frame` 崩溃（String schema 硬编码；latent） | `adapters/read/universe.py:522,610-614` | verified | fc2858c；ch 腿 delist_date=Date 兼容（tests/dualbridge.py 新增 date? kind；tests/test_pit_universe.py 90 passed）；复跑 probe_delist_date_ch（after 不崩且 PIT 正确）；证据 R21/DATA/I4.md、probe_delist_date_ch_before/after.txt、i4_before_failure.txt | verified（R02 复查 §1） |
| R01-DATA-I5 | I | `data rebuild` 崩溃断点续跑可产生 `(trade_date,ts_code)` 重复行；无 integrity gate | `adapters/rebuild.py:437-461` | verified | fc2858c；测试 tests/test_rebuild.py::test_rebuild_resume_after_crash_no_duplicate_rows、::test_build_final_db_aborts_on_duplicate_daily；证据 R21/DATA/I5.md | verified（R02 复查 §1） |
| R01-DATA-I6 | I | `save_manifest` 非原子（违反 R13 atomicio 单点），截断 JSON 无法加载 | `adapters/rebuild.py:34-37` | verified | fc2858c；测试 tests/test_rebuild.py::test_load_manifest_truncated_json_quarantined、::test_save_manifest_atomic_failure_keeps_previous；atomicio commit 失败清 tmp 一并修；证据 R21/DATA/I6.md | verified（R02 复查 §1） |
| R01-DATA-I7 | I | duckdb vs ch `vol/amount` 单位漂移（ch=股/元，旧 teajoin 库=手/千元，100×/1000×），且双腿测试测不出 | `adapters/read/source.py:11` | verified | fc2858c；测试 tests/test_source.py::test_load_daily_normalizes_duckdb_units（duckdb 源 1234 手→123400 股、5678 千元→5678000 元，恒等实现必挂）；证据 R21/DATA/I7.md、probe_unit_scale.txt | verified（R02 复查 §1） |
| R01-DATA-I8 | I | `refresh` 丢弃失败原因（playbook 承诺"含错误原因"） | `adapters/refresh.py:49-50` | verified | fc2858c；测试 tests/test_refresh.py::test_refresh_records_failed_errors（str(exc)[:120] 进 manifest/report）；证据 R21/DATA/I8.md | verified（R02 复查 §1） |

## M8 —— M7/M8 组合与回测

| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填） | 复查 |
|---|---|---|---|---|---|---|
| R01-M8-I1 | I（建议升 C） | artifact 覆盖写崩溃窗口：manifest 最后写、旧 manifest 不先失效 → "新数据+旧 manifest"可被加载（实测混合 artifact：nav 与 nav_series 矛盾仍通过校验） | `adapters/strategy_artifacts.py:197-246`；`adapters/execution_store.py:156-217` | verified | 0687914；复跑 /tmp/opencode/reviewer-m8/probe3_stale_manifest.py 与 probe4_exec_hybrid.py（after 均 fail loudly，不再混合可加载）；测试 tests/test_strategy_artifacts.py::test_failed_overwrite_never_hybrid_loadable、tests/test_backtest_persistence.py::test_failed_overwrite_never_hybrid_loadable；证据 R21/M8/I1-before/after-probe3-strategy-hybrid.txt、I1-before/after-probe4-exec-hybrid.txt | verified（R02 复查 §1） |
| R01-M8-I2 | I | `FillBatch.order_quantity` 恒等于 `filled_quantity`，部分成交在 fill 行不可审计 | `platform/src/factorlab/app/backtest/fills.py:275`（R04-Q7 校正：原 core/execution/fills.py 经 R19/R20 迁至 app/backtest，旧坐标不再反引号标注以免门扫描） | verified | 0687914；测试 tests/test_backtest_fills.py::test_partial_buy_order_quantity_auditable（order 1000 / filled 400）；::test_partial_buy_fill_batch_validator_accepts_filled_lt_order；证据 R21/M8/I2-before-test-red.txt、I2-after-test-green.txt | verified（R02 复查 §1） |
| R01-M8-I3 | I | `load_adj_event_window` 空窗口返回 Null 类型 frame，违反"typed empty"契约 | `adapters/read/market_open.py:374` | verified | 0687914；测试 tests/test_backtest_ca_gate.py::test_loader_existing_table_empty_window_typed、::test_loader_existing_table_empty_codes_typed（String/Date 显式 dtype）；证据 R21/M8/I3-before/after-probe1-empty-adj.txt | verified（R02 复查 §1） |
| R01-M8-I4 | I | `decision_range` 只过滤决策但 schedule 用全量 target，范围外尾部未决决策仍会使运行失败 | `app/backtest/backtest.py:173-181` | verified | 0687914；测试 tests/test_backtest_runtime.py::test_decision_range_ignores_out_of_range_trailing（范围外尾部未决不参与）；证据 R21/M8/I4-I5-before-test-red.txt、I4-I5-after-test-green.txt | verified（R02 复查 §1） |
| R01-M8-I5 | I | 尾部未决决策直接 fail 全 run，与 m8-06a §6.3"合法终止，不 drop 中间结果"文档矛盾 | `app/backtest/backtest.py:316`；`overnight.py:85-88` | verified | 0687914；测试 tests/test_backtest_runtime.py::test_trailing_unresolved_legal_termination_preserves_results、::test_in_range_trailing_decision_still_fails（范围内未决仍硬错）；tests/test_backtest_persistence.py::test_trailing_result_roundtrip；证据 R21/M8/I4-I5-before/after.txt | verified（R02 复查 §1） |
| R01-M8-I6 | I | M8 加载忽略 manifest 的 columns/created_at/runtime_version/日期范围；`artifact_count: true` 当 1 通过 | `adapters/execution_store.py` load 路径 | verified | 0687914；测试 tests/test_backtest_persistence.py::test_load_rejects_bool_artifact_count、-manifest_columns_mismatch、-invalid_created_at、-invalid_runtime_version、-date_range_mismatch、-missing_date_range；证据 R21/M8/I6-before-test-red.txt、I6-after-test-green.txt | verified（R02 复查 §1） |
| R01-M8-I7 | I | 持久化不携带 `execution_timing`，加载端硬编码 NEXT_OPEN（未来静默错标风险） | `adapters/execution_store.py:288-298` | verified | 0687914；测试 tests/test_backtest_persistence.py::test_manifest_persists_execution_timing、::test_load_rejects_missing_execution_timing、::test_load_rejects_next_close_execution_timing、::test_save_rejects_next_close_artifacts；证据 R21/M8/I7-before/after.txt | verified（R02 复查 §1） |

## EVAL —— 评估与统计

| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填） | 复查 |
|---|---|---|---|---|---|---|
| R01-EVAL-C1 | C | `layered_backtest` 只滤 null 不滤 NaN：NaN signal 被排进 D1、NaN fwd 使 D10 净值此后全 nan（实测） | `core/eval/layered.py:105-107,124` | verified | 87958b2；复跑 /tmp/opencode/reviewer-eval/probe_layered.py（after D10 净值 finite、summary 非 NaN）；测试 tests/test_layered.py::test_layered_backtest_nan_signal_excluded_from_top_decile、::test_layered_backtest_nan_forward_not_poisoning_nav_tail、::test_layered_backtest_matches_kernel_on_nan_panel；证据 R21/EVAL/red_batchA.txt → green_batchA.txt、after/probe_layered.txt | verified（R02 复查 §1） |
| R01-EVAL-C2 | C | `weekly_ic`（Web 曲线）含 NaN：mean 0.788 vs summary/kernel 1.0，同页两口径（实测） | `core/eval/ic_series.py:25` | verified | 87958b2；复跑 probe_turnover_nan.py（after weekly_ic mean 1.0 == kernel）；测试 tests/test_ic_series.py::test_weekly_ic_nan_rows_excluded_matches_kernel、::test_weekly_ic_infinite_signal_excluded；证据 R21/EVAL/before/after/probe_turnover_nan.txt | verified（R02 复查 §1） |
| R01-EVAL-I1 | I | Web 详情页忽略 `spec.target`：20d 因子显示 5d IC 曲线（实测符号相反） | `surfaces/web/app.py:145` | verified | 87958b2；复跑 probe_web.py（after matches 20d=True）；测试 tests/test_web.py::test_factor_detail_ic_curve_uses_spec_target、::test_factor_detail_5d_target_regression；证据 R21/EVAL/after/probe_web.txt | verified（R02 复查 §1） |
| R01-EVAL-I2 | I | `factor_correlation` 的"秩相关"不是 Spearman：并列/NaN/零方差时错，可报 1.0（实测 probe） | `app/analysis/correlation.py:110-113` | verified | 87958b2；复跑 probe_correlation.py（并列 0.8、NaN 成对删除、零方差 NaN）；测试 tests/test_correlation.py::test_spearman_average_rank_on_ties、-nan_pairwise_deletion、-zero_variance_is_nan_not_one、-degenerate_pair_count_not_1；证据 R21/EVAL/after/probe_correlation.txt | verified（R02 复查 §1） |
| R01-EVAL-I3 | I | 相关性按**日频**跑却文档称"周频与 IC 同口径"；resIC 的"同口径"引用同样失真 | `app/analysis/correlation.py:95` | verified | 87958b2；相关性改周频（align_weekly）与 IC 同口径；测试 tests/test_correlation.py::test_factor_correlation_aligns_weekly_not_daily；复跑 probe_correlation case4（n_weeks 10→2）；证据 R21/EVAL/after/probe_correlation.txt | verified（R02 复查 §1） |
| R01-EVAL-I4 | I | 20M 护栏抽样按帧序取前 N 行（代码位次偏置），非文档所称"均匀抽样" | `app/analysis/correlation.py:60-65` | verified | 87958b2；护栏抽样改等距 stride；测试 ::test_guard_subsampling_covers_head_and_tail；复跑 probe_correlation case5（[1..5]→{1,5,9,13,17} 覆盖头尾） | verified（R02 复查 §1） |
| R01-EVAL-I5 | I | Web 详情缺 top-level 标量（如 `signal_null_ratio`）直接 500，违反"降级展示不崩溃" | `surfaces/web/templates/factor.html:31` | verified | 87958b2；测试 tests/test_web.py::test_factor_detail_missing_top_level_scalars_degrade（真 GET 200 且降级）；证据 R21/EVAL/red_web.txt → green_correlation_web.txt | verified（R02 复查 §1） |
| R01-EVAL-I6 | I | `layered.periods` 与 kernel `n_weeks` 在单股周不等（2 vs 1），同一 summary 内部矛盾 | `core/eval/layered.py:105-109` | verified | 87958b2；测试 tests/test_layered.py::test_layered_periods_matches_kernel_min_stocks、::test_layered_two_stock_week_counted_like_kernel；复跑 probe_layered (1)（2 vs 1 → 1 vs 1）；证据 R21/EVAL/after/probe_layered.txt | verified（R02 复查 §1） |
| R01-EVAL-I7 | I | `t_stat/sign_consistent` 分母含退化周（实测 t=182 vs 标准 141），UI 仍标"显著"；契约 §3.1 与 §6 自相矛盾 | `kernels/quant_core/quant_core/__init__.py:188-199`；`templates/factor.html:17-19` | verified | 87958b2；测试 tests/test_quant_core_shim.py::test_degenerate_weeks_excluded_from_t_and_sign_denominators；复跑 probe_kernel_adversarial（t 182.24→141.16，sign 0.6→1.0）；契约 §3.1/§6 勘误见 08432ab；证据 R21/EVAL/after/probe_kernel_adversarial.txt；【R02 partial 已闭环】a9bd28c：契约正文旧口径行已加【R21 勘误 7a】指针（分母=n_ok） | verified（R02 §1 原判 reopened/partial → R22 批次闭环，证据见左列） |
| R01-EVAL-I8 | I | kernel decile 用 ordinal rank → 并列信号分层随行序漂移（实测 spread −0.08 vs 0.0） | `kernels/quant_core/quant_core/__init__.py:160-161` | verified | 87958b2；kernel decile 改 average-rank 对称分位；测试 tests/test_quant_core_shim.py::test_decile_ties_row_order_invariant_numeric、::test_heavy_ties_deterministic_nan_spread；复跑 probe_kernel_adversarial（A/B same=True）；§4.3 向量 1 逐字段不变（after/probe_contract.txt）；【R02 partial 已闭环】a9bd28c：契约正文 decile 旧式已加【R21 勘误 7b】指针（average-rank 对称分位） | verified（R02 §1 原判 reopened/partial → R22 批次闭环，证据见左列） |
| R01-EVAL-I9 | I | 测试盲区：turnover 只查范围、无 NaN/tie/零方差/退化周用例、web e2e 只查字符串 | `platform/tests/test_quant_core_shim.py:172` 等 | verified | 87958b2；补强：turnover NaN/tie/零方差/退化周（tests/test_quant_core_shim.py）、correlation 四类对抗、web e2e 数值断言（tests/test_e2e_web.py）；整包 244 passed/4 skipped（green_all_eval.txt） | verified（R02 复查 §1） |

## TOOLS —— 研究侧数据工具

| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填） | 复查 |
|---|---|---|---|---|---|---|
| R01-TOOLS-C1 | C | `daily_basic.total_mv/turnover_rate` 单位错 1e4（600519 实测 16883.6 vs 应 ≈1.688e8 万元；turnover 4409.91 vs 0.441%），18.16M 行全错 | `research/tools/ch_ingest/ingest_daily.py:55-56` | verified | 5187030；测试 research/tools/ch_ingest/tests/test_ingest_daily_derive.py::test_total_mv_and_turnover_units_600519；重灌后 after/verify_points.txt（600519 2026-07-31：168,836,020.9 万元 / 0.4410%）；对账 after/reconcile_all_run2.log（exit 0） | verified（R02 复查 §1） |
| R01-TOOLS-C2 | C | 退市文件 in-file `code` 被忽略：`000018/000023/000024/000033/000038` 携带 600811 数据（实测 1612 个交易日 close+vol 完全相等） | `research/tools/ashare_ingest/import_daily.py:118-145` | verified | 5187030；测试 research/tools/ashare_ingest/tests/test_import_daily_delisted.py（9 条含子进程 E2E）；重生成 daily_fact.parquet（sha256 79f68fee…，18,124,805 行）并重灌；after/check_600811_content.txt（600811 7598 行逐值 MATCH，000018 等 0 行）；after/probe5/6/7 | verified（R02 复查 §1） |
| R01-TOOLS-I1 | I | parquet null→0：CH `adj_factor/amount` 为 0（1.29M 行，333 只 argMax=0），平台 qfq 基准 0 除 | `ch_ingest/ddl.sql:23-41`；`ingest_daily.py` insert | verified | 5187030；DDL 改 Nullable + NaN/非正 adj→NULL（after/ddl_alter.txt）；测试 tests/test_ch_data_semantics.py；after/probe6_adjfactor.txt（adj_factor 非正=0、latest<=0=0、NULL=1,259,244） | verified（R02 复查 §1） |
| R01-TOOLS-I2 | I | checkpoint 由 worker 进程 read-modify-write 无合并/锁（实测 8 worker 存活 1，丢 7）；无 BatchFlock 锁路径 | `ch_ingest/ch_write.py:49,81-83`；`ch_state.py:44-54` | verified | 5187030；state.json.lock 阻塞 flock + 主进程记账；测试 tests/test_ch_state_concurrency.py（8 spawn 全存活、外部 key 合并）；证据 R21/TOOLS-A/RED-GREEN.md | verified（R02 复查 §1） |
| R01-TOOLS-I3 | I | 失败分区仍 exit 0（`run_pool` 返回被忽略），cron/CI 误报成功 | `ingest_bars.py:22`；`ingest_tick.py:30`；`ch_write.py:84-90` | verified | 5187030；run_pool 返回失败数，ingest_bars/ingest_tick 失败非空 exit 1；测试 tests/test_ch_write_exit.py（5）；【R02 partial 已闭环】fb62c08 + 0d09bf5：解析错误 exit 1；非空 code 零解析 fail fast；空 sheet stub 跳过不阻断重灌，见 R22/R02/tools/I6b-* | verified（R02 §1 原判 reopened/partial → R22 批次闭环，证据见左列） |
| R01-TOOLS-I4 | I | `stk_limit` 除权日基准错（raw pre_close）：2025 年 99.7% 除权日 band 偏离；CA Gate 只保护持仓 | `derive_stk_limit.py:3-4` | verified | 5187030；pre_close 除权参考价重灌后重派生 stk_limit；after/probe2_stklimit.txt（2025 越带宽 314→17、事件日 297→0；2024/2026 6/6）；docstring 删除除权日近似 | verified（R02 复查 §1） |
| R01-TOOLS-I5 | I | `stk_limit` 缺失语义双方矛盾：生产者称"无限制合法" vs 平台 fail-closed | `ch_ingest/README.md:66-67` vs `core/execution/fillability.py:99-105` | verified | 平台侧 3af7275（fillability 缺行=合法无限制）+ 生产侧 5187030；测试 platform/tests/test_open_fillability.py（改 1 增 1；-k fillability 218 passed）；README/interface 口径对齐 08432ab；【R02 partial 已闭环】620940a：平台侧 stk_limit 市场级覆盖率门（正常日不误伤，人为缺口 fail loudly），见 R22/R02/data/i5-* | verified（R02 §1 原判 reopened/partial → R22 批次闭环，证据见左列） |
| R01-TOOLS-I6 | I | `daily_basic` 5 个占位列对用户可见且 100% NULL（`pe_ttm` 全表 0 非空，实测）；`idx_ret` 广告但 index_daily 空 | `ingest_daily.py:79-85`；`interface.md:324-326` | verified | 5187030（保留 DDL 空列：平台读路径依赖列存在，README 标注占位无源）+ 08432ab（interface daily_basic 扩展字段标注 5 列恒 NULL、idx_ret 恒 NULL）；grep 证据见 TOOLS-A/README.md | verified（R02 复查 §1） |
| R01-TOOLS-I7 | I | reconcile 只对行数且不含派生表（stk_limit/adj_detail/adj_event）；bars 行数文档过期 | `ch_ingest/reconcile.py:23-29,67-84` | verified | 5187030；reconcile 扩到 daily 恒等式/日期范围 + stk_limit 期望行数独立复算/band/悬空 + adj_detail/adj_event；复跑 platform/.venv/bin/python research/tools/ch_ingest/reconcile.py → exit 0 全绿（after/reconcile_all_run2.log）；测试 tests/test_ch_data_semantics.py::test_reconcile_daily_covers_derived_tables；bars 行数 README 同步 1,853,379,840 | verified（R02 复查 §1） |
| R01-TOOLS-I8 | I | layer3 tick 对非当日事件静默编造 pre/post 窗口；异常被吞（`except Exception: r={}`） | `universe_stages/scripts/run_layer3_tick.py:78-90` | verified | b192235；非当日事件跳过并记录 issues、特征异常落 feature_error（不吞）；tick reader 拒绝跨日重标；测试 research/tools/universe_stages/tests/test_layer3_tick_guard.py（5，红→绿）；证据 R21/TOOLS-B/I8-red.txt、I8-cli-red.txt、I8-green.txt | verified（R02 复查 §1） |
| R01-TOOLS-I9 | I | layer1 端到端不可跑（fundamentals/7z 缺）；MIGRATION_GAP 标题强于可执行状态 | `universe_stages/universe_paths.py:65-72` | verified | b192235；preflight_layer1/2/3 接入口（缺 fundamentals/7z → 点名路径与获取指引、无 traceback）；MIGRATION_GAP 对齐可执行状态；测试 tests/test_preflight.py（8，红→绿）；证据 R21/TOOLS-B/I9-red.txt、I9-green.txt；【R02 partial 已闭环】dc8c2b6：三 CLI T2 自举 + 裸跑（env -u PYTHONPATH）usage exit 0 测试，见 R22/R02/tools/I6a-* | verified（R02 §1 原判 reopened/partial → R22 批次闭环，证据见左列） |
| R01-TOOLS-I10 | I | `converters --only-day` 会原子替换整月产物为单日文件（"单日验证"脚枪） | `converters/convert_tick_to_parquet.py:318-326` | verified | b192235；--only-day 默认落 validation 根、显式指向生产根拒绝（月产物字节不变）；测试 research/tools/converters/tests/test_convert_tick_e2e.py::test_only_day_writes_to_validation_root_and_leaves_month_untouched、::test_only_day_refuses_production_out_root；证据 R21/TOOLS-B/I10-red.txt、I10-green.txt | verified（R02 复查 §1） |

## STRAT —— 策略 / LOB 周边

| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填） | 复查 |
|---|---|---|---|---|---|---|
| R01-STRAT-C1 | C | `strategy_crash_bottom` 跌停过滤静默失效：取 6 位 code 对 canonical ts_code join（M7-05 后），文档 +20pp 特性消失【实测代码】 | `strategy_crash_bottom.py:96-97,506-510` | verified | 0bb1b2f；6 位 code 归一后再 join；测试 research/tools/strategies/tests/test_strategy.py::test_strategy_limit_down_normalizes_code_format；复跑 probe_strategy（after 过滤生效 weeks=0，before weeks=1/nav=1.10）；证据 R21/STRAT/probe_before.txt、C1_C2_I5_probe_after.txt | verified（R02 复查 §1） |
| R01-STRAT-C2 | C | 段边界清仓成本未计、holdings 跨段泄漏（实测 2 段只收 1 次成本，应为 3 次），81.9%/1.86 被低估 | `strategy_crash_bottom.py:117-123,182-184` | verified | 0bb1b2f；段界清仓计卖出成本 + holdings 重置 + 再入场买入成本；测试 ::test_strategy_segment_boundary_charges_liquidation_and_reentry 等 4 条；复跑 probe_strategy_cost.py（after total_cost=0.0105 = 3 次）；证据 R21/STRAT/C2_rules_mode_red_green.txt | verified（R02 复查 §1） |
| R01-STRAT-C3 | C | `quark_download_server.py` 入口死代码（`NameError: COOKIES`，实测） | `quark_download_server.py:95` | verified | 0bb1b2f；入口改用共享 quark_client.cookies（缺失显式 exit 1）；测试 research/tools/quark_download/tests/test_quark_download.py::test_server_main_cookie_missing_exits_with_message_not_nameerror、::test_server_main_proceeds_past_cookie_check_with_manifest；证据 R21/STRAT/C3_red.txt（NameError 2 failed → 8 passed） | verified（R02 复查 §1） |
| R01-STRAT-I4 | I | `lib/tickkit.py` 模块级 import factorlab 无 `ensure_platform()`：单跑 `extract_sz_cancels` 在 T2 下 collection error，全量套跑靠测试顺序泄漏假绿 | `lib/tickkit.py:17`；`extract_sz_cancels.py:44-51` | verified | 0bb1b2f；tickkit 模块自举 ensure_platform + 落位断言；测试 research/tools/lob_fact/tests/test_platform_bootstrap.py（污染解释器 + T2 子进程单模块 collection/单跑）；证据 R21/STRAT/I4_before_t2.txt → I4_red.txt → I4_after_t2.txt | verified（R02 复查 §1） |
| R01-STRAT-I5 | I | `--long --mc` 崩溃（`KeyError: 'returns'`） | `strategy_crash_bottom.py:408` | verified | 0bb1b2f；long 模式 per_episode['returns'] 补齐；测试 ::test_strategy_long_episodes_carry_returns_for_monte_carlo；probe mc long ok（C1_C2_I5_green.txt） | verified（R02 复查 §1） |
| R01-STRAT-I6 | I | 策略宣称结果无 in-tree 产物、不可复现（`platform/results/` 空） | `research/docs/strategies/crash_bottom_leader_strategy.md` | verified | 0bb1b2f；策略文档改标历史快照 + 不可复现原因（index_daily 0 行 / stock_st 缺失 / circ_mv 全 null）+ 恢复条件 + 重跑命令链（research/docs/strategies/crash_bottom_leader_strategy.md）；证据 R21/STRAT/i6_data_availability.txt、i6_factor_run.log、i6_run_note.md；真产物待数据恢复后重跑 | verified（R02 §1：按“标注不可复现”闭环） |
| R01-STRAT-I7 | I | lob 生产 gate 只强制 M1a/M4，M2/M3/ghost_vol 只记录不拦截（与 W3 memo 矛盾） | `pipeline/run_lob_batch.py:145-174` | verified | 0bb1b2f；lob batch 补 M2 差量全分类硬门 + 结构性 error 不豁免 + 未完成 exit 1；memo 口径修订（w3/w4）；测试 research/tools/lob_fact/tests/test_run_lob_batch.py（4 条；33 passed）；证据 R21/STRAT/I7_red_green.txt | verified（R02 复查 §1） |
| R01-STRAT-I8 | I | v2 下载重试 3 次仍缺链接时清空全部已成功 URL，导致可下载文件也报无链接 | `quark_download_v2.py:159-160` | verified | 0bb1b2f；重试只剔除仍缺链 fid、保留成功 URL；测试 ::test_v2_partial_link_failure_keeps_successful_urls；证据 R21/STRAT/I8_red.txt（f1 也报 no link → 9 passed） | verified（R02 复查 §1） |

## EVID —— 文档 / 证据完整性

| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填） | 复查 |
|---|---|---|---|---|---|---|
| R01-EVID-C1 | C | 152 份因子档案的验证数字无 in-tree 产物且当前不可复跑（results/ 空、duckdb 无、CH 无 stock_st），未标"历史快照"【实测】 | `research/docs/factors/**`；`platform/results/` | verified | 0d21e7e；152 份档案 front matter 加 snapshot 标注 + research/docs/factors/README.md（不可复跑原因/恢复条件）；标注脚本 docs/verification/R21/EVID/annotate_factor_archives.py --check 齐备；python3 research/tools/factor_lib/build_index.py --check 索引一致；证据 R21/EVID/C1-not-reproducible.txt、C1-annotation.txt、C1-index-gate.txt | verified（R02 复查 §1） |
| R01-EVID-C2 | C | R12/R13 的"真 CH 端到端/Web 冒烟"无原始输出（目录仅 status.md + platform-pytest.log），不符合本仓证据纪律 | `docs/verification/R12/status.md:43`；`R13/status.md:40` | verified | e3d2454；R12 真 CH 端到端复跑（FACTORLAB_DATA_BACKEND=ch factorlab run R12/r12_smoke.yaml → exit 0，n_weeks=3）+ R13 Web 冒烟（factorlab serve + curl：GET / 200、GET /factor/r12_smoke 200）；原始输出 docs/verification/R12/r21-recheck-run.txt、r21-recheck-layout.txt、R13/r21-recheck-web.txt + 两个 html；status.md 追加 R21 补录节 | verified（R02 §1：按“标注不可复现”闭环） |
| R01-EVID-I1 | I | research README 基线过期：237/51（R19）vs 实测 245/59；"T1 35"算术不符 | `research/README.md:24,40,43` | verified | 0d21e7e；实测：emb/bin/python -m pytest research/tools -q → 268 passed/10 skipped；platform/.venv/bin/python -m pytest research/tools/{strategies,ch_ingest,factor_lib,1m_features,ashare_ingest,universe_stages}/tests -q → 116 passed；research/README.md 按 Makefile 名单更新；证据 R21/EVID/I1-I2-research-T2.txt、I1-research-T1.txt、I1-I2-per-dir-counts.txt | verified（R02 复查 §1） |
| R01-EVID-I2 | I | "183 tests" 过期：实测 185（lob_fact） | `CLAUDE.md:74`；`research/CLAUDE.md:51` | verified | 0d21e7e；lob_fact 实测 191 tests；CLAUDE.md:74 与 research/CLAUDE.md:51 更新（含 T1/T2 基线）；证据 R21/EVID/I1-I2-per-dir-counts.txt | verified（R02 复查 §1） |
| R01-EVID-I3 | I | interface.md 结构损坏：5 个重复 `## 6`；§5 误引；`data.backend.open_read` 旧路径（:628,:793） | `platform/docs/interface.md` | verified | 08432ab；interface.md 消除 5 个重复 '## 6'（M8-02 降 ###、M7-05 归 §5.1、测试/数据平台改 §7/§8）、'见 §5' 误引修正、data.backend.open_read → factorlab.app.bootstrap.open_read；cd platform && .venv/bin/python -m pytest -q tests/test_doc_paths_exist.py tests/test_catalog.py → 29 passed；bash scripts/gates.sh 全绿 | verified（R02 复查 §1） |
| R01-EVID-I4 | I | 6 份已交付 spec 仍写"待评审"；m4b spec 仍文档已删除的 `cost` 参数且无勘误头 | `platform/docs/superpowers/specs/` m3a/m3b/m4a/m4b/m5/free-form | verified | 08432ab；m3a/m3b/m4a/m4b/m5/free-form spec 状态改已实现（补正文档漂移）；m4b 加 cost→cost_rate 勘误头；tests/test_doc_paths_exist.py 通过；证据 R21/EVID/I4-spec-status.txt | verified（R02 复查 §1） |
| R01-EVID-I5 | I | quark cookie fallback 文件在仓库内且未 gitignore（当前无泄露，风险路径） | `quark_client.py:33-35`；`.gitignore` | verified | e3d2454；.gitignore 加 quark_cookies*/.env*；git ls-files 无凭据文件；git check-ignore -v research/tools/quark_cookies.txt 命中；证据 R21/EVID/I5-gitignore.txt | verified（R02 复查 §1） |
| R01-EVID-I6 | I | platform README 仍教已退役的双分支纪律 | `platform/README.md:44-48` | verified | 08432ab；platform/README.md §仓库纪律改单仓单树（指向 ../CLAUDE.md/AGENTS.md）；证据 R21/EVID/I1-I2-I5-I6-doc-diff.txt | verified（R02 复查 §1） |
| R01-EVID-I7 | I | pending #11 三项已完成未标记；#12① "460 处"与门实测 68 不符（口径未注明） | `docs/pending-items.md:70-78,82` | verified | e3d2454；pending #11 三项标 ✅（附证据指向）、❌④（catalog 拆分未做）；#12① 改门实测 68 处并注明口径（AST 字符串常量处数、排除 core/factio/）；证据 R21/EVID/I7-pending-verify.txt、I7-dataiface-count.txt | verified（R02 复查 §1） |
| R01-EVID-I8 | I | R19 迁移证据混入未标注的失败 traceback（superseded 状态），生成脚本未存 | `docs/verification/R19/02-migration-diff.txt:15-26` | verified | e3d2454；R19/README.md 标注 02-migration-diff.txt:15-23 为 superseded 失败 traceback（正文不改写）+ gen-02-migration-diff.py 补存 + 复现输出 02-migration-diff-r21-repro.txt（旧侧源已删如实声明不可复现） | verified（R02 复查 §1） |

---

## R02 复查（2026-09-15 第二轮严格 review）

- 报告：`r02-2026-09-15-strict-review/report.md`（4 路审查 + coordinator 复核；R21 评审期间已提交至 `9a5c3d3`）
- R21 复核：63 行 fixed-claimed 中约 56 行 verified；7 行 reopened/partial（见下表）；未发现伪造证据。
- 新增：2 Critical + 9 Important + Minor（下表；Minor 全表见 R02 报告）。

### R21 复查例外（reopened / partial）

| 关联 R01 ID | 判定 | 原因 | 联动 R02 ID |
|---|---|---|---|
| R01-DATA-C1 | reopened | staleness 判定窗口依赖：短窗/分块下 fill-seed 复活死价格 | R02-C2 |
| R01-TOOLS-I9 | partial | universe_stages CLI T2 裸跑 import 崩溃（preflight 未执行） | R02-I6a |
| R01-TOOLS-I3 | partial | import_daily 解析错误不改退出码 | R02-I6b |
| R01-ENG-I3 | partial | 插件别名/动态导入副作用先于拒绝、registry 无回滚 | R02-I7 |
| R01-TOOLS-I5 | partial | fail-open 后平台侧无 stk_limit 覆盖率门 | — |
| R01-EVAL-I7/I8 | partial | quant-core 契约 :90/:93 未同步 | R02-I9 |
| R01-EVID-C2 / R01-STRAT-I6 | 已按标注闭环 | 原始输出永缺/不可复现（如实标注） | — |

### R02 新增发现

| ID | 级 | 问题 | 关键位置 | 状态 | 修复说明（团队填） | 复查（reviewer） |
|---|---|---|---|---|---|---|
| R02-C1 | C | 分钟未来门旁路：`day_sum(im_delay(close, 2**2-5))`==k=-1；`im_mean(close, 2**0-1)` 窗口 0 静默 0.0；import 别名同样绕过；无运行时兜底（测试注释称有）【实测】 | `core/engine/minute_gate.py:32-62,88-91,103-127`；`core/ops/minute_ops.py:25-27,62-64` | verified | 808c40b（证据 1aabaa8）；分钟门三处封堵：_try_num 折叠 Pow/IfExp/Call、_call_names 解析 import 别名、im_* 运行时硬校验（k/window≥1、int 拒 bool）最后防线；测试 test_minute_gate.py::test_gate_direct_pow_ifexp_call_folds/::test_gate_resolves_import_aliases/::test_minute_scope_rejects_aliased_future_shift_e2e、test_minute_ops.py::test_im_delay_runtime_rejects_non_positive_or_non_int；复现 /tmp/opencode/reviewer-r02-newturf/probe11（before 用未来分钟/窗口0 静默 0.0 → after ValueError）；证据 docs/verification/R22/R02/engine/C1-*.txt、repro-*-C1-probe11/7*.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R02-C2 | C | staleness gate 窗口依赖：短窗/分块（<250 交易日）绕过后 fill-seed 复活死价格，与 interface.md fail-loudly 承诺矛盾【实测】 | `app/run.py:178,185`；`core/engine/compute.py:321`；`adapters/read/staleness.py` | verified | da8bba0（fix）+ f55befa（回归修复）+ ea65fa0；判定窗口无关：load_last_close_dates 全历史 + calendar 有界回看（550 自然日）、seed 候选超阈值拒绝；回归修复：guard 只对 ref 仍 listed 的 code 断言（退市 code seed 照常注入，零迁移）；240 日窗口/30d 分块实测 after RAISED（before RUN OK signal=14.4），wcorr IC delta 0.0；测试 tests/test_pit_staleness.py（25，含 red→green 守护）；证据 R22/R02/data/c2-*.txt、staleness/seed-guard-fix.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R02-I1 | I | industry 100% NULL（5861/5861）→ `fillna(industry_mean)`/`gp_*` 静默塌成全市场单组【实测】 | `adapters/process_ops.py:162-169`；`ch_ingest/ingest_daily.py:205` | verified | b766092（生产侧如实标注：industry 无源 + 影响 + 不要 advertise；README/ddl 注释）+ 28a8f42（平台守卫：fillna(industry_mean) 全空/零匹配 fail loudly；gp_*(key,x) 键列全空 compute_formula fail fast）；测试 test_process.py::test_fillna_industry_mean_all_null_industry_fails_loud、test_compute.py::test_gp_key_all_null_fails_loud/::test_gp_key_with_values_ok；证据 R22/R03/tools/I1-doc-annotations.txt、R22/R03/guards/r02i1-r03i4-i5.txt；interface.md daily_basic/行业段已标注恒 NULL（08432ab + R03 agent） | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R02-I2 | I | process 链无有限值门：单个 Inf 毒化整日截面（standardize→全 NaN；接 winsorize→整帧 null） | `adapters/process_ops.py:100-145` | verified | 9dbe0ed；process 链统一非有限→null（standardize/winsorize/clip/fillna/neutralize 等 7 处理器）；probe3 before：inf 毒化整日（nan=45）→ after 有限值存活；测试 tests/test_process.py::test_*_inf_treated_as_invalid* 等 11 条（red 9 failed→green 50 passed）；证据 R22/R02/engine/I2-*.txt、repro-*-I2-probe3*.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R02-I3 | I | BatchFlock throttle 永久关闭时看门狗失效 → 挂死（probe 12s 超时；生产 `_mem_gate` 即此路径） | `adapters/batch_flock.py:175-191` | verified | 5088f77；BatchFlock 无 future+throttle=False 分支按距上次完成记账 stall，requeue 重试 strikes 后收敛；probe6 before 挂死 12s（exit 124）→ after <1s 返回 0/1/0；测试 tests/test_batch_flock.py::test_throttle_closed_without_futures_stalls_instead_of_hanging；证据 R22/R02/engine/I3-*.txt、repro-after-I3-probe6-flock.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R02-I4 | I | adv20 左窗按日历交易日：长停牌股行情行 <20 → 恒 null（契约=20 个有行情日） | `app/run.py:585-603,659-664` | verified | d973e0e（证据 9857481）；adv20/prev_close 左窗按「有行情行数」补足（load_daily_tail_dates，每 code 第 n 个最近行情行）；实测停牌 10 日后 adv20 逐值 = 最后 20 行情行均值（before null）；测试 tests/test_minute_engine.py::test_minute_adv20_covers_suspension_beyond_calendar_window；证据 R22/R02/data/i4-*.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R02-I5 | I | 240 网格断言不查 index 范围/session_type：index 1..240 与 session=7 均被接受 | `core/engine/minute.py:98-108` | verified | c9d463c（证据 18d1e88）；分钟网格断言补 index 0..239 范围与 session_type 0/1/2；probe1 before 接受 index 1..240/session=7 → after 双 ValueError；测试 tests/test_minute_engine.py::test_minute_grid_index_range_enforced/::test_minute_grid_session_type_contract_enforced；证据 R22/R02/engine/I5-*.txt、repro-after-I5-probe1.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R02-I6a | I | universe_stages 三 CLI T2 裸跑 ModuleNotFoundError（无 `_env.ensure_platform()`；测试以 PYTHONPATH 掩盖）【实测】 | `universe_paths.py:19`；`scripts/run_layer*.py` | verified | dc8c2b6（证据 3a368a6）；三 CLI 入口 ensure_platform 自举（layer2 另修模块级 duckdb import 的 --help 崩溃）；T2 裸跑 env -u PYTHONPATH：before 3×ModuleNotFoundError → after 3×usage exit 0；测试 research/tools/universe_stages/tests/test_cli_bootstrap.py（9，含假 factorlab 污染解释器）；证据 R22/R02/tools/I6a-*.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R02-I6b | I | import_daily：有非空 code 零解析静默回退文件名；解析错误不改退出码（新 code 冲突分支命中仍 exit 0） | `ashare_ingest/import_daily.py:178-189,354-360` | verified | fb62c08 + 0d09bf5（证据 3a368a6/0534e9a/R22/R02/tools/I6b-empty-sheet-fix.txt）；非空 in-file code 零归一化 → fail fast 点名原文（不再静默回退文件名）；解析错误摘要 + exit 1；空 sheet（无表头，3 个既有 stub 000047/920305/920680）→ 无数据跳过不改退出码（重灌不会整体失败）；测试 test_import_daily_delisted.py（25，含 e2e exit 码与空 sheet）；真实源扫描 368/371 归一化成功 | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R02-I7 | I | 插件别名/动态导入：副作用先执行、builtin 被替换、无回滚（字面形态已堵） | `adapters/plugins.py:79-109,155-170` | verified | 7c8e26a；AST 解析 import 别名 + 命名常量；动态注册形态 import 前 registry 快照，冲突回滚（registry + sys.modules）再抛错；probe before 三形态 ts_mean 被替换 9.9.9 → after 别名/常量前移拒绝、动态回滚；测试 tests/test_ops.py::test_alias_import_override_rejected_before_import_without_force/::test_dynamic_registration_conflict_rolls_back_registry 等 6（含 --force 正例）；证据 R22/R02/plugins/ | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R02-I8 | I | run 分块路径无互斥：并发 run 同 output_dir 无锁；崩溃可产生 signal/labels/summary 混合 | `app/run.py:424-474`；`adapters/parquet_artifacts.py:251-259` | verified | 3cd1b1f（证据 60858f8）；run 产物落盘单写者非阻塞 flock（并发 fail fast）+ 旧 summary 原子失效 tombstone + 每文件 sha256 + load 交叉校验；probe before 崩溃后 load_signal OK（signal.max=-10.0 混合）→ after tombstone + 三场景全 RAISED；测试 tests/test_artifact_persistence.py +5；证据 R22/R02/data/i8-*.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R02-I9 | I | 文档/证据漂移：quant-core 契约未同步（n_ok/average-rank）；ENG 证据命令含占位符；DATA README 称未接线（实际已接线且无 run_factor 级测试）；EVID 归属与 README 不符 | 多处 | verified | a9bd28c + 041957c + a7afc95 + 59f1de6；quant-core 契约旧口径行加【R21 勘误 7a/7b】指针；R21/ENG/README.md 逐 finding 精确复跑命令（39 passed 实跑）；R21/DATA README 接线状态更正（fc2858c 真接线）+ run_factor 级接线回归（C1/pit_qfq 存根变异必败）；EVID README 归属更正；证据 R22/R02/evidence/ | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |

---

## R03 挖矿实战发现（2026-09-16）

- 报告：`r03-mining-2026-09-16/report.md`（同轮产物：`max_effect_20d_{high,extcnt,zmax}` 三变体已归档）

| ID | 级 | 问题 | 位置 | 状态 | 修复说明（团队填） | 复查（reviewer） |
|---|---|---|---|---|---|---|
| R03-I1 | I | `exclude_st` 使 152/152 库内 spec 在 CH 不可原样运行（缺 stock_st）；挖矿被迫用无 ST 影子 spec，`_extcnt` 等对 ST 语义敏感【实测】 | `adapters/read/universe.py`（exclude_st 分支）；CH schema | verified | 79d256a（证据 a6accd2）；显式降级开关 FACTORLAB_ST_DEGRADE=allow：缺 stock_st + exclude_st=true 时 warning（含'无 ST 口径'）+ is_st=null + 不过滤，summary 恒写 st_degrade 审计；默认仍 ValueError；有表时开关无副作用；测试 test_pit_universe.py +4、test_run_factor.py +6、test_minute_engine.py +1（双腿）；真 CH before exit1 → after 正常评估 n_weeks=3；证据 R22/R03/tools/ | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R03-I2 | I | 评估 `coverage.pct_valid` 恒 1.0（null 行在进 kernel 前已过滤）与 `signal_null_ratio`（3.35%）矛盾——口径误导【实测】 | `app/evaluate.py:38`；`adapters/rust_ic.py`（null 过滤）；`core/eval/metrics.py:6` | verified | 136c30d（证据 fddac2a）；coverage 在 null 过滤前计算（total=对齐后全行、valid=非空且有限），与 signal_null_ratio 口径一致；kernel/shim 契约不动；真数据 extcnt total 934236/valid 896750/pct 0.9599（旧 1.0）；测试 test_eval_rust_ic.py +4、test_eval_alignment.py +2（red 4 failed→green 37 passed） | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R03-I3 | I | 离散/重并列信号分层评估静默 NaN：D1=NaN、spread=NaN、`empty_groups=[]` 无告警（`ts_count` 类计数因子）【实测】 | `core/eval/layered.py`（分组与空组判定） | verified | 0455502；layered 新增 degenerate_decile_groups（全期无有效收益的非空组），app/evaluate 落 summary + notes，CLI show 打印警告；真数据 extcnt degenerate_groups=[0,1,6]；测试 test_layered.py +3、test_evaluate_notes.py、test_cli_list_show.py +2；正常面板逐字节不变 | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R03-I4 | I | codegen 代数化简坑：`_s - _s + 1.0` 被 sympy 折叠为常量 → `ts_cum_sum(1.0)` 运行时 AttributeError；**lint 通过但不可运行**（静态门与运行脱节）【实测】 | `core/engine/compute.py`（codegen 调用链）；vendor `expr_codegen` simplify | verified | 28a8f42；codegen 前守卫两层：ts_/cs_/gp_ 纯字面量数据参数 → FactorDSLError（带代数化简提示）；codegen_exec 执行期异常（含 sympy 折叠如 x-x+1.0）→ FactorDSLError 带指引（原始错误保留，不再裸 AttributeError）；测试 test_compute.py::test_rejects_folded_constant_data_arg/::test_rejects_literal_constant_data_arg/::test_positive_control_cum_sum_expression；证据 R22/R03/guards/r02i1-r03i4-i5.txt（before AttributeError → after 明确 DSL 错） | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R03-I5 | I | 真游程/逐行计数不可干净表达：vendor 已有 `ts_cum_count`（`polars_ta/wq/time_series.py:335`）未注册；缺 `ts_streak`；今回以抗折叠累计技巧绕过（不可分块）——开放算子 Plan 1 实证案例 | `core/ops/`（注册面）；`research/docs/factors/vol_run_energy/symrun_r30_streak.md` | verified | 开放算子 Plan 1 已闭环注册侧：df6851f（polars_ta 签名感知分类表，ts_cum_count=ts/unbounded）+ c7b3d66（拆白名单闸门，库函数直写）；实测 compute_formula('ts_cum_count(close > 2)') 正常产出、cumulative_ops_used 命中且 --chunk-days fail fast；证据 R22/R03/guards/r02i1-r03i4-i5.txt；残余：vendor 无 ts_streak（真游程算子未实现）——已列为 Plan 2 算子档案候选，不影响 ts_cum_count 直用 | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R03-M1 | M | 误 import 平台宏（`from polars_ta.prefix.wq import returns`）报 expr_codegen 深层裸 traceback，无"裸用"指引【实测】 | `core/factor/ast_gate.py` 或 lint 前校验 | verified | ba59430；平台宏名单单点（PLATFORM_MACRO_NAMES），ast_gate 对 Import/ImportFrom 宏名前置拒绝（'平台宏 returns 请裸用'，含别名）；compute_formula 与 lint 均清晰 FactorDSLError（before expr_codegen 裸堆栈）；测试 test_ast_gate.py +5、test_compute.py +2、test_cli_lint_v2.py +1（red 8 failed→green 51 passed） | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R03-M2 | M | `factor-mine` skill 文档漂移：种子 glob 未适配族子目录、路径缺族、duckdb 指南过期 | `.claude/skills/factor-mine/SKILL.md` | verified | 55e754c + dde8aae；factor-mine skill 三处漂移同步（种子 glob '*/<stem>.md'、路径补族目录、duckdb→CH 指南/字段核对/ST 降级开关），同族 assumption-review/code-review 与档案 _template 一并校准；实测 157 候选；证据 R22/R03/tools/M2-doc-sync.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R03-I6 | I | 分钟源幸存者偏差阻断全市场分钟因子：`rules.exchanges` 池在 CH 上 fail-fast（"日线在而分钟整日缺"：每月 63~83 只退市股；2024-01-02 在册 5327 中 84 只无任何 1m 行 + 36 只部分覆盖）。挖矿改用静态覆盖池 `ref`（脚本 `research/tools/factor_lib/gen_minute_pool.py`，池 `research/factor/intraday/_pools/bars1m_2024h1.yaml`，5207 只含 238 只 BJ）【实测】 | `app/run.py`（missing_day fail-fast）；`adapters/read/universe.py` | verified | 3c67c0d + 3fd1565 + 47fe5be；平台显式覆盖口径：FACTORLAB_MINUTE_UNCOVERED=fail 或 drop（默认 fail 逐值不变）；drop 剔除「daily 在而分钟整日缺」的 code-day，warning + summary 审计字段 minute_uncovered{mode,dropped_code_days,dropped_codes,dropped_dates,...}；部分覆盖（0<n<240）仍 fail-fast（折日锚定 239，静默剔除会坏网格不变量）；新增只读覆盖辅助 load_bars_1m_coverage + 真 CH 对拍；测试 tests/test_minute_coverage.py + test_minute_engine.py 7 条（含无未来日期 spy、drop 分块==整段）；真 CH：默认 exit1（4 缺日）→ drop exit0 + warning + 审计；证据 R22/R03/minute/r03_i6_*.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R03-I7 | I | 分钟 bar 陈旧尾部（238/239 常 amount=0 陈旧 OHLC）需公式作者手写零成交守卫，否则"触高时间"类因子 1%~3.3% 截面被系统性偏移；且 1m-funnel 文档示例用 `AND` 守卫写法不可用（`&` 被 AST 门拒），须嵌套 `if_else`+`None`【实测】 | `docs/superpowers/specs/2026-09-08-factorlab-1m-funnel-design.md:52-53`；`core/factor/ast_gate.py` | verified | 223358b；分钟面板按公式引用派生 has_trade（amount>0；缺 amount 明确报错；不进折日输出）；AST 门移除 BoolOp/And/Or/Not（and/or/not 前置拒绝 + 嵌套 if_else/has_trade 指引），& 维持拒绝（实测 a>0 按位与 b>0 在 Python 优先级下静默全 false——正是门要防的错值）；funnel/DSL 设计/interface/catalog 示例改可用写法；测试 test_minute_engine.py::test_minute_has_trade_guard_excludes_stale_tail_bars 等 + test_ast_gate.py +2；真 CH 引擎链：守卫 0.958159（229/239）vs 无守卫 1.0；make lint-factors 159/159；证据 R22/R03/minute/README-R03-I7.md | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R03-M3 | M | `evaluation.decile_returns.spread` 符号随 direction 翻转：同一因子 direction -1→+1 时 raw IC 不变（+0.0756）而 spread 从 +0.00676 变 -0.00676，易误读【实测】 | `kernels/quant_core` spread 语义；`docs/catalog.md` | verified | 98feec6；contract 语义不改（spread=(g0−g9)×direction）；interface 评估段补精确公式与读法（spread<0 = 与声明方向一致；有效性看 ic/long_short）、CLI list 表尾加同款提示；测试 tests/test_cli_list_show.py::test_list_spread_sign_hint；probe R22/R03/misc/probe_m3_spread_sign.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R03-I8 | I | 策略回测被 CA Gate 全面拦停：30 只持仓的月度窗口几乎必撞某只除权（实测升级：多月窗 5/5 全撞、逐月窗重试后 2025-03 干净；样例 600116.SH@2025-01-08、600177.SH@2025-09-12；多年连续回测不可行，fail-closed 按 m8-06a §5.5 by design【实测】——"回测消费因子"的实操路径被阻断，需 CA 处理里程碑或明确分段工作流文档） | `app/backtest/backtest.py:_assert_ca_gate` | verified | 98feec6；取舍=分段工作流**文档化**（不做 helper）：技术核实 run_backtest 无 initial state（m8-06a §3.1）→ 分段即独立 run，拼接会丢弃除权价格落差与重复换手/费用，诱导错误结论；interface.md CA Gate 后新增「分段工作流」（decision_range 示例、all-cash 边界唯一精确拼接、何时必须等 CA 里程碑），closeout spec 注记；fail-closed 由 tests/test_backtest_ca_gate.py::test_b13_segmented_runs_pass_but_full_run_fails_closed 锁定 + 突变自检；证据 R22/R03/misc/ | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R03-M4 | M | `TargetPortfolio` 校验用 `map_elements` 触发 `PolarsInefficientMapWarning`（每次策略构建都刷警告；性能与"测试输出 pristine"纪律双违背）【实测】 | `core/domain/portfolio.py:151` | verified | bd86c46；TargetPortfolio 两处校验向量化（str.contains(CANONICAL_TS_CODE_PATTERN) / is_in），消除 PolarsInefficientMapWarning；测试 tests/test_portfolio_domain.py::test_no_inefficient_map_warning_on_construct + null 拒绝向量化 2 条；证据 R22/R03/misc/r03_m4_red.txt → r03_m4_green.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |
| R03-M5 | M | `adapters/intraday.py` docstring 写 "datetime = minute-end"，与实测（bar 起点标注：tick 对拍 delta=0 在起点）不符——仅文档漂移，索引→时钟映射不受影响【probe】 | `adapters/intraday.py` docstring | verified | 98feec6；实测对拍（000021.SZ@2025-08-12）：bar 起点标注（15:00 竞价 bar 与 time_ms=15:00:00 成交 delta=0）；修 adapters/intraday.py docstring（bar 起点口径）+ interface 分钟面 + funnel spec 勘误；行为零变；证据 R22/R03/misc/probe_m5_bar_time_labeling.txt | verified（R06 批次复核：全量/门绿 + 证据 token 门；证据见左列） |

---

## R04 功能性 Review 提案（2026-09-16）

- 报告：`r04-efficiency-2026-09-16/report.md`（5 路并行**实测**审视：算得快 / 迭代快 / 更整洁 / 存储 / 顶层结构）
- 性质：**改进提案**（非缺陷台账）；量化事实与 P 编号提案见报告，实施由用户/开发团队决定
- 亮点数字：日频 universe 重复解析占 52%、分钟链峰值 RSS 34.95GB（分块后 6.95GB）、全库 lint 552s→4s（~100×）、
  M8 回测 356 次 CH 查询/21 决策、CH ZSTD 可省 ≈72GiB、僵尸文件可回收 ~1.25G、代码克隆 43 组、真 NameError 1 处
- 待拍板：D1 证据位置 / D2 两树 docs 处置 / D3 契约归属 / D4 方案 B 立项 / D5 `projects/ashare_alpha3` 归档或删
- **用户拍板（2026-09-16）**：D1=governance/evidence；D3=迁 knowledge/contracts；D5=归档到 _archive；
  **快速项已授权开发团队执行**（清单与验收锚点见报告 §7）；D2/D4 待定
- **可执行实施计划**：`r04-efficiency-2026-09-16/structure-plan.md`（目录重整方案 A：12 Task，含 git mv 命令、同步清单、风险与验收）

---

## R05 使用验证发现（2026-09-16）

- 报告：`r05-usage-2026-09-16/report.md`（真实 CLI 验证团队新工作：开放算子 / ST 降级 / 分钟链）
- 已验证可用：ST 降级开关 ✅、`ts_arg_max`/`ts_corr` 真解锁 ✅、`BBANDS` artifact 边界清晰拒绝 ✅
- 勘误：`ts_quantile` 不存在于 polars_ta（open-operators design/plan 已加勘误节）

| ID | 级 | 问题 | 位置 | 状态 | 修复说明（团队填） | 复查（reviewer） |
|---|---|---|---|---|---|---|
| R05-I1 | I | 分类表未标注返回形态：`BBANDS` 返回 Struct 过 lint；**带 process 链时错误晦涩**（Struct 上 clip/quantile），无 process 时边界拒绝清晰【实测】 | `core/ops/_generated_ta_ops.py`；process 链报错路径 | verified | fa8aa1e；OpMeta 增 returns（scalar/struct/multi，默认 scalar）；生成器按 3 行合成 frame dtype 探测标注（8 struct / 3 multi / 370 scalar；探测失败登记 PROBE_FALLBACKS，FROMOPEN_1 一条）；compute_formula 在 process 链前静态拒绝非标量返回并点名算子（替代 Struct 上 clip/quantile 天书）+ 运行期 numeric 兜底门；lint 同源拒绝；测试 test_op_classification.py +7、test_compute.py +4、test_cli_lint_v2/test_open_ops_e2e；生成表 --check exit 0；证据 R23/usage/r05-i1-struct-gate.txt；残余：字段访问（.upperband）与完整 conformance 归 Plan 2 | verified（R06 回填复查：test_op_classification 通过；证据见左列） |
| R05-I2 | I | spec 顶层未知字段静默忽略：`bogus_field: 123` 过 lint；`op_meta` 被静默吞掉，而报错文案引导"补 op_meta"（机制未实现/Plan 2）——**指引无效且无声** | `core/spec.py`（字段校验）；`core/engine/semantics.py:217` | verified | 928b3ee + 985b50e；spec 全模型 extra=forbid（bogus_field 明确拒绝）；op_meta 非空 → 明确报「op_meta 暂未支持（Plan 2）」（缺省/null/空映射放行）；semantics 未知算子指引改为公式内 def / factorlab op add，不再承诺 op_meta；lint 走严格解析；测试 test_spec.py +4、test_spec_strict.py（8）；make lint-factors 159/159（存量无 spec 因 strict 失败/用 op_meta）；证据 R23/usage/r05-i2-spec-strict.txt | verified（R06 回填复查：test_spec_strict 通过；证据见左列） |
| R05-M1 | M | 新解锁面不可发现：`op list` 仍 57、`catalog.md` 仅 `ts_corr`，512 条分类面无 CLI/文档检索入口（团队 R22 summary §偏差 5 已记账，归 Plan 2） | `surfaces/cli/main.py`（op list）；`platform/docs/catalog.md` | verified | 1c54ea4；最小发现入口：factorlab op list --catalog 列分类表全集（528 条：name/partition/window/source/returns），默认 op list 注册面视图不变；op doc <name> 未注册但分类表有 → 回退打印元数据与来源（未知名 exit 1）；interface.md 标注入口；测试 test_cli_op_registry.py::test_cli_op_catalog_and_doc_fallback；证据 R23/usage/r05-m1-op-discovery.txt；完整 op/catalog 同源仍归 Plan 2（文档注明） | verified（R06 回填复查：op list --catalog 528 条可用） |
| R05-C1 | C | **运行安全**：`factorlab run` 无内存上限/看门狗。2023–2025 分钟链（`--chunk-days 20`）运行期间主机内存耗尽、SSH 卡死（用户报告），ClickHouse 一度无响应；进程零输出、D 状态；已清理恢复（CH `SELECT 1` OK、daily 18.12M 行完整）【实测+用户报告】 | `app/run.py`（无内存护栏；对比研究侧批处理有 `_mem_gate`） | verified | 2d4cbb2；进程内存护栏（P0 事故修复）：app/memory.py — MemoryWatchdog（psutil ~5s 采样 + chunk 边界/落盘前协作检查 → MemoryLimitExceeded 干净中止，无半成品；未设 env = 零行为变化）；显式 FACTORLAB_MAX_MEMORY 时 CLI 落 RLIMIT_AS 硬上限（VA 预留实测校准防误杀）；guard_minute_chunk_days 按 64KB/(code·日) 估算（显式超大块 >32GB fail fast、>8GB 告警；默认自动 20 日/块路径引用 R04-P1 6985bc5）；推荐 FACTORLAB_MAX_MEMORY=8GB + FACTORLAB_MIN_AVAILABLE_MEMORY=2GB；测试 tests/test_memory_guard.py 39 条 + 相关电池 199 passed；人为 1KB 阈值 clean abort（exit1、无 summary、loader 拒绝）+ 默认行为不变演示；文档：platform/docs/interface.md §进程内存护栏、根 AGENTS.md 重任务运行协议、README 指针；证据 docs/verification/R23/safety/ | **verified（2026-09-16 对抗性复查）**：①默认自动分块（无参/无环境）峰值 RSS ~6.1GB，IC 与非分块基线逐位一致（0.07558398337274398）；②`FACTORLAB_MAX_MEMORY=2GB` → RSS 2.3GB 干净中止（exit1、无 summary、报错含“已干净中止、未落半成品”）；③`--chunk-days 999` 静态拒绝（估算 215GB、exit1、指引 27/默认 20）；④显式 50 日 → 14.8GB 告警后放行；⑤默认路径零告警。残余建议（非阻塞）：RSS 看门狗默认关闭（默认保障=分钟链自动分块+静态块门），日频长窗仍建议生产显式设 FACTORLAB_MAX_MEMORY |
| R05-I4 | I | `align_weekly` 按 (code, ISO周) 取**各股自己的周内最后交易日** → 分钟链（停牌无补行）同一 ISO 周产出多个评估日期：3 年案例 **154 ISO 周 → 370 对齐日期 → n_weeks=171**（每周日期数分布 1/2/3/4/5 = 31/63/32/23/5；样例 2025W22 含 1 只、2 只股的微截面）——t_stat/recent_26w 口径被抬高、微小截面混入“周”统计【实测】 | `core/eval/alignment.py:6-20`；`adapters/rust_ic.py` | verified | 29d1e07；align_weekly 每周恰一个评估日期：每 code 每周仍取自己最后观测，但 date 统一改标为帧内该 ISO 周的全局最后交易日（此前按 code 各自 date → 分钟链 154 周→370 日期、n_weeks=171、微小截面混入周统计）；日频链每 code 周末有骨架行 → relabel 恒等、零迁移；测试 tests/test_eval_alignment.py::test_align_weekly_one_date_per_iso_week_minute_like + no_leak 契约更新；R22 零迁移 6 代表 spec 逐值 delta=0（283s 实跑）；证据 docs/verification/R23/usage/r05-i4-align-weekly.txt | verified（R06 回填复查：test_eval_alignment 通过；证据见左列） |

---

## R06 迁移后复查发现（2026-09-16，post-migration strict review）

- 报告：`r06-2026-09-16-post-migration-review/report.md`（三路并行对抗复查：路径完整性 / 功能回归 / 台账与验收）
- **无 C**；R24/R27/R28 可复验声明全部独立复跑通过（平台 3150/13/0、策略首例逐帧一致、data 零写等）
- M 级 10 条只登记在报告 §3（按 README 约定不进台账）；证据 `r06-2026-09-16-post-migration-review/evidence/`

| ID | 级 | 问题 | 位置 | 状态 | 修复说明（团队填） | 复查（reviewer） |
|---|---|---|---|---|---|---|
| R06-MIG-I1 | I | **HEAD 上 `make gates` 红**：G-ANNOTATE（`netflow_vol.md` 缺 `snapshot:`，已提交）+ G-INDEX（盘上 167 vs 生成器 168，untracked `accel_inflow.yaml` 未归档）——挖矿在途未收口【实测】 | `governance/ops/gates.sh:74`；`knowledge/dossiers/factors/reversal_20d/netflow_vol.md`；`research/factor/liquidity/accel_inflow.yaml` | verified | 08a327b（snapshot 标注）+ 挖矿侧索引收口；11 份新档案补 `snapshot:`（R21 约定）→ G-ANNOTATE 回绿；G-INDEX 由挖矿循环重生（extsum 等新 spec 轮末入索引）；门复核 `make gates` exit 0 全绿（含全部并行提交，证据 governance/evidence/verification/R24/16-r06-fixes/ 与 eafe3a6/e23e758） | verified（R06 复查：make gates exit 0） |
| R06-SKILL-I2 | I | **单点化后仍在写根 `results/`**：factor-mine 技能坐标未更新，单点化提交后仍活跃写 `results/_mine_round_{8,9,10}.md`（记录与 run 产物分裂）【实测】 | `.claude/skills/factor-mine/SKILL.md:89,116,122,139` | verified | 00e749f；factor-mine SKILL.md 4 处坐标改 `runs/platform`（round 记录 → `runs/platform/_mine_rounds/`）；`_mine_round_{1..12}.md` 迁入 `_mine_rounds/`（sha256 前后逐一同一 HASH-MATCH: OK）；证据 R24/16-r06-fixes/09-mine-rounds-move.txt | verified（R06 复查：技能内零 results/ 写点） |
| R06-MIG-I3 | I | **根 `results/` 2.6G 残留未清**：清理项在 `migration-r04.md:68 C3` 标"未竟"但未登记 `pending-items`，与"运行产物单点化"声明不符【实测】 | 根 `results/`；`governance/workspace/pending-items.md` | verified | 19e15cc；根 results/ 盘点+清理：唯一产物 14 目录移入 runs/platform（2.32GB）、旧态重复删 0.35GiB（删前差异小文件 tar 备份见 governance/evidence/verification/R24/16-r06-fixes/10-mig-i3-cleanup.txt（sha256 ad7b7b1b…））、2 份根更新版本以 `__root-dup-20260916` 双份保留待研究侧裁决；pending-items #22 登记；证据 governance/evidence/verification/R24/16-r06-fixes/10-mig-i3-cleanup.txt | verified（R06 复查：根 results/ 已清除） |
| R06-LEDGER-I1 | I | **台账闭环从未落地**：93/93 行 `fixed-claimed`、复查列 92/93 为空；R02 声称的 verified 从未回填状态列 → 唯一状态源不可信【实测】 | `governance/evidence/reviews/findings.md`（全表） | verified | 2177f29；门新增闭环摘要（复查列覆盖/fixed-claimed 未复查逐轮计数/报告判定不一致 WARN）+ `--closure` 清单；closure.md 记录 R02 复查结论出处（R02 报告 §1 例外表）与「状态回填属 reviewer 职责，团队不代填，待 R06 复查回填」；证据 R24/16-r06-fixes/ledger/04-closure.txt | verified（R06 复查：92 行复查列回填 + 门 9/9 对抗 RED） |
| R06-LEDGER-I2 | I | **门对「修复说明」证据路径零覆盖**：该列 269 个路径 token 0/269 在反引号内；注入裸文本死路径 → 门 exit 0【实测】 | `governance/ops/check_reviews.py:299-306` | verified | 2177f29；裸文本证据路径统一解析（全仓索引+后缀匹配+R21 相对根+简写+迁移映射；屏蔽 code span/URL/注释），历史不精确引用按 (行ID, token) 精确豁免并逐条打印；对抗 m7 注入 RED（修复前 exit 0）；证据 ledger/06-9-scenario-adversarial.txt、gate-adv-m7-deadpath-plain.txt | verified（R06 复查：m5/m7 注入均 RED，门加固生效） |
| R06-LEDGER-I3 | I | **门不区分"有证据/有文字"**：注入`已修复，无证据。`（非空无 commit/路径）→ 门 exit 0【实测】 | `governance/ops/check_reviews.py:295-296` | verified | 2177f29；fixed-claimed 修复说明必须含 SHA 形状 token 或存在的路径 token，否则 RED；m5 注入 `已修复，无证据。` RED（修复前 exit 0）；全量 93 行扫描全部满足；证据 ledger/gate-adv-m5-fixdesc-no-evidence.txt | verified（R06 复查：m5 空证据注入 RED） |
| R06-TEST-I5 | I | **策略入口 E2E 测试指向死路径**：`_PLATFORM_RESULTS = _REPO/"platform"/"results"`（已迁）→ 集成测试恒 skip，cc829ad 修复无回归保护【实测】 | `research/tools/strategies/tests/test_run_strategy_cli.py:27,204,235` | verified | f3968f4；测试结果根改 `settings.results_dir`（runs/platform）：集成用例 BEFORE 2 skipped → AFTER 2 passed 真跑（真 CH+真 signal，9.4s）；整文件 10 passed；证据 R24/16-r06-fixes/cli-strategy/01-before/02-after | verified（R06 复查：集成用例不再 skip，10 passed） |
| R06-TOOLS-I6 | I | 死符号残留：`run_strategy.py:22 _REPO_ROOT` 定义后零引用【实测】 | `research/tools/strategies/run_strategy.py:22` | verified | b87fd55；删除 `run_strategy.py:22 _REPO_ROOT`（全仓 grep 零引用 + py_compile + --dry-run exit 0 + 10 用例绿）；证据 R24/16-r06-fixes/cli-strategy/04-i6-grep-zero-refs.txt | verified（R06 复查：符号已删除） |

> **R06 回填说明（reviewer 2026-09-16）**：本轮完成台账闭环回填——92 行存量行（R01 63 / R02 12 / R03 13 / R05 4）依 R02 复查 §1 判定、R22/R23 批次验收与 R06 独立复跑（平台全量、`make gates` exit 0、证据 token 门、定点测试）落 `verified`/例外闭环注；R06 8 行按本轮对抗复查落 `verified`。`check_reviews --closure` 的「复查列未覆盖 / 报告-台账状态告警」应清零。

---

## R07 缺口专项发现（2026-09-16，gap audit）

- 报告：`r07-2026-09-16-gap-audit/report.md`（三路并行实测：数据/运行 · 工程/质量 · 功能后置；pending-items 22 条逐条核实）
- 结论：登记项大多属实；**门红复发**（挖矿在途，`make gates` exit 2 @`639bf3f`）；新发现 8 条 I（登记如下）+ 一批 M/backlog（M 只在报告 §3）
- 证据：`r07-2026-09-16-gap-audit/evidence/{data-audit,eng,feature}/`

| ID | 级 | 问题 | 位置 | 状态 | 修复说明（团队填） | 复查（reviewer） |
|---|---|---|---|---|---|---|
| R07-MIG-I1 | I | **门红复发**：G-LEGACY（`extcnt.md:85` 旧坐标 tracked）+ G-INDEX（新因子 `max_effect_20d_extsum` 未重生）+ G-ANNOTATE（缺 snapshot）；`make gates` exit 2——与 R06-MIG-I1 同类，挖矿在途未收口【实测】 | `governance/ops/gates.sh`；`knowledge/dossiers/factors/volatility/max_effect_20d_extcnt.md:85` | verified | 5919337 + ad43a87（循环 2）；extcnt.md 旧坐标修正；重生成因子索引；补 extsum/turnrank_top10 snapshot（annotate 循环至绿）；`make gates --structure` 的 G-LEGACY(tracked)/G-INDEX/G-ANNOTATE 全绿；证据 governance/evidence/verification/R24/17-r07-fixes/mig/ | verified（R35 复查：与声称一致；证据 governance/evidence/verification/R35/R07-MIG-I1/） |
| R07-MIG-I2 | I | **档案模板根因**：`_template.md:75` 仍写旧结果根占位引用（results 根 + `<name>` 目录 + summary 文件名）→ 存量旧坐标 164 行/158 文件持续增长（含迁移后新写 5 处）【实测】 | `knowledge/dossiers/factors/_template.md:75` | verified | 79bbc79（workspace）+ 458030b（research）；模板 `_template.md` 旧结果根/路径修正；tracked 干净档案 156 份 163 处机械替换（具体指针口径修前 164 行/156 文件 → 修后 0）；`_mine_round_*` 引用改 runs/platform/_mine_rounds/；残余为 README 明示不改写的历史尾行与行级豁免；证据 17-r07-fixes/mig/ | verified（R35 复查：与声称一致；证据 governance/evidence/verification/R35/R07-MIG-I2/） |
| R07-GATE-I3 | I | **G-LEGACY 判据盲区**：untracked 不扫（真实存活漏网）/ 裸 `results/` 无 pattern / 模式表缺 `research/tools/lib/` / 3 README 整文件豁免可掩盖任意行【实测】 | `governance/ops/gates.sh:57-93` | verified | 83bf9a2 + ffa78ef（skill 纪律）；G-LEGACY 重写为 tracked+untracked 双扫（ls-files -o + grep）、裸 results/ 单条 PCRE 负向后顾排除 platform/results/runs/results/test_results、补已迁 lib 与档案目录模式、全文豁免改行级；TDD：注入修前 0 捕获 → 修后 A/B 均捕获、误报探针 0；证据 17-r07-fixes/mig/05-06 | verified（R35 复查：与声称一致；证据 governance/evidence/verification/R35/R07-GATE-I3/） |
| R07-DATA-I4 | I | **`daily_basic.circ_mv` 全空**（`float_shares` 16.87M 行可派生未派生）→ 10 spec×4 族 CH 静默全 null；README/DDL「无数据源」失真【实测】 | `platform/tools/ch_ingest/ingest_daily.py:172-175` | verified | 18e8531；ingest_daily 派生 circ_mv=close×float_shares（同 total_mv 口径，NaN→NULL）；DDL/README/interface 同步；CH 单表重灌 18,124,805 行、circ_mv 非空 0→16,873,795、reconcile exit 0、抽样逐值 rel=0；下游探针 signal_null_ratio 1.0→0.0；证据 17-r07-fixes/data-i4/ | verified（R35 复查：与声称一致；证据 governance/evidence/verification/R35/R07-DATA-I4/） |
| R07-CONTRACT-I5 | I | **契约缺 NEXT_WINDOW**：interface.md 0 处（:2277 仍 "NEXT_OPEN only"），与代码/测试/R22 E2E 矛盾【实测】 | `knowledge/contracts/interface.md:2277` | verified | 73f30f6；interface.md §6 新增「R22 Minute-Window Execution」小节（NEXT_WINDOW 枚举/配置/窗口成交语义/WINDOW_END_BASED/schema v2/验收），修正 4 处 NEXT_OPEN-only 误导表述；test_doc_paths_exist 通过；证据 17-r07-fixes/data-i4/06-next-window-contract.diff | verified（R35 复查：与声称一致；证据 governance/evidence/verification/R35/R07-CONTRACT-I5/） |
| R07-STRAT-I6 | I | **策略面口子**：`factorlab lint --strategy` 不存在（exit 2）；`universe_override` YAML 接了不消费（只 dry-run 打印）【实测】 | `research/tools/strategies/run_strategy.py:44`；CLI | verified | 56f5f9e（platform）+ 9e1fdaf（research）；`factorlab lint` 自动识别策略文档走 load_strategy_doc 严格校验（未知键/NEXT_WINDOW/V1 rules，exit≠0）；universe_override 运行链消费（canonical 精确匹配、空交集 fail fast、null 零变化）；测试 test_cli_lint_strategy.py（7）+ override 双腿 3；证据 17-r07-fixes/strategy/ | verified（R35 复查：与声称一致；证据 governance/evidence/verification/R35/R07-STRAT-I6/） |
| R07-LINT-I7 | I | **lint 不校验库函数 arity/形态**：`ts_cum_count(close,5)` lint OK → 运行 TypeError（"lint OK ≠ 运行"一类）【实测】 | lint 静态管线（`core/engine/semantics.py` 系） | verified | 16fbc84；生成器记录 arity（必需位置参数/上限），OpMeta 增 min_args/max_args，semantics._check_arity 静态校验越界/缺参 → SemanticError；`ts_cum_count(close,5)` lint exit 1（对照运行期 TypeError）；make lint-factors 173/173；证据 17-r07-fixes/lint/ | verified（R35 复查：与声称一致；证据 governance/evidence/verification/R35/R07-LINT-I7/） |
| R07-DATA-I8 | I | **CA Gate 多年连续回测硬阻断**（R03-I8 复核）：真实事件最小复现拦截；分段重基后不可算连续 Sharpe/回撤——"多年回测"无连续产物【实测】 | `knowledge/contracts/interface.md` §6 | verified | 377432c（platform）+ b87ddd2（契约/设计）；CA 处理落地：load_adj_detail_window + apply_corporate_actions（现金分红入账/送转 Decimal 精确缩放 floor/配股 V1 不参与+warning/多事件复合），backtest 在 snapshot 后 orders 前应用；4 年 53 决策 4 事件单 run 连续 NAV/Sharpe/Drawdown 产出，手算对拍与存根必败锁定；残余 fail-closed（停牌持仓/缺明细/缩股/负值/缺表）已文档化；证据 17-r07-fixes/ca/ | verified（R35 复查：与声称一致；证据 governance/evidence/verification/R35/R07-DATA-I8/） |

---

## R08 指标全量核对发现（2026-09-16，metrics audit）

- 报告：`r08-2026-09-16-metrics-audit/report.md`（口径走查 + 三路独立复算 + 业界对照）
- 复核结论：**数值层全部通过**（IC/decile/turnover 逐值、分层 178/178、M8 恒等式 81/81、标签 bit-exact）
- 证据：`r08-2026-09-16-metrics-audit/evidence/{recompute,strategy,labels}/`（40+ 文件）
- 未登记项（已立项 v2 口径/增强）只在报告：D2-D6、E1-E4、playbook 矛盾等

| ID | 级 | 问题 | 位置 | 状态 | 修复说明（团队填） | 复查（reviewer） |
|---|---|---|---|---|---|---|
| R08-MET-I1 | I | **历史产物与现行口径不一致（无版本字段）**：`max_effect_20d_high` summary coverage 为 R03-I2 修复前口径（1.0/896750 vs 复算 0.9599/934236）；`intraday_high_time` weekly 每周多日期（R05-I4 前产物，n_weeks=27 vs 154）；`summary.evaluation` 无 `version`【实测】 | `runs/platform/max_effect_20d_high/summary.json`；`runs/platform/intraday_high_time/weekly.parquet`；口径修复提交 `3c67c0d`/`29d1e07` | open |  | 复查注记（open，R35 实测）：R30 Task11 已处置（39 重跑/16 删/17 在途例外）；`intraday_high_time` 已 v2/daily；`max_effect_20d_high` 仍 v1 旧口径（在途例外）。待团队回填修复说明（证据 governance/evidence/verification/R35/R08-MET-I1/）。 |
| R08-DATA-I2 | I | **131 只退市股 `adj_factor` 全 NULL → 全历史标签为 null**（含退市前整段）；4.42M 行中 134 只 code 全历史 signal null（43,941 行=0.99%）→ 评估样本系统性缺尾部风险段【实测】 | CH `adj_factor` × `daily`（2023-2026 退市 47/48/30/9 只）；`platform/tools/ch_ingest/adj_backfill.py` | open |  | 复查注记（open，R35 实测）：R30 Task12 已落地（sidecar 83,967 行/180 码；工具+19 测试；CH 抽 5 码非空且一致；面板窗 NULL 41,984→157）。待团队回填修复说明（证据 governance/evidence/verification/R35/R08-DATA-I2/）。 |

---

## R09 分钟链计算效率发现（2026-09-17，minute perf）

- 报告：`r09-2026-09-17-minute-perf/report.md`（折日瓶颈剖析 + 病态公式形态 + 优化建议）
- 背景：按 R30/D9 逐日口径重跑分钟因子时逐因子计时；数值口径经 R08 复核无问题，本处纯**吞吐/可用性**。
- 证据：`r09-2026-09-17-minute-perf/evidence/timings.md`（实测计时）

| ID | 级 | 问题 | 位置 | 状态 | 修复说明（团队填） | 复查（reviewer） |
|---|---|---|---|---|---|---|
| R09-PERF-I1 | I | **分钟折日逐算子逐组逐行物化**：`minute_ops.py` 所有 `im_*`/`day_*` 内联 `.over(["code","date"], order_by=...)`，多算子串联不共享分组；含 `im_delay` 序列再产品叠加（平方/乘滞后）的形态直接 ≥15min **超时**（vol_asym/autocorr_micro/vol_price_corr）【实测】 | `platform/src/factorlab/core/ops/minute_ops.py:76-135`；`core/engine/minute.py:compute_minute_factor_panel` | verified | `core/engine/minute_fold.py` 融合路径（commit `2b4daae`）：day_* 聚合参数物化一次 + 组内 over 广播（死赋值剪枝）；im_* 预排序/`seq_*` 去 order_by/CSE；不支持形态完整回退旧路径。after 同窗（`governance/evidence/verification/R31/minute-perf/`）：vol_price_corr fold 64.7s→27.3s（**2.37×**）、autocorr 1.36×、am_pm_vol 1.29×；真数据 4 因子逐 cell 对拍：纯逐行参数 bit-exact，旧路径嵌套 over 形态 per-factor（autocorr ≤3.3e-7、vol_price_corr ≤2.5e-6，f32 ulp；旧路径自身 vs f64 oracle 达 1.2e-5）；平台全量 3447 passed（1 预存红：并发在途 `im_cummax` 未同步 catalog.md） | verified（R35 复查：与声称一致；证据 governance/evidence/verification/R35/R09-PERF-I1/） |
| R09-PERF-I2 | I | **条件取值 `day_max(if_else(minute_index==k,x,None))` 全组扫描物化**：列内近全 null 仍整组 max 扫描；`day_first/day_last` 亦双 over【实测】 | `platform/src/factorlab/core/ops/minute_ops.py:102-135` | verified | 融合路径条件重写（commit `7760357`）：`day_max/day_min(if_else(cond,x,None))` 条件外提 → `x.filter(cond).max/min` 单次 agg（简单 `minute_index` 比较内联、复合条件物化条件列）；新算子 `at_minute(x,k)`（k int 0..239 静态门+运行时双防线；语义=当日 k 行值广播全组、缺失→null；catalog/interface 同步）；`day_first/day_last` 单次 `sort_by().first/last`。真数据对拍（`governance/evidence/verification/R31/minute-perf/after-p3/`）：P3 vs P2 全 7 因子逐 cell max\|Δ\|=0；条件因子 lunch_jump/close_auction_premium bit-exact、open_minute_mom f32 ulp；`at_minute` vs `day_max(if_else)` 0/281338 差异；同进程 fold：close_auction_premium 17.8→11.9s（1.50×）、lunch_jump 20.7→16.2s、open_minute_mom 14.6→12.3s；after-p3 bench 四基线 fold 13.7/18.7/21.1/23.2s（before 18.2/20.4/31.2/64.7s）；平台全量 3470 passed、15 skipped（catalog 预存红清零） | verified（R35 复查：与声称一致；证据 governance/evidence/verification/R35/R09-PERF-I2/） |
| R09-PERF-M3 | M | **无逐阶段计时/剖析开关**：`factorlab run` 不输出折日/label/评估/分层分段墙钟与 RSS，慢在哪一步只能外部掐表【实测】 | `platform/src/factorlab/app/run.py`（分钟链 `_run_factor_minute`） | verified | `app/profile.py` 分段计时（`--profile` / env `FACTORLAB_PROFILE=1`；stderr 人读摘要 + `summary.runtime.profile` append-only）——段 read_data/fold/label/evaluate/layered_backtest/persist，20Hz + 段边界采样；日频/分钟链与评估装配同接入。提交 `df0c3b0 feat(engine): run --profile 分段计时（R09-M3）`；测试 `tests/test_profile_timing.py` 9 条（含"不调 evaluate/backtest 无对应段"禁止行为与折日断线变异检出）；证据 `governance/evidence/verification/R31/minute-perf/before/timings.md`（before 4 因子逐段基线）与同目录 after/after-p3/after-p4 全程复用 | verified（R35 复查：与声称一致；证据 governance/evidence/verification/R35/R09-PERF-M3/） |

- **§2.5 分块策略（chunk 并行）备注（R09-PERF-P4，2026-09-19）**：
  `--chunk-workers N`（默认 1=现行为；N≥2 按 chunk 并行「读+折日」有序合并）
  + 并发前预算门 N×3.6GB/chunk vs `FACTORLAB_MAX_MEMORY`（打开 DB 前拒绝）
  + CH 批读单查询旋钮 `FACTORLAB_CH_MAX_THREADS`/`FACTORLAB_CH_MAX_BLOCK_SIZE`
  （spike 证收益在噪声带 → 平台默认不变）。同窗 after-p4：N=2 总墙钟加速
  1.36–1.44×、峰值 RSS ≤6.2GB < 8GB；N=3 在 8GB 护栏下拒绝；N=1 vs N=2
  真数据 4 因子逐 frame bit-exact。提交 `730f1c8`；证据
  `governance/evidence/verification/R31/minute-perf/after-p4/`。

## R31 flab 使用发现（2026-09-18，研究员视角）

- **R31-API-I1（功能缺口，影响入库判定，open）**：`flab factor resic` 无 horizon/
  `--fwd-col` 参数，固定 weekly 栅格 + `forward_return_5d`。minute 库成员全部是
  daily/forward_return_1d 口径（evaluation v2 / D11），resic 判定口径错配：
  retention 系统性偏低，把真实增量因子误挡在门外（实测 jump_ratio weekly-retention
  0.29/cn_spread 0.46 均"观察"，但逐日 1d 口径分年 resIC 三年同号 |t|=6~10）。
  建议：加 `--fwd-col/--horizon` 参数或按候选因子 evaluation.target 自适应。
  临时替代：研究侧脚本按日残差检验（本次已用，证据见 jump_ratio/cn_spread 档案）。
- **R31-API-P1（体验，open）**：`flab factor admit` 默认 `--scales daily`，minute
  候选必须显式传 `--scales minute`，且 admit 内部 resic 同样受 I1 影响——admit 的
  verdict 对 minute 因子不可直接采信，需配合分年逐日增检复核（已在 intraday_campaign.md 固化流程）。

- **R31-STAT-I1（判据级缺陷，重要，2026-09-19）**：D10 的固定 t 阈值未做多重性定标。
  quantresearch 全历史重审计（99 试验联合 max-T 校准，B=300）：联合临界值 |t|≈3.45，
  旧阈值 2.5~3 的放行在 FWER 意义下不成立；且**原始 t 排名与条件信息排名几乎不相关**
  （abs_auction_premium 原始 t=3.2 但条件 t=7.4；auction_range 原始 t=8.6 条件 t=-0.6）。
  现行 29 员参考库经后向归约仅 20 员携带独有信息（9 员为换皮）。
  "分钟文法饱和"的旧结论系 weekly-resIC 判据错配所致，条件口径下发现率 λ̂≈0.5/试验，未饱和。
  方法与全量证据：`/data/students/gaolei/quantresearch/{README.md,REPORT.md,results/*}`。

- **R31-DQ-I1（阻塞级，open，2026-09-19）**：读取门（Plan DQ-M1 T7）已上线，但
  health artifact **从未发布过 PASS**——`data/health/ashare_daily/` 全部 8790 分区
  health_status=UNKNOWN（data_version=LEGACY，completeness=UNKNOWN），且 UNKNOWN 的
  完整性检查独立于 accept_quality opt-in（"不靠 coverage 推"），research 侧无合规通路。
  后果：所有新 `flab factor run` 在评估段 rc=8（面板产物可生成，评估 summary 缺失）。
  请求：a) 平台跑数据健康发布链（health.py publish 全历史或至少 2023-01→2026-09），
  b) 或为 UNKNOWN-LEGACY 提供 research opt-in 通道（含 manifest），
  c) 顺带：`flab data status --pretty` 报 USAGE 错（与全 CLI 的 pretty 约定不一致）。
