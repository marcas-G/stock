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

-- ========== moneyflow（T7 Plan P：日线资金；读路径 load_daily 按需 LEFT JOIN） ==========
-- 来源：夸克网盘 `日线资金--每日沪深京个股日线数据和资金流数据` → data/raw/fund_flow/**.zip
-- 内的 zj.xls（GBK TSV，见 platform/tools/lib/moneyflow.py）。灌入 ingest_moneyflow.py
-- （CREATE IF NOT EXISTS 幂等 + TRUNCATE + INSERT 全量重算；R3 裁决：本 DDL 与脚本内建表同步维护）。
-- 金额单位=元（源 亿/万 已归一）；占比列=百分数；缺失为 NULL（源 `-`/`—`）。
-- 读面：load_daily(cols=[...]) 请求 main_net_inflow 等 18 列时 LEFT JOIN（缺行 → null）。
CREATE TABLE IF NOT EXISTS factorlab.moneyflow (
    ts_code       String,               -- '000001.SZ'
    trade_date    Date,
    main_net_inflow Nullable(Float64),  -- 主力净流入（元）
    auction       Nullable(Float64),    -- 集合竞价（元）
    super_in      Nullable(Float64),    -- 超大单流入（元）
    super_out     Nullable(Float64),    -- 超大单流出（元）
    super_net     Nullable(Float64),    -- 超大单净额（元）
    super_net_pct Nullable(Float64),    -- 超大单净占比（%）
    big_in        Nullable(Float64),
    big_out       Nullable(Float64),
    big_net       Nullable(Float64),
    big_net_pct   Nullable(Float64),
    mid_in        Nullable(Float64),
    mid_out       Nullable(Float64),
    mid_net       Nullable(Float64),
    mid_net_pct   Nullable(Float64),
    small_in      Nullable(Float64),
    small_out     Nullable(Float64),
    small_net     Nullable(Float64),
    small_net_pct Nullable(Float64)
) ENGINE = MergeTree
  ORDER BY (ts_code, trade_date);

-- ========== 资金流扩充（R30 项 2）：板块资金 + 概念成分 ==========
-- 来源：同一 `日线资金--每日沪深京个股日线数据和资金流数据` 分享树内 `hyzj.xls`（行业）/
-- `gnzj.xls`（概念）与 `gn_detail.csv`（概念成分）。解析 `lib/moneyflow.py`
-- （parse_zj_sector/parse_gn_detail，BK 码单独校验），pan_update/parse_fund_flow.py
-- 先写 fact（data/fact/moneyflow_sector/、data/fact/concept_members/），
-- 本目录 ingest_moneyflow.py 读 fact 全量替换灌入（TRUNCATE+INSERT 幂等）。
-- 语义：18 项资金指标与 moneyflow 同套同名同单位（元/%；源 亿/万 已归一）；
-- 覆盖：hyzj/gnzj 自 2026-02-04 起（源 20260903/0904/0908/0909 为增仓排名变体、
-- 20260422 为个股串档——解析侧显式跳过，不入库）；gn_detail 自 2026-05-19 起每日快照。
CREATE TABLE IF NOT EXISTS factorlab.moneyflow_sector (
    trade_date    Date,
    board_type    String,               -- 'industry' | 'concept'（hyzj | gnzj）
    board_code    String,               -- 'BK0465'（BK+4 位；源代码壳已去）
    board_name    String,               -- 板块名（逐日源值；源改名如实反映）
    main_net_inflow Nullable(Float64),  -- 18 项资金指标：列名/语义/单位与 moneyflow 一致
    auction       Nullable(Float64),
    super_in      Nullable(Float64),
    super_out     Nullable(Float64),
    super_net     Nullable(Float64),
    super_net_pct Nullable(Float64),
    big_in        Nullable(Float64),
    big_out       Nullable(Float64),
    big_net       Nullable(Float64),
    big_net_pct   Nullable(Float64),
    mid_in        Nullable(Float64),
    mid_out       Nullable(Float64),
    mid_net       Nullable(Float64),
    mid_net_pct   Nullable(Float64),
    small_in      Nullable(Float64),
    small_out     Nullable(Float64),
    small_net     Nullable(Float64),
    small_net_pct Nullable(Float64)
) ENGINE = MergeTree
  ORDER BY (board_type, board_code, trade_date);

-- concept_members：**每日快照**（PIT 成员；同一 (trade_date, board_code) 当日成分）。
-- 非长表 PIT 序列：判断"某日某概念成分"须按 trade_date 取当日快照。名称可为空串
-- （源实测 BK1753 20260728 为空；改名逐日如实）。ts_code 带交易所后缀（B 股
-- 200/201→SZ、900→SH 已覆盖）。
CREATE TABLE IF NOT EXISTS factorlab.concept_members (
    trade_date    Date,
    board_code    String,               -- 'BK0490'
    board_name    String,               -- 概念名（可空串，源如此）
    ts_code       String                -- '000009.SZ'
) ENGINE = MergeTree
  ORDER BY (trade_date, board_code, ts_code);

-- ========== fundamentals（T8 Plan P：财报 xlsx 当期快照） ==========
-- 来源：夸克网盘 `财报报表---有史以来--每周更新` 的 `*更新简化个股基本面数据.xlsx`
-- → data/raw/financial/ → parse_fundamentals_xlsx.py → fact
-- （data/fact/fundamentals/fundamentals_snapshot.parquet）→ 本表（ingest_fundamentals.py，
-- CREATE IF NOT EXISTS 幂等 + TRUNCATE + INSERT 全量替换；R3 裁决：本 DDL 与脚本内建表同步维护）。
-- 单位沿用源：股本列=万股、金额列=元；updated_date=源更新日（行键，非单一快照日）。
-- **限制声明**：本表是**当期快照**（非历史 PIT 序列）——同一 ts_code 可有多行不同 updated_date；
-- 不能据此回溯"某历史日已知财务值"。PIT 历史待多期快照累积或人工 *_financial.parquet。
CREATE TABLE IF NOT EXISTS factorlab.fundamentals (
    ts_code        String,               -- '000001.SZ'
    updated_date   Date,                 -- 源更新日期（非空；行级唯一键之一）
    report_period  Nullable(String),     -- 源报告期原样（'6'/'9'/…），不臆造日期
    list_date      Nullable(Date),       -- 上市日期
    market         Nullable(String),     -- 源市场：sz/sh/bj
    industry       Nullable(String),     -- 行业
    sw_industry    Nullable(String),     -- 申万行业
    sw_sub         Nullable(String),     -- 申万细分
    total_shares   Nullable(Float64),    -- 总股本（万股）
    float_a_shares Nullable(Float64),    -- 流通A股（万股）
    eps            Nullable(Float64),    -- 每股收益（元）
    total_assets   Nullable(Float64),    -- 总资产（元）
    current_assets Nullable(Float64),    -- 流动资产（元）
    fixed_assets   Nullable(Float64),    -- 固定资产（元）
    intangible_assets Nullable(Float64), -- 无形资产（元）
    shareholders   Nullable(Float64),    -- 股东人数
    current_liab   Nullable(Float64),    -- 流动负债（元）
    long_liab      Nullable(Float64),    -- 长期负债（元）
    capital_reserve Nullable(Float64),   -- 资本公积金（元）
    net_assets     Nullable(Float64),    -- 净资产（元）
    revenue        Nullable(Float64),    -- 营业收入（元）
    operating_cost Nullable(Float64),    -- 营业成本（元）
    op_profit      Nullable(Float64),    -- 营业利润（元）
    invest_income  Nullable(Float64),    -- 投资收益（元）
    op_cashflow    Nullable(Float64),    -- 经营现金流（元）
    total_cashflow Nullable(Float64),    -- 总现金流（元）
    inventory      Nullable(Float64),    -- 存货（元）
    total_profit   Nullable(Float64),    -- 利润总额（元）
    net_profit     Nullable(Float64),    -- 净利润（元）
    undist_profit  Nullable(Float64)     -- 未分配利润（元）
) ENGINE = MergeTree
  ORDER BY (updated_date, ts_code);

CREATE TABLE IF NOT EXISTS factorlab.index_daily (
    ts_code String,
    trade_date Date,
    pct_chg Float64
) ENGINE = MergeTree
  ORDER BY (ts_code, trade_date);
-- 空表：load_daily 的 idx_ret LEFT JOIN 不炸、恒 NULL。**R29 裁决（2026-09-16）：当前
--   无可用补数路径**——候选源 teajoin index_daily 接口（token 2026-08-22 过期）且本管线
--   无 index_daily 灌入脚本（data refresh 指数增量只写 duckdb 平台库）；触发条件=token 恢复
--   或新增 index→CH 灌入工具后补 000852.SH 全历史。旧注释所指 ingest_index_sina.py
--   全仓不存在（R07-D3 死引用，已删，勿再 advertise）

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
