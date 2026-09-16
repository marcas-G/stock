# R30 Task 10 — Plan P 真实端到端验收（夸克网盘 + ClickHouse 真跑）

- 轮次：R30（Plan `knowledge/design/workspace/2026-09-16-pan-data-update-plan.md` Task 10）
- 起始 HEAD：`970c15e`（分支 `restructure/monorepo`，2026-09-17）
- 环境：`FACTORLAB_MAX_MEMORY=8GB`；CH `127.0.0.1:8123/factorlab` 在线；仓根 `quark_cookies.txt` 有效
- 裁决（任务指令）：**分钟本轮只取「最近 1 个月缺失日」（2026/09，12 个交易日）做试点**；
  其余 4023 个缺失日 zip（2017-10 起历史缺口 + 2020-2026 缺口）留定时首跑全量补齐。
  长期实现：`minutes_pilot_sync.py`（复用生产代码路径，见该文件头注）。

## 步骤与结果

| # | 步骤 | 命令 | 关键结果 | 证据 |
|---|---|---|---|---|
| 1 | dry-run 清单 | `cli.py all --dry-run` | daily to_fetch=4 / adopted=371；minutes to_fetch=4035 / adopted=1609；fund_flow to_fetch=44；financials to_fetch=4。9/1-9/16、9/1-9/15 增量日K、9 月分钟/资金日 zip、财报 xlsx 均在列；日K 全量（3.8G）/财务 parquet（367M）与两个财务 zip（802M/786M）为 size limit 候选 | `01-dryrun-root-listdir.txt` `01b-dryrun-level2-listdir.txt` `02-dryrun-all.txt` `03-sync-plan-sizes.txt` |
| 2a | 真跑 sync | `cli.py sync --categories daily,fund_flow,financials` | daily：downloaded=3（2 增量+说明）、adopted=371（退市股 xlsx）、manual=1（全量 3.8G）；fund_flow：downloaded=44（318.2 MB）；financials：downloaded=1（xlsx 2.17MB）、manual=3。**failed=0**；state runs.status=ok | `04-sync-real.log` `04-sync-size.txt` |
| 2b | 分钟试点 sync | `minutes_pilot_sync.py 2026/09` | 12/12 downloaded（191.4MB），0 failed；state `minutes/` 12 件、阶段标记清空 | `05-minutes-pilot.log` |
| 3a | daily build | `cli.py build --categories daily` | import_daily 16964 tasks（全量重解析 17min）→ ingest_daily 18,191,285 行×5 表 → stk_limit 17,921,186 → adj_detail 18,191,285 / adj_event 57,352。CH **daily max 2026-08-21 → 2026-09-16**（9 月 12 个交易日） | `06a-ch-pre-build.txt` `06c-ch-post-daily-build.txt` |
| 3b | fund_flow build | `cli.py build --categories fund_flow` | moneyflow 1,113,668 行 / 202 日 / 5,596 码；**002281.SZ 2026-09-16 main_net_inflow=2,030,000,000.0** 与源 zj.xls `20.3亿` 精确一致（auction=39,220,000.0、super_in=3,070,000,000.0 同步核对） | `07a-raw-moneyflow-002281.txt` `07b-moneyflow-parse-real.txt` `08b-ch-moneyflow-check.txt` |
| 3c | financials build | `cli.py build --categories financials` | xlsx → fact 5,556 行（丢弃 updated_date 缺失 16 行）→ CH `fundamentals` **5,556 行**（38 个 updated_date），表列/样本已查 | `09b-ch-fundamentals-check.txt` |
| 3d | minutes build | `cli.py build --categories minutes` | convert production：81 月、80 skip、**202609 转换 12 日 16,009,200 行**；ingest_bars：新增 1 分区 16,009,200 行；bars_1m max → 2026-09-16；转换错误 5,222 条均为既定隔离（899xxx 指数 4,272 / B 股 948 / rows_not_240 2），0 suffix-isolated | `10-build-minutes.log` `10b-ch-bars-check.txt` |
| 4 | verify（reconcile 全量） | `cli.py verify` | daily 层 5 表 + 派生表（stk_limit/adj_detail/adj_event）+ bars_1m 81 分区 + tick 3 表全部一致；不变量违规=0；**全库一致 rc=0** | `11-verify-reconcile.log` |
| 5 | 幂等 | ① `all --dry-run --categories daily,fund_flow,financials` ② `all --categories daily,fund_flow,financials` ③ `publish --categories minutes` | ① 三个类别 to_fetch 仅剩 4 个 manual 大件（设计语义：未落盘不记账）；② **downloaded=0 adopted=0**，build/publish 全部「已标记，跳过」，reconcile 全库一致，runs.status=ok；③ publish 别名跳过（build/publish 共标记） | `12-idempotent-dryrun.txt` `13-idempotent-all.log` `13b-publish-minutes-alias.txt` |
| 6 | 定时器 | `install_pan_timer.sh install` + `status` | systemd --user 可用：`pan-data-update.timer` **enabled/active (waiting)**，下次触发 `2026-09-17 08:11:27 CST`；`Linger=yes`（注销后仍运行）。未走 crontab 回退 | `14-timer-install.txt` `14b-timer-status.txt` |
| 7 | 读新数据 | `FACTORLAB_DATA_BACKEND=ch factorlab run min_spec_r30.yaml --no-backtest` | exit 0；`date_start=2026-07-01 date_end=2026-09-16`，panel_rows=260,214，n_weeks=9，weekly 末周含 **2026-09-16**，signal 当日 5,212 行；CH 新交易日查询（daily/moneyflow/bars）见证据 | `15-factorlab-lint.txt` `15-factorlab-run-ch.log` `15b-factorlab-run-summary.txt` `16-ch-new-day-queries.txt` |
| 8 | 回归 | `make test-research`（tools 部分）；平台全量；gates | platform/tools **519 passed**（基线 515 + 新增 4 测试）；research/tools 2 failed 为挖矿在途 G-INDEX（非本轮，见下）；平台全量见 `18-platform-suite.log` | `17-test-research.log` `18-platform-suite.log` |

