-- ClickHouse factorlab 库 DDL（幂等：可重复执行）
-- 端口 19000（机器 9000 被其它服务占用）；server 配置见 /data/students/gaolei/clickhouse/

CREATE DATABASE IF NOT EXISTS factorlab;

-- ========== daily 层（平台读路径直接消费；18M 行不分区，主键索引足够） ==========

CREATE TABLE IF NOT EXISTS factorlab.daily (
    ts_code    String,                 -- '000001.SZ'（tushare 口径）
    trade_date Date,
    open       Float64,
    high       Float64,
    low        Float64,
    close      Float64,
    pre_close  Nullable(Float64),      -- 派生：close per-code shift（raw 口径）；组内首行 NULL
    change     Nullable(Float64),      -- 派生：close - pre_close
    pct_chg    Nullable(Float64),      -- 派生：(close/pre_close - 1) * 100
    vol        Float64,                -- 单位=股（源 parquet 原生；与 tushare "手"差 100 倍，见 README）
    amount     Nullable(Float64)       -- R21 I1：退市股源 amount=NaN → NULL（旧版非 Nullable 灌成 0）
) ENGINE = MergeTree
  ORDER BY (ts_code, trade_date);

CREATE TABLE IF NOT EXISTS factorlab.adj_factor (
    ts_code String,
    trade_date Date,
    adj_factor Nullable(Float64)       -- R21 I1：NaN/<=0 → NULL（qfq 基准 argMax 跳过）
) ENGINE = MergeTree
  ORDER BY (ts_code, trade_date);

CREATE TABLE IF NOT EXISTS factorlab.daily_basic (
    ts_code       String,
    trade_date    Date,
    total_mv      Nullable(Float64),   -- 派生：close×total_shares（源万股 → 万元 tushare 口径）
    turnover_rate Nullable(Float64),   -- 派生：vol/float_shares×100（%）
    circ_mv       Nullable(Float64),   -- 派生：close×float_shares（源万股 → 万元）；R07-DATA-I4（旧版空列占位）
    pe_ttm        Nullable(Float64),   -- 空列占位：无数据源（LEFT JOIN 不断裂，值恒 NULL）
    pb            Nullable(Float64),   -- 空列占位
    dv_ratio      Nullable(Float64),   -- 空列占位
    volume_ratio  Nullable(Float64)    -- 空列占位
) ENGINE = MergeTree
  ORDER BY (ts_code, trade_date);

CREATE TABLE IF NOT EXISTS factorlab.trade_cal (
    cal_date Date,
    is_open  UInt8 DEFAULT 1           -- 派生自 daily_fact distinct trade_date（全为交易日）
) ENGINE = MergeTree
  ORDER BY cal_date;

CREATE TABLE IF NOT EXISTS factorlab.stock_basic (
    symbol    String,                  -- 6 位纯数字（platform daily.code 桥梁）
    ts_code   String,                  -- symbol + 交易所后缀
    list_date Date,                    -- 代理 = 该代码在 daily_fact 的最早 trade_date（非真实上市日）
    market    String,                  -- 板块名规范值：主板/创业板/科创板/北交所（平台 execution rules 显式消费，非 code 推断替代）
    industry  Nullable(String),        -- 恒 NULL：无行业数据源（fillna(industry_mean)/gp_rank/gp_mean(industry,…) 塌成全市场单组；neutralize(by=industry) loud fail；不要 advertise——见 README）
    delist_date Nullable(Date)         -- R21 DATA-C1：退市日（sidecar last_trade+1 / 断流>250 兜底）；平台 is_listed = t < delist_date
) ENGINE = MergeTree
  ORDER BY (symbol, ts_code);
-- R21 存量库迁移（CREATE IF NOT EXISTS 不改已有列类型/不补列；已执行，见
-- governance/evidence/verification/R21/TOOLS-A/after/ddl_alter.txt）：
--   ALTER TABLE factorlab.daily      MODIFY COLUMN amount     Nullable(Float64);
--   ALTER TABLE factorlab.adj_factor MODIFY COLUMN adj_factor Nullable(Float64);
--   ALTER TABLE factorlab.stock_basic ADD COLUMN IF NOT EXISTS delist_date Nullable(Date) AFTER industry;

CREATE TABLE IF NOT EXISTS factorlab.index_daily (
    ts_code String,
    trade_date Date,
    pct_chg Float64
) ENGINE = MergeTree
  ORDER BY (ts_code, trade_date);
-- 空表：load_daily 的 idx_ret LEFT JOIN 不炸、恒 NULL。可选：sina 拉 000852.SH 填充（ingest_index_sina.py）

-- ========== 派生表（daily 层之后；各由专用脚本幂等自管，此处仅为 DDL 门面） ==========
-- stk_limit: 由 derive_stk_limit.py 管理（TRUNCATE + INSERT…SELECT 全量派生；
--   规则/制度边界/已知近似见该脚本 docstring）。平台执行层读路径消费：
--   (trade_date, ts_code) IN 过滤；缺行 = has_limit=False（无限制，合法语义）。
CREATE TABLE IF NOT EXISTS factorlab.stk_limit (
    ts_code    String,
    trade_date Date,
    up_limit   Float64,
    down_limit Float64
) ENGINE = MergeTree
  ORDER BY (trade_date, ts_code);

