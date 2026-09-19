# 1.25M 命中 root-cause 分群（Plan DQ-M1.5 T3，只读）

- 输入：`data/fact/daily_fact/daily_fact.parquet` ｜ 退市目录：`data/fact/daily_fact/delisted_codes.parquet`
- 唯一命中行：**1,251,010**（amount/float_shares/adj_factor 三字段同缺）；命中（行×字段）：**3,753,030**
- 代码分流：全史缺列 315 码（1,246,656 行，其中有成交 1,146,550 行）｜ 部分缺列 13 码（4,354 行，其中有成交 156 行）
- 字段首次可用日（LEGACY_SCHEMA 判据）：adj_factor=1990-12-19，amount=1990-12-19，float_shares=1990-12-19

## 四分类计数

| 类 | 命中数 | 占比 |
|---|---|---|
| EXPECTED_MISSING | 312,912 | 8.338% |
| SOURCE_LIMITATION | 3,440,118 | 91.662% |
| TRUE_ERROR | 0 | 0.000% |
| LEGACY_SCHEMA | 0 | 0.000% |

## 按字段

| 字段 | EXPECTED_MISSING | SOURCE_LIMITATION | TRUE_ERROR | LEGACY_SCHEMA |
|---|---|---|---|---|
| amount | 104,304 | 1,146,706 | 0 | 0 |
| float_shares | 104,304 | 1,146,706 | 0 | 0 |
| adj_factor | 104,304 | 1,146,706 | 0 | 0 |

## 按年份（行级；三字段同步缺失）

| 年份 | EXPECTED_MISSING | SOURCE_LIMITATION | TRUE_ERROR | LEGACY_SCHEMA |
|---|---|---|---|---|
| 1990 | 54 | 27 | 0 | 0 |
| 1991 | 714 | 2,109 | 0 | 0 |
| 1992 | 513 | 4,488 | 0 | 0 |
| 1993 | 981 | 14,220 | 0 | 0 |
| 1994 | 315 | 34,821 | 0 | 0 |
| 1995 | 579 | 38,796 | 0 | 0 |
| 1996 | 249 | 52,389 | 0 | 0 |
| 1997 | 1,047 | 85,605 | 0 | 0 |
| 1998 | 1,479 | 101,616 | 0 | 0 |
| 1999 | 1,869 | 107,124 | 0 | 0 |
| 2000 | 3,741 | 118,119 | 0 | 0 |
| 2001 | 6,054 | 127,698 | 0 | 0 |
| 2002 | 6,111 | 126,414 | 0 | 0 |
| 2003 | 4,071 | 130,560 | 0 | 0 |
| 2004 | 6,471 | 130,026 | 0 | 0 |
| 2005 | 8,853 | 122,670 | 0 | 0 |
| 2006 | 22,050 | 99,762 | 0 | 0 |
| 2007 | 21,177 | 99,063 | 0 | 0 |
| 2008 | 15,222 | 104,928 | 0 | 0 |
| 2009 | 11,763 | 106,437 | 0 | 0 |
| 2010 | 10,950 | 115,341 | 0 | 0 |
| 2011 | 12,033 | 130,665 | 0 | 0 |
| 2012 | 10,422 | 141,228 | 0 | 0 |
| 2013 | 11,241 | 138,651 | 0 | 0 |
| 2014 | 21,897 | 135,405 | 0 | 0 |
| 2015 | 35,997 | 121,326 | 0 | 0 |
| 2016 | 23,271 | 136,797 | 0 | 0 |
| 2017 | 28,194 | 133,104 | 0 | 0 |
| 2018 | 23,076 | 136,857 | 0 | 0 |
| 2019 | 7,962 | 147,933 | 0 | 0 |
| 2020 | 9,672 | 137,727 | 0 | 0 |
| 2021 | 4,662 | 127,797 | 0 | 0 |
| 2022 | 9 | 104,778 | 0 | 0 |
| 2023 | 213 | 74,001 | 0 | 0 |
| 2024 | 0 | 39,168 | 0 | 0 |
| 2025 | 0 | 11,556 | 0 | 0 |
| 2026 | 0 | 912 | 0 | 0 |

## 按市场

| 市场 | EXPECTED_MISSING | SOURCE_LIMITATION | TRUE_ERROR | LEGACY_SCHEMA |
|---|---|---|---|---|
| SH | 134,361 | 1,697,937 | 0 | 0 |
| SZ | 178,551 | 1,742,181 | 0 | 0 |

## 按退市状态

| 退市 | EXPECTED_MISSING | SOURCE_LIMITATION | TRUE_ERROR | LEGACY_SCHEMA |
|---|---|---|---|---|
| True(退市) | 312,912 | 3,440,118 | 0 | 0 |

## 补充异常（非缺失，供 Task 2/健康交叉核对）

| 项 | 行数 | 备注 |
|---|---|---|
| float_shares == 0 | 51 | circ_mv=0；代码 688287.SH |
| adj_factor <= 0 | 8,235 | 其中 <0 为 ADJ_NEGATIVE（Task 2 字段级语义冻结范围） |
| amount > 0 行中 volume 缺失 | 0 | volume 全表无 NULL（本项应为 0） |

## 影响范围（消费方，引用现状）

- 因子 spec：`amount` 36 条（reversal_20d 27 / intraday 5 / momentum_20d 2 / misc 1 / liquidity 1）；`circ_mv` 10 条（crash_bottom_leader 7 / size 2 / reversal_20d 1）；`float_shares`/`adj_factor` 无 spec 直引（经复权视图与 daily_basic 派生）。
- 平台读路径：`adapters/read/adjust.py`（adj_factor 复权视图）、`adapters/catalog.py`、`research/data_bars.py`、`core/engine/forward.py`、`adapters/ic_kernel.py`、`core/execution/valuation.py`（circ_mv 市值）、`tools/universe_stages/**`、`tools/1m_features`（日级注入 amount/volume）、`tools/ch_ingest/ingest_daily.py`（canonical 三表派生）。
- 语义：缺失保持 NULL（不填充）；退市股金额/复权链断流影响退市前区间复权与幸存者偏差修正、流通市值类因子（circ_mv 为 0 或 NULL）。

## 修复候选（本阶段不修数据）

| 类 | 可否 backfill | 源/手段 | 成本 | 优先级 |
|---|---|---|---|---|
| EXPECTED_MISSING | 无需（语义为无成交） | 保持 NULL + 显式 flag（停牌行） | 低 | P3 |
| SOURCE_LIMITATION | 部分可 | 退市码 amount：网易/腾讯历史 CSV（含成交额）核对后回填；adj：扩展现有 delisted_adj_factor sidecar 全史；float_shares：需股本结构源（成本最高） | 中-高 | P1（adj/amount）/P2（float_shares） |
| TRUE_ERROR | 直接可（若有） | 增量源刷新即可（本期为 0 命中） | 低 | P0 |
| LEGACY_SCHEMA | 不适用 | 字段全史可用，无命中 | - | - |

> 本报告只审不改：未写 canonical/CH，未改任何 raw/fact parquet。
