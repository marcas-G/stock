# R30 评估指标 v2 — 批 2：Task 13（D9 逐日口径）+ Task 15（D12 单一实现）

- 计划：`knowledge/design/platform/plans/2026-09-16-factorlab-eval-metrics-v2.md`（执行顺序第 2 项）
- 设计：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md` §2 D9/D11/D12
- 依赖批 1（commits `e530732` / `4df9506`：spread v2 + `version=2`）——本批不回退

## Task 13（D9 逐日评估口径）

### 交付

| 项 | 内容 |
|---|---|
| spec 字段 | `FactorSpec.evaluation_frequency: daily\|weekly = "daily"`（strict 解析；CLI `--eval-frequency` 覆盖） |
| 标签 | `DEFAULT_FORWARD_HORIZONS = (1, 5, 20)`（`forward_return_1d` 全链落盘）；面板列清单 `FORWARD_COLUMNS` 单点派生 |
| 评估装配 | `app.evaluate.evaluate_run(frequency=...)`：daily=面板原样（**禁止 align_weekly**，spy 测试锁定）/ weekly=旧口径零变更；`evaluation.frequency` 落盘（单输出顶层 / 多输出顶层；逐输出 bridge 亦带） |
| 日频统计 | `adapters.rust_ic.evaluate_factor_daily`（target 固定 `forward_return_1d`，D11）；`layered_backtest(periods_per_year=252)` 日频年化；`ic_series` 改名自 `weekly_ic`（周期无关，旧名保留别名） |
| 展示 | `run` stdout 带 `freq=`；`list/show` 按 `frequency` 渲染（历史产物无该键 ≡ weekly 旧口径）；Web 曲线改用 `ic_series` |
| 文档 | interface.md：spec 字段 / CLI / 评估桥接 / forward horizons=(1,5,20) / layered 年化 252 / weekly.parquet 语义 / Web 曲线；防漂移测试 `test_eval_docs.py::test_interface_daily_eval_frequency_contract` |

### 证据索引

| 文件 | 内容 | 命令（按原样执行） |
|---|---|---|
| `00-baseline-fullsuite.txt` | 改动前全量基线：3099 passed / 11 skipped / **1 failed**（该 failed 为改动中途 live-edit 产物：`test_regression_152` 子进程读磁盘已改代码；下方新全量为准） | `cd platform && .venv/bin/python -m pytest -q` |
| `01-task13-red.txt` | Step 1 先红：8 failed（字段缺失/日频分支缺失/1d 标签缺失） | `cd platform && .venv/bin/python -m pytest tests/test_eval_daily.py -q` |
| `02-task13-green-focused.txt` | 相关面转绿：50 passed（eval/layered/weekly/rust_ic） | 见文件首行 |
| `03-task13-fullsuite.txt` | **平台全量：3109 passed / 11 skipped / 0 failed（765s）**（基线 +10 = 新增 9 测试 + R22 weekly 回归转绿；R30 终验收记录为 3089/11，计划中「≥3151」为 R06 期旧树计数，本树无法复现——以 0 failed 与不回退为准） | `cd platform && .venv/bin/python -m pytest -q` |
| `04-task13-step4-runs.txt` | Step 4 真实运行（low_vol_20d / max_effect_20d_high，CH + 8GB 护栏）：命令、daily summary 关键值、panel sha256、CLI stdout | 文件内命令 |
| `05-task13-gates.txt` | `make gates`：唯一失败 = **G-INDEX 预存红**（挖矿在途 spec 未入索引，与批 1 基线一致）；G-IMPORTS/G-LINT(196)/G-VENV 等全绿 | `make gates` |
| `task13-daily-vs-weekly.md` / `.json` | 同数据 daily vs weekly 差异表（IC/t/换手/Sharpe/成本 7bp） | `platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-task13-15/compare_daily_weekly.py` |
| `compare_daily_weekly.py` | 差异表脚本（断言 API daily == CLI 落盘 summary 逐值一致） | 同上 |

### 口径要点（验收锚）

- daily：6 日 × 5 股合成面板手算 —— 每日 Spearman ρ = [1.0, 0.9, 0, −1.0, 0.9, −0.5]、
  mean=0.216667、t 用 `statistics` 独立聚合；`n_weeks`=6=交易日数（不是周数）。
- 每日调仓 layered：D1 成员隔日全换 → 换手 [0,1,0,1]；组收益 [0.035,0.055,0.055,0.035]、
  净值 cumprod、年化 = 0.0445×252（成本 7bp 后）；同面板按 52 年化得到不同值（证明分支传入）。
- **禁止行为断言**：daily 路径 `align_weekly` 零调用（app/bridge 双点 spy）；
  weekly 正向对照证明 spy 非死。
- 真实对照：daily IC t 显著更高（7.01 vs 4.10）、年换手更高（35.5 vs 21.9）、
  7bp 年化成本拖累更高（0.0248 vs 0.0153）——与 D9「日频换手/成本显著更高」预期一致。

## Task 15（D12 单一实现 + 去 Rust 叙事）

（本批后继步骤；裁决/迁移/对拍证据见 `task15-*` 文件与 README 后续追加节）
