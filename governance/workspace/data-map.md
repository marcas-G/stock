# 数据地图（data-map）

每个数据单元的唯一位置、唯一生产者（Primary Owner）、血缘（上游→本单元→下游消费者）、
更新方式。**没有孤儿资产**：任何 data/ 下目录都能在本表找到行；表外的数据单元不存在。

> **外部数据源（2026-09-17 Plan P T11 起）：夸克网盘（唯一）**。分享 `level2_detail/`
> 四类别（日K/分钟/日线资金/财报）经 `platform/tools/pan_update/` → `make data-update`
> 更新；旧外部源 teajoin（Tushare 代理）/腾讯 kline/jqdata 生产路径已删除或退役，
> 原 teajoin 使用指南见 `knowledge/contracts/teajoin-guide.md`（存档，不再维护）。

维护规则：新增/搬移数据必须同步改本表（谁改谁负责）。**裁决权分层**（2026-09-15 R6 明确）：
本表是**数据资产实例**（哪个目录是什么、谁生产谁消费、到期日）的唯一权威；
目录结构与分类规则以 `directory-conventions.md` 为准；两者与磁盘不一致时**以磁盘为准并修订文档**。

## A. 数据资产（data/）

| # | 单元 | 位置 | 规模 | 生产者（Owner） | 上游血缘 | 下游消费者 | 更新方式 |
|---|---|---|---|---|---|---|---|
| A1 | 原始逐笔/行情 zip | `data/raw/quark_downloaded/` | 111G（256 日 × ~293 码 × 74,630 zip） | quark 网盘分享下载（skill: quark-share-download；脚本 platform/tools/quark_download/） | 夸克网盘外部 | A4 转换器、A5 抽取器、verify_cancels_sample | 增量下载（新交易日到货后 append 日期目录） |
| A2 | 分钟原始 zip | `data/raw/minutes/` | 20G（1609 zip，2020-01..2026-08） | 上游分钟数据源（zip 形态） | 夸克网盘（唯一外部源） | A3 转换器 | 增量（`make data-update` 按 (年,月,日) 差集补 zip） |
| A3 | 分钟事实库 | `data/fact/bars_1m/` | 19G（80 个月 Hive parquet + `_state/` + `_dataset_metadata.json`） | `platform/tools/converters/convert_minutes_to_parquet.py` | A2 | CH bars_1m（B1）、platform 1m 漏斗、ashare_ingest/validate_minutes 对账、universe_stages 分层特征 | 逐月转换 + `_state` 断点续跑；**重跑前先读 `_dataset_metadata.json`** |
| A4 | 逐笔事实库 | `data/fact/tick_fact/` | 82G（orders/trades/snapshots/cancels + `_manifest/`） | `platform/tools/converters/convert_tick_to_parquet.py`（cancels 由 `platform/tools/lob_fact/pipeline/extract_sz_cancels.py`） | A1 | CH tick_* 3 表（B1）、lob_fact 重建（A6）、ashare_ingest/validate_tick 对账 | 逐日转换 + manifest 记账；cancels 单表可独立补抽 |
| A5 | 日线事实 | `data/fact/daily_fact/daily_fact.parquet` | 435M（18,191,285 行 / 5,879 码；1990-12-19..2026-09-16；2026-09-17 T10 后实测） | `platform/tools/ashare_ingest/import_daily.py`（通达信日K导出 → parquet；R19 收编） | A8（夸克网盘） | CH daily 层 5 表（B1；`ch_ingest/ingest_daily.py`）、`ch_ingest/adj_backfill.py`（除权 7 列 → adj_detail/adj_event）、universe_stages 各层、1m_features 日级注入 | 全量重算（zip → parquet），行数对账见 ch_ingest/reconcile；更新链：`make data-update`（pan_update daily 阶段） |
| A6 | 订单簿重建（L3） | `data/fact/lob_fact/` | 113G（lob_events 99G / lob_sweep_meta 12G / lob_checkpoints 3G + `_batch/` + panel_1s + panel_1m + `panel_runs/`） | `platform/tools/lob_fact/pipeline/run_lob_batch.py`（W2 引擎 + W4/W5 批算） | A1 + A4（δ-lag 锚定） | factor_panel.py 面板、audit_w5、未来 tick 因子 | **单写者 flock 纪律**（`_batch/.lock`）：批算与 compact 互斥；状态 `_batch/state.json`（13 月 250/250 完成） |
| A7 | 校准/验证制品 | `data/calib/lob_fact_calib/`（w1/w3 校准 JSON）、`data/calib/validation/`（date_shift_exceptions.csv + step0a/step0b） | 3.7M | `platform/tools/lob_fact/diag/{calibrate_w1,measure_w3}.py`；验证战役脚本（已归档，可再生成） | A1/A4 | lob_fact 引擎对拍（CALIB_OUT）、converter 生产路径（DATE_SHIFT_EXC_PATH） | 只增不改（冻结制品）；**validation/date_shift_exceptions.csv 被生产路径读取，禁止删除** |
| A8 | 日线原始 | `data/raw/daily/`（4 个通达信日K zip + 退市股/ + 源说明） | 3.8G | 通达信日K导出（上游） | 夸克网盘（唯一外部源） | A5（`ashare_ingest/import_daily.py` 直接 glob 本目录；**只读**） | 增量导出（新 zip 到货；`make data-update` 自动下载）；`daily_pre.parquet` 中间物已于 2026-09-17（T11）清理 |
| A9 | 股票池（golden） | `data/ref/universes/v4_top300.parquet` | 6.6M | **冻结、不维护**——上游 golden 参考（jqdata 口径；重生成需 jqdata 环境，网盘无等价；源脚本归档 `_archive/2026-09-16-ashare-alpha3/references/v4_jqdata_final_original.py`，2026-10-16 到期——见 pending-items #9） | A5（市值/行情口径） | `universe_stages/scripts` 10/11/20/30（读作 golden_v4_top300）；layer1 产出的是 `v4_top300_local.parquet`（outputs，非本文件） | **只读参考，冻结（2026-09-17 T11 裁决）**；sha256 `ed515841…`（见 R30/task11 清单） |
| A10 | 指数基准 | `data/ref/000905.SH.parquet`（中证 500） | 20K（20,649B） | **退役**：`platform/tools/ashare_ingest/import_index.py`（腾讯 kline）已于 2026-09-17（T11）删除 | 外部（腾讯 kline，已退役） | universe_stages benchmark | **冻结、不维护**（T11 裁决）：网盘指数目录只有 `截止_2026-09-16_指数…_日线.zip`（100MB，超直链上限不可核对内容、文件名不含 000905/中证500）→ 不满足覆盖判定；需要时浏览器下载该 zip 人工核对替换；sha256 `02775ff2…` |
| A11 | 未解包归档 | `data/raw/20260817.7z` | 5.3G | quark 网盘（单日全码包） | 外部 | universe_stages `ticks_root`（**需先解包**，见 pending-items #3） | 解包后排期（**先 `df` 复核余量**——2026-09-15 实测 338G 可用；见 pending-items #3） |
| A12 | 预留面板区 | `data/panel/` | 空 | —（公约预留给未来跨源大面板） | — | — | 新面板落位时更新本表 |

