# R30 A3 证据：bars_1m 月分区吸收同月新增日（源清单比对 + 回退 fail loud）

## Commit

| commit | 说明 |
|---|---|
| `36f3da9` | `fix(tools): 分钟月分区提交补源清单比对（A3，同月新增日重转/源回退 fail loud）` |
| 本证据提交 | `docs(verification): A3 关闭（interface §8 提交语义 + pending A3✅/A4 + 证据）` |

- 需求源：`governance/workspace/pending-items.md` A3 / A4（2026-09-17）
- 代码：`platform/tools/converters/convert_minutes_to_parquet.py`
  （`_source_listing` / `_source_relation` / `_commit_status` / `SourceRollbackError`）
  + `governance/ops/check_dataiface.py`（G-READ 登记转换器自产月回执清单读）
- 测试：`platform/tools/converters/tests/test_convert_minutes_commit.py`（6 条，TDD 红→绿）
- 契约：`knowledge/contracts/interface.md` §8「分钟月分区提交语义（A3，2026-09-17）」

## 根因与判定规则

根因：`convert_month_worker` 的 `_committed_ok` 只看产物（schema/rows/row_groups），
不比对转换时的源归档 → 同月新增 zip 被既有 `_SUCCESS` 钉死（2026-09 实测 days=12 /
源已有 20260917.zip）。

修复后已提交月判定 = 产物校验 + 源回执比对（`_daily_manifest.parquet` 逐日源 zip
name+size；取轻量可靠者，不重读内容）：

| 源 vs 回执 | 判定 | 行为 |
|---|---|---|
| 逐 (name,size) 一致 | ok | 幂等跳过，产物不重写 |
| 含回执全部归档且另有新增 | stale | 清理重转吸收 |
| 同名 size 变化（源被替换） | stale | 清理重转 |
| 回执归档在当前源缺失（源回退） | rollback | `SourceRollbackError` fail loud，保留产物 |
| 回执缺失/不可读 | stale | 保守重转 |

**裁定（源回退 fail loud，理由）**：源日被删时自动重转 = 用更少的天数覆盖已消费的
事实分区，历史日不可逆丢失；故保留已提交产物、非零退出、提示人工确认（确认后先删
分区产物再跑）。新增/替换是正常增量路径，自动吸收。

## 证据清单（本目录）

| 证据 | 命令 | 结果 |
|---|---|---|
| `01-red.txt` | stash 实现后 `pytest …test_convert_minutes_commit.py -q` | **5 failed / 1 passed**（新增日/回退/size 变化/manifest 缺失均红） |
| `02-green.txt` | 实现后同测试 | **6 passed** |
| `03-mutation.txt` | `python3 …/minute-month-absorb/mutation.py` | **5/5 突变被抓**（恒 ok / 恒 stale / 回退不 fail loud / 忽略 size / manifest 缺失按 ok）；恢复后逐字节一致 + rc=0 |
| `04-ch-minutes-pre.txt` | CH 查询 bars_1m 202609 | 16,009,200 行，max=2026-09-16（全局 max 同） |
| `05-reconvert-202609.log` | `FACTORLAB_MAX_MEMORY=8GB …convert_minutes_to_parquet.py --mode production --start-year 2026 --start-month 9 --end-year 2026 --end-month 9 --workers 1` | 首跑：`202609: stale（新增 1: 20260917.zip）-> 清理重转`，days=13 / rows=17,344,800 / 6m59s / maxRSS 728MB；**二跑 skipped=1 / 0.96s（幂等）** |
| `06-local-artifact-post.txt` | `sha256sum …/year=2026/month=09/part-000.parquet` | `111239dd…`（与 `_conversion.json` 记录一致；二跑未改写） |
| `07-reingest-202609.log` | 删 `bars_1m_202609` 断点后 `FACTORLAB_MAX_MEMORY=8GB …/ingest_bars.py` | 任务 81（已完成 80，待跑 1）→ 17,344,800 rows / 14.2s / maxRSS 957MB |
| `08-ch-minutes-post.txt` | CH 查询 | 202609=17,344,800 行（+1,335,600=9/17 单日）/ max=2026-09-17 / 全局 max=2026-09-17 / 13 天 |
| `09-reconcile.log` | `make reconcile` | **全库一致**（bars_1m 81 分区一致；exit=0） |
| `10-check-day-2024-01-15.log` | `FACTORLAB_DATA_BACKEND=ch …run_1m_feature.py check-day 2024-01-15` | 交集 5,249 行，两特征 max\|Δ\|=0，**PASSED** |
| `11-check-day-2026-09-17.log` | 同 check-day 9/17 | 失败（预期）：引擎/本地池漂移 fail-fast——9/17 bars 有 27 个 daily 池外 code（×240=6,480 行），非 A3 引入；`11b-pool-drift-202609.txt` 给出 9 月逐日 10~28 个漂移 code |
| `12-ch-local-20260917.txt` | `verify_20260917.py`（新吸收日 CH×本地全列） | 1,335,600 行键集全等，OHLCV/amount max\|Δ\|=0，session/trade_date/datetime 全等，**PASSED** |
| `13-gates.txt` | `make gates` | G-DATAIFACE/结构门全绿；仅 G-INDEX 红（在途挖矿 spec 未归档，预存） |
| `14-tools-suite.txt` | `FACTORLAB_MAX_MEMORY=8GB … pytest platform/tools -q` | **595 passed**（基线 589 + 本项新增 6） |
| `15-platform-suite.txt` | `cd platform && FACTORLAB_MAX_MEMORY=8GB .venv/bin/python -m pytest -q`（Makefile 口径） | **3207 passed, 11 skipped, 0 failed**（14m29s；本项无平台 src 改动） |
| `16-test-research.txt` | `make test-research` | 平台工具面 **595 passed**；research/tools 2 failed 为**在途挖矿**（`knowledge/dossiers/factors/…/amihud_minute.md` 等新 spec 未归档/索引未重建，属他人在途文件），非本项回退 |
| `17-month-classification-audit.txt` | 用已提交 `_commit_status` 只读扫描全部 81 个月 | **80 ok / 1 stale**（仅 202608 缺 6 日 8/24~8/31 待重转；202609 已 ok）——无历史月误重转 |
| `mutation.py` / `verify_20260917.py` | 复现脚本 | — |

