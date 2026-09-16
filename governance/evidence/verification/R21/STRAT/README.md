# R21 STRAT 子系统修复证据（R01-STRAT-C1/C2/C3/I4/I5/I6/I7/I8）

负责人：STRAT 子系统修复。边界：未改 `docs/reviews/**`、`platform/**`（
`platform/src/factorlab/adapters/strategy_artifacts.py` 的改动属其他 agent）、
`ch_ingest/ashare_ingest/converters/universe_stages`。未 commit。

必跑命令（T1）：

```
platform/.venv/bin/python -m pytest -q research/tools/strategies/tests \
  research/tools/lob_fact/tests research/tools/quark_download/tests
```

结果：`T1_full.txt`（230 passed，含新增 C2 rules 回归）+ `T1_with_lib_converters.txt`
（加 lib/converters 共 269 passed）；T2（emb）：`T2_lob_touched.txt`（45 passed）、
`T2_converters_lib.txt`、`I4_after_t2.txt`。

| finding | 修复 | 测试 | 红→绿证据 |
|---|---|---|---|
| C1 跌停过滤静默失效 | `strategy_crash_bottom.py` join 前两侧 code 规范化到 6 位（临时键） | `test_strategy_limit_down_normalizes_code_format` | `C1_C2_I5_red.txt` → `C1_C2_I5_green.txt`；probe `probe_before.txt` vs `C1_C2_I5_probe_after.txt`（.SZ panel + 6 位 limit_down：修复前 weeks=1/nav=1.10，修复后 weeks=0 过滤生效） |
| C2 段界清仓成本/持仓泄漏 | 段界模拟清仓（按上段末仓位计卖出成本，计入本周 r_net）+ holdings 每段重置（set/dict 两模式）；末段不强制清仓 | `test_strategy_segment_boundary_charges_liquidation_and_reentry`、`test_strategy_holdings_reset_across_episodes_blocked_without_reset`、`test_strategy_rules_mode_holdings_reset_at_segment_boundary`、`test_strategy_segment_boundary_no_double_charge_without_next_episode` | `C1_C2_I5_red.txt` → `C1_C2_I5_green.txt`；rules 模式回归 `C2_rules_mode_red_green.txt`；probe 修复后 `total_cost=0.0105`（3 次成本）、`avg_turnover=1.0` |
| C3 quark server 死入口 | `main()` 改用共享客户端 `quark_client.cookies()`（缺失显式 exit 1）；删死函数 `_load_cookies` | `test_server_main_cookie_missing_exits_with_message_not_nameerror`、`test_server_main_proceeds_past_cookie_check_with_manifest` | `C3_red.txt`（NameError 2 failed → 8 passed） |
| I4 T2 单跑 collection error | `lib/tickkit.py` 模块级 `ensure_platform()` 自举（与 lib/tickdata 同模式） | `test_platform_bootstrap.py::test_tickkit_bootstraps_platform_not_ambient`、`::test_extract_sz_cancels_single_module_collects_and_runs` | `I4_before_t2.txt`（T2 ModuleNotFoundError）→ `I4_red.txt`（污染解释器红）→ `I4_after_t2.txt`（T2 单模块 10 passed；自举 2 passed） |
| I5 `--long --mc` KeyError | long 模式 episode 补 `returns` 周收益单元 | `test_strategy_long_episodes_carry_returns_for_monte_carlo` | `C1_C2_I5_red.txt` → `C1_C2_I5_green.txt`；probe `mc long ok 1` |
| I6 结果不可复现 | 如实标注历史快照 + 恢复条件 + 重跑命令链（真跑尝试见下） | —（数据面） | `i6_data_availability.txt`（index_daily 0 行 / stock_st 缺失 / panel signal 全 null）、`i6_factor_run.log`、`i6_run_note.md` |
| I7 lob gate 语义 | `day_gate` 补 M2 差量全分类硬拒；recs 落 m2/m3（ghost_vol 原被丢）；结构性门拒 → error；月未完成 exit 1 | `test_day_gate_m2_classification_identity`、`test_process_date_records_m2_m3_diagnostics`、`test_day_outcome_structural_gate_reasons_are_error_not_hard`、`test_main_cli_structural_error_exits_nonzero_no_success` | `I7_red_green.txt`（4 failed → 33 passed） |
| I8 v2 清空已成功 URL | 重试耗尽只丢失败 fid，保留成功 URL | `test_v2_partial_link_failure_keeps_successful_urls` | `I8_red.txt`（f1 也报 no link → 9 passed） |

## I6 真跑尝试（结论：当前数据状态下不可复现）

真实执行了因子重生成（`FACTORLAB_DATA_BACKEND=ch factorlab run … --no-backtest
--chunk-days 500`），产物落盘 11,947,238 行但 **signal 全 null**（`n_weeks=0`）——
`index_daily` 空（无 000852.SH）与 `stock_st` 缺失所致。全 null 无效 panel 已删除，
避免被误当重跑结果。文档已按 finding 要求标注历史快照、不可复现原因、恢复数据条件
与完整重跑命令链：`research/docs/strategies/crash_bottom_leader_strategy.md` 文首状态块。

## 未解决点 / 建议文档修改

- **I6 真产物**：待 `index_daily`（000852.SH 全历史）与 `stock_st` 灌入后按文档命令链
  重跑；预期数字低于历史值（C1/C2 修复后跌停过滤真实生效、段界成本真实扣减）。
  建议 findings 台账保留 I6 为 open-until-rerun，或改为"已标注历史快照 + 条件"。
- **I7 口径**：W3 memo §3 的"unattributed 0（本批门）"已修订为"差量全分类
  （missing == attributed + unattributed），unattr>0 记录不拒"（技术论证见 memo §3）。
  `run_lob_batch.main` 月未完成现退出非 0；runbook 若按旧"总是 0 退出"编排需同步。
- **C1 附带**：`strategy_long_backtest` 接收 `limit_down` 参数但从不使用（既存问题，
  非本 finding 范围；长模式已被文档证伪）。建议后续要么接线要么删参数。
