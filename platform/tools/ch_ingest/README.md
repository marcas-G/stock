# ch_ingest — 事实库 → ClickHouse 灌入管线

把用户三大事实库（daily_fact / bars_1m / tick_fact parquet）全量灌入本机
ClickHouse（`127.0.0.1:8123` HTTP——clickhouse-connect 仅支持 HTTP；`19000` 是 tcp client 端口，见 `config.yaml`）。库 `factorlab`。

## 表与源

| CH 表 | 源 | 行数 | 任务粒度 |
|---|---|---|---|
| daily / adj_factor / daily_basic / trade_cal / stock_basic | `data/fact/daily_fact/daily_fact.parquet`（相对 `stock/`） | 18,124,805（R21 重生成实测） | 单进程，TRUNCATE 幂等 |
| stk_limit（派生：板块带宽 × pre_close，规则见脚本 docstring） | `factorlab.daily` | 17,854,764（R21 重灌） | `derive_stk_limit.py`，TRUNCATE+INSERT 全量 |
| adj_detail / adj_event（派生：daily_fact 除权 7 列） | `data/fact/daily_fact/daily_fact.parquet`（相对 `stock/`） | 18,124,805 / 57,173 | `ch_ingest/adj_backfill.py`（R19 从 ashare 项目 12 号脚本归位），DROP+CREATE 全量 |
| bars_1m | `data/fact/bars_1m/year=YYYY/month=MM/` | 1,853,379,840（R21 probe 实测） | 月分区 ×80 |
| tick_trades / tick_orders / tick_snapshots | `data/fact/tick_fact/{trades,orders,snapshots}/year=YYYY/month=MM/` | 98.6 亿 | 月分区 ×13×3 |
| moneyflow（Plan P T7；列映射见 `lib/moneyflow.py`） | `data/raw/fund_flow/**/*.zip` 内 `zj.xls`（月 zip 补历史 + 当月日 zip 增量，日 zip 覆盖月 zip） | 1,119,242（2026-09-18 实测，203 日） | `ingest_moneyflow.py`，TRUNCATE+INSERT 全量；**空源拒绝灌入**（不清表） |
| moneyflow_sector（R30 项 2；板块资金，列名/语义同 moneyflow 18 项） | `data/fact/moneyflow_sector/moneyflow_sector.parquet`（由 `pan_update/parse_fund_flow.py` 从 `hyzj.xls`/`gnzj.xls` 生成） | 77,524（实测，147 日 / 558 板块：行业 18,480 + 概念 59,044；自 2026-02-04） | `ingest_moneyflow.py`，TRUNCATE+INSERT 全量；**空 fact 拒绝灌入** |
| concept_members（R30 项 2；概念成分每日快照，PIT 成员） | `data/fact/concept_members/concept_members.parquet`（由 `parse_fund_flow.py` 从 `gn_detail.csv` 生成） | 7,281,555（实测，87 日 / 964 板块；自 2026-05-19，每日 81,219..85,972 行） | `ingest_moneyflow.py`，TRUNCATE+INSERT 全量；**空 fact 拒绝灌入** |
| fundamentals（Plan P T8；当期快照，非 PIT 历史） | `data/fact/fundamentals/fundamentals_snapshot.parquet`（由 `pan_update/parse_fundamentals_xlsx.py` 从周更小 xlsx 生成） | 5,556（T10 实测） | `ingest_fundamentals.py`，TRUNCATE+INSERT 全量；**空快照拒绝灌入** |

R21 行数为重灌后实测；每次重生成 daily_fact 后以 `reconcile.py` 输出为准（README 数字只在
明显漂移时更新——对账门本身按源 parquet metadata 动态取数，不读本表）。

R21 存量库迁移（CREATE IF NOT EXISTS 不改已有列，需显式 ALTER；2026-09-15 已执行）：
```sql
ALTER TABLE factorlab.daily      MODIFY COLUMN amount     Nullable(Float64);
ALTER TABLE factorlab.adj_factor MODIFY COLUMN adj_factor Nullable(Float64);
ALTER TABLE factorlab.stock_basic ADD COLUMN IF NOT EXISTS delist_date Nullable(Date) AFTER industry;
```

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
python platform/tools/ch_ingest/adj_backfill.py   # adj_detail + adj_event（平台 venv）

# 5b) 退市股 adj 补灌（R08-DATA-I2；按需，sidecar 缺省缺失=旧行为）
python platform/tools/ch_ingest/delisted_adj_backfill.py   # 腾讯 hfq → sidecar（限流可重跑续传）
python platform/tools/ch_ingest/ingest_daily.py --only adj_factor   # coalesce 后重灌