## 关键数字

- 202609：16,009,200 → **17,344,800** 行（+1,335,600 = 9/17 单日）；max **2026-09-17**。
- 幂等：二次 convert `skipped=1` / 0.96s，产物 sha 不变；reconcile 全绿。
- 对拍：2024-01-15 引擎×本地 max|Δ|=0；9/17 CH×本地全列 max|Δ|=0。
- 内存：convert maxRSS **728MB**、ingest maxRSS **957MB**（均 ≪ 8GB 护栏）。

## 未竟（→ pending-items A4）

重转同月后 CH 不自动重灌：`ch_ingest` 月断点只记布尔、不带源指纹，
`ingest_bars.run_pool` 仍按旧断点跳过 → `make data-update` verify 会红。本次交付为
手工删键重灌；A4 已登记源指纹方案与旧标记迁移约束（不能全量重灌）。

---

# A4（2026-09-18）：bars_1m 月断点源指纹——转换器重转同月 → CH 自动重灌

## Commit

| commit | 说明 |
|---|---|
| `fix(tools): ch_ingest 月断点记源指纹（A4，转换器重转月自动重灌）` | `ch_source.source_fingerprint` + `ch_state` 指纹断点/迁移回填 + `ch_write.run_pool` 指纹判定与 `--force` + `ingest_bars --force` + G-READ 登记 |
| `docs(verification): A4 关闭（interface 提交语义 + pending ✅/A5 + 证据）` | 契约/pending/本证据 |

- 需求源：`governance/workspace/pending-items.md` A4（A3 连带缺口），迁移约束：
  旧布尔断点不可按「未完成」处理（81 bars+39 tick ≈ 18.7 亿+99.5 亿行全量重灌不可接受）。
- 设计：月断点值 = 转换器月回执 `_state/…/_daily_manifest.parquet` 源 zip **name+size**
  摘要（sha256，与 A3 `_source_relation` 同一清单，不自创 digest 源）；指纹变化 →
  `ingest_task` 既有 `DROP PARTITION`+重建（事务边界不变）；旧布尔 → 只回填不重灌；
  存量偏差逃逸口 = `ingest_bars.py --force YYYYMM[,YYYYMM...]`（点名重灌）。
- 测试：`platform/tools/ch_ingest/tests/test_bars_month_fingerprint.py`（7 条，TDD 红→绿）

## 证据清单（A4 段，接 A3 编号）

