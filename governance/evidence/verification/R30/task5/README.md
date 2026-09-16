# R30 Task 5 证据：日K 全量前缀修正 + daily 阶段链

- 需求源：`.superpowers/sdd/2026-09-16-pan-data-update-plan/task-5-brief.md`
- 上位设计：`knowledge/design/workspace/2026-09-16-pan-data-update-design.md` §3/§4
- 代码：`platform/tools/ashare_ingest/import_daily.py`、`platform/tools/pan_update/stages.py`
- 测试：`platform/tools/ashare_ingest/tests/test_import_daily_full_prefix.py`、
  `platform/tools/pan_update/tests/test_daily_wiring.py`

## Commit

| commit | 说明 |
|---|---|
| `392f0c2` | `fix(tools): import_daily 全量快照识别支持网盘命名（19910101至前缀）` |
| `bf95ed8` | `test(tools): import_daily 全量识别守卫（旧标记兼容/选最新/增量任务）` |
| `f694c07` | `feat(tools): pan_update daily 阶段链` |
| `0d793da` | `docs(verification): R30 Task5 证据` |
| `00ef05e` | `fix(tools): import_daily 多增量取覆盖最新 + minutes 链补 production 模式`（修复轮 1） |
| 本证据提交 | `docs(verification): R30 Task5/6 修复轮1 证据` |

## 复现命令与结果

| 证据 | 命令 | 结果 |
|---|---|---|
| `01-red.txt` | `platform/.venv/bin/python -m pytest platform/tools/ashare_ingest/tests/test_import_daily_full_prefix.py -q`（实现前） | **2 failed**：`AttributeError: module 'import_daily' has no attribute '_is_full_snapshot' / '_newest_full'`（功能缺失，红） |
| `01b-red-daily-chain.txt` | `platform/.venv/bin/python -m pytest platform/tools/pan_update/tests/test_daily_wiring.py -q`（填链前） | **2 failed**：`KeyError: 'daily'`（`STAGE_CHAINS` 未配置，红） |
| `02a-green-import-daily.txt` | `platform/.venv/bin/python -m pytest platform/tools/ashare_ingest/tests -q` | **30 passed**（原 25 + 本次 5） |
| `02b-green-daily-chain.txt` | `platform/.venv/bin/python -m pytest platform/tools/pan_update/tests -q` | **57 passed**（T4 基线 55 + daily 接线 2） |
| `03-gtopo.txt` | `platform/.venv/bin/python governance/ops/check_tool_layering.py` | **0 处**（工具拓扑：不跨工具 import） |
| `04-mutation.txt` | `python3 governance/evidence/verification/R30/task5/mutation.py` | **14/14 突变被抓住**（初轮 10 + 修复轮 4）；恢复原文后两组测试 rc=0 |
| `05-tools-suite.txt` | `platform/.venv/bin/python -m pytest platform/tools -q` | **406 passed**（T4 基线 399 + 本次 7） |
| `06-fix-round1.txt` | 修复轮 1（评审 I2 + Minor）：红→绿→突变→G-TOPO→全量回归 | 红 4 failed → 绿 36/62 → 14/14 → 417 passed |

## 行为要点（测试锁死）

- **全量识别**：`_is_full_snapshot(path)` 认 `19910101至` 前缀（网盘命名）或旧标记
  `07月31日`（兼容）；支持 Path 与带 `.name` 的对象。
- **全量选取**：`_newest_full(paths)` 正则 `至(\d{8})` 取结束日最大；日期不可解析
  （旧命名）→ 原序首个。`_build_tasks` 两份全量共存时只解析最新一份（priority=0），
  非前缀 zip 进入增量（priority=1），增量缺失会使 task 缺 priority=1。
- **daily 阶段链**：`STAGE_CHAINS["daily"]` = 4 步，
  `[platform/.venv/bin/python] × [import_daily.py → ingest_daily.py → derive_stk_limit.py → adj_backfill.py]`；
  解释器与脚本路径全部经 `config.repo_root()` 派生（不硬编码工作区路径），
  测试断言 `cmd[0]` 为平台 venv 且脚本存在。

## 突变（`mutation.py`，10 个）

- import_daily：`_is_full_snapshot` 恒 False/恒 True/丢旧标记兼容；`_newest_full` 取原序首个；
  `_build_tasks` 用 `full_zips[0]`；忽略增量 zip——6/6 被抓。
- stages：daily 空链/缺一步/步序颠倒/首步不用平台 venv——4/4 被抓。
- 说明：初版"忽略增量"未被既有 e2e 抓住（e2e 只断言退市股行数），补
  `test_build_tasks_includes_incremental_zip` 守卫后抓住；这正是"存根必败"标准要求的闭环。

## 接口与裁决

1. `_build_tasks` 内部变量改名（`full_zip` 单份实体 / `full_zips` 候选集），并 `sorted()` 稳定
   遍历序；`import_daily` 对外签名不变（brief 要求）。
2. daily 链接线补 `test_daily_wiring.py`（brief 未列该文件）：TDD 要求阶段链有断言，
   与 T6 `test_minutes_wiring.py` 对称；仅断言路径/顺序/解释器，不触网不落盘。
3. 守卫测试（旧标记兼容、`_build_tasks` 选最新/含增量）必要性见上"突变"节，
   以独立 test 提交入库（不复写已提交的 `392f0c2`）。

## Concerns

1. `_build_tasks` 增量仍只解析 `incr_zips[0]`（排序后首个），与旧实现"取 glob 首个"同为
   单增量假设；网盘若同时保留多份增量 zip，需 T9/后续任务明确取最新或全解析。
   当前设计 §4 只承诺"最新全量 + 其后增量"，按现状保留行为。
2. `_newest_full` 对旧命名（无 `至YYYYMMDD`）无日期可比，两份旧全量并存时仍取原序首个；
   旧命名遗留场景由 `pan_update --prune` 或人工清理，未做月日回退解析。
3. daily 链命令未带参数（依赖各脚本默认路径）；env（内存护栏）由 T9 调用方经
   `run_category_stage(env=...)` 透传。

---

## 修复轮 1（独立评审 PASS；Important I2 + Minor，提交 `00ef05e`）

- I2（多增量只取排序首个 → 静默漏数据）：新增 `_select_increments`——区间解析归一 YYYYMMDD；
  只保留 `end > 最新全量 end`；被另一条区间包含者丢弃（保留覆盖更全的）；互不重叠全部保留；
  区间不可解析 / 旧命名全量无日期 → 保守保留。`_build_tasks` 遍历全部入选增量（不再取 `[0]`）。
- Minor（`19910101至` 注释写“前缀”但实现用子串 `in`）：实现改 `startswith`，新增
  `test_prefix_requires_start_of_name` 反例（子串形态不算全量）。
- 红→绿→突变→回归：见 `06-fix-round1.txt`；`04-mutation.txt` 与 `mutation.py` 已同步为
  14 条重跑（初轮 10 条为历史记录）；全量回归 **417 passed**。