## B. 外部数据服务（不占工作区磁盘）

| # | 单元 | 位置/连接 | 规模 | 生产者 | 消费者 | 说明 |
|---|---|---|---|---|---|---|
| B1 | ClickHouse `factorlab` | `127.0.0.1:8123`（HTTP；19000 是 tcp client 端口），db=factorlab | **13 表**（2026-09-16 实测：tick_orders 5,810,986,406 / tick_trades 3,797,289,378 / tick_snapshots 344,059,684 / bars_1m 1,853,379,840 / daily=adj_detail=adj_factor=daily_basic 18,124,805 / stk_limit 17,854,764 / adj_event 57,173 / stock_basic 5,861 / trade_cal 8,772；**index_daily 0 行**，见 pending-items #25） | `platform/tools/ch_ingest/`（ingest_daily/bars/tick + derive_stk_limit） | platform ch 后端、研究只读查询 | 对账：`make reconcile`（全一致才 exit 0；**单解释器** `platform/.venv/bin/python`，R27 起 emb 退役）；进度：`ch_ingest/state.json`（**单 JSON**，R4b 由目录形态迁移而来；运行时，不入 git） |
| B2 | 平台库 `factorlab.duckdb` | `platform/data/`（相对平台树根） | **当前不存在** | 旧外部源全量写入路径**已退役**（2026-09-17 T11 删 `adapters/rebuild|refresh|mirror_db`） | platform duckdb 后端（测试/历史只读库） | 生产读取走 `FACTORLAB_DATA_BACKEND=ch`；duckdb 仅双腿测试与历史只读库 |
| B3 | teajoin Tushare 代理 | `https://teajoin.com` | **已退役**（2026-09-17 T11：`data rebuild/update/refresh/verify` CLI 与 fetcher 删除；token 2026-08-22 过期不再续期） | 外部 API（历史） | —（无生产消费者） | 存档说明见 `knowledge/contracts/teajoin-guide.md` |