## 验收中发现并修复的 3 个真实阻塞（红→绿）

1. **ingest_daily ArrowMemoryError（RLIMIT_AS 24GiB 下 VA 超限）**
   根因：40 核机 glibc 多线程 arena VA 预留 ~18GB，`ingest_daily`（18.19M 行 + Arrow IPC）
   峰值 VmPeak **26.03 GiB** > 24 GiB（=`3×FACTORLAB_MAX_MEMORY` 地板值），RSS 仅 7.55 GiB。
   修复：`cli._stage_env()` 对 stage 子进程默认 `MALLOC_ARENA_MAX=2`（显式设置优先）→
   VmPeak **16.41 GiB**。平台公式本身的校准观察另登记 pending（未改平台）。
   证据：`06-rootcause-vm-{nolimit,nojemalloc,arena2}.txt`、`06-rootcause-ingest-nolimit.log`；
   测试：`platform/tools/pan_update/tests/test_cli.py::test_stage_env_caps_malloc_arenas_by_default`
   `::test_stage_env_respects_explicit_malloc_arena_max`。
2. **ingest_moneyflow 不认大写成员名**：上游 `20260911.zip` 成员全大写（`ZJ.XLS`）。
   修复：`_ZJ_RE` 加 `re.IGNORECASE`；测试 `test_load_frames_accepts_uppercase_zj_member`。
3. **分钟转换器不识别「7z 内容 + .zip 命名」**：上游 2026-09-02..16 共 11 天实为 7z
   （魔数 `7z\xbc\xaf\x27\x1c`），`ZipReader` 必 BadZipFile。
   修复：`open_reader` 按魔数选 reader（扩展名仅用于定位路径）；
   测试 `test_open_reader_sniffs_7z_content_under_zip_name`（真造 7z 归档必败存根）。

