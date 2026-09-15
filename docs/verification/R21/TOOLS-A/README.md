# R21 TOOLS-A 交付证据（R01-TOOLS-C1/C2 + DATA-C2 + I1..I7）

执行人：stock 开发（TOOLS-A）。日期：2026-09-15。
台账：`docs/reviews/findings.md` TOOLS 段；报告：`docs/reviews/r01-2026-09-15-strict-review/report.md`。

## 0. 备份与回退

| 项 | 值 |
|---|---|
| 旧 fact 备份 | `data/fact/daily_fact/daily_fact.parquet.bak-R21`（sha256 `36c64f70…b58fe7e`，18,162,795 行） |
| 新 fact（最终） | `data/fact/daily_fact/daily_fact.parquet`（sha256 `79f68fee…1235f6`，18,124,805 行 / 5,861 codes / 1990-12-19..2026-08-21） |
| sidecar | `data/fact/daily_fact/delisted_codes.parquet`（sha256 `12a47749…03afc`，331 codes） |
| 回退 | `mv daily_fact.parquet.bak-R21 daily_fact.parquet` + 重跑 `ingest_daily → derive_stk_limit → adj_backfill`（脚本均 TRUNCATE/DROP 幂等） |

注：第一次重生成（sha `83a87dc7…`）在验证时发现 **错标退市文件（前复权序列）抢在原生
raw 文件之前**赢得 600811 重叠日；已加"原生 2_ / 错标 3_ 信任序"并作废式重跑（run2）。
最终 fact 与原生 `600811_东方集团.xlsx` 逐值对拍一致（`after/check_600811_content.txt`）。
| CH 表 before/after 行数 | 见下表与 `before/probe1_basics.txt` / `after/probe1_basics.txt` |

## 1. before → after 行数

| CH 表 | before | after |
|---|---|---|
| daily / adj_factor / daily_basic / adj_detail | 18,162,795 | 18,124,805 |
| stk_limit | 17,889,079 | 17,854,764 |
| stock_basic | 5,866 | 5,861 |
| adj_event | 57,173 | 57,173 |
| bars_1m / tick_* | 1,853,379,840 / 98.6 亿 | 未动，reconcile 全绿 |

## 2. Finding → 修复 → 证据

| finding | 修复 | 测试 | after 证据 |
|---|---|---|---|
| C1 单位 1e4 | `ingest_daily.derive_daily_fields`：total_mv=close×total_shares；turnover=vol/(float×1e4)×100 | `test_ingest_daily_derive.py::test_total_mv_and_turnover_units_600519` | `after/verify_points.txt`（600519=168,836,020.9 万元 / 0.4410%；000001 逐值） |
| C2 退市文件 code 错位 | `import_daily`：delisted 解析 in-file `code`（`normalize_src_code`）、shard 名嵌真实代码+idx、merge 按真实代码+日期去重；sidecar | `test_import_daily_delisted.py`（9 个：解析/兜底/冲突/分片/原生优先/合并/E2E 子进程） | `after/probe5|6|7`：5 只假 code 0 行；600811=7598 行（raw，与原生文件逐值一致） |
| DATA-C2 pre_close raw | `derive_daily_fields`：除权参考价（5 形态 + half-up），change/pct_chg 基于它 | `test_ingest_daily_derive.py`（现金+送转/只派现/只送转/配股/half-up/无事件/退市无事件列/首行） | 300842 2024-04-10 pre_close=50.07、pct=+1.8175%；2025 全 4,925 事件日公式一致（0 违规，raw 前收时 4,900+ 违规） |
| I1 NULL→0 | DDL `amount`/`adj_factor` Nullable；ingest NaN→NULL、adj_factor<=0→NULL；存量 ALTER 已执行 | `test_ingest_daily_derive.py::test_nullify_invalid_adj_factor_and_amount`；`test_ch_data_semantics.py` | `after/probe6`：非空 <=0=0、latest<=0=0；NULL=1,259,244（NaN 1,251,061 + 负值 8,183）；600811 全 NULL |
| I2 checkpoint 竞态 | `ch_state.mark_done`：`state.json.lock` 阻塞 flock + 新鲜读改写；`ch_write` worker 不再写断点（主进程 `on_result` 记账） | `test_ch_state_concurrency.py`（8 spawn 并发全存活；重读合并） | 测试 54 passed |
| I3 失败 exit 0 | `run_pool` 返回失败数；`ingest_bars`/`ingest_tick` 非空 exit 1 | `test_ch_write_exit.py`（5 个） | 测试全绿 |
| I4 stk_limit 除权基准 | 依赖 DATA-C2（pre_close 修复后 TRUNCATE 重灌）；docstring 删除"除权日近似" | `test_ch_data_semantics.py::test_stk_limit_ex_date_band_uses_adjusted_pre_close` | `after/probe2`：2025 越带宽行 314→17（event_rows 297→0）；300842 band=60.08/40.06 |
| I5 缺行语义矛盾 | `fillability.py`：`has_limit=False` = 合法无限制 → FILLABLE@raw open；band 检查仅 has_limit=True | `platform/tests/test_open_fillability.py`（改 1 增 1） | 平台 `-k "fillability or execution_quantity or market_open"`：218 passed |
| I6 占位列/index_daily | 平台消费方存在（`source.py:_PLATFORM_COLS`、`idx_ret` LEFT JOIN、`tests/test_source.py`）→ 保留 DDL 空表；README 标注"不要 advertise" | —（判定记录） | 见 §4 建议 |
| I7 reconcile 盲区 | `reconcile.py`：daily 恒等式/日期范围 + stk_limit 期望行数独立复算/band/悬空 + adj_detail/adj_event 行数+uniq；README bars 数更新 | `test_ch_data_semantics.py::test_reconcile_daily_covers_derived_tables` | `after/reconcile_all_run2.log`：全库一致（含 bars 80 分区/tick 39 分区） |
| delist_date（DATA-C1 生产侧） | import_daily 写 sidecar（331 codes，in-file code 优先/文件名兜底/空文件权威）；ingest_daily 写 `stock_basic.delist_date=last+1`；断流>250 兜底 | `test_ingest_daily_derive.py::test_compute_delist_dates_*`；`test_ch_data_semantics.py` | `after/probe_delisted.txt`：600005.SH @2026-08-14 `is_listed=False`、load_daily 空；覆盖 329 codes（920305.BJ=2026-04-30） |