> **R29 数据缺口裁决（2026-09-16）**：CH 无 `stock_st` 表（ST 口径按
> `FACTORLAB_ST_DEGRADE` 显式降级，不伪造；pending #26）；`index_daily` 0 行且无可用
> 补数脚本（pending #25）。两表恢复条件与证据见 pending-items 与
> `governance/evidence/verification/R29/data-t4/`。

## C. 代码与项目（单仓三树 + projects/ 本地目录）

| # | 项目 | 分支/角色 | 权威内容 |
|---|---|---|---|
| C1 | `platform/`（仓库内） | 平台树 | src/factorlab、tests、（契约 4 篇 → `knowledge/contracts/`、设计 → `knowledge/design/platform/`，R24） |
| C2 | `research/`（仓库内） | 研究树 | factor/（170 tracked / 196 on-disk yaml（在途挖矿），15 族；2026-09-16 实测）、tools/（剩余研究工具：strategies/factor_lib）——档案/索引/playbook R24 迁 `knowledge/dossiers/`；数据生产线工具集 R27 归位 `platform/tools/` |
| C3 | `platform/kernels/quant_core`（R18 起；原 `projects/quant_core_shim`） | 仓库内（内核发行物唯一声明点） | quant-core 0.1.0 shim（仅装 `platform/.venv`；Rust 版到位时同目录换 build backend） |
| C4 | `_archive/2026-09-16-ashare-alpha3/`（原 `projects/ashare_alpha3`；**R19/R20 收编完成**） | 本地归档（无 git；2026-10-16 到期） | 数据侧已完成 → `platform/tools/ashare_ingest/`；股票池段已完成 → `platform/tools/universe_stages/`（R20）；R24 Task 11 归档 |

## D. 归档区

**归档批次的唯一事实表在 `archive-policy.md` §"归档批次"**（2026-09-15 R6 消除双写）——
本表不复制；此处只记一句：`_archive/` 内各批次按该表到期清理，到期程序见同文件。

## 血缘链（关键两条，供追溯）

```
quark 网盘 → A1 raw zip → A4 tick_fact → A6 lob_fact → factor_panel → tick 因子面板
                                      ↘ verify_cancels_sample（交叉复验）
夸克网盘 → A8 daily zip → A5 daily_fact（ashare_ingest/import_daily）→ CH daily 层（ch_ingest）→ universe_stages 多级股票池 → A9 golden 对照
夸克网盘 → A2 minutes zip → A3 bars_1m → CH bars_1m → platform 1m 漏斗
夸克网盘 → 日线资金 zip → CH moneyflow；财报 xlsx → fundamentals_snapshot → CH fundamentals（Plan P T7/T8/T10）
```

## 豁免清单（路径未重指但合法）

`_archive/**`、历史战役备忘（`platform/tools/lob_fact/notes/*.md`）、`knowledge/design/platform/**` 历史
spec/plan、`universe_stages/MIGRATION_GAP.md`（R20 迁入）——记录当时事实，不参与 grep 门。