-- adj_detail / adj_event: 由 ch_ingest/adj_backfill.py 管理（R19 收编自 ashare_alpha3/scripts/12_ch_adj_backfill.py）
--   （DROP + CREATE + 流式全量，源 = daily_fact.parquet 除权 7 列）。
--   adj_event 是平台 CA Gate 事件源（ts_code String + trade_date Date 契约）。
CREATE TABLE IF NOT EXISTS factorlab.adj_detail (
    ts_code String,
    trade_date Date,
    fq_factor Nullable(Float64),
    fq_deduct Nullable(Float64),
    div_cash Nullable(Float64),
    div_bonus Nullable(Float64),
    div_transfer Nullable(Float64),
    rights_num Nullable(Float64),
    rights_price Nullable(Float64)
) ENGINE = MergeTree
  ORDER BY (ts_code, trade_date);

CREATE TABLE IF NOT EXISTS factorlab.adj_event (
    ts_code String,
    trade_date Date
) ENGINE = MergeTree
  ORDER BY (ts_code, trade_date);

-- ========== bars_1m（19GB / 18.5 亿行：按月分区） ==========

CREATE TABLE IF NOT EXISTS factorlab.bars_1m (
    datetime     DateTime64(3),        -- 源 parquet timestamp[ms] 原样（UTC 存储，不转时区；见 _dataset_metadata.json）
    trade_date   Date,
    code         String,               -- '000988.SZ' 带后缀（源 code_format，见 _dataset_metadata.json）
    minute_index UInt16,               -- 0..239
    session_type UInt8,                -- {0,1,2}
    open Float32, high Float32, low Float32, close Float32,  -- 源即 float32，原样保留
    amount Float64, volume Float64
) ENGINE = MergeTree
  PARTITION BY toYYYYMM(trade_date)
  ORDER BY (code, datetime);

-- ========== tick 3 表（98.6 亿行：按月分区） ==========

CREATE TABLE IF NOT EXISTS factorlab.tick_trades (
    trade_date   Date,
    code         String,
    time_ms      UInt32,               -- 日内毫秒（09:25:00=33900000）
    trade_no     UInt64,
    bs           UInt8,                -- {0=sell,1=buy}（源 UInt8，保持）
    price_x10000 Int32,                -- 价格×10000（源 Int32，保持整型精确；÷10000 得元）
    volume       UInt32,
    ask_seq      UInt64,
    bid_seq      UInt64
) ENGINE = MergeTree
  PARTITION BY toYYYYMM(trade_date)
  ORDER BY (trade_date, code, time_ms);

CREATE TABLE IF NOT EXISTS factorlab.tick_orders (
    trade_date    Date,
    code          String,
    time_ms       UInt32,
    order_no      UInt64,
    exch_order_no UInt64,
    order_type    LowCardinality(String),
    bs            LowCardinality(String),
    price_x10000  Int32,
    volume        UInt32
) ENGINE = MergeTree
  PARTITION BY toYYYYMM(trade_date)
  ORDER BY (trade_date, code, time_ms);

CREATE TABLE IF NOT EXISTS factorlab.tick_snapshots (
    trade_date Date,
    code String,
    time_ms UInt32,
    price Float64, volume Float64, amount Float64, n_trades Float64, iopv Float64,
    trade_flag LowCardinality(String), bs LowCardinality(String),
    cum_volume Float64, cum_amount Float64, high Float64, low Float64, open Float64, prev_close Float64,
    ask_p1 Float64, ask_p2 Float64, ask_p3 Float64, ask_p4 Float64, ask_p5 Float64,
    ask_p6 Float64, ask_p7 Float64, ask_p8 Float64, ask_p9 Float64, ask_p10 Float64,
    ask_v1 Float64, ask_v2 Float64, ask_v3 Float64, ask_v4 Float64, ask_v5 Float64,
    ask_v6 Float64, ask_v7 Float64, ask_v8 Float64, ask_v9 Float64, ask_v10 Float64,
    bid_p1 Float64, bid_p2 Float64, bid_p3 Float64, bid_p4 Float64, bid_p5 Float64,
    bid_p6 Float64, bid_p7 Float64, bid_p8 Float64, bid_p9 Float64, bid_p10 Float64,
    bid_v1 Float64, bid_v2 Float64, bid_v3 Float64, bid_v4 Float64, bid_v5 Float64,
    bid_v6 Float64, bid_v7 Float64, bid_v8 Float64, bid_v9 Float64, bid_v10 Float64,
    wavg_ask Float64, wavg_bid Float64, ask_total Float64, bid_total Float64,
    unweighted_index Float64, n_issues Float64, n_up Float64, n_down Float64, n_flat Float64
) ENGINE = MergeTree
  PARTITION BY toYYYYMM(trade_date)
  ORDER BY (trade_date, code, time_ms);
