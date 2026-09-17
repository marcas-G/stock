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
