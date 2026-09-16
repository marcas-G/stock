# R01 证据索引（probe 脚本 + 原始输出）

所有 probe 为只读复现用；运行前 `cd /data/students/gaolei/stock/platform`（除注明外）。
涉及 ClickHouse 的 probe 只读且必须限流（tick 表数十亿行，禁止全扫）。

## engine-dsl/ → R01-ENG-C1/C2/I5

| 文件 | 用途 | 运行 |
|---|---|---|
| `future_subscript.yaml` | C1 最小复现：`signal = close[-1]` | `.venv/bin/factorlab lint <file>`（实测 exit 0 = 漏洞存在；修复后应被拒） |
| `future_const.yaml` | C2 最小复现：`_n=3; ts_delay(close,-_n)` | 同上 |
| `neg_shift_lint.yaml` | I5：lint 不跑语义门 | 同上 |
| `probe_run_factor.py` | C1/C2 端到端复现（run_factor → 断言 signal(t)==close(t+k)） | `.venv/bin/python <file>`（需 CH） |
| `future_subscript_summary.json` / `future_const_summary.json` | 实测产物 summary（signal 基于未来收盘） | 只读 |

## data/ → R01-DATA-C1/C2/I3/I4/I5/I7

| 文件 | 用途 |
|---|---|
| `probe_delisted.py` | C1：退市股仍 is_listed（600005.SH） |
| `probe_pre_close.py` | C2：ex-date pre_close=raw 前收（300842.SZ） |
| `probe_pit_chunk.py` | I3：pit_qfq full vs chunk |
| `probe_delist_date_ch.py` | I4：CH delist_date Date 类型崩溃 |
| `probe_ch.py` / `probe_prod_read.py` / `probe_coverage.py` / `probe_market_open.py` | 数据面辅助证据 |
| `REVIEW.md` | 数据面子代理完整报告 |

## eval/ → R01-EVAL-C1/C2/I1-I9

| 文件 | 用途 | 运行 |
|---|---|---|
| `probe_layered.py` | C1：NaN → D1/D10 nan；I6：periods≠n_weeks；M3：负收益 drawdown | `.venv/bin/python <file>`（无需 CH） |
| `probe_turnover_nan.py` | C2：weekly_ic vs kernel（NaN） | 同上 |
| `probe_contract.py` / `probe_kernel_adversarial.py` | 契约向量核对；t_stat 分母、ordinal tie | 同上 |
| `probe_correlation.py` | I2/I3/I4：非 Spearman、按日跑、有偏抽样 | 同上 |
| `probe_web.py` | I1/I5：target 忽略、缺字段 500 | 同上 |
| `probe_cross_section.py` / `probe_sign_smalln.py` | resIC 精确性复核 | 同上 |

## m8/ → R01-M8-I1 等

| 文件 | 用途 |
|---|---|
| `probe3_stale_manifest.py` | I1：覆盖写崩溃 → 混合 artifact 可加载（核心证据） |
| `probe4_exec_hybrid.py` | I1：M8 侧同一问题 |
| `probe1_empty_adj.py` / `probe2_trace.py` / `probe5_empty_tamper.py` | CA Gate / NAV 账务 / 空表 tamper |
| `review.md` | M7/M8 子代理完整报告（含 NAV 恒等式验证结论） |
| `stub_plugin.py` | 测试桩有效性演示 |

注：probe 依赖的 `/tmp` 中间 duckdb/artifacts 未随仓存档，脚本含自建逻辑。

## tools/ → R01-TOOLS-C1/C2/I1/I4 等

| 文件 | 用途 |
|---|---|
| `probe1_basics.py` | daily 派生字段核对 |
| `probe2_stklimit.py` | I4：stk_limit 除权日偏离 |
| `probe3_violations.py` / `probe4_fractional.py` / `probe5_collision.py` | 派生/碰撞检查 |
| `probe6_adjfactor.py` | I1：adj_factor=0 破坏 qfq |
| `probe7_parquet.py` | 源 parquet ↔ CH 对拍 |
| `ref_unparsed.py` | v4.py vs jqdata 冻结原版 AST 对照（无漂移结论） |

## strategies/ → R01-STRAT-C2

| 文件 | 用途 |
|---|---|
| `probe_strategy_cost.py` | C2：段边界清仓成本未计（2 段应 3 次只收 1 次） |

## evidence-audit/ → R01-EVID-I1/I2 等

| 文件 | 用途 |
|---|---|
| `gates-rerun.log` | `bash scripts/gates.sh` 复跑输出（与 R20 证据一致） |
| `build-index-check.log` | `build_index.py --check` 复跑输出 |
| `r4-parity.diff` | 早期轮次证据比对 |
