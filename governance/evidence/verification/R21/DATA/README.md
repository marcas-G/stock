# R21 DATA 子系统修复证据（R01-DATA-C1 / I3-I8）

负责人：DATA 子系统修复。基线下测试：`tests/test_pit_universe.py test_adjust.py
test_qfq_chunk_invariance.py test_audit.py` + rebuild/refresh/source 相关共 216 passed。

> **R22 状态更新（2026-09-15，R02-I9 DATA 部分）**：本 README 初版写于接线提交
> 之前，正文中「app/run.py 未改/接线片段待 coordinator」的表述**已被 fc2858c
> 取代**——`app/run.py` 已在 **fc2858c**（R21 DATA 修复本身）完成 C1 gate 与
> pit_qfq 全局 asof base 的真接线；R22 复审的 C2（staleness 窗口无关）与 I4
> （adv20 左窗按行情行数）又在 **da8bba0 / d973e0e** 继续加固。接线后验证记录：
>
> | 验证 | 层级 | 结果 |
> |---|---|---|
> | `tests/test_pit_staleness.py::test_prod_like_no_delist_column_gate_fires` | 纯函数/PIT 骨架 | 无 delist_date + 断流 → gate fired |
> | `tests/test_pit_staleness.py::test_run_factor_wires_stale_gate_long_window` | **run_factor 级（新增）** | 300 日全窗 → run_factor 直接 ValueError（C1 gate 真接线，非内存 patch） |
> | `tests/test_run_factor.py::test_run_factor_pit_qfq_asof` | run_factor 级 | 除权日值 = 8×1.5/11 - 1（全局 base 生效） |
> | `tests/test_run_factor.py::test_run_factor_pit_qfq_full_equals_chunked` | **run_factor 级（新增）** | FULL vs CHUNK-2 逐 cell 一致；首块（事件前）= 10×1.0/1.5（证明用全局 base 而非块内 latest） |
> | `docs/verification/R22/R02/data/c2-after-probe.txt` | run_factor 级复现 | 240 日窗口/30d 分块修复后均 ValueError（窗口无关判定） |
> | `docs/verification/R22/R02/data/i9-wiring-stub-check.txt` | 存根检查 | 把 gate/base 接线替换为 no-op 后新增测试必败（测试能识别存根） |
>
> 下方正文保留初版口径（历史记录），以本节状态为准。

| finding | 修复 | 证据 |
|---|---|---|
| C1 退市股永不退市 | `adapters/read/staleness.py` 运行期 gate（250 交易日）+ `app/run.py` 接线（**fc2858c 已落**；R22 da8bba0 改窗口无关） | `C1.md`、`probe_delisted_before.txt`、`probe_stale_gate_prod_after.txt` |
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
- run_factor 级回归（R22 已补齐并提交）：`platform/tests/test_pit_staleness.py::
  test_run_factor_wires_stale_gate_long_window`（C1 gate 真接线——run_factor 直接
  ValueError）、`test_run_factor_short_window_dead_price_rejected` /
  `test_run_factor_chunked_dead_price_rejected`（C2 窗口无关）与
  `platform/tests/test_run_factor.py::test_run_factor_pit_qfq_full_equals_chunked`
  （pit_qfq 全局 base——FULL vs CHUNK-2 逐 cell 一致 + 首块显式锚点 10×1.0/1.5）；
  存根替换必败证据 `docs/verification/R22/R02/data/i9-wiring-stub-check.txt`。

边界遵守（写档时口径）：EVID 侧未改 `research/**`、`docs/reviews/**`；
`app/run.py` 后由 `fc2858c` 接线（见上）；`platform/docs/interface.md`/`catalog.md`
分别由 `08432ab`/`ea2ebfe` 在本轮同步。
