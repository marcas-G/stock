# R30 Task 6 证据：minutes 阶段链 + zip_days rel_path 映射

- 需求源：`.superpowers/sdd/2026-09-16-pan-data-update-plan/task-6-brief.md`
- 上位设计：`knowledge/design/workspace/2026-09-16-pan-data-update-design.md` §2/§3/§4
- 代码：`platform/tools/pan_update/stages.py`（`STAGE_CHAINS["minutes"]`）
- 测试：`platform/tools/pan_update/tests/test_minutes_wiring.py`

## Commit

| commit | 说明 |
|---|---|
| `735d1c5` | `feat(tools): pan_update minutes 阶段链` |
| `0afe60e` | `docs(verification): R30 Task6 证据` |
| `00ef05e` | `fix(tools): import_daily 多增量取覆盖最新 + minutes 链补 production 模式`（修复轮 1，T5+T6 同提交） |
| 本证据提交 | `docs(verification): R30 Task5/6 修复轮1 证据` |

## 复现命令与结果

| 证据 | 命令 | 结果 |
|---|---|---|
| `01-red.txt` | `platform/.venv/bin/python -m pytest platform/tools/pan_update/tests/test_minutes_wiring.py -q`（填链前） | **2 failed / 2 passed**：链断言 `KeyError: 'minutes'`（功能缺失，红）；rel_path 映射断言先绿（T2/T3 契约守卫） |
| `02-green.txt` | `platform/.venv/bin/python -m pytest platform/tools/pan_update/tests -q` | **61 passed**（T5 基线 57 + minutes 接线 4） |
| `03-gtopo.txt` | `platform/.venv/bin/python governance/ops/check_tool_layering.py` | **0 处**（工具拓扑：不跨工具 import） |
| `04-mutation.txt` | `python3 governance/evidence/verification/R30/task6/mutation.py` | **9/9 突变被抓住**（初轮 7 + 修复轮 2）；恢复原文后 rc=0 |
| `05-tools-suite.txt` | `platform/.venv/bin/python -m pytest platform/tools -q` | **410 passed**（T5 基线 406 + minutes 4） |
| `05-fix-round1.txt` | 修复轮 1（评审 I1）：红→绿→突变→G-TOPO→全量回归 | 红 1 failed → 绿 62/36 → 9/9 → 417 passed |

## 行为要点（测试锁死）

- **minutes 阶段链**：`STAGE_CHAINS["minutes"]` = 2 步，
  `[platform/.venv/bin/python] × [convert_minutes_to_parquet.py → ingest_bars.py]`（顺序断言）；
  解释器与脚本路径全部经 `config.repo_root()` 派生，断言平台 venv 且脚本存在。
- **zip_days 映射**：`config.CATEGORIES["minutes"].kind == "zip_days"`；
  `share.iter_category` 的 rel_path = `<年>/<月>/<YYYYMMDD>.zip`（相对类别根、无类别前缀），
  `repo_root()/local_root/rel_path` 与 `<年>/<月>/文件名` 逐段一致（小树 fixture 离线验证）；
  `sync` 消费边界 `to_fetch = rel_path`、`dest_root/rel_path` 直拼（无额外层级）。

## 突变（`mutation.py`，7 个）

- stages：minutes 空链/缺一步/步序颠倒/首步不用平台 venv/convert 指错工具目录——5/5 被抓。
- 映射契约：`share._join` 加类别前缀、`sync._dest_for` 多加一层目录——2/2 被抓
  （证明 rel_path 映射测试不是空转）。

## 接口与裁决

1. 本任务只追加 `STAGE_CHAINS["minutes"]`，不改 `stages.py` 其余语义（T4 的
   `run_category_stage`/flock/日志不变）。
2. rel_path 映射部分无新实现代码（T2 遍历 + T3 `_dest_for` 已直拼）；按 brief 要求以测试
   锁死"分享树 rel_path == 本地 `local_root` 相对布局"，并由突变证明判别力。
3. minutes 链命令未带参数（各自读默认源目录/配置）；env（内存护栏）由 T9 调用方经
   `run_category_stage(env=...)` 透传。

## Concerns

1. `convert_minutes_to_parquet.py` 的 production 模式按"月"处理（需 `_dataset_metadata.json`
   与 `source_unit_regimes` 配置）；分钟链在 T9/T10 真跑时需确认月分区参数与配置已就位，
   本任务只锁"命令接线与路径/顺序"，未验证真实转换语义。
2. `ingest_bars.py` 内部按 `_SUCCESS` 月分区幂等续跑；阶段记账（T4）以整链为单位，
   分钟链中途失败重跑会整链重放（convert 幂等性由 T10 真跑验证）。
3. 实时映射假设网盘 `A股分钟线/<年>/<月>` 结构与本地一致（设计 §2 实测）；若网盘出现
   额外层级（如 `<交易所>/<年>`），T10 遍历需复核。

---

## 修复轮 1（独立评审 PASS；Important I1，提交 `00ef05e`）

- I1（minutes convert 静默空转）：convert 缺参时缺省 `--mode validation --day 20260817`（写
  calib 目录、生产 `bars_1m` 不生成）。修复：命令补 `--mode production`（converter 按
  `_committed_ok` 幂等跳过已提交月，默认 2020-2026/1-12 全月扫描）。
- 测试：`test_minutes_convert_runs_production_mode`（先红：`--mode` 不在命令中；
  修复后绿并断言 `ingest_bars` 仍在其后）。
- 红→绿→突变→回归：见 `05-fix-round1.txt`；`04-mutation.txt` 与 `mutation.py` 已同步为
  9 条重跑（初轮 7 条为历史记录）；全量回归 **417 passed**。