# 6) 对账（退出码 0=全一致）
python reconcile.py                 # 全表（含 moneyflow / moneyflow_sector / concept_members / fundamentals）
python reconcile.py moneyflow       # 个股资金流：行数/日期/天数/关键列空值 vs raw zip 源
python reconcile.py moneyflow_sector # 板块资金：行数/日期/天数/关键列/BK 码/类型分型 vs fact
python reconcile.py concept_members  # 概念成分：行数/日期/键 uniq/码格式/每日行数 min+max vs fact
python reconcile.py fundamentals    # 财报快照：行数/updated_date/关键列空值 vs fact parquet
```

## 设计

- **幂等**：任务 = (表, 年月分区)。重跑先 `ALTER TABLE ... DROP PARTITION 'YYYYMM'`
  再插 → 整月原子替换，无半量残片。
- **断点**：单文件 `state.json`（键 `<table>_<yyyymm>`）。`mark_done` 只由主进程在
  `state.json.lock` 上阻塞 flock 后「新鲜读盘 + 合并 + 原子写」，worker 不碰断点
  （R21 TOOLS-I2：旧 8 worker 各自 read-modify-write 丢标记；`ch_write.run_pool`
  也只由主进程 on_result 记账）。旧 `state.json/` 目录 + `.done` 文件形态自动迁移
  留档（`state.json.legacy-*`），读取兼容 30 天。失败任务无键，重跑即可。
- **流式**：pyarrow `iter_batches(5M)` → `insert_arrow`（按列名匹配，零转换）；
  tick 单月最大 3.5 亿行，不可整月物化。
- **类型**：bars `minute_index` int16→UInt16 cast；tick trades `bs` 源即 UInt8；
  snapshots 65 列源 schema 原样。
- **内存**：8 worker × 每批 5M 行 ≈ 4GB；CH server 上限 12GB；polars 物化 3GB。
- **对账**：CH 侧用 `system.parts.sum(rows)`（按分区精确行数）vs parquet metadata
  `num_rows`（快，不读数据）。

## 数据口径（与源事实库一致，注意与 tushare 差异）

- `vol` 单位 = **股**（tushare 是手=×100）；`amount` = 元；退市股无 amount/复权源 →
  `amount`/`adj_factor` 为 **NULL**（R21 I1；旧版非 Nullable 灌成 0）
- `pre_close/change/pct_chg`（R21 DATA-C2）：无事件日 = 昨收；**除权除息日 = 除权参考价**
  `round((prev - div_cash/10 + rights_price×rights_num/10) / (1 + div_bonus/10 + div_transfer/10), 2)`
  （half-up；单位：元或股/10股），与 knowledge/contracts/catalog.md 承诺一致；组内首日 NULL
- `total_mv` = close×total_shares（源 total_shares 单位=万股 → 万元；R21 C1 修 1e4）；
  `circ_mv` = close×float_shares（同万元口径；R07-DATA-I4 起派生，非占位）；
  `turnover_rate` = vol/(float_shares×1e4)×100（%）
- `adj_factor` <=0（vendor 后复权价异常）归 NULL——qfq 基准 `argMax` 跳过 NULL（R21 I1）
- **退市股 adj 补灌（R08-DATA-I2，2026-09-17）**：退市股源（`daily/退市股/*.xlsx`）为
  8 列精简格式，无复权列 → CH `adj_factor` 全 NULL、评估全历史丢行。补口
  `delisted_adj_backfill.py`：腾讯复权 K 线（双域名，hfq）→ `adj=后复权价/收盘`，
  raw 收盘逐日对拍 `daily_fact`（不一致拒绝）；部分有 vendor 值的按 vendor 段末端
  锚定比例常数（漂移 >0.5% 拒绝）→ sidecar `data/fact/daily_fact/delisted_adj_factor.parquet`
  （+`.meta.json`；写盘自动合并=断点续跑）→ `ingest_daily --only adj_factor` 灌入时
  `coalesce`（只填 NULL，不覆盖 vendor）；`reconcile` 校验 sidecar 每条键在 CH 非空。
  范围默认 2022-01-01 起（评估窗口 2023+ 与 warmup）；已知精度边界：hfq 小数位 +
  低价股 → 派生 TR 日差 p99≈0.16%（恢复样本优于全空，证据 R30/eval-v2-task14-12-11/）
- `daily_basic` 的 `pe_ttm/pb/dv_ratio/volume_ratio` 4 列为占位空列（无数据源；
  平台读路径 `_PLATFORM_COLS` 仍映射它们，DDL 保留；**不要在文档/目录里宣传可用**）
- `stock_basic.list_date` 为 daily_fact 最早交易日代理
- `stock_basic.industry` **恒 NULL**（R02-I1 生产侧如实标注）：daily_fact 无行业列，
  离线基本面源（TDX 财务 parquet，当前本身缺源）也不含行业分类——**无源可补，不伪造**。
  影响：读路径 `fillna(industry_mean)` 与 `gp_rank/gp_mean(industry,…)` 的
  `.over([...,"industry"])` 会塌成全市场单组（静默产出全市场值）；`neutralize(by=industry)`
  loud fail；`WHERE industry='半导体'` 一类筛选恒空。
  结论：**不要 advertise**——`knowledge/contracts/catalog.md` 的 `industry` 行（申万最新归属）
  与生产数据面不符，其生成源在 platform 侧（本工具无权改，coordinator 侧登记）；
  在补源落地前，研究侧一律按「industry 不可用」对待。
- `stock_basic.delist_date`（R21 DATA-C1 生产侧）：退市目录 sidecar 权威
  （`data/fact/daily_fact/delisted_codes.parquet`，in-file code 优先、文件名兜底、空文件也算）
  + 断流兜底（不在 sidecar 且 >250 交易日无数据）→ `最后交易日 + 1 天`；
  平台语义 `is_listed = t < delist_date`
- `stock_basic.market` = 板块名规范值（主板/创业板/科创板/北交所，段规则同
  derive_stk_limit.py）；平台 execution rules loader 显式消费（2026-09-08
  ch_prod 真实段实测补列）
- `index_daily` 空表（**R29 裁决：当前无可用补数路径**——候选源 teajoin `index_daily`
  接口（token 2026-08-22 过期）且本管线无 index_daily 灌入工具（platform `data refresh`
  的指数增量只写 duckdb 平台库，不接 CH）；旧注释所指 `ingest_index_sina.py` 全仓不存在
  （R07-D3 死引用，已删）；平台 `idx_ret` LEFT JOIN 依赖表存在，移除会破坏读路径——
  **不要 advertise**。触发条件：token 恢复或新增 index→CH 灌入工具后补 000852.SH 全历史）
- `stk_limit` 仅覆盖有涨跌停的日子：<1996-12-16 无行、上市首日（pre_close NULL）无行、
  注册制新股前 5 交易日无行（缺行 = 平台 has_limit=False = 无限制，合法；
  R21 I5 已与平台 fillability 统一——缺行按 raw open FILLABLE，不再 fail-closed）
- `adj_detail` 全量行级；`adj_event` 仅除权事件日（div_cash/div_bonus/div_transfer/rights_num ≠ 0）
- `reconcile.py`（R21 I7）对账 daily 5 表行数 + daily 恒等式/日期范围 + 派生三表
  （stk_limit 期望行数独立复算、band/日期不变量；adj_detail/adj_event 行数与 uniq）
- `reconcile.py`（终评 I3）新增两表：**moneyflow** 行数/日期范围/天数/`main_net_inflow`
  空值数/`ts_code` 键异常 vs `raw/fund_flow` zip 全量帧（与灌入同一 `load_frames`
  语义，含日 zip 覆盖月 zip 与去重）；**fundamentals** 行数/`updated_date` 范围/天数/
  `total_shares` 空值数/键异常 vs fact parquet（与灌入同一 `load_fact` 语义）。
- `reconcile.py`（R30 项 2）新增两表：**moneyflow_sector** 行数/日期/天数/关键列空值/
  board_type 合法值/BK 码格式/名称空串数/键 uniq/行业+概念分型行数 vs fact；**concept_members**
  行数/日期/天数/键 uniq/BK 码与 ts_code 格式/名称空串数/每日行数 min+max vs fact
  （均复用灌入同一 loader，与灌入同语义）。
- **项 2 解析侧跳过语义**：hyzj/gnzj 源中「增仓占比排名」变体（20260903/0904/0908/0909）
  与个股串档（20260422）由 `lib/moneyflow.parse_zj_sector` 抛 `UnsupportedSectorFormat`，
  `load_sector_frames` 记 warning 跳过——不入库、不静默（reconcile 用同一 loader，源口径一致）。
  任一不一致 → 非零退出（`make reconcile` / `pan_update verify` 原样传播）