## manual_required 清单（4 项，已打印进日志与 state 外）

| 类别 | 文件 | 原因 → 放置目录 |
|---|---|---|
| daily | `19910101至20260831A股日k线.zip`（3.79GB） | size limit → `data/raw/daily/` |
| financials | `个股财务数据_2026-09-04_更新.zip`（802MB） | size limit → `data/raw/financial/` |
| financials | `财务季报年报_2026-09-04_更新.zip`（786MB） | size limit → `data/raw/financial/` |
| financials | `2026-09-04_financial.parquet`（368MB） | size limit → `data/raw/financial/` |

放入后重跑 `make data-update` 即自动 adopted 接续（T9 修复轮 1 语义，本轮 daily 371 件
退市股 xlsx 已实测 adopted）。

## 残余风险与未竟

1. **daily 2026-08-22..08-31 缺口**：新全量快照 `19910101至20260831` 未到手（manual），
   本地旧全量（至 07-31）+ 旧增量（至 08-21）+ 新增量（09-01 起）拼出 9 月但缺 8 月末
   6 个交易日。CH daily/bars/各派生表该段为空（daily 日期范围证据可见）。**补法：人工放置
   新全量后重跑；或等待上游补 8 月增量。** 已登记 pending。
2. **分钟全量未取**（裁决内）：state 仅记 2026/09 十二件；下次 `make data-update`（定时首跑）
   将取剩余 4023 个缺失日 zip（~40GB，预计数小时），并触发 convert 对 2017-2019 全量月
   的分区转换（12h `TimeoutStartSec` 内可完成与否取决于网速；flock 防重叠）。
3. **reconcile 尚未覆盖 moneyflow/fundamentals**（T7/T8 即转 T11）：本轮以源帧 vs CH 行数/
   样本值人工核对（1,113,668 / 5,556，002281 对账精确）。
4. **平台内存护栏公式校准**：`_AS_ARENA_PER_CPU=64MB` 低估多 arena 预留（实测 headroom 项
   ~18GB）；本轮以 stage env 方式规避，未动平台常量。建议平台侧复核（登记 pending）。
5. **research/tools 2 失败为挖矿在途 G-INDEX**（`intraday/am_pm_vol.md` 缺档案等，T9 同时
   记录，非本轮引入）：需挖矿循环收口后 `make index` 重生成。
6. `fund_flow` 下载含 3 个非数据文件（`day_read.py`/`通知…txt`/`资金使用说明.7z`，38.5MB）
   ——差集语义按清单全量同步的副产物，不影响解析（仅 `*.zip` 进 ingest）。

## 复现

```bash
cd /data/students/gaolei/stock
# 1) dry-run
FACTORLAB_MAX_MEMORY=8GB platform/.venv/bin/python platform/tools/pan_update/cli.py all --dry-run
# 2) sync（daily/fund_flow/financials 全量；minutes 试点月）
FACTORLAB_MAX_MEMORY=8GB platform/.venv/bin/python platform/tools/pan_update/cli.py sync --categories daily,fund_flow,financials
FACTORLAB_MAX_MEMORY=8GB platform/.venv/bin/python governance/evidence/verification/R30/task10/minutes_pilot_sync.py 2026/09
# 3) build/verify
FACTORLAB_MAX_MEMORY=8GB platform/.venv/bin/python platform/tools/pan_update/cli.py build --categories daily,fund_flow,financials,minutes
FACTORLAB_MAX_MEMORY=8GB platform/.venv/bin/python platform/tools/pan_update/cli.py verify
# 4) 读新数据
FACTORLAB_DATA_BACKEND=ch FACTORLAB_MAX_MEMORY=8GB platform/.venv/bin/factorlab run \
  governance/evidence/verification/R30/task10/min_spec_r30.yaml --no-backtest --max-memory 8GB
```
