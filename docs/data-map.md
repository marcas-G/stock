# 数据地图（data-map）

每个数据单元的唯一位置、唯一生产者（Primary Owner）、血缘（上游→本单元→下游消费者）、
更新方式。**没有孤儿资产**：任何 data/ 下目录都能在本表找到行；表外的数据单元不存在。

维护规则：新增/搬移数据必须同步改本表（谁改谁负责）。**裁决权分层**（2026-09-15 R6 明确）：
本表是**数据资产实例**（哪个目录是什么、谁生产谁消费、到期日）的唯一权威；
目录结构与分类规则以 `directory-conventions.md` 为准；两者与磁盘不一致时**以磁盘为准并修订文档**。

## A. 数据资产（data/）

| # | 单元 | 位置 | 规模 | 生产者（Owner） | 上游血缘 | 下游消费者 | 更新方式 |
|---|---|---|---|---|---|---|---|
| A1 | 原始逐笔/行情 zip | `data/raw/quark_downloaded/` | 111G（256 日 × ~293 码 × 74,630 zip） | quark 网盘分享下载（skill: quark-share-download；脚本 research/tools/quark_download/） | 夸克网盘外部 | A4 转换器、A5 抽取器、verify_cancels_sample | 增量下载（新交易日到货后 append 日期目录） |
| A2 | 分钟原始 zip | `data/raw/minutes/` | 20G（1609 zip，2020-01..2026-08） | 上游分钟数据源（zip 形态） | 外部数据源 | A3 转换器 | 增量（新月份到货补 zip） |
| A3 | 分钟事实库 | `data/fact/bars_1m/` | 19G（80 个月 Hive parquet + `_state/` + `_dataset_metadata.json`） | `research/tools/converters/convert_minutes_to_parquet.py` | A2 | CH bars_1m（B1）、platform 1m 漏斗、ashare 验证与特征 | 逐月转换 + `_state` 断点续跑；**重跑前先读 `_dataset_metadata.json`** |
| A4 | 逐笔事实库 | `data/fact/tick_fact/` | 82G（orders/trades/snapshots/cancels + `_manifest/`） | `research/tools/converters/convert_tick_to_parquet.py`（cancels 由 `research/tools/lob_fact/pipeline/extract_sz_cancels.py`） | A1 | CH tick_* 3 表（B1）、lob_fact 重建（A6）、ashare 交叉验证 | 逐日转换 + manifest 记账；cancels 单表可独立补抽 |
| A5 | 日线事实 | `data/fact/daily_fact/daily_fact.parquet` | 435M（18,162,795 行） | `projects/ashare_alpha3/scripts/01_import_daily.py`（通达信日K导出 → parquet） | A8 | CH daily 层 5 表（B1）、ashare 各层、1m_features 日级注入 | 全量重算（zip → parquet），行数对账见 ch_ingest/reconcile |
| A6 | 订单簿重建（L3） | `data/fact/lob_fact/` | 113G（lob_events 99G / lob_sweep_meta 12G / lob_checkpoints 3G + `_batch/` + panel_1s + panel_1m + `panel_runs/`） | `research/tools/lob_fact/pipeline/run_lob_batch.py`（W2 引擎 + W4/W5 批算） | A1 + A4（δ-lag 锚定） | factor_panel.py 面板、audit_w5、未来 tick 因子 | **单写者 flock 纪律**（`_batch/.lock`）：批算与 compact 互斥；状态 `_batch/state.json`（13 月 250/250 完成） |
| A7 | 校准/验证制品 | `data/calib/lob_fact_calib/`（w1/w3 校准 JSON）、`data/calib/validation/`（date_shift_exceptions.csv + step0a/step0b） | 3.7M | `research/tools/lob_fact/diag/{calibrate_w1,measure_w3}.py`；验证战役脚本（已归档，可再生成） | A1/A4 | lob_fact 引擎对拍（CALIB_OUT）、converter 生产路径（DATE_SHIFT_EXC_PATH） | 只增不改（冻结制品）；**validation/date_shift_exceptions.csv 被生产路径读取，禁止删除** |
| A8 | 日线原始 | `data/raw/daily/`（daily_pre.parquet + 2 个通达信日K zip + 退市股/） | 4.2G | 通达信日K导出（上游） | 外部数据源 | A5（01_import_daily.py 直接 glob 本目录） | 增量导出（新 zip 到货） |
| A9 | 股票池（golden） | `data/ref/universes/v4_top300.parquet` | 6.6M | **上游 golden 参考**（jqdata 口径，生成链路未在工作区留存——见 pending-items #9） | A5（市值/行情口径） | ashare scripts 10/11/20/30（读作 golden_v4_top300）；layer1 产出的是 `v4_top300_local.parquet`（outputs，非本文件） | 只读参考；更新需外部源头（登记待考） |
| A10 | 指数基准 | `data/ref/000905.SH.parquet`（中证 500） | 24K | `ashare_alpha3/scripts/02_import_index.py` | 外部 | ashare benchmark_daily_pre | 重跑脚本 |
| A11 | 未解包归档 | `data/raw/20260817.7z` | 5.3G | quark 网盘（单日全码包） | 外部 | ashare `ticks_root`（**需先解包**，见 pending-items） | 解包后排期（**先 `df` 复核余量**——2026-09-15 实测 338G 可用；见 pending-items #3） |
| A12 | 预留面板区 | `data/panel/` | 空 | —（公约预留给未来跨源大面板） | — | — | 新面板落位时更新本表 |