## 3. 命令与原始输出

```
# fact 重生成（4 worker，约 25 分钟；run2 为最终版）
platform/.venv/bin/python research/tools/ashare_ingest/import_daily.py \
    --out data/fact/daily_fact/daily_fact.parquet.new --workers 4
# 校验后原子替换（同目录 mv）
# 存量 DDL 迁移（3 条 ALTER，记录在 after/ddl_alter.txt）
platform/.venv/bin/python research/tools/ch_ingest/ingest_daily.py
platform/.venv/bin/python research/tools/ch_ingest/derive_stk_limit.py
platform/.venv/bin/python research/tools/ch_ingest/adj_backfill.py
platform/.venv/bin/python research/tools/ch_ingest/reconcile.py all
# 测试（T1）
platform/.venv/bin/python -m pytest research/tools/ashare_ingest/tests \
    research/tools/ch_ingest/tests -q          # 55 passed
# 平台受影响测试
cd platform && .venv/bin/python -m pytest -q tests/ \
    -k "fillability or execution_quantity or market_open"   # 218 passed
# 常驻门
bash scripts/gates.sh                          # 全绿
# CH 真读：最小因子（pre_close/pct_chg）
FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/factorlab run \
    docs/verification/R21/TOOLS-A/after/r21_probe_preclose.yaml --no-backtest \
    --output-dir /tmp/opencode/r21_factor               # 2024-04-10 signal=1.817456
```

## 4. 未解决点 / 建议的文档修改（不改 platform/docs、docs/reviews）

1. **I5 残留（"应有行但缺"不可辨）**：`MarketOpenSnapshot` 只有 9 列（无 listing-age/注册制
   段证据），fillability 层无法区分"合法无限制"与"生产者漏派生"。本修复统一为"缺行=无限制"。
   建议后续在产品侧加显式 no-limit 标记或平台 coverage 门核对 `stk_limit` 日期覆盖率；
   当前由 `reconcile.py` 的期望行数不变量 + 平台覆盖 gate 兜底。
2. **I6 文档漂移**：`platform/docs/interface.md:324-326` 与 `catalog.md` 把
   `pe_ttm/pb/dv_ratio/volume_ratio/circ_mv`、`idx_ret` 宣传为可用，但生产 CH 中 5 列恒 NULL、
   `index_daily` 空表（`idx_ret` 恒 NULL）。平台读路径依赖列存在（移除会破坏 SQL），
   故保留 DDL；建议文档加"占位/无数据源"标注或从目录下架。另 `catalog.md` 的
   `pre_close/change/pct_chg` 行已与修复后实现一致（除权参考价），无需改。
3. **I4 残留（12 行 / 2024-2026 非 2025）**：除权日仍有 12 行 close 越带，均为
   ①2 位小数参考价舍入的 ±0.01 边界；②重整/长期停牌复牌等无带宽日（如 603007.SH
   2024-12-26、300176.SZ 2026-08-21）。属 vendor 价格精度与缺"复牌无限制"证据的数据
   缺口，已在 `derive_stk_limit.py` docstring 记录；订单级路径由 CA Gate/universe 覆盖。
4. **920305.BJ**：其退市文件为空文件，靠"退市目录存在=权威信号"的 sidecar 文件名兜底
   拿到 delist_date（2026-04-30）；>250 断流兜底当前命中 0 个新增 code（全部已在 sidecar）。
5. **B 股退市文件**（200xxx/900xxx，38 个）按 `_build_tasks` 既有规则排除，未纳入 sidecar。

## 5. 本 agent 触碰的文件（platform 仅 2 个）

- research/tools/ashare_ingest/{import_daily.py, datapaths.py, tests/test_import_daily_delisted.py}
- research/tools/ch_ingest/{ingest_daily.py, ch_state.py, ch_write.py, ingest_bars.py, ingest_tick.py,
  derive_stk_limit.py, reconcile.py, ddl.sql, README.md, tests/{test_ingest_daily_derive,
  test_ch_state_concurrency, test_ch_write_exit, test_ch_data_semantics}.py}
- scripts/check_dataiface.py（G-READ 登记随调用点移动）
- platform/src/factorlab/core/execution/fillability.py（I5）+ platform/tests/test_open_fillability.py
- docs/verification/R21/TOOLS-A/**（证据）
