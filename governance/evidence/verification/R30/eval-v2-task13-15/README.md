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

### 交付

| 项 | 内容 |
|---|---|
| 裁决 | **A**：kernel 并入 `core/eval/kernel.py`；删 `platform/kernels/quant_core`（pyproject/壳/独立 dist）；`adapters/rust_ic.py` → `adapters/ic_kernel.py`；P-6 端口文档保留——理由与实现前影响核查见 `task15-decision.md` |
| 迁移 | `git mv` 内核文件 + 仅 docstring/注释去叙事（非 docstring AST 全等）；`adapters/ic_kernel.py` 调用 `core.eval.kernel`；`platform/pyproject.toml` 删 `quant-core` 依赖与 `[tool.uv.sources]`；`uv.lock` 同步删除（`uv lock --check` 通过）；venv `uv pip uninstall quant-core` |
| 去叙事 | pyproject/内核/端口/评估模块注释删除"Rust 内核未来替换"；`reinstall_editable.sh` 单包化；`gates.sh` G-VENV 改为**反向断言**（`import quant_core` 必须失败）；README/CLAUDE.md/AGENTS(平台)/workspace 文档/playbook 同步 |
| 测试 | `test_quant_core_shim.py` → `test_eval_kernel.py`（契约断言保留）；`test_eval_rust_ic.py` → `test_eval_ic_kernel.py`；`RustICKernel` → `IcKernel`；全仓 import 更新（89 处引用清零，仅存历史说明 1 处） |
| 门兼容 | `check_reviews.py` MAP_PREFIX 登记 R30 Task 15 重命名（历史 finding 行 append-only 不追改；precedent = Plan P T11 退役映射） |

### 证据索引

| 文件 | 内容 |
|---|---|
| `task15-decision.md` | 方向裁决 + 理由 + 实现前 Web/分层影响核查（零影响） |
| `task15-decision-grep.txt` | 迁移前（f453c5f）内核位置引用快照 + Web import 面 |
| `task15-parity-code-identity.txt` | **纯搬移证明**：旧/新内核去 docstring AST 全等 |
| `task15-parity-old.json` / `task15-parity-new.json` | 迁移前/后内核在 R08 三面板（low_vol_20d/max_effect_20d_high/intraday_high_time，sha256 冻结）全字段结果 |
| `task15-parity-nondeterminism.txt` | 同实现连续调用最后一两位不同（polars 并行归约 ~1e-15）——对拍容差依据 |
| `kernel_parity.py` | 对拍脚本（`--impl quant_core|factorlab` 采集；`--compare` 结构全等 + rel_tol=1e-9） |
| `06-task15-gates.txt` | `make gates`：唯一失败 = 预存 G-INDEX；G-VENV 反向断言绿（quant_core 已删除）、G-REVIEWS 绿、G-IMPORTS 431 文件绿 |
| `07-task15-fullsuite.txt` | **平台全量（迁移后）：3109 passed / 11 skipped / 0 failed（759s）**——与 Task 13 后逐数一致（含 R22 6 代表 spec weekly 值级回归、test_web/test_e2e_web、测试数 3120 vs 基线 3111 = 新增 9） |
| `08-task15-tools.txt` | `make test-research`：platform/tools **530 passed**；research/tools 2 failed = **预存挖矿在途**（新增 spec 未归档/未入索引：`knowledge/dossiers/factors/intraday/am_pm_vol.md` 缺失、索引 196 spec vs 渲染 177）——与 G-INDEX 同根因，属挖矿收尾，非本批引入（`??` 在途文件自开工前即存在） |

### 对拍结论

- 结构全等 + 数值 `max_rel ≈ 9.1e-15`（容差 1e-9；纯搬移 + polars 并行归约噪声已单独证因）；
- R08 复算三面板 `ic.mean` 与 R08/R22 已发数值逐值一致（low_vol_20d `0.07007485259689888` 等）。
