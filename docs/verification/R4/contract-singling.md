# R4c 契约单点证据（2026-09-15）

## 收敛项与对照
| 项 | 收敛前 | 收敛后 | 验证（改前 vs 改后） |
|---|---|---|---|
| parse_ms | converters 自写 numpy 版（与 factio 同规则两份） | factio.timeparse.parse_ms_numpy（**第三个入口**，标量/polars/numpy parity 由测试锁） | **真数据逐字节一致**：逐笔成交 168,273 行 sha 0985bce1…；逐笔委托 188,980 行 sha 7f080e78… |
| 板块分类 | ingest_daily 自写前缀→中文标签；derive_stk_limit 自写前缀 | boards.board_expr/zh_market_expr + STAR/CHINEXT_PREFIXES 常量 | 标签 **5,866 个真实代码逐值一致**；涨跌停 SQL **逐字节一致** |
| 列契约 | 3 套拷贝（factio.schema / ch_ingest.PROJECTION / run_lob_batch._TICK_COLS） | ch_ingest.PROJECTION 从 schema 派生（DDL↔契约逐列同源门）；run_lob_batch 投影见 R4a | ch_ingest 6 smoke 测试 |
| 路径字面量 | converters×2/ingest_daily/reconcile/run_1m_feature 硬编码 | core.factio.paths + partitions | G-CONTRACT 报告模式计数下降；测试断言无硬编码 |

## 未竟（登记 pending）
- **表名常量**（core/factio/tables.py）：平台 read/* 的 duckdb|ch 编译对里 460 处表名字面量，
  收敛需逐条改 SQL 字符串 + 位级门配套；风险高于收益，登记为下一轮专项。
- run_lob_batch 的  投影已改从 lib.tickdata.PROJECTIONS（R4a）；其派生子集守卫在 lib 内。

## 解释器映射修正
ch_ingest **属 T1**（模块级 import clickhouse_connect，emb 未装）——此前文档写 T2 有误；
已在根 CLAUDE.md / README 修正，测试用 importorskip 在 emb 下 skip、平台 venv 真跑（6 passed）。
