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

接线验证（app/run.py 未改；把总结的接线片段 patch 到 run.py 内存副本真跑）：
`verify_wiring_run.py` + `verify_wiring_run.txt`（pit_qfq FULL/60/120 位级一致；当前
run.py 未接线 FULL/60 仍 False；生产 600005 gate fired）。

边界遵守：未改 `app/run.py`（接线片段在总结）、未改 `research/**`、
`docs/reviews/**`、`platform/docs/interface.md`、`catalog.md`。