## B. 外部数据服务（不占工作区磁盘）

| # | 单元 | 位置/连接 | 规模 | 生产者 | 消费者 | 说明 |
|---|---|---|---|---|---|---|
| B1 | ClickHouse `factorlab` | `127.0.0.1:8123`（HTTP；19000 是 tcp client 端口），db=factorlab | tick_orders 5,810,986,406 行等 8 表 | `research/tools/ch_ingest/`（ingest_daily/bars/tick + derive_stk_limit） | platform ch 后端、研究只读查询 | 对账：`make reconcile`（全一致才 exit 0；**解释器用平台 venv** `platform/.venv/bin/python`——emb 缺 clickhouse_connect）；进度：`ch_ingest/state.json`（**单 JSON**，R4b 由目录形态迁移而来；运行时，不入 git） |
| B2 | 平台库 `factorlab.duckdb` | `platform/data/`（相对平台树根） | **当前不存在** | `factorlab data rebuild`（数据源=teajoin API） | platform duckdb 后端 | 重建需先 redeem teajoin token（2026-08-22 已过期）；或改用 `FACTORLAB_DATA_BACKEND=ch`。见 pending-items |
| B3 | teajoin Tushare 代理 | `https://teajoin.com`（FACTORLAB_TEAJOIN_TOKEN） | — | 外部 API | platform data rebuild/fetcher | token 过期，重建前先 redeem |

## C. 代码与项目（单仓三树 + projects/ 本地目录）

| # | 项目 | 分支/角色 | 权威内容 |
|---|---|---|---|
| C1 | `platform/`（仓库内） | 平台树 | src/factorlab、tests、docs/（契约 4 篇 + superpowers） |
| C2 | `research/`（仓库内） | 研究树 | tools/、factor/（152 yaml，14 族）、docs/（factors 档案 + strategies + playbook） |
| C3 | `projects/quant_core_shim` | 无 git（本地包） | quant-core 0.1.0 shim（emb 已 `pip install -e`） |
| C4 | `projects/ashare_alpha3` | 无 git（本地项目） | A 股 alpha 项目（config.yaml 消费 A3/A5/A9/A10） |

## D. 归档区

**归档批次的唯一事实表在 `archive-policy.md` §"归档批次"**（2026-09-15 R6 消除双写）——
本表不复制；此处只记一句：`_archive/` 内各批次按该表到期清理，到期程序见同文件。

## 血缘链（关键两条，供追溯）

```
quark 网盘 → A1 raw zip → A4 tick_fact → A6 lob_fact → factor_panel → tick 因子面板
                                      ↘ verify_cancels_sample（交叉复验）
A2 minutes zip → A3 bars_1m → CH bars_1m → platform 1m 漏斗
A8 daily zip → A5 daily_fact → CH daily 层 → ashare 各层 → A9 股票池
```

## 豁免清单（路径未重指但合法）

`_archive/**`、历史战役备忘（`tools/lob_fact/notes/*.md`）、`docs/superpowers/**` 历史
spec/plan、ashare `MIGRATION_GAP.md`——记录当时事实，不参与 grep 门。
