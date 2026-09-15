# ch_ingest — 事实库 → ClickHouse 灌入管线

把用户三大事实库（daily_fact / bars_1m / tick_fact parquet）全量灌入本机
ClickHouse（`127.0.0.1:8123` HTTP——clickhouse-connect 仅支持 HTTP；`19000` 是 tcp client 端口，见 `config.yaml`）。库 `factorlab`。

## 表与源

| CH 表 | 源 | 行数 | 任务粒度 |
|---|---|---|---|
| daily / adj_factor / daily_basic / trade_cal / stock_basic | `data/fact/daily_fact/daily_fact.parquet`（相对 `stock/`） | 18,162,795 | 单进程，TRUNCATE 幂等 |
| stk_limit（派生：板块带宽 × pre_close，规则见脚本 docstring） | `factorlab.daily` | 17,889,079 | `derive_stk_limit.py`，TRUNCATE+INSERT 全量 |
| adj_detail / adj_event（派生：daily_fact 除权 7 列） | `data/fact/daily_fact/daily_fact.parquet`（相对 `stock/`） | 18,162,795 / 57,173 | `ch_ingest/adj_backfill.py`（R19 从 ashare 项目 12 号脚本归位），DROP+CREATE 全量 |
| bars_1m | `data/fact/bars_1m/year=YYYY/month=MM/` | 1,854,876,240 | 月分区 ×80 |
| tick_trades / tick_orders / tick_snapshots | `data/fact/tick_fact/{trades,orders,snapshots}/year=YYYY/month=MM/` | 98.6 亿 | 月分区 ×13×3 |

## 用法

```bash
# 0) 先起 CH（或 start.sh）
# 1) 建库建表（幂等）
/path/to/clickhouse client --port 8123   # HTTP（clickhouse-connect 用） < ddl.sql

# 2) daily 层（18M 行，单进程 ~分钟级）
python ingest_daily.py

# 3) bars_1m（80 任务 × 8 worker，约 20-30 分钟）
python ingest_bars.py

# 4) tick 3 表（39 任务 × 8 worker，约 1-1.5 小时；断点续跑）
python ingest_tick.py
python ingest_tick.py --trades      # 只灌 trades

# 5) 派生表（daily 灌完才可跑）
python derive_stk_limit.py          # stk_limit（规则见脚本 docstring）
python research/tools/ch_ingest/adj_backfill.py   # adj_detail + adj_event（T1）

# 6) 对账（退出码 0=全一致）
python reconcile.py
```

## 设计

- **幂等**：任务 = (表, 年月分区)。重跑先 `ALTER TABLE ... DROP PARTITION 'YYYYMM'`
  再插 → 整月原子替换，无半量残片。
- **断点**：`state.json/` 目录下 `<table>_<yyyymm>.done` 标记；已完成任务自动跳过。
  失败任务无标记，重跑即可。
- **流式**：pyarrow `iter_batches(5M)` → `insert_arrow`（按列名匹配，零转换）；
  tick 单月最大 3.5 亿行，不可整月物化。
- **类型**：bars `minute_index` int16→UInt16 cast；tick trades `bs` 源即 UInt8；
  snapshots 65 列源 schema 原样。
- **内存**：8 worker × 每批 5M 行 ≈ 4GB；CH server 上限 12GB；polars 物化 3GB。
- **对账**：CH 侧用 `system.parts.sum(rows)`（按分区精确行数）vs parquet metadata
  `num_rows`（快，不读数据）。

## 数据口径（与源事实库一致，注意与 tushare 差异）

- `vol` 单位 = **股**（tushare 是手=×100）；`amount` = 元
- `pre_close/change/pct_chg` 为 raw 派生（per-code shift，组内首日 NULL）
- `total_mv` = close×total_shares/1e4（万元）；`turnover_rate` = vol/float_shares×100
- `daily_basic` 后 5 列（circ_mv/pe_ttm/pb/dv_ratio/volume_ratio）为占位空列（无数据源）
- `stock_basic.list_date` 为 daily_fact 最早交易日代理；`industry` 恒 NULL
- `stock_basic.market` = 板块名规范值（主板/创业板/科创板/北交所，段规则同
  derive_stk_limit.py）；平台 execution rules loader 显式消费（2026-09-08
  ch_prod 真实段实测补列）
- `index_daily` 空表（可选灌 000852.SH）
- `stk_limit` 仅覆盖有涨跌停的日子：<1996-12-16 无行、上市首日（pre_close NULL）无行、
  注册制新股前 5 交易日无行（缺行 = 平台 has_limit=False = 无限制，合法）
- `adj_detail` 全量行级；`adj_event` 仅除权事件日（div_cash/div_bonus/div_transfer/rights_num ≠ 0）
