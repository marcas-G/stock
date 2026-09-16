# R21 TOOLS-B 修复证据（R01-TOOLS-I8 / I9 / I10）

修复者：TOOLS-B（研究侧工具：universe_stages layer1/3 段 + converters）。
所有 red 输出均为"最终测试 + 修复前实现"复跑（I8 CLI/I9 用 `git checkout --` 临时还原后运行，
随后恢复修复版）；green 输出为修复版最终复跑。每个文件头部含命令与 exit code。

## R01-TOOLS-I8：layer3 非当日事件编造窗口 / 异常静默吞

- 根因：`_worker` 不校验事件日期 vs 请求日（别日事件产出 NaN 伪窗口），
  `except Exception: r={}` 把特征异常降级成空 dict；`WindTickStore.read_trade_csv`
  用请求日重标任意 tick 目录的时间戳。
- 改动：`universe_stages/scripts/run_layer3_tick.py`、`readers/tick.py`、
  `universe_paths.py`（`ticks_root(day)` 按日拼路径）。
- 测试：`universe_stages/tests/test_layer3_tick_guard.py`
  - `test_non_trade_date_event_is_skipped_with_reason`
  - `test_feature_exception_is_marked_not_swallowed`
  - `test_invalid_event_time_is_skipped_with_reason`
  - `test_reader_refuses_to_relabel_other_day`
  - `test_cli_records_skipped_events_and_writes_only_matching_rows`
- 证据：`I8-red.txt`（最终测试 5 条全失败，含 CLI；对 HEAD 实现复跑）、
  `I8-cli-red.txt`（CLI 旧行为明细：产物 2 行，其中非当日事件一行为全 NaN 伪窗口）、
  `I8-green.txt`（5 passed）。

## R01-TOOLS-I9：layer1 端到端不可跑 / MIGRATION_GAP 措辞过强

- 根因：fundamentals PIT 缺源、`20260817.7z` 未解包，脚本在深层 reader 抛裸
  `FileNotFoundError`；`MIGRATION_GAP.md` 标题 "CLOSED" 只覆盖公式，不说明可执行状态。
- 改动：`universe_paths.py`（`MissingInput` + `preflight_layer1/2/3`）、
  `scripts/run_layer1.py`、`scripts/run_layer2_sas.py`、`scripts/run_layer3_tick.py`、
  `MIGRATION_GAP.md`（Executable status 表 + 定位修正）、`README.md`。
- 测试：`universe_stages/tests/test_preflight.py`（8 条：缺源点名+获取路径、正路径、
  layer1/layer2 CLI 干净报错、合成小样本 layer1 端到端真跑出 Top300）。
- 证据：`I9-red.txt`（修复前 7 failed / 1 passed）、`I9-green.txt`（8 passed）。

## R01-TOOLS-I10：`--only-day` 原子替换整月产物

- 根因：单日模式与全量共用输出根，`MonthWriter` 以单日内容覆盖
  `tick_fact/<table>/year=/month=/part-000.parquet`，manifest 也一并覆盖。
- 改动：`converters/convert_tick_to_parquet.py`：`--only-day` 默认落
  `data/calib/tick_fact_validation/`（`VALIDATION_ROOT`），新增 `--out-root`；
  显式把输出指到生产根 → 拒绝执行（exit != 0，零写入）；manifest/summary 带
  `mode`、`out_root`。`converters/README.md` 同步。
- 测试：`converters/tests/test_convert_tick_e2e.py`
  - `test_only_day_writes_to_validation_root_and_leaves_month_untouched`
  - `test_only_day_refuses_production_out_root`
  - 既有两条 e2e 改为全量模式（不再拿 `--only-day` 当生产入口）。
- 证据：`I10-red.txt`（月产物被 `PAR1...` 覆盖）、`I10-green.txt`（4 passed）。

## 聚合验证

- `T1-after.txt`：`platform/.venv/bin/python -m pytest -q research/tools/universe_stages/tests research/tools/converters/tests`
  → 32 passed。
- `T2-after.txt`：`emb/bin/python -m pytest research/tools -q` → 268 passed / 10 skipped。
- `T2-targeted.txt`：两目录 `-rs` → 28 passed / 2 skipped（1 为 `run_layer2` CLI 需 duckdb，
  1 为既有 daily_fact importorskip），skip 均可解释。
- `gates-after.txt`：`bash scripts/gates.sh` → 结构门全绿（G-TOPO/G-PART/G-MARK 接线
  ENFORCED 0 违规；早前一次复跑出现的 G-READ 失败属 TOOLS-A 在途未提交文件，最终复跑已消失）。
- 两个解释器（T1/T2）下 I8 的 5 条 + I9 的 7 条测试均真跑（测试内显式
  `_env.ensure_platform()` + 子进程 `PYTHONPATH=platform/src`），不依赖测试顺序泄漏；
  仅 `run_layer2` CLI 测试在无 duckdb 的解释器下显式 skip（T1 真跑）。

## 未解决 / 建议文档修改

- `scripts/validate_layer1_parity.py`、`scripts/tail_capture_audit.py` 仍直读
  golden/daily（未接 preflight）；两者所需前置与 layer1/2 不完全重合，
  本轮未动，建议后续按"该脚本真实所需"补最小 preflight。
- 真实 layer1/layer3 端到端仍需数据侧补齐（pending #3 解包、#4 fundamentals 源）；
  本包已把状态写实到 `MIGRATION_GAP.md`，未伪称可跑。
- `docs/reviews/findings.md` 按边界未改（仅只读引用）。
