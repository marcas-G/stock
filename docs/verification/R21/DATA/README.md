# R21 DATA 子系统修复证据（R01-DATA-C1 / I3-I8）

负责人：DATA 子系统修复。基线下测试：`tests/test_pit_universe.py test_adjust.py
test_qfq_chunk_invariance.py test_audit.py` + rebuild/refresh/source 相关共 216 passed。

| finding | 修复 | 证据 |
|---|---|---|
| C1 退市股永不退市 | `adapters/read/staleness.py` 运行期 gate（250 交易日）+ app/run.py 接线片段（待 coordinator） | `C1.md`、`probe_delisted_before.txt`、`probe_stale_gate_prod_after.txt` |
| I3 pit_qfq 分块依赖 | `load_pit_qfq_base_adj` + `view_prices(pit_qfq_base_col=...)` | `I3.md`、`probe_pit_chunk_before.txt`、`probe_pit_chunk_after.txt` |
| I4 CH delist_date Date 崩溃 | `_uf_skeleton_ch` `toString(toYYYYMMDD(toDateOrNull(toString(col))))` | `I4.md`、`probe_delist_date_ch_before/after.txt`、`i4_before_failure.txt` |
| I5 rebuild 重复行 | staging 日期对账 + resume=False 清表 + build_final_db integrity gate | `I5.md` |
| I6 manifest 非原子 | atomicio 原子写 + 截断隔离 + atomicio commit 残留修复 | `I6.md` |
| I7 vol/amount 单位漂移 | duckdb 读面 ×100/×1000 归一（股/元） | `I7.md`、`probe_unit_scale.txt` |
| I8 refresh 丢原因 | failed_errors 进 report/manifest | `I8.md` |

GREEN 原始输出：`green_c1.txt` / `green_i3.txt` / `green_i4.txt` / `green_i5_i6.txt` / `green_i7_i8.txt`。

接线验证（**R02-I9(c) 更正：R21 实际已接线**，此前"未改 app/run.py"口径作废）：

- 中间态（归档保留、未改）：`verify_wiring_run.py` + `verify_wiring_run.txt` 把总结的接线
  片段 patch 到 `app/run.py` **内存副本**真跑——pit_qfq FULL/60/120 位级一致；当时工作区
  `run.py` 尚未接线（FULL/60 仍 False）；生产 600005 gate fired。该文件记录的是接线前证据。
- 接线后（R21 正式落树）：`fc2858c` 已改 `app/run.py`——`assert_no_stale_listed`
  接入 run 链（fill-seed 之前），生产路径生效。
- run_factor 级回归：committed 侧未见（`fc2858c` 只带 staleness 单测）；由 **R02-C2 在本轮补充**
  （`platform/tests/test_pit_staleness.py::test_run_factor_short_window_dead_price_rejected`
  / `test_run_factor_chunked_dead_price_rejected` 等，另一 agent 工作区进行中）。

边界遵守（写档时口径）：EVID 侧未改 `research/**`、`docs/reviews/**`；
`app/run.py` 后由 `fc2858c` 接线（见上）；`platform/docs/interface.md`/`catalog.md`
分别由 `08432ab`/`ea2ebfe` 在本轮同步。