| 证据 | 命令 | 结果 |
|---|---|---|
| `18-a4-red.txt` | 实现前 `pytest …test_bars_month_fingerprint.py -q` | **7 failed**（源指纹 API/迁移/force 全红，失败原因=特性缺失） |
| `19-a4-green.txt` | 实现后同测试（无 monkeypatch 真读 parquet 回执/真写断点 JSON） | **7 passed** |
| `20-a4-mutation.txt` | `python3 …/mutation_a4.py` | **5/5 突变被抓**：指纹恒 None / 判定忽略指纹 / 旧布尔不回填 / 忽略 force / 断点恒写布尔；恢复后逐字节一致 + rc=0 |
| `pre-sha.txt` / `pre-ch.txt` | 改前 sha + CH | state 96bae213…（81 bars+39 tick 全布尔）；bars_1m 202608=19,950,960（15 天）/202609=17,344,800；全表 1,870,724,640 行 |
| `22-a4-reconvert-202608.log` | `FACTORLAB_MAX_MEMORY=8GB …convert_minutes_to_parquet.py --mode production --start-year 2026 --start-month 8 --end-year 2026 --end-month 8 --workers 1` | `202608: stale（新增 6）-> 清理重转`：days=21 / rows=27,942,240 / 9.7min（源 8/24~31 六日吸收） |
| `22b-local-after-reconvert.txt` | 产物/回执字段 | `_conversion.json` days=21 rows=27,942,240；manifest 21 行（至 20260831.zip）；part sha `4a33e0bf…`（旧 `4b39e16f…`） |
| `21-a4-migrate-backfill.log` | `FACTORLAB_MAX_MEMORY=8GB …ingest_bars.py`（**迁移路径**） | `任务 81（已完成 81，待跑 0）`——旧布尔只回填指纹、0 重灌 |
| `21b-state-after-migration.txt` | 断点形态 | 81 bars 全部 64-hex 指纹（0 布尔）；39 tick 仍布尔；202608 fp=`a0ec7c60…` |
| `23-a4-reconcile-red.log` | `make reconcile` | **红**（exit Error 1）：`MISMATCH bars_1m/202608: CH=19,950,960 src=27,942,240`——迁移设计让存量偏差由对账暴露 |
| `24-a4-force-202608.log` | `…ingest_bars.py --force 202608` | `任务 81（已完成 80，待跑 1）` → 202608 重灌 **27,942,240 rows** |
| `24b-ch-after-force.txt` | CH 查询 | 202608=27,942,240；全表 1,878,715,920（+7,991,280=6 个新增日） |
| `25-a4-reconcile-green.log` | `make reconcile` | **全库一致**（bars_1m 81 分区全绿，exit=0） |
| `26-a4-data-update-run1-skip.log` | `FACTORLAB_MAX_MEMORY=8GB make data-update`（第 1 次） | downloaded=0（分享无新增）→ 阶段全「已标记，跳过」；reconcile 全绿 |
| `27-a4-data-update-absorb.log` + `27b-a4-pan-minutes-chain.log` | 模拟 A4 触发（注入旧指纹 + 清 minutes 阶段闩锁；备份见 `/tmp/opencode/a4/*.pre-absorb`）后 `make data-update` | minutes 链**真跑**：converter `skipped: 81` → ingest `任务 81（已完成 80，待跑 1）` → `bars_1m/202608: 27,942,240 rows`（**指纹变化自动重灌**）→ verify 全库一致；23.0s |
| `28-a4-data-update-idempotent.log` + `28-pre/post-run2-sha.txt` | 再跑 `make data-update`（第 2 次） | downloaded=0、阶段全跳过、reconcile 全绿；202608/202609 产物与 state.json **sha 逐字节不变** |
| `29-a4-gates.txt` | `make gates` | 数据接口/结构门全绿；仅 **G-INDEX 预存红**（在途挖矿 spec 未归档，同 A3） |
| `30-a4-test-research.txt` | `FACTORLAB_MAX_MEMORY=8GB make test-research` | platform/tools **602 passed**（A3 基线 595 + 本项 7）；research/tools 2 failed 为**在途挖矿**（G-INDEX 同源），非本项回退 |
| `31-a4-platform-suite.txt` | `cd platform && FACTORLAB_MAX_MEMORY=8GB .venv/bin/python -m pytest -q` | **3207 passed, 11 skipped**（与 A3 基线一致，无平台 src 改动） |
| `mutation_a4.py` | 复现脚本 | — |

## A4 关键语义与数字

- 指纹源：转换器月回执的源 zip (name,size) 摘要（sha256 64-hex）；与 A3 比对同一份
  清单——转换器「重转」与 ingest「重灌」同一触发器，不自创第二判据。
- 202608：15 天 19,950,960 → **21 天 27,942,240 行**（CH 与本地一致）；全表
  +7,991,280 行。
- 迁移：81 存量月 0 重灌（仅 81 次指纹回填）；存量偏差不静默吞——由 `reconcile`
  暴露 → `--force YYYYMM` 点名重灌。
- data-update：无新增时全跳过（run1）；模拟重转触发时分钟链 23.0s 内自动吸收并
  全绿（absorb run）；二跑产物/断点 sha 不变（幂等）。
- tick 残余（无 `_daily_manifest.parquet`，断点仍布尔）→ pending A5。
