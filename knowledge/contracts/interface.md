# FactorLab 接口文档（M1–M8 + 读路径双后端）

> **数据面现状（2026-09-17，Plan P T11 起）**：生产数据源 = ClickHouse
> （`FACTORLAB_DATA_BACKEND=ch`），更新链 = 夸克网盘 → `make data-update`
> （`platform/tools/pan_update/`）→ 转换/灌入 CH，见 §8「数据平台（网盘更新链）」。
> duckdb 平台库写路径（teajoin 源）已退役删除，duckdb 后端仅作测试/历史只读；
> 文中 M3b/M4a 等历史小节的「平台库唯一数据源」为当时口径。

本文件描述已交付的 CLI、Spec、因子脚本和 Python API（M1–M8，叠加
duckdb|ch 读路径双后端与 bars_1m/tick 读接口，见 §4 读路径双后端小节）。
实现与设计文档冲突时以 `knowledge/design/platform/specs/2026-08-15-factor-dsl-platform-design.md`
为准；读路径双后端与 intraday 接口设计见
`knowledge/design/platform/specs/2026-09-06-factorlab-dual-backend-read-path-design.md`
（配套计划 `knowledge/design/platform/plans/2026-09-06-factorlab-dual-backend-read-path.md`）；
日频收口（WS1-WS7：spec.target 接线 / corr 无有效周 / 多输出逐输出评估 / 停牌冻结 /
CA Gate / 真实信号链 e2e）见
`knowledge/design/platform/specs/2026-09-07-factorlab-daily-closeout-design.md`；
bars_1m 漏斗机制（spec.interface=bars_1m / im_*、day_* 算子族 / 折日评估复用 /
load_bars_1m_codes，2026-09-08 开工）见
`knowledge/design/platform/specs/2026-09-08-factorlab-1m-funnel-design.md`（配套计划
`knowledge/design/platform/plans/2026-09-08-factorlab-1m-funnel.md`）。

## 0. M5 汇总：Web 可视化

M5 在 M4b 结果落盘（summary.json/weekly.parquet）之上补齐浏览器可视化闭环：

- **`factorlab serve`**（`--port`/`--host`，默认 `127.0.0.1:8000`）：只读启动
  Web 服务，可视化 `settings.results_dir`（默认 `$QUANTRESEARCH_ROOT/results/platform/`（R37 归位；无产物区环境回退仓内 `runs/platform/`，本机为兼容软链）；`FACTORLAB_RESULTS_DIR` 可覆盖）下
  已保存因子（见 §1 与 §4 `factorlab.surfaces.web`）。
- **web 包**（`factorlab.surfaces.web`）：`app.create_app(results_dir)` 构建 FastAPI 只读
  应用——列表 `/` + 详情 `/factor/<name>`，Jinja2 模板 + Plotly 图表内嵌；
  `charts` 构造 IC 曲线/十分位柱状/分层净值 figure JSON；缺失/损坏 summary 与
  缺 evaluation 字段的因子降级展示不崩溃（见 §4）。
- **`factorlab.core.eval.ic_series.ic_series`**（旧名 `weekly_ic` 保留为别名）：
  逐期 RankIC 序列（Spearman 秩相关，与 `core.eval.kernel` 同源定义；signal/target null
  过滤、有效股票 < 3 的期 ic = null）——daily 面板→逐日曲线、weekly 对齐面板→
  逐周曲线；详情页 IC 曲线数据源（见 §4）。
- 集成测试 `tests/test_e2e_web.py`：真实 results 目录（main 工作树，3 个因子）
  冒烟——列表含因子名、详情含图表数据、旧因子降级、缺失 404（见 §7 测试）。

## 0. M4b 汇总：分层回测与因子清单

M4b 在 M4a 评估链路之上补齐单因子评估闭环：

- **分层回测**（`factorlab.core.eval.layered.layered_backtest`，见 §4）：评估面板按
  signal 分档（默认十分位）等权组合累计净值 + long-short + 摘要指标；`factorlab run`
  默认产出并写入 `summary.json.evaluation.layered_backtest`（`--no-backtest` 关闭，
  `--groups N` 调整档数）。
- **CLI `list` / `show`**：已保存因子清单与单因子完整摘要（见 §1）。
- **M4a 遗留接线**：`pit_qfq` 消费（`run_factor` 传 `asof=spec.date.end`）、
  评估输入面板落盘为 `weekly.parquet`（**文件名保留历史布局**；D9 起 daily 模式
  内容为日频面板、weekly 模式为周频对齐面板）、`results_dir` 锚定
  （`--output-dir` 缺省 `<results_dir>/<name>/`；`results_dir` 默认 `$QUANTRESEARCH_ROOT/results/platform/`（R37 归位；无产物区环境回退仓内 `runs/platform/`，本机为兼容软链））。
- 回测期数口径与评估期数一致：signal/forward 全 null 的期（头部窗口
  未满/尾部无未来收益）不计入，`bt["periods"] == evaluation["n_weeks"]`（实测）。

## 0.1 M4a 汇总：端到端评估链路

M4a 打通「平台库数据 → 因子计算 → 复权视图 → 周频评估」端到端链路：

- **数据源切换**：平台库 `data/factorlab.duckdb`（`settings.platform_db`，
  `FACTORLAB_PLATFORM_DB` 覆盖）为**唯一数据源**，项目自包含；外部只读库路径
  已从代码彻底移除。`load_daily` 加载平台库 `daily`（`trade_date/ts_code` →
  `date/code` 映射、`adj_factor` 恒 join，见 §4）。
- **`factorlab run <spec.yaml>`**：计算 + 评估端到端命令（见 §1）：run_factor 日频
  面板 → **评估频率分支**（D9：daily 默认=逐日截面直接评估、**不做周频对齐**；
  weekly=ISO 周对齐对照）→ `core.eval.kernel` 评估 → `summary.json` 追加 `evaluation`
  字段（含 `frequency`），另落盘 `weekly.parquet`（评估输入面板）。
- **复权视图（adjustment）**：spec `adjustment` 字段（`raw|qfq|hfq|pit_qfq`，
  默认 `qfq`）决定因子计算所用价格口径（`view_prices`，见 §4）；前向收益恒用
  **total_return 口径**（raw close×adj，先于复权视图计算、避免二次复权）。
- **eval 包**（`factorlab.core.eval`）：`alignment.align_weekly`（ISO 周最后交易日
  对齐，仅 weekly 模式调用）、`metrics.coverage_report`（覆盖率；valid = signal
  非 null 且有限，`target_col` 给定时 target 同样要求——调用方在过滤前计算，
  R03-I2）；`ic_kernel.evaluate_factor_weekly` / `evaluate_factor_daily`（内核
  频率分支评估桥接，见 §4）在 `factorlab.adapters.ic_kernel`（R8 分层：纯计算
  在 core、I/O 与内核桥接在 adapters）。
- **`default_universe` 接线**：`factorlab run` 缺省 `--universe` 时回落
  `settings.default_universe`（`FACTORLAB_DEFAULT_UNIVERSE`），未配置再用 spec
  内联 universe（见 §4 `resolve_codes`）。

## 1. CLI

安装 editable 包后，命令入口为 `factorlab`。

| 命令 | 说明 |
|------|------|
| `factorlab version` | 打印包版本 |
| `factorlab lint <spec.yaml>` | 校验 Spec、AST 白名单与引擎同序语义门（未知算子/负位移/未来下标，含池公式），失败时以非 0 退出 |
| `factorlab run <spec.yaml> [--universe U] [--max-memory M] [--output-dir DIR] [--no-float32] [--backtest/--no-backtest] [--groups N] [--eval-frequency daily\|weekly] [--set k=v ...] [--chunk-days N] [--warmup-days N] [--chunk-workers N] [--profile] [--no-read-cache]` | 计算因子并评估（daily 逐日默认 / weekly 对照）+ 分层回测（默认），落盘 `$QUANTRESEARCH_ROOT/results/platform/<name>/`（缺省 results_dir（R37 归位）；`--set` 生成 `runs/platform/<name>_<k><v>.../` 参数变体；`--chunk-days` 日期分块，见 §运行-分块计算；`--profile` 分段计时，见下） |
| `factorlab list` | 列出已保存因子与最近运行摘要（扫描 `results_dir/*/summary.json`，按运行时间倒序） |
| `factorlab show <name>` | 查看单因子完整摘要（spec 原文/评估/分层回测） |
| `factorlab corr <name1> <name2> ... [--against reference\|all\|<names>]` | 因子两两相关性（≥2 个）：周度横截面秩相关均值 + 全局 Pearson；任一因子无 results 报错（数据源 `<results_dir>/<name>/panel.parquet`（默认 `runs/platform/`） 的 signal，按 date+code inner join；join 后超 2000 万行每周降采样 5000 只）。`--against reference`（D10）= 与参考库 `$QUANTRESEARCH_ROOT/factor/_reference.yaml` daily 组成员的并集矩阵（names 可省略=库内自相关矩阵；只读库清单，不扫全库、不跨 scales）；`--against all`= 显式扫全库；`--against a,b`= 显式名单 |
| `factorlab svd [name1 ...] [--weeks 15] [--all]` | 因子库 SVD 分解：奇异值谱 + 主成分载荷（因子结构/有效维度分析）；缺省 names = **参考库 daily 组**（D10，读 `_reference.yaml`）；`--all` = 全部有 panel 因子（排除验证目录，D10 前旧默认，显式 opt-in）；抽样 weeks 个交易周（concat+pivot 单次操作，规避多 join 段错误） |
| `factorlab resic <name1> <name2> ... [--target 名] [--min-stocks 30] [--against reference\|all\|<names>]` | 横截面联合诊断：组内互评（默认，≥2 因子）或 `--target` 显式候选（可不在 names 中，基准应排除 target）。输出整组联合回归 R²（fwd ~ 整组逐周 OLS 均值）与每因子正交化残差 IC（resIC = 候选对基准逐周 OLS 残差 vs fwd 的周频 rankIC 均值/t 值 + 被基准解释 R²）。数据源 = results 多 run 单输出 panel 按周频对齐汇聚；每周样本 < max(min_stocks, 基准数+2) 剔除；错误路径 Exit 1（含"无结果"/"公共周"/多输出 panel 文案）。`--against`（D10）= 库外候选对参考库的**增量信息评估**（候选取 names 或 `--target`；输出 `corr_max/corr_mean/r2_lib/resIC/retention/verdict`，见 §D10）——候选 ∈ 基准报错。近共线因子建议先跑 corr/svd |
| `factorlab ref list [--scales daily\|minute]` | 参考库成员清单（D10；读 `$QUANTRESEARCH_ROOT/factor/_reference.yaml`）：按 scales 组打印 name/style/加入日期/entry corr_max/resic_t/理由；文件缺失/格式错 Exit 1 |
| `factorlab op list [--catalog]` | 列出已注册算子；`--catalog` 列**分类表全集**（含未注册库函数：name/partition/window/source/returns） |
| `factorlab op doc <name>` | 查看算子名称、类别、版本与 docstring；未注册但在分类表 → 回退打印分类元数据（partition/window/source/returns） |
| `factorlab op add <plugin.py> [--force]` | 校验并注册用户插件；同名冲突（含内建算子名）在插件 import/副作用执行前拒绝，需 `--force` |
| `factorlab op remove <name>` | 禁用用户插件，保留已计算历史结果 |
| `factorlab serve [--port 8000] [--host 127.0.0.1]` | 启动只读 Web 可视化（浏览器查看已保存因子列表与图表，扫描 `results_dir`） |
| `factorlab catalog dump [--out FILE]` | 列/算子活文档的机器可读 JSON（schema 元数据同源生成，供写因子的 AI 开写前阅读；缺省打 stdout） |
| `factorlab catalog docs [--out FILE]` | 目录正文 markdown（与 `catalog.md` 同源生成，活文档防陈旧） |

### `factorlab run <spec.yaml>`

计算因子并评估的端到端命令（平台库数据源）：

- 数据源：`settings.platform_db`（`data/factorlab.duckdb`，可用 `FACTORLAB_PLATFORM_DB`
  环境变量覆盖）。
- `--universe U`：覆盖 spec 的 universe（6 位代码或 universe 引用名/文件路径）；
  缺省时回落 `settings.default_universe`（`FACTORLAB_DEFAULT_UNIVERSE`），
  未配置则用 spec 内联 universe。
- `--max-memory M`：运行期 DuckDB `memory_limit`（默认 `4GB`；**仅** DuckDB
  连接——进程级护栏见下方「进程内存护栏」`FACTORLAB_MAX_MEMORY`）。
- `--output-dir DIR`：落盘目录，默认 `settings.results_dir / <spec.name>`
  （默认 `$QUANTRESEARCH_ROOT/results/platform/`（R37 归位；无产物区环境回退仓内 `runs/platform/`，本机为兼容软链）——从包位置派生、与 cwd 无关；`FACTORLAB_RESULTS_DIR` 可覆盖，
  相对 cwd 解释——`list`/`show` 扫描同一目录）。
- `--no-float32`：关闭 float32 内存护栏。
- `--backtest/--no-backtest`：默认产出分层回测并写入 evaluation；`--no-backtest`
  关闭（快速评估，评估面板落盘不受影响）。
- `--groups N`：分层档数（默认 10，`N >= 2`）。
- `--eval-frequency daily|weekly`：覆盖 spec 的 `evaluation_frequency`（D9，
  R30 Task 13）——daily 默认逐日口径、weekly 旧口径对照；缺省取 spec 值。
- `--set k=v`（可多次）：覆盖 spec 的 `params`（见 §2），生成**参数变体**——
  变体名 `<spec.name>_<k><v>...`（如 `vol_run_energy_win100_gain1.5`），results
  独立目录，与默认变体（`runs/platform/<name>/`）并存不覆盖。值类型解析
  int → float → bool（`true/false`）→ str；格式错误（缺 `=` 或空值）以非 0
  退出提示。`--output-dir` 显式给出时优先于变体目录。
- `--chunk-days N`：日期分块（交易日/块，`N >= 1`）。缺省：**分钟链
  （`interface: bars_1m`）按 20 交易日/块自动分块**（R04-P1——非分块全市场峰值
  RSS 实测 34.95GB / 16GB 机 OOM，20 日/块 6.95GB 且不变慢；需整段可显式传大于
  窗口长度的 N——显式极大块按块峰值估算告警/拒绝，见「进程内存护栏」）；日频
  缺省单块整段跑。长样本
  （2015+ 全市场）超过 16GB 内存护栏时使用，语义保证与整段跑逐 cell 一致
  （见下方"分块计算"）。**累计算子（`ts_cum_sum`/`ts_cum_max`/`ts_cum_min`/
  `ts_cum_prod` 及 `vwap` 宏展开产物）与分块不兼容**——分块下 fail fast
  （R01-ENG-I1，见下方已知限制）。
- `--warmup-days N`：TS 窗口预热天数（`N >= 0`；缺省按公式自动提取窗口最大值
  + 20 安全垫）。
- `--profile`（R09-M3）：分段计时开关（env `FACTORLAB_PROFILE=1` 等效；缺省
  关闭 = 零行为变化）。开启后 stderr 输出各段墙钟/峰值 RSS 人读摘要，并把
  `summary.json` 的 `runtime.profile`（append-only 新键）更新为同一报告：
  `{"version": 1, "clock": "wall_ms", "rss_unit": "mb", "total_wall_ms": N,
  "segments": {"<段>": {"wall_ms": 正整数, "rss_peak_mb": N, "rss_delta_mb": N,
  "calls": N}}}`。段：`read_data`（label 前信号侧；分钟链含折日装载/拼装）、
  `bars_read`（R31 分钟链 bars_1m 批读总段——直读或经缓存；跨 chunk 累加，
  `read_data` 子段；日频无）、`fold`（分钟折日
  `compute_minute_factor_panel`，`read_data` 子段；日频无）、
  `cache_lookup`/`cache_hit`/`cache_miss`/`cache_fallback`（R31 bars 读缓存
  子段：指纹+manifest 探测 / 命中读盘 / 未命中直读+落缓存 / 坏条目回退直读；
  缓存关闭时无）、
  `label`、`evaluate`、`layered_backtest`（`--no-backtest` 时无）、`persist`
  （artifact/panel/weekly 落盘；summary.json 自身重写不计入）。实现
  `app/profile.py`（段边界采样 + 20Hz 采样线程，峰值按段窗口归属）。
- `--chunk-workers N`（R09-PERF-P4）：分钟链 chunk 并行度（`N >= 1`，默认 1 =
  现行为顺序执行；仅 `interface: bars_1m` 生效，日频链忽略）。`N >= 2` 时按
  chunk 并行「注入/批读/成员过滤 + 折日」，按 chunk 顺序合并——分钟窗不跨日，
  数值与 N=1 逐 cell 严格一致（真 CH 4 因子 bit-exact 对拍：`after-p4/
  chunk_workers_parity.json`）。**并发前内存预算门（R09 复评 F1：按生效
  chunk_days 校准）**：单 chunk 估值 = `3.6GB × max(chunk_days, 10)/10`
  （10 日/块 = P2/P3 同窗实测每 chunk/进程峰值上界 RSS 3.0–3.6GB；20 日/块
  R04-P1 实测 6.95GB 与 7.2GB 外推同量级；下限 10 日 = 校准块长，更小块固定
  开销不降），估算 `N × 单 chunk` 超过 `FACTORLAB_MAX_MEMORY` → 打开 DB 前
  `MemoryLimitExceeded` 干净拒绝（示例：8GB 护栏下 **chunk_days≤10** 时 N=3
  拒绝、N=2 放行——实测 N=2 峰值 RSS ≤6.2GB；默认 `chunk_days=20` 时 N=2
  估算 14.4GB 同样拒绝——实测放行后峰值 10.37GB、看门狗 8.3GB 中止）；
  未设预算且估算 >8GB 时响亮告警（N 为显式 opt-in，宿主 memguard 兜底）。任一 chunk 失败 →
  整体失败（取消未启动任务、异常原样传播、无半成品）；R03-I6 覆盖审计（drop
  剔除集跨块累计）与看门狗 chunk 边界协作检查语义不变。**barrier 测试证明
  N≥2 真的并发折日**（非顺序伪并行）。
- **分钟面分派（W5）**：`interface: bars_1m` 的 spec（字段与口径见 §2）由 run
  分派 `run_factor_minute`（engine.minute，B7.1）——折日面板与日频同列契约，
  下方评估/分层回测同一路径零改动（`weekly.parquet` + `evaluation`
  照常落盘）。分钟面**仅 ClickHouse 后端**：设置 `FACTORLAB_DATA_BACKEND=ch`
  （并指向含 bars_1m 的 `FACTORLAB_CH_DATABASE`）。`--warmup-days` 对分钟链
  忽略（日内窗无预热；注入列 adv20 左窗由引擎独立预取 20 交易日）；`--chunk-days`
   缺省按 20 交易日/块自动分块（R04-P1，内存语义见上一条），分块结果 == 整段
   严格相等。
- **bars_1m 批读 CH 查询调优（R09-PERF-P4）**：分钟批读查询设置可经 env
  `FACTORLAB_CH_MAX_THREADS` / `FACTORLAB_CH_MAX_BLOCK_SIZE`（正整数）单查询
  注入 `max_threads`/`max_block_size`（`adapters/ch_read.bars_read_settings`）；
  未设 = 服务器默认（零行为变化），非法值 fail loud。spike（真 CH 同窗单查询
  10 变体，`governance/evidence/verification/R31/minute-perf/spike/`）显示两
  旋钮相对默认在噪声带（3.1–3.6s），平台不设默认值；全局 `join_use_nulls=1`
  恒在（调用方不可覆盖）。并行读（`--chunk-workers`）要求 CH 客户端线程级
  单例（clickhouse-connect 同 session 并发查询被禁）。
- **bars_1m 批读 Arrow 流读取（R31）**：分钟批读改
  `clickhouse-connect.query_arrow_stream` + 逐批 `pl.from_arrow` +
  `pl.concat(rechunk=False)`（`adapters/ch_read.query_arrow_stream_df`；
  `load_bars_1m_codes` 经句柄能力探测优先走流，测试桩/无该能力句柄回退
  `query_df`）。dtype（UInt16/Date/DateTime64(3)/Float32/Float64 等）、null
  形态、行序与 `query_df` 逐 bit 一致；真 CH 同窗实测 3.8–4.0s vs
  `query_arrow` 整表 4.9–7.7s（证据 `R31/minute-perf/read-cache/parity.json`）。
  空结果流（0 个 RecordBatch）回退 `query_arrow` 取投影 schema（空窗仍返回同
  投影空 frame）；部分批次失败异常原样抛出（不返回半量）。
- **bars_1m 读磁盘缓存（R31；默认开，仅分钟链）**：`load_bars_1m_codes`
  按 chunk 缓存**解码后** frame（datetime naive ms/code 6 位，与直读出口逐
  bit 一致；行序 = SQL `ORDER BY code, datetime` 原序）。缓存键 =
  `sha256(v1|codes 排序集|date_start|date_end|columns 排序集|源指纹)`；源指纹 =
  `system.parts`（当前库 `table='bars_1m' AND active` 的 Σrows +
  max(modification_time)）拼接 `max(datetime)` 再 sha256——**历史回填/月分区
  替换/新数据都会改变指纹 → 旧键不再命中**（进程内按 300s memo，避免逐 chunk
  重查 max(datetime)）。目录 `~/.cache/factorlab/bars_1m/`
  （`FACTORLAB_READ_CACHE_DIR` 覆盖）；`manifest.json` 单点登记
  key/file/sha256/size/fingerprint/created_at/last_access/hits；上限
  `FACTORLAB_READ_CACHE_MAX_GB`（默认 30）按 `last_access` **LRU 淘汰**，期限
  `FACTORLAB_READ_CACHE_TTL_DAYS`（默认 7）按 `created_at` 过期清理。数据文件
  为 Arrow IPC（lz4），经平台原子写单点（同目录 tmp + fsync + `os.replace` +
  目录 fsync，失败不留目标/不更新 manifest）；读命中先 size+sha256 校验，
  损坏/半成品/文件缺失/元数据损坏 → **回退直读**（清坏条目，不 fail）。
  开关：`FACTORLAB_READ_CACHE=0`（默认开）或 CLI `--no-read-cache`
  （`RunContext.read_cache=False`）完全关闭（不建目录、不查指纹）；命中/
  未命中/回退写 stderr（run.log，前缀 `[read-cache]`）并计入 `--profile`
  段（见上；缓存关闭时无 cache 段）。并发（`--chunk-workers`）下同键双读
  双写语义安全（原子替换幂等）。实现
  `adapters/read/chunk_cache.py`；两连跑证据
  `governance/evidence/verification/R31/minute-perf/read-cache/`（读段
  `bars_read` 12.8→7.0s，`read_data` 40.7→31.4s，bit-exact max|Δ|=0）。
- 落盘：`panel.parquet`（run_factor 日频面板）、`weekly.parquet`（评估输入面板——
  daily 模式为日频面板 / weekly 模式为周频对齐面板；文件名保留历史布局）、
  `summary.json`（run_factor 摘要 + `evaluation` 字段——频率分支评估
  （`frequency`/`target`）+ `layered_backtest` 分层回测，CLI 层追加后重写）。
- 错误路径以非 0 退出并打印原因：spec 不存在、平台库缺失、universe 无有效股票、
  `--set` 格式错误、公式/process 校验失败等。

示例：

```bash
factorlab run factor/demo.yaml --universe 600519 --output-dir out/run1
factorlab run factor/demo.yaml --groups 5          # 5 档分层回测
factorlab run factor/demo.yaml --no-backtest       # 仅评估，不产分层回测
factorlab run factor/demo.yaml --set win=100       # 参数变体（runs/platform/demo_win100/）
factorlab run factor/demo.yaml --set win=100 --set gain=1.5   # 多变体参数
factorlab run factor/crash_bottom_leader_timed.yaml --chunk-days 500   # 2015-2026 分块计算
```

### 分块计算（`--chunk-days`）

长样本（2015+）全市场面板的 date×code 全网格超过 16GB 无页面文件内存护栏时
（实测 ~850 交易日可跑、再长段错误），按交易日分块计算：日历切成
`--chunk-days` 交易日/块，每块独立跑完整流水线（加载→停牌补全→前向收益→
复权视图→因子→process），块间无数据依赖，最后拼接。

**语义保证**（与单块整段跑逐 cell 一致，回归测试验证）：

- TS 窗口（`ts_*`/`ta_*`）：每块带 warmup 重叠段（自动提取公式窗口最大值
  + 20 天安全垫，覆盖 `ts_delay` 等偏移；纯横截面公式 warmup=0），窗口历史完整；
- CS 算子（`cs_rank`/process 链的 winsorize/standardize 等 per-date 横截面）：
  块内每日期全市场股票完整，结果与整段跑一致；
- qfq 复权：块内 `adj_factor` 按全局基准（样本末每代码最新 adj）归一，绝对水平
  类因子（直接用 close 值的公式）跨块一致；hfq 无基准无需处理；**pit_qfq 的
  asof base 全局装载**（`argMax(adj_factor, trade_date) WHERE trade_date <= asof`
  一次查库，FULL/CHUNK 共用同一 base 列——R01-DATA-I3 修复，此前按块内帧重算会
  分块漂移）。

**已知限制**：

- 每块块尾的 `forward_return_h` 为 null（块尾无未来数据；单块跑只有样本末如此），
  周频评估时该周跳过，块大小 500 天时损失 <1%；
- **累计算子**（`ts_cum_sum`/`ts_cum_max`/`ts_cum_min`/`ts_cum_prod`，含 `vwap`
  宏展开产物）依赖块内全历史，分块每块重置 → 与整段跑逐 cell 不一致：引擎在
  `--chunk-days` 下 **fail fast**（`ValueError`，指引去掉 `--chunk-days` 或改用
  `ts_sum`/`ts_mean` 窗口算子）——R01-ENG-I1；
- process 链的 `fillna(method="forward")` 在块首重新填充，块边界前几行与单块跑
  略异（低频使用）；
- 块大小 + warmup 应控制在约 850 交易日以内（单块内存 ≈ 已验证可跑的
  3.5 年量级）。

### 进程内存护栏（R05-C1，2026-09-16 P0 事故后）

背景：3 年全市场分钟链运行（`--chunk-days 20`）叠加 LLM 服务（21GB
llama-server）与多 agent 会话触发主机内存耗尽、SSH 卡死、ClickHouse 一度无
响应（进程 D 状态零输出；事故记录 `governance/evidence/reviews/r05-usage-2026-09-16/report.md`）。
`factorlab run`（日频 `run_factor` 与分钟链 `run_factor_minute` 两条路径）内置
进程内存护栏：

- **开关**：`FACTORLAB_MAX_MEMORY`（进程 RSS 上限）与
  `FACTORLAB_MIN_AVAILABLE_MEMORY`（系统可用内存下限）——支持
  `"8GB"`/`"512MB"`/纯字节数（1024 进制）。`factorlab run` CLI 在 **env 未设时
  默认化**（R30）：`FACTORLAB_MIN_AVAILABLE_MEMORY=6GB` 安全预检 +
  `FACTORLAB_MAX_MEMORY=min(16GB, 12% 物理内存)`（本机 125GB → ≈15GB）；显式
  `off`/`none` 可逐项关闭。默认化只在 CLI 进程内生效（run 结束即原样恢复），
  **API 直调 `run_factor`/`run_factor_minute` 且 env 未设仍 = 不启用（零行为
  变化，避免误杀 CI/小 run）**。注意与 `--max-memory`（DuckDB 连接上限）是两个东西。
- **软看门狗**：daemon 线程约 5s 采样进程 RSS 与系统可用内存；run 链在
  **chunk 边界与落盘前**协作检查——超限抛 `MemoryLimitExceeded`
  （`ValueError` 子类，CLI 干净 exit 1；文案含当前 RSS/可用内存、阈值、建议：
  减小 `--chunk-days`、调整/设置 `FACTORLAB_MAX_MEMORY`、避免与 LLM/多 agent
  并发重任务）。**干净中止**：落盘前中止 → 无任何产物；写盘中断 → R02-I8
  summary tombstone 失效协议保证 loader 拒绝不完整目录（无半成品可加载）。
- **硬上限兜底**：显式设置 `FACTORLAB_MAX_MEMORY` 时，CLI 入口（`factorlab
  run`）在跑之前落 POSIX `resource.setrlimit(RLIMIT_AS)` 硬护栏，公式
  `max(3×RSS 上限, 当前 VA + RSS 上限 + 12GB + 64MB×核数)`。为什么远高于 RSS
  阈值：polars/glibc arena 的**虚拟地址空间**预留远大于 RSS（本机实测小 run
  VmSize 13.6GB vs VmRSS 0.4GB），贴阈值设会让正常 run 误报 MemoryError
  （校准见 `governance/evidence/verification/R23/safety/`）。  非 POSIX（Windows）/设置失败 →
  warning 降级，软看门狗仍生效；Python API 直调 `run_factor` 只启软看门狗
  （不替宿主进程设进程级 rlimit）。R30 默认化**只加软看门狗**——`RLIMIT_AS`
  硬上限仍只由显式 `FACTORLAB_MAX_MEMORY` 触发（避免默认值把宿主虚拟地址
  空间锁死）。
- **推荐运行方式**（R30）：重任务（全市场/长窗 run、分钟链、灌库/回测批跑）
  统一经 `governance/ops/heavy.sh <命令>` 启动——flock 限 2 并发 + 启动前可用
  内存 <8GB 拒绝 + 默认注入 `FACTORLAB_MAX_MEMORY=8GB`/
  `FACTORLAB_MIN_AVAILABLE_MEMORY=6GB`/`OMP_NUM_THREADS=8`/
  `POLARS_MAX_THREADS=8` + `nice -n 10`。显式设置的值优先于注入默认（16GB 机 +
  LLM 并发推荐 `FACTORLAB_MAX_MEMORY=8GB`）；宿主级 memguard 守护
  （2s 采样、只杀本用户重任务）与取证日志见根 `AGENTS.md`「重任务运行协议」。
- **分钟长窗防护**：分钟链默认 20 交易日/块自动分块（R04-P1，见上）；
  **显式** `--chunk-days` 另按 `code 数 × min(chunk_days, 窗口交易日数) ×
  64KB/(code·日)`（R04-P1 实测校准：5207 code × 117 日非分块峰值 34.95GB、
  20 日/块 6.95GB）估算块峰值——> 32GB **拒绝**（ValueError，不启动批读）、
  > 8GB **告警**（`MinuteChunkSizeWarning`）后照跑；默认自动路径只告警不拒绝
  （残余风险由运行时看门狗接管）。用自适应估算而非固定天数阈值：小宇宙
  （测试/单票）合法长窗不误拒。

```bash
# 重任务（R30 推荐）：经 heavy.sh（限并发 + 内存闸 + 默认护栏 + nice）
governance/ops/heavy.sh env FACTORLAB_DATA_BACKEND=ch factorlab run factor/demo_1m.yaml
# 等价显式写法（不经常驻闸时）：
FACTORLAB_MAX_MEMORY=8GB FACTORLAB_MIN_AVAILABLE_MEMORY=6GB \
  FACTORLAB_DATA_BACKEND=ch factorlab run factor/demo_1m.yaml
```

### `factorlab.adapters.atomicio`：原子写单点（R13）

平台里所有"落盘要么完整、要么不出现"的写都经此模块（原先四份实现，`execution_store` 还缺第五份）：

- `atomic_write(path, writer)`：`writer(tmp)` 成功后替换（历史签名兼容，调用点零改动）；
- `atomic_write_bytes / atomic_write_text / atomic_write_parquet`：常用形态；
- 语义：同目录 `.<name>.<rand>.tmp` + 文件 `fsync` + `os.replace` + **目录 fsync** + 失败清理
  （目标不出现、不留 tmp）+ **权限按 umask 设定**（`mkstemp` 的 0600 会把产物锁成同户可读——
  2026-09-15 实测回归，见 `governance/evidence/verification/R13/`）。

采用方：`parquet_artifacts` / `strategy_artifacts` / `results_fs.write_run_outputs` /
`batch_flock`（state 与 `_SUCCESS`）/ `execution_store.save_backtest_result`。

### `factorlab.adapters.results_fs` / `factorlab.adapters.panel_store`：results 布局单点（R12）

`<results_dir>/`（默认 `runs/platform/`）的**文件名、路径拼装、读取语义、原子写**只有这两个模块知道；`app/` 与 `surfaces/`
不得自己读写产物或出现布局字面量（门：`tests/test_architecture.py::test_results_io_only_in_adapters`）。

- `results_fs.panel_path / weekly_path / summary_path(results_dir, name) -> Path`：布局单点；
- `results_fs.read_summary(path) -> dict`：缺失 → `FileNotFoundError`；非法 JSON/非 dict → `ValueError`；
- `results_fs.read_weekly(results_dir, name) -> pl.DataFrame`：缺失 → `FileNotFoundError`；
- `results_fs.write_run_outputs(out_dir, *, weekly, summary)`：**发布单点**——`weekly.parquet` +
  `summary.json` 同目录 tmp + `fsync` + `os.replace` 原子落盘，失败不留目标、不留 tmp
  （此前 `app.evaluate.publish_run` 直写，崩在中途会留半截 `summary.json`）；
- `results_fs.list_result_dirs(results_dir) -> list[str]`：因子目录枚举；
- `panel_store.ParquetPanelStore.load_dates(results_dir, name) -> pl.DataFrame`：**只读 `date` 列**
  （相关性分析的抽样周用；缺失语义同 `load_panel`，报错文案单点在 `ports.panel_store.panel_missing`）。

### `factorlab list` / `factorlab show <name>`

`run` 后的运维闭环（同 `results_dir` 锚定）：

- `factorlab list`：扫描 `results_dir/*/summary.json`（损坏/不可读的跳过），按运行
  时间倒序展示 `name | category | dir | ic_mean | spread | run_at`；无结果时提示
  「暂无因子结果（先运行 factorlab run）」。表尾附 spread 口径提示（v2：正值=表现与
  声明方向一致；含历史 v1 产物时另行注记；详见评估段）。
- `factorlab show <name>`：读 `results_dir/<name>/summary.json`，展示 spec 原文、
  `evaluation.ic` 与 `layered_backtest.summary`（各档 + long-short 摘要指标）；
  因子不存在或读取失败以非 0 退出并打印原因。

示例：

```bash
factorlab list
factorlab show demo
```

### `factorlab serve`

run 后运维闭环的可视化环节（同 `results_dir` 锚定，只读）：

- 启动只读 Web 服务，浏览器打开 `http://127.0.0.1:8000/` 查看因子列表与单因子
  图表（周度 RankIC 曲线/十分位收益/分层回测净值，Plotly 内嵌）。
- `--port 8000` / `--host 127.0.0.1`：监听地址（默认仅本机回环）。
- 只读：不写 `results_dir`，不依赖平台库（图表数据来自落盘的 `summary.json`
  与 `weekly.parquet`）；损坏 summary 列表页跳过、详情页 404（与 `list`/`show`
  缺失兼容一致）。

示例：

```bash
factorlab serve                       # http://127.0.0.1:8000/
factorlab serve --port 9000 --host 0.0.0.0
```

### `factorlab catalog dump` / `factorlab catalog docs`（M5：列/算子活文档）

schema 元数据同源生成的**活文档**（教学与帮助，非校验门——数据全开放、无字段
白名单，可用列随当前数据面实探）：

- `factorlab catalog dump [--out FILE]`：机器可读 JSON（`ensure_ascii=False` +
  sort_keys，确定性——两次导出逐字节一致）。`--out` 缺省打 stdout（原样输出，
  不经 rich 折行）。正文结构：`scope`（范围声明）/`source_ref`（规格源）/
  `open_surface`（`columns` 字段语义/单位/产生时点、`operators` 平台自有 +
  注册清单 + 元素级方法链白名单 + 分区前缀族、`def_composition` 组合规范与示例、
  `naming` 命名类约定）/`closed_gates`（三类墙门表）/`error_handbook`（错误修复
  手册全量）/`known_approximations`（已知近似）。
- `factorlab catalog docs [--out FILE]`：目录正文 markdown（同生成器，
  `catalog.md` 与之逐字节一致——防陈旧；文件缺失/不一致即测试失败）。
- 同源防漂移：列清单 = `source._PLATFORM_COLS`/`_DAILY_BASIC_MAP`/`_SPECIAL_COLS`
  常量并集、命名 = `engine.reserved` 常量、方法链 = `ast_gate.ALLOWED_EXPR_METHODS`、
  注册清单 = `registry.list_ops()` 实时快照；错误手册每条文案样板逐字存在于源码
  （DB 无关门还带 probe——真实触发且报错含样板），对照测试引用真实存在。
- 错误语义：目录描述含省略式/占位词（`validate_catalog` 的 `STUB_WORDS`）或空
  字段时 dump/docs 以非 0 退出并点名路径（活文档本身完整才可导出）。

示例：

```bash
factorlab catalog dump --out /tmp/cat.json   # AI 开写前读这份 JSON
factorlab catalog docs                       # 正文打 stdout
```

## 2. Spec 文件

Spec 为 YAML。最小单因子示例：

```yaml
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2020-01-01"
  end: "2021-01-01"
formula: |
  from polars_ta.prefix.wq import ts_delay
  signal = ts_delay(close, 1)
```

字段：

- `name`：必填，`^[A-Za-z_][A-Za-z0-9_]{0,63}$`。
- `category`：必填，`ohlcv_core | ohlcv_retail | valuation | custom`。
- `direction`：必填，`1` 或 `-1`。
- `interface`（1m 漏斗 v1）：`daily | bars_1m`，缺省 `daily`（旧 spec 逐字节
  不变）。`bars_1m` = 分钟模板（im_* 日内窗口族 + day_* 折日族 + 日级注入列，
  窗口/门/引擎语义见 §4 `factorlab.core.engine.minute`）；`daily` 公式引用 im_*/day_*
  在 compute_formula scope 门拒绝。
- `universe`：`ref | codes | rules | formula` **四选一互斥**（M4/G2 公式化股票池；
  同时出现多个 → 加载期报错，pydantic 不静默取优先）。
  - `universe.codes`：显式代码列表（canonical ts_code）。
  - `universe.rules`：规则字典（`exclude_st/min_list_days/exchanges` 等，
    PIT 语义——成员资格逐日由 `resolve_universe_frame` 决定）。
  - `universe.ref`：命名引用或文件路径（查 `universes_dir`）。
  - `universe.formula`（M4）：**池公式**——布尔条件逐 (code, 交易日) 定池，
    候选骨架 = 全市场 canonical（SSE/SZSE，不带 codes 名单）；池文法/门链/
    求值语义见 §3「池公式」。
- `date.start` / `date.end`：可选，`YYYY-MM-DD`。
- `target`：`forward_return_5d | forward_return_20d`，默认 `forward_return_5d`。
  **仅 weekly 评估模式消费**（见下）；daily 模式评估目标固定 `forward_return_1d`（D11）。
- `evaluation_frequency`（D9，R30 Task 13）：`daily | weekly`，**默认 `daily`**——
  daily=逐日口径（每日截面/每日调仓/1 日 forward）；weekly=旧口径可选对照
  （ISO 周对齐 + `target`），逐值零变更。CLI `--eval-frequency` 可覆盖 spec 值。
  结果落 `evaluation.frequency`（历史产物无该键 ≡ weekly 旧口径）。
- `weighting`（E1 产品入口，R30 fix 波）：`equal_weight | market_cap`，**默认
  `equal_weight`（零回归）**。`market_cap` = 分层 decile 组收益按决策日
  `total_mv` 市值加权（`Σ(mv×fwd)/Σ(mv)`，PIT），结果附
  `weighting={"mode": "market_cap", "mv_col": "total_mv"}` 披露键；运行链把
  `total_mv` 按需带进评估面板（legacy panel 扩展列），signal artifact 保持
  单列契约；缺市值列 / 非正市值 fail loud。`interface: bars_1m` +
  `market_cap` 加载期拒绝（分钟链不供给市值）。**E1b（`circ_mv`）**：
  kernel/bridge `mv_col=` 参数已支持（R07-DATA-I4 已完成，`circ_mv` 数据面
  可用——见 §4 daily_basic 扩展字段）；spec 入口当前只接 `total_mv`
  （未暴露 `mv_col` 字段，后续按需加）。
- `outputs`（M2 多信号输出）：可选声明输出列表；**缺省 `None` = 单输出 `signal`**
  （与旧 spec 逐字节兼容）。多输出示例：
  ```yaml
  outputs: [momentum, gap]
  formula: |
    momentum = ts_mean(close / ts_delay(close, 1) - 1, 10)
    gap = close - open
  ```
  声明四规则（spec 加载期校验，违例报错）：①名字须匹配
  `^[A-Za-z_][A-Za-z0-9_]{0,63}$`；②非保留名——`__factorlab_*` 前缀、
  `in_universe`（平台内部）、数据侧未来列纪律（`forward_*`/`future_*`/
  `target`/`label`）都不可作输出名；③非结构冲突名——`date/code/close/panel/
  labels/summary`（面板结构列/落盘文件名）不可作输出名；④全局唯一。
  运行期：公式必须**实际产出**每个声明名（codegen 前后双保险 fail fast 点名），
  共享一趟向量化 pass，per-output 独立 process/artifact/summary（详见 §4.5）。
- `process`：可选字符串列表。
- `adjustment`：复权视图口径 `raw | qfq | hfq | pit_qfq`，默认 `qfq`（`pit_qfq`
  研究日视角防未来：`run_factor` 装配传 `asof=spec.date.end`，date.end 缺省用面板
  数据末端日期——见 §4 `run_factor`）。**`interface: bars_1m` 时强制 `raw`**
  （分钟价 raw 不复权事实；≠ raw 在打开 DB 前 ValueError，B1.2）。
- `operators`：可选 DSL 宏映射。`name: {params: [p1, p2, ...], formula: "..."}`，
  `formula` 中按位置引用 `params`；公式内 `name(args)` 调用在计算前展开为
  `formula`（参数 AST 绑定替换，展开先于平台薄封装；公式内 `def` 同名函数优先）。
  宏公式须为单表达式（`mode="eval"`），可引用平台薄封装（`returns` 等）与 `ts_*` 算子；
  展开后数据列引用（如宏公式内的 `volume`）自动纳入加载。
- **daily_basic 扩展字段**：公式可引用 `turnover/total_mv/circ_mv/pe_ttm/pb/dv_ratio/
  volume_ratio`（daily_basic left join 自动加载，历史早期覆盖不足 → 缺失传播，
  以 `signal_null_ratio` 呈现）。经典价值/技术因子（`value_bp`、`turnover_level` 等）
  依赖这些字段。**R07-DATA-I4 更新**：ch 生产库 `circ_mv` 已派生可用
  （close×float_shares，万元；16.87M 行非空，覆盖同 total_mv）；
  **`pe_ttm/pb/dv_ratio/volume_ratio` 4 列仍为占位空列（100% NULL，无数据源）**，
  引用可加载但信号恒缺失；`idx_ret`（index_daily）为空的 `000852.SH` 可选灌入位，
  生产库当前恒 NULL——**R29 裁决（2026-09-16）**：CH 侧当前无可用补数路径（原注释所指
  `ingest_index_sina.py` 全仓不存在，死引用已删；候选源 teajoin `index_daily` 接口随旧源
  退役不可用；网盘指数目录 `截止_*_指数…_日线.zip` 超分享直链上限，仅人工可下），
  触发条件 = 人工取得指数日线补数并新增 index→CH 灌入工具后补 000852.SH 全历史，不伪造数据。
- `params`：可选顶层参数映射 `dict[str, number|str|bool]`（缺省空）。formula（含
  operators 宏体、def 体）内 `${name}` 文本引用在编译期替换为字面量；引用未声明
  的参数名报错。`factorlab run --set k=v` 覆盖（合并进 spec.params）并生成变体
  （见 §1 run）。例：
  ```yaml
  params: {win: 200, gain: 2.0}
  formula: |
    signal = ts_rank(volume, ${win}) * ${gain}
  ```
- **未知字段严格拒绝（R05-I2）**：spec 全模型（`FactorSpec` 及其嵌套
  `universe/date/operators/factors/combine`）`extra="forbid"`——字段名写错或照抄
  不存在字段在加载期（`lint`/`run` 同源）明确报错并点名字段，不再静默忽略。
  `op_meta` 为 Plan 2 预留（黑盒/外部函数分区声明）：当前**非空即报**
  "op_meta 暂未支持（Plan 2）"；未知算子请改用公式内 `def`（本因子专用）或
  注册插件算子（`factorlab op add`，见 §1 op 命令）。
- `formula` 或 `factors`：二选一。
- 使用 `factors` 时必须提供 `combine`。
- `combine.method`：`ic_weight | equal_weight | weight_sum`；`weight_sum` 时
  `weights` 非空且数量等于 `factors` 数量。

多因子示例：

```yaml
name: composite_demo
category: custom
direction: 1
universe:
  rules: {exclude_st: true, min_list_days: 120}
factors:
  - name: a
    formula: "signal = close / open - 1"
  - name: b
    formula: "signal = close - open"
combine:
  method: equal_weight
```

## 3. 因子脚本

`formula` 是受白名单限制的 Python 代码块，最终保留列 = spec.outputs 声明
（缺省 `[signal]`；声明多输出时每个声明名都是一等输出列，中间赋值——`_`
前缀约定——不保留）。

允许：

- 赋值
- `def` 自定义函数
- `class`
- 白名单 `import`
- 表达式、算术、比较、`a if cond else b`
  （布尔与/用嵌套 `if_else` 表达；`and`/`or`/`not` 与 `&` 不可用——R03-I7）
- 下标 `x[0]`
- `#` 注释

禁止：

- `for` / `while`
- 副作用、文件/网络/子进程/系统调用
- `eval`、`exec`、`open`、`compile`、`__import__`
- 属性调用，例如 `pl.read_csv(...)`
- 白名单以外的 `import`

白名单 import 前缀：

- `polars`
- `polars_ta.prefix.`
- `factorlab.core.ops.`

### 分区与 lookback

- `ts_*` 按 asset 排序并只使用历史窗口。
- `cs_*` 按 date 分组。
- `gp_*` 按 date + group key 分组。
- `ts_delay(x, d)` 和 `ts_delta(x, d)` 的位移不能为负（lookback 只能取过去），
  执行前静态拒绝三类形态：①字面量（含可折叠表达式如 `1 - 2`、`d=-1`、`-1.0`）；
  ②**顶层常量赋值间接**（`_d = -3` 后 `ts_delay(x, _d)`，last-wins 近似；
  def 参数绑定常量经内联同样覆盖）；③**import 别名**（`ts_delay as td` 后
  `td(x, -2)`）。未知变量（def 形参/未赋值名）无法静态判断，放行（2026-09-06
  起常量与别名形态不再放行——AI 生成因子惯用命名常量与别名，原声明被证实为
  未来函数绕过路径）。
- 未知算子会在执行前被拒绝。
- **`def` 内窗口/截面算子合法**（free-form）：计算前 `def` 经**内联展开**（参数
  绑定、中间变量提升到顶层、唯一命名防冲突）——窗口算子成为顶层 `ts_*` 调用，
  按 asset 分区正确，无跨资产泄漏。支持多语句函数体、def 调 def（不依赖定义
  顺序）；边界：递归 def（含间接）拒绝、函数体仅支持赋值与单个带返回值
  `return`、`_` 前缀不能作 def 名（中间变量约定）。
- **元素级方法链**：`ts_delta(x, 1).abs()` 语法合法——AST 门放行白名单
  （`abs/log/log1p/sqrt/exp/sign/floor`，与元素级函数名单同源）方法在表达式结果
  上的调用，计算前改写为函数调用 `abs(ts_delta(x, 1))`（expr_codegen 的 AST
  处理不支持属性调用）。模块/对象属性调用（`np.abs`、`pl.read_csv`）与窗口方法
  （`x.rolling_mean`）仍被拒——窗口语义必须走 `ts_*` 算子。
- **参数引用**：formula 内 `${name}` 文本引用 spec.params（见 §2 `params`）——
  宏体/def 体内同样可见，编译期替换为字面量。
- 平台薄封装算子（`returns/vwap/adv20`）在解析期**展开为 `ts_` 表达式**再交给
  `expr_codegen`，保证按 asset 分区。**必须裸用**：从任何模块 import 宏名
  （含 `from polars_ta.prefix.wq import returns`）都在 ast gate 被拒（R03-M1，
  codegen 前 fail fast；展开器内部的别名解析保留，gate 之后的变换不耦合）。
- **组算子（M3）**：`gp_rank(key, x)` / `gp_mean(key, x)`（gp_ 前缀族）按
  date + group key 分组。`expr_codegen` 把 gp_ 前缀函数翻译为
  `cs_<名>(<去 key>).over(_DATE_, '<key 列>')`——key 只作分区列、不参与函数体
  （实现是注册于 `factorlab.core.ops.platform_ops` 的裸原语；翻译产物符号
  `cs_mean/cs_rank` 注入生成代码 exec 作用域——它们本身**不注册**，公式层直写
  被分区门按未知算子拒绝）。**gp_ 前缀是分区硬前提**：不带前缀的组算子名
  （历史 group_rank/group_mean 已移除）不注册不 alias——宁报错（partition 门
  拒绝）不静默跨日混组。group key 可为属性列（`industry` 等，见 §4 属性数据面）。
- **属性空值语义**：组键属性为 null/''（'' 由供给层规范化为 null）的行 → 当日
  null 分区组（同日空键行互组互均/互排秩），**绝不进任何真实组的统计**
  （防污染）。字符串属性只能作**组键**，不能做字面量比较（sympy 面限制——
  `industry == '银行'` 报 SympifyError；行业条件等值用法走 process 层
  `neutralize(by=industry)` / `fillna(industry_mean)`）。
- 元素级纯函数白名单（**实测**，2026-09-06 修订——if_else 在引擎自带作用域
  `compute._ELEMENTWISE_COLS`/`partitions` 白名单内，M6-03 universe masking
  同源使用）：`abs/exp/floor/log/log1p/sign/sqrt` + **`if_else(cond, a, b)`
  函数形式可用**——`max(x,0)` 直接写 `if_else(x > 0, x, 0)`，无需
  `(x+abs(x))/2` 变形。方法链名单（`.abs()` 等）仅限前 7 个元素函数
  （不含 if_else）；方法链基表达式不可为裸 Name（如 `_d.abs()` 被拒，
  需用 `abs(_d)` 函数形式）。

平台薄封装/宏算子（`returns/vwap/adv20/gp_rank/gp_mean`）在公式中**裸用**——平台
编译期自动展开/注册，import 同名符号被 ast gate 拒绝（R03-M1）；注册到注册表的
算子可通过 `factorlab op list` 查看。

- **返回形态标注（R05-I1）**：分类表元数据 `OpMeta.returns ∈ scalar|struct|multi`
  （polars_ta 生成表由 3 行合成 frame 冒烟探测：Struct dtype → `struct`、多列 →
  `multi`；探测失败保持 `scalar` 并记录在生成表 `PROBE_FALLBACKS`）。`struct`
  返回（如 `BBANDS`→upperband/middleband/lowerband、`ts_MACD`、`ts_KDJ`）与
  `multi` 返回（如 `ts_regression_slope/intercept`）**不得直接作因子输出或进
  process 链**——`lint` 与引擎静态拒绝并给出指引（此前 BBANDS 过 lint、带
  process 时在 Struct 列上以 clip/quantile 深层报错）；字段访问（`.upperband`）
  随算子档案归 Plan 2，当前用标量算子组合表达。
- **分类面最小发现入口（R05-M1）**：`factorlab op list` 只反映**注册面**；
  `factorlab op list --catalog` 列分类表全集（name/partition/window/source/
  returns，含未注册库函数），`factorlab op doc <name>` 对未注册但分类表存在的
  算子回退打印元数据。完整同源（算子档案/`catalog.md` 合并检索）归 Plan 2。

### 池公式（`universe.formula`，M4/G2 公式化股票池）

**v1 文法**：单个布尔表达式——裸表达式（`close > 15`）或单条赋值
（`signal = close > 15`）。**赋值名不参与语义**（引擎统一归一为 `signal`——
不需要"名单 ∧ 条件"时在池侧设第二来源；名单经 per-code 0/1 成分标志属性
进公式）。def/多语句/多目标/注解赋值/空文本 → 加载后 fail fast（文案含
"池公式"，指引 v1 文法）。

```yaml
universe:
  formula: "if_else(close > ts_mean(close, 20), volume > 1000000, False)"
```

多条件一律嵌套 `if_else`（`and`/`or`/`not` 在 AST 门拒绝——codegen 无法对列
表达式求值；`&` 因优先级陷阱（`a > 0 & b > 0` 解析为链式比较）不开放）。

语义：股票池 = **上市骨架 ∩ 池公式条件**——成员资格逐 (code, 交易日) 由数据
决定（动态池）；`universe.formula` 分支无 codes 名单，候选骨架 = 全市场
canonical（SSE/SZSE）。

**门链**（与主公式同一展开/门链 + 两扇池专属门；静态门在打开 DB 前 fail
fast，动态门在求值后即时报错）：

- params `${}` 替换 / 用户宏展开 / AST 门 / 保留名双门 / future 显式引用拒绝
  ——全同主公式（保留名**绑定**门跑在归一前：赋值名会被归一掉，但
  `in_universe` 等内部名仍不可作绑定入口）。
- 布尔可判定（静态）：表达式须含比较（`close > 15`、`ts_mean 窗口` 可参与
  比较）；纯数值表达式（`close + 1`）在 DB 打开前拒绝。多条件用嵌套 `if_else`
  （如 `if_else(close > 20, volume > 100, False)`）。
- dtype 门（动态）：求值结果列必须 `Bool`——含比较但数值结果的表达式
  （`if_else(close > 15, close, 0.0)`）求值后拒绝。
- 未知列走 M1 报错助手（可用列清单 + 最相似候选），与主公式同一供给体系。

**求值顺序**（Signal/Label 两 runtime 各自**独立**求值成员资格，不跨 runtime
传递；每 runtime 对主公式 ∪ 池公式引用列**并集一趟供给**——属性读取恰一次）：

1. 上市骨架（align/listing）→ 停牌补全 → 复权视图（`qfq` fixed sample
   base——池条件与主公式同基准、同 chunk 无关）；
2. 池公式在**全骨架**上 unmasked 求值——池公式里的 CS 见完整 listed 当日
   横截面、`gp_*` 按属性全骨架组统计（成员过滤不可能先于成员计算）；
3. `in_universe = 骨架 ∧ 池条件`（条件 null → 非成员）→ 主公式经内部 mask
   只见**池成员当日横截面**（4.4 不变式：池外股不进截面统计、池外无输出
   行）；TS 类主公式仍见池外完整历史（listing 先行语义不变）；
4. 主公式 TS 算子池公式同款合法——chunked 自动 warmup 取
   `max(主公式窗口, 池公式窗口)`；
5. 空池：**全样本零成员 fail fast**（不产出空 artifact）；部分日无成员是合法
   语义（该日无输出行，后续日恢复）；
6. `universe_override`：dry-run 白名单只限骨架（候选子集），池条件照常判定。

**labels 键 = 池成员 t**：Label runtime 独立求值成员资格，与 Signal runtime
同复权视图基准、同窗口左界（chunked 下 Label 窗口左扩到 load_start，池 TS
warmup 一致；seed/fill 同构——成员资格不漂移，对齐校验不失败）。forward
returns 恒在 raw 价格上、复权视图之前计算（池条件才消费视图价格）。

## 4. Python API

### `factorlab.core.spec.load_spec(path) -> FactorSpec`

读取 YAML 并返回 Pydantic `FactorSpec`。校验失败抛出 `pydantic.ValidationError`。

### `factorlab.core.factor.ast_gate.validate_formula(source: str) -> None`

校验因子脚本。失败抛出 `factorlab.core.factor.errors.FactorDSLError`。

**平台宏 import 门（R03-M1）**：`returns/vwap/adv20/gp_rank/gp_mean`（名单单点 =
`core.ops.platform_ops.PLATFORM_MACRO_NAMES`，与注册面同源）必须**裸用**；从任何
模块（含 `polars_ta.prefix.*` 与定义模块 `factorlab.core.ops.platform_ops`）import
这些名字都在 codegen 前 fail fast：`平台宏 <name> 请裸用（不要 import）——…`。
`compute_formula`、`prepare_static` 与 `factorlab lint` 同门生效。

### `factorlab.core.engine.compute.compute_formula(df, formula, asset="code", date="date", universe_mask=None, outputs=None) -> pl.DataFrame`

在小样本 Polars DataFrame 上执行因子脚本，返回按 `date, asset` 排序、仅含
声明输出的面板：`outputs=None`（缺省）→ `[date, asset, signal]`；`outputs=["a","b"]`
→ `[date, asset, a, b]`。当前不加载 DuckDB。

声明了但公式未产出的输出名 → codegen 前/后双保险报错
（`因子脚本未产出声明输出列: [...]（outputs 声明与实际定义不符）`）。M1 保留
名双门（绑定+读取）在函数顶部无条件生效。

执行前依次：AST 白名单校验 → 幂等注册 `polars_ta` 算子族与平台薄封装 →
分区校验（拒绝未知算子）→ 负 lookback 拒绝（`ts_delay/ts_delta` 负位移）。

### `factorlab.core.engine.partitions.validate_partition_calls(source) -> None`

校验因子脚本中的调用均为已知算子、公式内 `def` 函数或元素级纯函数，否则抛
`FactorDSLError`（含源码位置）。

### `factorlab.core.engine.partitions.reject_future_shifts(source) -> None`

拒绝 `ts_delay/ts_delta` 的字面量负位移（防未来函数）。

### `factorlab.core.ops.platform_ops.inline_defs(source) -> str` / `rewrite_expr_methods(source) -> str`

free-form 源码变换（`compute_formula` 与 `run_factor` 展开链内自动调用，用户一般
无需手动使用）：

- `inline_defs(source)`：公式内 `def` 内联展开——窗口算子合法化为顶层 `ts_*`
  调用（分区正确），多语句提升、def 调 def 递归展开、多次调用独立实例化
  （唯一命名防变量串扰）。无 def 原样返回（幂等）。递归 def（含间接）抛
  `FactorDSLError`。
- `rewrite_expr_methods(source)`：元素级方法链改写为函数调用（`X.method(...)` →
  `method(X, ...)`，白名单与 ast_gate 的 `ALLOWED_EXPR_METHODS` 同源）。
  无方法链原样返回。

### 算子族注册

```python
from factorlab.core.ops.polars_ta_wrappers import register_polars_ta_ops
from factorlab.core.ops.platform_ops import register_platform_ops

register_polars_ta_ops()  # wq/ta/tdx 算子族
register_platform_ops()   # returns/vwap/adv20/gp_rank/gp_mean（cs_mean/cs_rank 不注册，见 §3 组算子）
```

两者均幂等，`compute_formula` 已在内部调用，用户无需手动注册。

### 算子注册

```python
import polars as pl
from factorlab.core.ops.registry import factor_op

@factor_op("ts_tail_ratio", kind="ts", version="0.1.0")
def ts_tail_ratio(x: pl.Expr, n: int) -> pl.Expr:
    spread = x.rolling_quantile(0.9, window_size=n) - x.rolling_quantile(0.1, window_size=n)
    return spread / x.rolling_std(window_size=n)
```

`kind` 取值：`el | ts | cs | gp | ta`。

**分区前缀铁律（2026-09-14 实测加门）**：引擎据**名字前缀**（不是 `kind`）决定分区语义——
`ts_` → `.over(asset, order_by=date)`；`cs_` → `.over(date)`；`gp_` → 翻译为
`cs_<后缀>(...).over(date, <key>)`；**其余前缀与裸名一律当元素级函数**（行级、无分区）。

| kind | 插件命名要求 | 说明 |
|---|---|---|
| `ts` / `ta` | **必须** `ts_` 前缀 | ta 族内置即 `ts_MACD`/`ts_RSI` 形态；函数体写裸 `x.rolling_*`，分区由引擎施加 |
| `cs` | **必须** `cs_` 前缀 | 函数体写横截面原语，分区由引擎施加 |
| `el` | 裸名即可 | 元素级（无窗口、无分组）——裸名语义本就如此 |
| `gp` | **暂不支持插件自定义** | printer 会把 `gp_X` 译成 `cs_X` 且要求该符号可导入（内置仅 cs_mean/cs_rank）；组算子请在公式内用 `gp_rank`/`gp_mean` 组合 |

裸名注册 `kind="ts"` 的算子**会在注册期报错**（不是静默）。裸名的两种实测静默失效
（证据见 `tests/test_plugin_partition_prefix.py`）：①**行序窗口**——生成代码无 `.over()`，
滚动窗口跨 code 块边界（9 日假库实测：分区版首窗 null，裸名版 19.33——窗口吃到另一
code 的行）；②**预热提取失效**——`_ts_window_days` 只认 `ts_/ta_` 前缀，裸名窗口对引擎
不可见（`tail_ratio(x,20)` → 0 天，`ts_tail_ratio(x,20)` → 20 天），加载不足时窗口首段
静默错值。真实日频主链上二者**可能**恰好逐位相同（满载历史 + 窗口裁剪把污染行裁掉，
实测一次 sha256 相同）——那是加载策略的巧合而非保证，工具路径/短帧/非常规行序即显形。

已装违规插件不阻断 CLI——`op list` 告警并禁用该算子（引用它的公式在分区门报"未知
算子"），`op add --force` 改名后重新注册。

### 插件管理

用户插件放在 `~/.factorlab/plugins/`。插件文件必须只定义纯函数，并通过
`factor_op` 注册算子。`op add` 会做 AST 安全扫描（含上面的分区前缀门）：
拒绝危险导入（`import`/`from ... import` 两形态，含 os/sys/subprocess/socket/
shutil 及 importlib/ctypes/builtins/pickle/multiprocessing 等间接入口）与
`eval`/`exec`/`open`/`compile`/`__import__` 调用；**同名冲突（含内建算子名）
在插件 import/副作用执行前拒绝**——未 `--force` 时不可能静默覆盖内建算子
（R01-ENG-I2/I3）。

**三种自定义"处理函数"的层级**（按复用范围递增）：

1. **公式内 `def`（零注册，本次专用）**——写在 spec 的 `formula` 里即可；计算前
   **内联展开**（窗口算子提升为顶层 `ts_*` 调用 → 分区正确）、def 调 def、多语句函数体。
   约束：函数体仅赋值 + 单个 `return`；禁递归；体只能用**已注册算子与元素级函数**
   （不能直接写 `x.rolling_*`——那是属性调用，AST 门拒）。仅本 spec 可见。
2. **`operators` 宏（零注册，spec 内参数化复用）**——`name: {params: [...], formula: "单表达式"}`，
   公式内 `name(args)` 计算前展开。仍只在本 spec 可见（跨 spec 复用靠复制或提升到 3）。
3. **全局注册（跨因子复用）**——`@factor_op` 写进插件 .py + `factorlab op add`；
   `op list`/`op doc` 可见，任何 spec 的公式可直接调用；run 链在装配点自动加载。
   **提升代价**：函数体从"算子组合"改写为"polars 表达式原语"（`x.rolling_*`），
   并按上表加分区前缀——分区语义由引擎按前缀施加，函数体内不写 `.over()`。

### `factorlab.app.run.run_factor(spec, ctx) -> FactorResult`

装配完整链路：universe 解析 → `load_daily`（含 `adj_factor`）→ 停牌补全 →
前向收益（total_return 口径，raw close×adj）→ 复权视图（`adjustment` 口径，
因子计算使用）→ `compute_formula`（公式引用 stock_basic 属性列时先按需 join
属性面——见下"属性数据面"）→ process 链 → 落盘 `panel.parquet` + `summary.json`。
周频对齐在评估阶段（`eval`）进行，run_factor 输出日频面板。

**M4 池模式**（spec 用 `universe.formula`）：主公式之外池公式独立求值——同一
面板（fill/视图/属性 join 完成后）全骨架 unmasked 求布尔条件 → 成员资格 =
骨架 ∩ 条件（主公式 mask 与输出行都改为按池成员）→ 空池 fail fast。Signal 与
Label 两条 runtime 各自独立求值同一成员资格（同视图基准/同窗口左界），详见
§3「池公式」。

**free-form 展开链**（spec.formula 处理顺序）：`${param}` 文本替换（spec.params；
operators 宏体经副本一并替换，def 体在 formula 文本内命中；未知参数名抛
`ValueError`）→ 用户宏展开（spec.operators，宏体可引用 `${}`）→ AST 校验 →
def 内联（窗口算子合法化）→ 元素级方法链改写 → 平台薄封装展开。
`factorlab run --set k=v` 在 CLI 层合并进 spec.params 并生成变体名
`<name>_<k><v>...`（见 §1 run；summary 的 `spec_yaml` 保留合并后的 params）。

`RunContext` 字段：`db_path`（默认 `settings.platform_db` = `data/factorlab.duckdb`）、
`output_dir`、`universe_override`（6 位代码或引用名称/路径，优先级最高）、`float32`、
`adjustment`（复权视图口径兜底 `raw|qfq|hfq|pit_qfq`，默认 `qfq`；spec 声明
`adjustment` 时以 spec 为准——spec 字段默认 qfq，未声明时即用默认值）、
`data_backend: Literal["duckdb","ch"] | None = None`（默认 None → `settings.data_backend`，
由 env `FACTORLAB_DATA_BACKEND` 覆盖）。run_factor 内部按 data_backend 经
`factorlab.app.bootstrap.open_read` 开读句柄（见 §4.0 读路径双后端）；duckdb 后端用
`db_path`，ch 后端忽略 db_path（直接连 `settings.ch_*` 配置库）。

**pit_qfq 消费（M4b）**：spec `adjustment=pit_qfq` 时复权视图调用
`view_prices(panel, "pit_qfq", asof=spec.date.end, pit_qfq_base_col=...)`——研究日
视角（asof 之后无信息）；`spec.date.end` 缺省时 `asof` 取面板数据末端日期。
`spec.date.end` 为字符串，装配内转 `datetime.date`（view_prices 的 asof 只接受
date 对象）。**R21（R01-DATA-I3）**：asof base 由 `load_pit_qfq_base_adj(rd, asof)`
全局装载（`argMax(adj_factor, trade_date) WHERE trade_date <= asof`，每 code 一次），
FULL/CHUNK 共用同一 base 列——保证分块结果与整段逐 cell 一致（此前按块内帧重算）。

`FactorResult`：`spec`、`panel`（列：`date, code, <outputs…>, forward_return_1d,
forward_return_5d, forward_return_20d, close`——legacy 单输出时 `<outputs…> = signal`；
close 为复权视图价格）、
`summary`（含 spec 原文、codes、universe_count、panel_rows、signal_null_ratio、process、
adjustment、float32）。

**多输出（M2）**：`outputs: [momentum, gap]` 时共享一趟 compute pass 产出全部
声明列，process 链逐输出独立执行（每输出以 `signal` 名义过链后收回原名，与各自
单输出运行逐值一致）。结果不再写单列 `signal.parquet`：`FactorResult.signal_artifact
= None`、panel 保留全声明列、逐输出 frame（`date/code/<output>`）在
`result.signals[output]`，summary 增 `outputs` 列表与 `signals.<output>.
{rows, null_ratio}` 逐输出统计（panel 无 signal 字面列时不再有
signal_rows/signal_null_ratio）。落盘布局与 loader 语义见 §4.5。单输出（缺省
`[signal]`）字节契约不变。

补全面板按交易日历截断到今天（trade_cal 含未来公告日，不产生未来 null 行）。

`factors`/`combine` 多因子组合不在平台范围（平台定位单因子计算与评估；语法保留校验但执行时明确拒绝）。

**interface 门**：spec.interface 非 "daily"（如 bars_1m 分钟模板）调 run_factor →
打开 DB 前 ValueError，文案指路 run_factor_minute（W4）。

### `factorlab.core.engine.minute`：bars_1m 分钟链（W4-W6；设计见 2026-09-08-1m-funnel spec）

分钟模板计算与折日评估。**折日**：每 (code, 交易日) 输出一行常数信号——日内
240 行折叠为日频形态，产物 frequency 恒 "1d"、评估/artifact 契约与日频零放宽；
分钟性只存在于 spec.interface 与引擎路径。

- `run_factor_minute(spec, ctx) -> FactorResult` 装配链（B4）：门链全在打开 DB
  前（interface≠bars_1m → ValueError；universe.formula 池公式 v1 排除 →
  ValueError（修订 R1）；spec.process → NotImplementedError（修订 R4）；
  adjustment≠raw → ValueError（B1.2）；data_backend=="duckdb" → ValueError
  （bars_1m 仅 CH）；spec.date.start/end 必须显式闭区间 → ValueError（修订 R5，
  分钟批读防全表扫描））→ 展开链（compute 共享 prepare_formula_pipeline）→
  候选 codes/trading_calendar/universe_frame（整段一次）→ 按
  chunk_days 分块（显式 `ctx.chunk_days` 优先；缺省 20 交易日/块——R04-P1
  内存护栏，chunk_calendar warmup=0；分钟窗不跨日，TS 窗=日内窗，分块 == 整段
  逐值一致；显式极大块经 `guard_minute_chunk_days` 估算告警/拒绝，见 §1
  「进程内存护栏」）→
  每块：日级注入列预取（adv20 左窗 = spec.start 前 20 交易日，修订 R2：在
  **有行情日行序列**上滚动，停牌日自动隔开）→ load_bars_1m_codes 批读（列
  投影按公式引用 ∩ bars 面裁剪——内存纪律）→ 块内成员日 = 池成员 ∧ 日线在
  （停牌日天然剔除）→ 整日缺失（日线在而 bars 无行）默认 fail fast（R03-I6：
  `FACTORLAB_MINUTE_UNCOVERED=drop` 显式剔除该 (code, date) + 审计——见下条）
  → compute_minute_factor_panel → 累积折日面板；空窗 → ValueError（修订 R3，
  镜像日频 M3b 文案）。label 单趟整段全窗（_compute_labels 复用，与日频链同
  骨架/同窗口），canonicalize 后按折日信号键集过滤对齐——label 键 == 信号键
  （停牌日两侧都无行）。→ write_factor_artifacts /
  write_multi_output_factor_artifacts 落盘（零放宽）。SignalMeta(frequency=
  "1d", EOD timing, adjustment="raw")。summary 增注
  `runtime_semantics="minute_intraday_fold_v1"`、`interface="bars_1m"`、
  `grid_rows_per_day=240`、`minute_uncovered`（R03-I6 审计，见下条）（其余
  字段与日频同构）。
- **分钟覆盖口径与幸存者偏差（R03-I6，2026-09-16）**：`bars_1m` 分钟源存在
  **幸存者偏差**——部分 code（多为后来退市）`daily` 有行而分钟**整日缺**
  （实测：2024-01-02 在册 5327 只中 84 只无任何 1m 行；2024H1 共 8439 个
  (code, date) 缺日集中于这 84 只；当日出现的 (code, date) 恒为标准 240 网格）。
  默认 `FACTORLAB_MINUTE_UNCOVERED=fail`：整日缺 → ValueError fail fast
  （数据不一致，行为逐值不变）。显式 `FACTORLAB_MINUTE_UNCOVERED=drop`
  （`settings.minute_uncovered`）时：该 (code, date) 从分钟宇宙**显式剔除**
  （等价于该日不参与——不伪造行、不静默），并发 `MinuteUncoveredWarning`
  （数量/日期跨度/审计指路），run summary 恒写审计字段
  `minute_uncovered: {mode, dropped_code_days, dropped_codes, dropped_dates,
  date_min, date_max, sample}`（fail/drop 都写；无缺口时计数 0/None/[]；落盘
  summary.json）。**部分覆盖**（当天有部分分钟行，如 239 行）**不是**覆盖缺口
  而是数据损坏：240 网格断言在两种模式下都 fail fast（`day_last` 等折日算子
  锚定 minute_index 239，不得静默丢弃；陈旧尾部 bar 的公式侧守卫见 R03-I7）。
  **语义纪律**：覆盖判定只依据请求窗口内数据（批读 date_end 恒 ≤
  spec.date.end，无未来信息）；`drop` 不消除幸存者偏差本身（缺分钟的历史
  code 仍不在样本内），只把缺口显式化、可审计——结果口径不可与完整覆盖混比。
  **研究口径建议**：全市场分钟回测需 coverage-aware 宇宙；平台提供只读聚合
  `load_bars_1m_coverage`（见 §4 适配器节）供生成**静态覆盖池**（用户侧
  `ref`/`codes` 选择，平台不隐式使用；静态池按全窗覆盖选样本身带前视偏差，
  只应作为研究便利并知情披露）。缺分钟段整体无 bars 时两种模式都 ValueError
  （覆盖与更新链见 §8 数据平台（网盘更新链））。
- `compute_minute_factor_panel(bars, formula, *, outputs=None, daily=None)
  -> pl.DataFrame` B4.7 **面板级纯计算入口**（loader 无关；引擎测试与批算工具
  共用同一代码路径）：date 列规范化（trade_date→date）→ 结构列/date dtype/
  minute_index dtype 校验 → session_type 存在时须 ∈ {0,1,2}（R02-I5）→ daily
  注入快照存在时逐键 left join（bars 有行而日线缺 → fail fast）→ 公式引用
  `has_trade` 时按 `amount > 0` 派生（R03-I7；bars 缺 amount → 专门报错）→
  未知列报错助手（点名实际可用列）→ **240 网格断言**（(date, code) 组行数恒 240、
  minute_index 组内唯一且范围 0..239；违者 ValueError 文案含"bars_1m 网格
  不完整/跨日泄漏疑似"）→ compute_formula(scope="bars_1m")
  → 折日输出组内 (date, code) 唯一性断言（双保险，修订 R6）→ keep-first
  dedup → 返回 [date, code, *outputs]（排序）。
- **注入列（B6，固定公开名，_formula_columns 按名探测供给）**：`prev_close`
  （T-1 raw 日收盘）/`eod_close`（T 日 raw 日收盘）/`day_amt`/`day_vol`
  （T 日全天，元/股）/`adv20_amt`/`adv20_vol`（T 及此前 20 个**有行情**交易日
  均值）。另有派生便利列 `has_trade`（R03-I7）：该分钟有真实成交 = `amount > 0`
  ——分钟 238/239 常为零成交陈旧 bar（amount=volume=0、OHLC 冻结），量价特征
  须 `if_else(has_trade, x, None)` 守卫（或嵌套
  `if_else(volume > 0, if_else(amount > 0, x, None), None)`）。`has_trade` 是
  逐分钟序列、按公式引用派生（无引用零行为变化）、只进公式作用域——不进折日
  输出/用户列。不注入 close/open/high/low/amount/volume（bars 同名列已占）；
  adj_factor 只进 label 链。
- **窗口/门语义**：im_*/day_* 走 expr_codegen CL 通道自包含分区表达式
  `.over(["code","date"], order_by="minute_index")`（窗口严格日内）；
  日频算符（ts_*/ta_*/cs_*/gp_*，含 returns/vwap/adv20 宏展开后残余）在分钟
  scope 门拒绝；im_delay k<0/k=0 拒绝。日频 scope 引用 im_*/day_* → 拒绝。
  网格/折日双断言是跨日泄漏的机械保证（B3.5）。
- **折日物化共享与条件取值（R09-PERF-I1/I2，2026-09-18）**：
  `compute_formula(scope="bars_1m")` 在 codegen 前尝试融合路径
  （`core/engine/minute_fold.py`）——AST 收集全部 day_* 聚合项（含嵌在算术/
  变量链中的），按依赖分 pass；每 pass 用同链 codegen 把聚合参数物化一次 →
  组内 over 广播，后续 pass/最终输出复用结果列。`im_*` 仍走 over 路径，但输入
  预排序一次（已物理有序则零代价）、经物理序变体去 order_by、重复子表达式 CSE
  临时列只算一次。**聚合单次化（I2）**：`day_first/day_last` 用
  `sort_by(minute_index).first/last` 单次 agg（旧双 over 极值定位等价，网格内
  minute_index 唯一）；`day_max/day_min` 的 `if_else(cond, x, None)`（及
  `x if cond else None`）条件取值形态**自动外提条件**，聚合改为
  `x.filter(cond).max/min` 单次 agg——不再物化 when/None 全列、不做全组 null
  扫描（R09 §2.3 针对 lunch_jump/close_auction_premium/open_minute_mom 类形态）；
  cond 为 `minute_index <cmp> 常量` 时直接内联 polars 条件，其余条件（如
  `volume > 0`）经 pass 物化条件列。**回退语义**：分析期不支持形态（跨层/未知
  调用、非简单赋值、保留前缀 `factorlab_fold_`/`factorlab_cse_` 名字冲突、缺网格
  列、依赖环）→ 完整回退既有 codegen 路径（行为与数值不变）。**数值锁定**：
  合成网格 + 真数据 5 因子同窗逐 cell 对拍（`after-p3/fold_parity.json`）——
  参数为纯逐行表达式/条件取值形态时 legacy-vs-融合 bit-exact
  （am_pm_vol/vol_asym/lunch_jump）；旧路径把 im_delay/day_mean 内联进 day_*
  聚合的嵌套 over 形态（autocorr_micro/vol_price_corr）仅末位 ulp 级差异（f32
  归约计划对嵌套敏感，null 掩码严格一致；新旧各自 vs f64 oracle 的舍入量级
  更大）；P3 树 vs 变更前 `HEAD` 融合树逐 cell max|Δ|=0（零变化硬门），证据/
  复现见 `governance/evidence/verification/R31/minute-perf/`。
- **`at_minute(k)` 条件取值便利算子（R09-PERF-I2，2026-09-18）**：取当日
  `minute_index == k` 行的 x 值广播全组（k 为显式 int ∈ 0..239；静态门
  `minute_gate` + 运行时 `minute_ops` 双防线拒 bool/float/负/越界）。k 行缺失
  或该行 x 为 null → 全组 null（不取邻近分钟、不跨日）；与
  `day_max(if_else(minute_index == k, x, None))` 逐位一致（重复 k 行取 max——
  标准 240 网格下 k 恒唯一）。融合路径把 `at_minute` 归一为等价条件节点单次
  filter 聚合。**慢形态指引**：精确取某分钟值优先写 `at_minute(x, k)`（读作
  "第 k 分钟那行的值"）；等价旧写法 `day_max(if_else(minute_index == k, x,
  None))` 已自动重写（无需改 spec）；`day_sum/day_mean(if_else(cond, x, 0))`
  的 0 填充掩码语义**不**适用本重写（保持原物化路径，数值口径不变）。
- **参数硬校验双防线（R02-C1，2026-09-15）**：静态门折叠 Pow/IfExp/`abs`/`int`
  常量形态并解析 `from ... import ... as` 别名（`2**2-5`→-1 不再静默取未来
  分钟；别名 `imd(...)` 与跨层 `tm(...)` 同样按原名判）；`im_*` 运行时在
  `minute_ops` 逐调用硬校验（k/window 必须 int 且 >= 1，拒 bool/float），
  `at_minute` 同样硬校验 k 必须 int ∈ 0..239（R09-PERF-I2）——静态门漏掉的
  动态形态在此 fail fast。
- **修订注记（实现期，W4-W6，规格同步）**：R1 池公式、R4 process、R5 闭区间
  强制、R3 空窗 raise（规格错误表原"空帧不抛"行已随修订）；R2 adv20 滚动语义
  （有行情日序列）；R6 折日双保险实施形态（组内 n_unique==1 断言 + keep-first
  dedup）；R7 warmup_days 忽略（日内窗无预热，summary 不标注键）。
- 门/错误表全集与测试矩阵见规格文档文末；真 CH 三对拍 e2e 见
  tests/test_minute_prod_e2e.py（integration 标记）。

### `factorlab.adapters.ic_kernel.evaluate_factor_daily / evaluate_factor_weekly`

频率分支评估桥接（D9，R30 Task 13；模块 R30 Task 15 由 `rust_ic` 改名 `ic_kernel`）——
两者共用同一 kernel（`factorlab.core.eval.kernel.evaluate_factor`）与过滤/coverage 语义：
- `evaluate_factor_daily(panel, factor_name, direction, target="forward_return_1d")`：
  **daily 默认口径**——评估面板原样进 kernel（**不得调用 `align_weekly`**），
  每日一个截面；target 固定 1 日 forward（D11）。
- `evaluate_factor_weekly(panel, factor_name, direction, target="forward_return_5d", weekly=None)`：
  weekly 可选对照——日频面板 → 周频对齐（ISO 周最后交易日）→ 评估（旧口径零变更）。

输入须含 `date/code/signal/target` 列，缺列抛 `ValueError`；`signal`/`target`
为 null 的行在桥接层过滤（停牌补全行、尾部无未来收益行不进入评估）。

返回 dict：`version`（口径版本，现值 2）、`frequency`（`daily|weekly`）、`factor`、
`target`、`n_weeks`（**评估期数**：daily=交易日数、weekly=ISO 周数）、`ic`
（mean/std/t_stat/ir 等）、`decile_returns`（含 spread，v2 正值=方向自洽）、`turnover`、
`coverage`（pct_valid/total_rows/valid_rows）；`weighting="market_cap"`（E1）
时另附 `weighting={mode, mv_col}` 披露键（等权缺省不附）。
空面板（列齐全）不崩溃，返回全 nan 结构（`n_weeks=0`）。`direction` 透传
`1/-1`（翻转信号方向）。

### `factorlab.core.eval.ic_series.ic_series(panel, target="forward_return_5d") -> pl.DataFrame`

逐期 RankIC 序列：每个日期（评估期）signal 与 target 的 Spearman 秩相关——与
`core.eval.kernel` 的 RankIC 同源定义（秩相关即秩的 Pearson，polars 1.38
`pl.corr(method="spearman")` 直接支持）。**周期无关**（R30 Task 13 改名自
`weekly_ic`；旧名保留为别名）：daily 面板→逐日 IC 曲线（Web 详情页对 daily 产物
的默认路径）、weekly 对齐面板→逐周曲线。输入须含 `date/code/signal/target`
四列，缺列抛 `ValueError`（不依赖 polars 内部异常）；`signal`/`target` null
行排除（复用 ic_kernel 的过滤语义）。面板中每个日期都保留一行：有效股票 < 3
（`MIN_STOCKS`）的期 ic = null（秩相关不稳健，含有效股票为 0 的期）。
返回 `(date, ic)` 按日期排序——`factorlab.surfaces.web` 详情页 IC 曲线数据源。

### `factorlab.core.eval.ic_decay.ic_decay(panel, horizons=(1, 5, 10, 20)) -> dict`

E2 IC 衰减（R30 Task 6，**因子侧统计**；append `evaluation.ic_decay`，不改变
主指标）：对每个 h 用与 `ic_series` **同源**的逐期 RankIC（Spearman；有效股票
< 3 的期不计、null/NaN 行排除）汇总 `n_periods/mean/std/t_stat/ir`
（`t_stat = mean/(std/√n_periods)`）。返回按 `"1"/"5"/"10"/"20"` 键的 dict
（含 `column`/`available` 字段）。**缺标签 → null**：面板无
`forward_return_{h}d` 列（如默认 `DEFAULT_FORWARD_HORIZONS=(1,5,20)` 下
`forward_return_10d` 不存在）时该 horizon `mean/std/t_stat/ir` 全 `None`、
`n_periods=0`——不插值、不崩溃；扩展 h 需先在 label 面加列。周期无关
（daily 面板→逐日、weekly 对齐面板→逐周）。多输出逐输出落
`evaluation.outputs.<o>.ic_decay`。h>5 重叠标签下该曲线是**诊断**（相对强弱），
t 推断仍以主 `ic`（D3 不重叠采样）为准；主口径仍固定 1 日 forward（D11）。

### `factorlab.app.analysis.cross_section`：横截面联合诊断（resIC）

给定一组因子的逐周横截面 OLS 诊断（A 层单因子评估的多因子补充，纯 polars
+ numpy——无回归库依赖）。数学口径权威记载于
`knowledge/design/platform/specs/2026-09-07-factorlab-resic-design.md`。

- `cs_r2(weekly_wide, cols, fwd_col="forward_return_5d", min_stocks=30) -> dict`
  组联合回归 R²：逐周 fwd ~ cols（含截距，`np.linalg.lstsq`）的 R² 周均值。
  返回 `{"mean", "n_weeks", "obs", "weekly": DataFrame[date, r2]}`；每周样本
  < max(min_stocks, len(cols)+2) 或 fwd 周内零方差（SST=0）的周剔除（weekly
  该日 null）；列缺失抛 ValueError；零有效周 mean=nan、n_weeks=0 不抛。
- `orthogonalized_ic(weekly_wide, target_col, base_cols, fwd_col=..., min_stocks=30) -> dict`
  正交化残差 IC：逐周 target 对 [1, base_cols...] OLS 取残差 e，resIC_t =
  Spearman(e_t, fwd_t)（average 秩 + Pearson，与 weekly_ic 同 tie 口径）。
  返回 `{"mean", "std", "t_stat", "n_weeks", "obs", "r2_absorbed", "weekly":
  DataFrame[date, resic]}`；t = mean/(std ddof=1/√n)；r2_absorbed = 1 −
  ‖e‖²/‖F−mean(F)‖²（候选被基准解释比例）。残差恒 0 周（完全共线）resIC
  = null 剔除出聚合、r2_absorbed 正常计入、不抛错（lstsq min-norm）；
  base 内部共线同样容忍。`base_cols=[]` 允许（退化为原始周频 rankIC，
  口径一致性锁，CLI 不暴露）。缺列抛 ValueError；每周 null/NaN/非有限行
  全列过滤后才入回归。
- `joint_diagnostics(names, results_dir, target=None, fwd_col=..., min_stocks=30) -> dict`
  磁盘双模式入口：逐因子读 `<results_dir>/<name>/panel.parquet`（默认 `runs/platform/`）（仅单输出 run——
  无字面 signal 列的 panel 抛专门 ValueError；文件缺失抛 FileNotFoundError）
  → signal rename 为因子名 → date cast → align_weekly 逐因子对齐 → 公共
  日期交集过滤（空交集抛"无公共周"ValueError）→ concat+pivot 汇聚（单次
  pivot 规避链式 join 段错误）→ inner join carrier（target 或 names[0]）
  的 fwd。`target=None`（互评）：names ≥ 2 否则 ValueError（消息含
  "--target" 提示），group = cs_r2(整组)，factors = 每因子对组内其余；
  `--target` 模式：target ∈ names → ValueError（"基准应排除 target"），
  base = names（≥1），group = cs_r2(base)，factors = target vs base。
  返回 `{"mode", "group", "factors": [{"name", "base", **resIC dict}]}`。
  用途：因子组去冗余 + 漏斗式分层筛选（候选相对基准池的边际新增预测力）。

### `factorlab.app.analysis.reference`：参考库（D10）

权威定义：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§3b。**择优最小独立集**——库级分析（corr/svd/resic）默认对参考库而非全库（近亲变异
会人为抬相关性），解决"库外新因子 vs 库内现状"的增量判断。

- 登记：`$QUANTRESEARCH_ROOT/factor/_reference.yaml`（`_` 前缀=非因子/非族既有约定；
  `FACTORLAB_REFERENCE` 可覆盖路径）。顶层 `scales` 映射**按信号来源**分组：
  `daily`（日线原生信号）/ `minute`（分钟聚合信号）——两类语义不同、**不混用对照**；
  `--target`（holding horizon）是评估参数，**不按 target 分库**（因子评估固定 1 日
  forward）。每项字段：`name / style / reason / added / entry_corr_max / entry_resic_t`
  （后两项=入库时对库内 max|ρ| 与残差 t；种子为 null）。初始库=daily 10 只
  （种子 `momentum_20d_turnrank_top2`；每风格一只），minute 组待合格分钟因子另立。
  入库标准（建议值，真实对照后校准）：显著 |t|≥2 且 |IR|≥0.1；独立 max|ρ|<0.7；
  风格覆盖；可复跑（产物+档案）。
- `load_reference(path=None) -> {scales: [ReferenceEntry]}`；未知 scales/缺字段/
  组内 name 重复 → ValueError。`reference_names(scales="daily", path=None) -> [name]`。
- `correlation.resolve_against(spec, results_dir, reference_path=None)`：解析
  `--against`——`reference`（读 yaml daily 组；**不扫全库、不跨 scales**，禁止行为
  由测试锁）/ `all`（显式扫全库）/ 逗号空白分隔名单。
- `cross_section.incremental_diagnostics(candidates, results_dir, base, fwd_col=..., min_stocks=30) -> dict`
  库外候选对基准库（参考库）的**增量信息评估**（spec §3b 表）：
  - `corr_max` / `corr_mean`：与库成员的周度截面秩相关 |ρ| 的最大/均值（signal-only，
    与 target 无关——同一实现 `factor_correlation` 提取候选对）；
  - `r2_lib`：候选被库成员**截面 rank 回归**（逐周 average-rank + OLS 含截距）
    解释的比例（周均 R²）；
  - `resic_mean/std/t`：回归残差的 rankIC（正交化后仍存的预测力）周均值/t；
  - `ic_mean/std/t`：候选原始 rankIC（同一批有效周，用于 retention 分母）；
  - `retention = resic_mean / ic_mean`（残差 IC 保留率）；
  - `verdict`：**可加入**（残差 t≥2 且 max|ρ|<0.7 且 retention≥50%）/ **冗余**
    （max|ρ|≥0.9 或 r2_lib≥0.9 或 retention<20%）/ **观察**（其余）；
  - 候选 ∈ 基准 → ValueError（基准应排除候选本身）；base 空 → ValueError。
  返回 `{"kind": "incremental", "base": [...], "candidates": [{...}]}`。

### `factorlab.surfaces.web`：Web 可视化（M5）

只读 FastAPI 应用，可视化 `results_dir` 下已保存因子（M4b 落盘产物）：
`factorlab.surfaces.web.app.create_app(results_dir: Path) -> FastAPI`，results_dir
显式传入（可测性）。

路由：

- `GET /`：因子列表页——扫描 `results_dir/*/summary.json`（损坏/不可读跳过，
  不中断列表），展示 name/category/direction/ic_mean/spread/run_at。
- `GET /factor/{name}`：单因子详情页——读 `summary.json`（缺失/损坏 → 404，
  与 spec §3.2 缺失兼容一致），渲染指标表 + 图表：RankIC 曲线
  （`weekly.parquet` 评估面板 + `ic_series`，target 取 summary——
  daily 产物=逐日曲线）、十分位收益柱状（`decile_returns.groups`）、
  分层回测净值（`layered_backtest.net_values`）；缺 weekly.parquet 或
  evaluation 字段时对应图表/指标降级（空串/空图），页面不崩溃。
- `GET /static/*`：静态资源（plotly.min.js 等）。

图表构造（`factorlab.surfaces.web.charts`，返回 plotly figure JSON 字符串内嵌模板）：
`ic_curve_figure(ic_series)`（周度 RankIC 折线，含 0 参考线）/
`decile_bar_figure(groups)`（十分位平均收益柱状）/
`layered_net_value_figure(net_values, dates)`（分层净值曲线，dates 空时 x 缺省）。

模板与静态文件在 `src/factorlab/web/{templates,static}/`；CLI 入口
`factorlab serve`（见 §1）。

### `factorlab.adapters.read.universe.resolve_codes(spec, rd, override=None, settings=settings) -> list[str]`

universe 解析优先级：`override` > spec 内联（`ref` 命名引用 / `codes` / `rules`）。
返回纯数字代码列表（`daily.code` 格式）。`rd` 为读句柄（`factorlab.app.bootstrap.open_read`
打开，duckdb|ch——双后端化后签名统一 `rd`，旧 `db`/路径位置参数已废弃）。
命名引用查 `~/.factorlab/universes/<name>.yaml`（或直接给文件路径）。
`default_universe`（`FACTORLAB_DEFAULT_UNIVERSE`）由 `factorlab run --universe`
缺省时消费（M4a 已接线）。

**挖掘约定**：同批次因子固定同一 universe（`--universe` 或共享 spec 引用），同池计算、同池比较。

`rules` 支持：`exclude_st`（ST 状态过滤）、`min_list_days`（list_date 距 date.start
或数据最早日期满 N 自然日）、`exchanges`（SSE/SZSE，BSE 不在 v1 集合）、
`exclude_bj`（**默认 true**：universe 解析默认排除 `.BJ`；仅显式 `false` 才纳入——
R37 范围裁定 2026-09-20，口径唯一事实源 `factorlab.core.scope`；ref 文件顶层
`exclude_bj: false` 对 codes 分支同义）。
code 候选先经 stock_basic.symbol 匹配归一（ch 侧两层 IN 命中索引；孤儿 code
不在 stock_basic → ch 后端不命中，duckdb 前缀匹配会命中——编译函数对语义，
测试双腿锁定）。

### `factorlab.adapters.read.source.load_daily(rd, codes, date_start=None, date_end=None, cols=None, float32=settings.use_float32) -> pl.LazyFrame`

读句柄只读加载（duckdb|ch，经 `open_read`）；SQL-first 过滤；`date` cast
`pl.Date`；数值列 float32。列映射双腿一致：`trade_date`（'YYYYMMDD'）→ `date`、
`ts_code`（'000001.SZ'）→ `code`（去后缀）、`vol`→`volume`、`turnover_rate`→
`turnover`（daily_basic left join，cols 含 turnover/total_mv/circ_mv 时）；
close 恒加载，adj_factor 恒 inner join。**R21 单位契约（R01-DATA-I7）**：离开读面
的 canonical 单位恒为 `volume=股`、`amount=元`；duckdb 历史库（旧外部源写入，
2026-09 随源退役）原始为 `手`/`千元`，`adapters/read/source.py` 在读适配层显式
×100/×1000 归一；ch 灌入原生即股/元（恒等）。两腿消费者所见单位一致。

**退市股 adj 补灌（R08-DATA-I2，2026-09-17）**：`daily_fact` 源的退市股为 8 列精简
格式（date/OHLCV，无复权列）且全量 zip 不含退市代码 → CH `adj_factor` 对退市股全
NULL → adj inner join 使这些代码全历史进不了评估。补口（`platform/tools/ch_ingest/
delisted_adj_backfill.py`）：腾讯复权 K 线（hfq）→ `adj=后复权价/收盘`，
**raw 收盘逐日对拍 `daily_fact`（不一致拒绝）**；部分已有 vendor 值的代码按 vendor
段末端锚定比例常数（漂移 >0.5% 拒绝）保持尺度连续；写 sidecar
`data/fact/daily_fact/delisted_adj_factor.parquet`（+ `.meta.json`；限流可断点续跑
合并）→ `ingest_daily` 灌 `adj_factor` 表时 `coalesce(vendor, sidecar)`（**只填
NULL，绝不覆盖 vendor**）→ `reconcile` 校验 sidecar 每条 (ts_code, trade_date) 在 CH
非空。恢复范围默认 `2022-01-01` 起（评估窗口 2023+ 与 warmup；更早历史腾讯/通达信
存在个别交易日差异，不在范围）。

**列供给：无字段白名单（M1，spec 决策③修订）**。`cols` 可请求：引擎特殊名字
（`date/code/adj_factor/idx_ret`）、平台映射名（`open/high/low/close/pre_close/
change/pct_chg/volume/amount`；`turnover/total_mv/circ_mv/pe_ttm/pb/dv_ratio/
volume_ratio` → daily_basic）以及 **daily/daily_basic 表上真实存在的任意列**
（按当前数据面 schema 实探，自动归入对应表——目录收录不是前提）。
供给失败 → `ValueError` **报错助手**：`未知列名: [...]（当前数据面可用列:
[...]）` + difflib 最相似候选（≤2）+ 原始列映射提示（`vol`→`volume`、
`ts_code`→`code`、`trade_date`→`date` 不收录为引擎名，请求原始名给提示）。
**注意**：内部实际立即执行查询（`execute().pl()` 后包 `lazy()`），SQL 错误在调用时抛出。
同文件另含 `load_daily_fill_state(rd, codes, *, before, cols, float32)`
（chunk 左边界停牌补前值，per-code 最新 non-null 状态；双腿参数化测试锁定）——
列分类与报错助手同 load_daily。

### 属性数据面（M3：per-code 静态属性按需供给）

`factorlab.adapters.read.attributes`（duckdb|ch 编译对 + decode 层规范化）：

```python
attributes_visible(rd) -> frozenset[str]
load_code_attributes(rd, cols, *, float32=settings.use_float32) -> pl.DataFrame
```

stock_basic 表除 `symbol/ts_code` 键列外的列为 per-code 静态属性（`industry`、
成分标志等——M3 首面 industry）。`load_code_attributes` **单次全量**供给
（每 code 至多一行；`SELECT symbol, <cols> FROM [db.]stock_basic`，`symbol` =
join 键 = daily.code 形态），列序返回 `[symbol, *cols]`。decode 规范化：
字符串属性**空串 → null**（库中 '' 等价缺失——与 process 层 industry 取数
"IS NOT NULL AND != ''" 同源语义，供给层统一表达，公式侧只见 null）；数值
属性 cast float32。`attributes_visible` schema 实探可见属性列集——**属性面无
白名单**，表上真实存在的任何静态属性列都可请求（缺表/探测失败 → 空集：
报错文案回落 load_daily 双面清单，不因缺表改变引擎行为）。

**装配（run_factor / `_compute_signal`）**：公式引用列按来源路由——daily/
daily_basic 真列（含平台映射名/特殊名）→ `load_daily` 现路径；stock_basic
属性列 → **按需 full-supply join**（left join，键 symbol=panel.code）。
**引用才 join**：公式未引用属性 → 对属性面零读取（有供给调用计数断言）。
`compute_formula` 直算路径无供给——df 已含的列（含属性名）原样可用
（design §5.1"存在即可写"）。属性列两种引用形态：

- 组键引用：`gp_rank(industry, close)` / `gp_mean(industry, close)`——key 列
  由属性面供给，见 §3 组算子/空值语义；
- 数值直接引用：`if_else(flag > 0.5, close, 0.0)`——数值属性值进公式面
  （industry null/'' 不影响同股其它属性——属性列空值彼此独立）。

供给失败（错拼属性列）→ load_daily 同款 M1 报错助手——可用列清单并入属性面
实探列（`industr` → 提示含 `industry` 与最相似候选）。industry 等静态属性是
**库中当前值近似**（非 PIT，不做历史追溯，design §5.4）。

### `factorlab.adapters.read.calendar.trading_calendar(rd, date_start=None, date_end=None) -> pl.Series` / `fill_suspensions(df, calendar) -> pl.DataFrame`

交易日历（trade_cal distinct date 升序；duckdb|ch 编译函数对，空表在调用点按
无日历处理）与停牌补全（日历×代码全连接，缺失数值 null，不默认填充——纯
polars 函数，不触库）。补全后输出按日期升序、组内代码顺序未承诺（调用方自行排序）。

### process 链

处理器：`winsorize(quantile=0.99)`、`standardize()`（别名 `zscore`）、`csranknorm()`、
`robustzscore()`、`neutralize(by=market|industry|size)`、`clip(lower, upper)`、
`fillna(method=value|forward|industry_mean)`。截面类处理器按 `.over("date")` 分组；
`fillna(method=forward)` 按代码内日期前向；`clip`/`fillna(value)` 为元素级。

**无效观测统一门（R02-I2，2026-09-15）**：所有处理器入口把非有限值（NaN/±Inf）
视为无效观测 → null：不参与分位数/均值/排名/demeaning 统计、不留在输出（`clip`
不把 Inf 静默截成边界；`fillna` 先归缺失再填）。此前仅 NaN 被隔离，Inf 会毒化
`standardize` 的 std（整日截面全 NaN）。

**spec 文件内的链项必须用 `=` 分隔**（`neutralize(by=industry)`），`key: value` 冒号
写法会被 YAML 解析为映射而报错。

`neutralize` 的行业依赖 `stock_basic`（v1 静态行业近似）与 `size` 的
`daily_basic.total_mv`——取数经 `ProcessCtx(db)` 读句柄（`Rd`；ProcessCtx 原
`db` 即读句柄，签名未改，run 链传入 open_read 结果）：行业每日期内分组 demean、
`size` 每日期内 total_mv 排名十分位分桶后组内 demean（无 daily_basic 匹配时报错）；
截面 N<10 时 size 中性化每桶 1 只股票，demean 恒 0（分组式中性化固有属性）。
fillna(method=industry_mean) 同理需 ProcessCtx(db)，缺上下文显式 ValueError。

零方差截面（standardize/robustzscore）输出 null（NaN 不是 null，fillna 无法处理）。

### `factorlab.core.engine.forward.compute_forward_returns / align_weekly`

前向收益 **total_return 口径** `close[t+h]×adj[t+h] / (close[t]×adj[t]) - 1`
（含分红再投资；输入须停牌补全且 close 为 raw 价格——先于复权视图计算，避免二次复权）；
签名 `compute_forward_returns(df, horizons=(1, 5, 20), close_col="close", adj_col="adj_factor")`，
缺 adj 列时显式报错。**D9（R30 Task 13）起默认 horizons 含 1d**——daily 评估
固定 1 日 forward（`forward_return_1d`，D11）；label/panel 列清单由
`FORWARD_COLUMNS` 单点派生。
`align_weekly` 对齐到 **ISO 周**最后一个交易日（跨年日期同属 ISO 周时合并为该周
最后交易日，与 tushare weekly 语义一致；**仅 weekly 评估模式调用**——daily 路径
不得调用，桥接层禁用测试锁定）。

### `factorlab.adapters.ic_kernel.evaluate_factor_daily / evaluate_factor_weekly`

频率分支评估桥接（D9，R30 Task 13；桥接的 `core.eval.kernel.evaluate_factor` 为
R30 Task 15 从独立 quant-core 包并入的单一实现）：daily = 面板原样进评估（**不调用
`align_weekly`**，每日截面）；weekly = 日频面板 → 周频对齐（`align_weekly`）→
评估（旧口径零变更）。
签名 `evaluate_factor_daily(panel, factor_name, direction, target="forward_return_1d") -> dict` /
`evaluate_factor_weekly(panel, factor_name, direction, target="forward_return_5d", weekly=None) -> dict`，
`panel` 需含 `date`（pl.Date）、`code`（str）、`signal`、`target` 四列，缺列抛
`ValueError`（中文消息，含缺失列名）。`direction` 约定 `1`（多）/`-1`（空）。
返回 kernel dict（`version`/`frequency`/`factor`/`target`/`n_weeks`/`ic`/`pearson_ic`/
`decile_returns`/`turnover`/`coverage`），另附加 `factor_name` 字段；内部 `factor`
恒为 `"_factor"`（kernel 内部列名约定）。

行为约定（实测）：
- `signal`/`target` 为 null 的行在桥接层**过滤**（kernel 拒绝 Python `None`，
  实测 `TypeError: must be real number`）；停牌补全行与尾部无未来数据的 forward
  行均不进入评估。`NaN` 不属 null，kernel 容忍（实测）。
- **coverage 口径（R03-I2）**：以**过滤前**评估面板计算——`total_rows` 含全部行，
  `valid_rows` 只计 signal/target 非 null 且有限（`is_finite`）的行，`pct_valid` 与
  同一 summary 的 `signal_null_ratio` 可对账。kernel 只见过滤后的行、自身 coverage
  恒 1.0，桥接层以 kernel 形状覆盖返回值；`core.eval.kernel.evaluate_factor`
  直调的 coverage 契约不变。
- 空面板（列齐全）不报错，透传后返回全 `nan` 结构（`n_weeks == 0`；coverage
  `pct_valid=0.0`、`total_rows=0`）。
- 列检查先于对齐：缺列的裸 `date`/`code` 空表（Null dtype）也报 `ValueError`，
  而非 polars dtype 错误。
- `direction` 原样透传为 int（`0` 实测按 `-1` 处理，属 kernel 内部语义，桥接层
  不校验）。
- **daily 期语义**：`n_weeks` = 评估期数（交易日数）——字段名保留历史键；
  `turnover.monthly/quarterly` = 4/12 个**连续评估日**桶的 decile 归属变化
  （不是日历月/季；日频换手权威口径 = `layered_backtest.turnover` 的
  `1−|S_t∩S_{t−1}|/|S_t|` 逐日序列）。
- **年化**：`evaluation.frequency=daily` 时 layered_backtest 年化系数 = **252**
  （`periods_per_year` 参数；weekly=52 缺省）——日频成本敏感度显著高于周频
  （成本 = 日频换手 × `cost_rate`，真实反映每日调仓）。
- **spread 符号约定（v2，R30 D1=B；`evaluation.version=2`）**：
  `decile_returns.spread.ret = (g9−g0)×direction`（`g9` = group9 = signal 最高档的
  全期组均值、`g0` = group0 = signal 最低档；原 quant-core 契约 2026-08-26 §3.1 为
  v1 口径）。`direction` **只翻转 spread 符号，不影响 `ic` 统计**。
  **读法（行业惯例，正=好）**：`spread>0` = 最佳档跑赢最差档、与声明方向一致；
  `spread<0` = 相反。实测（R30）：同一因子 direction −1→+1 时 spread
  −0.00676→+0.00676（v1 为 +0.00676→−0.00676）。判有效性/方向也可看 raw `ic`
  与 `layered_backtest.long_short`。`factorlab list` 表尾与 `factorlab show`
  有同款提示。
- **口径迁移（D1=B；历史不重算）**：`evaluation.version=2` = 现行正值口径；
  **历史 summary 无 `version` 键 ≡ v1**（`(g0−g9)×direction`，负=自洽）——**不重算**，
  按 v1 口径解读；档案 `snapshot:` 注记「spread 为 v1 口径（负=自洽）」。
  `factorlab list/show` 按 `version` 分渲染提示。
- **h>5 不重叠采样（D3，R30 Task 4）**：`target=forward_return_<h>d` 且 h>5 时，
  先对（weekly 已对齐的）评估面板按排序日期每 `stride` 取一个评估点作**统计面板**
  ——weekly `⌈h/5⌉` 周（20d → 每 4 周）/ daily `h` 交易日——再走既有统计（消除
  重叠标签的 t 虚高；5d 路径逐值不变）；结果附
  `sampling={"mode": "non_overlap", "stride_weeks": ⌈h/5⌉}`，并附
  `ic.t_stat_nw` = 对**未采样**重叠 IC 序列（`core.eval.ic_series` 逐期口径）
  的 Bartlett 核 Newey-West **诊断** t（`lag=⌊h/5⌋`，口径与 R08
  `04_overlap_ttest.py` 一致；**仅诊断，不替代主 t**；与采样后简单 t 对照，
  实测 low_vol_20d：2.53 vs 2.67）。coverage 仍以完整（未采样）评估面板为口径；
  h≤5 零变更（无 `sampling`/`t_stat_nw` 键）。日频默认 1d（D11）不触发。
- **市值加权 decile（E1，R30 Task 7）**：`weighting ∈ equal_weight|market_cap`
  （缺省 **equal_weight 零回归**——结果不含 `weighting` 顶层键）+ `mv_col ∈
  total_mv|circ_mv`（E1a/E1b；`circ_mv` 依赖 R07-DATA-I4 补数——2026-09-16
  已完成，CH `daily_basic` 非空 16.87M 行）。`market_cap` 时组收益 = 组内
  `Σ(mv×fwd)/Σ(mv)`（决策日已知市值，PIT），`decile_returns.weighting` 回填
  口径、结果附 `weighting={"mode": "market_cap", "mv_col": ...}` 披露。
  **null 市值（及 NaN）行剔除并计入 coverage**（`valid_rows` 不含；缺列 /
  非法 weighting / mv ≤ 0 fail loud）。读法：与等权对照可看组收益是否被大/小市值
  腿主导（小盘容量口径用 `circ_mv`）。kernel 直调 `evaluate_factor(...,
  weighting=, mv=)` 同语义。
  **产品入口（R30 fix 波）**：`FactorSpec.weighting`（默认 `equal_weight`）经
  `app.evaluate.evaluate_run` 全链透传 kernel（daily/weekly 与多输出逐输出）；
  `market_cap` 时运行链把 `total_mv` 按需带进评估面板（legacy panel 扩展列），
  signal artifact 保持单列，CLI `factorlab run` 无新增 flag（spec 驱动）；
  `layered_backtest` 保持等权口径（E1 只改 `decile_returns` 统计）。
- **IC 方向胜率字段（D4，R30 Task 3）**：`ic.sign_consistent` 保持 **raw 语义**
  （正 IC 期占比，不随 `direction` 变化）；新增
  `ic.direction_consistent_share = P(direction×IC>0)`——`direction=1` 即正 IC 期
  占比、`direction=−1` 为负 IC 期占比（按声明方向标注）。仅翻 `direction` 参数时
  两字段互补（如 0.64↔0.36，raw 不变）；翻经济方向（信号取负 + 声明方向取负）时
  `direction_consistent_share` 不变、`sign_consistent` 随数据翻。`factorlab list`
  逐行打印 `dir_consistent=<值|—>` 并附读法提示，`factorlab show` 打印
  `方向一致率`（含 dir 与 raw 对照）；历史产物缺该字段显示 `—`（不重算）。
- **dead-signal fail-loud（D5，R30 Task 2；收口 R07-D6）**：样本面板 signal 列
  **空值占比** ≥ `core.eval.metrics.DEAD_SIGNAL_NULL_RATIO`（默认 **0.99**，含 0.99）
  即判死信号——`evaluation.dead_signal=true` 先落盘供审计，`factorlab run` 随后以
  **非零退出**（`DeadSignalError`，消息含 `signal_null_ratio`），不再以 `n_weeks=0`
  静默等价于"无效因子"。**空值 = null 或非有限（NaN/±inf）**（R30 fix 波裁定：
  全 NaN 信号与全 null 同判死——`1/pb` 型空列常产出 NaN 而非 null，只数 null 会
  漏网；`signal_invalid_mask`，`null_rows`/`nonfinite_rows` 分列审计）。判定与
  summary `signal_null_ratio` 同源（真实行计数 `dead_signal_report`；分母=样本
  全量面板）。正常因子**不含该键**、行为零变化；多输出逐输出判定（任一输出为
  死信号即整体非零退出）。

### `factorlab.core.eval.layered.layered_backtest(panel, direction, n_groups=10, forward_col="forward_return_5d", cost_rate=0.0, periods_per_year=52) -> dict`

分层回测：每期按 signal 分档，各档 forward 等权平均累积净值；long-short = 最佳档 −
最差档净值差。输入**评估面板**：daily 模式为日频面板（`forward_col="forward_return_1d"`、
`periods_per_year=252`，每日调仓）；weekly 模式为周频对齐面板（`forward_col=spec.target`、
`periods_per_year=52` 缺省）。**调仓成本**（R9 起建模，可验证口径）：

- `cost_rate` = 每单位**单边换手**的买卖总成本（费率语义；如 A 股约 0.0007 = 0.1% 印花税
  + 双边佣金 0.005%×2 + 少量冲击，**由调用方给**，默认 0.0 = 与历史零成本结果逐值一致）；
- 每期净收益 `net_t = gross_t − cost_rate × turnover_t`；`turnover_t = 1 − |S_t∩S_{t−1}|/|S_t|`
  （等权、忽略漂移；首期 0、档空期 0）；
- 返回值新增 `cost_rate` 与 `turnover`（各档逐期序列，`long_short` = 两腿之和）——成本可审计，
  不是黑箱；**spec 级接线（把费率写进因子/策略 spec）待费率口径的研究决策后再做**。

语义：

- **方向感知 + average-rank 分档（D2，R30 Task 1）**：每期用 **average rank**（并列
  同档、行序无关）经对称分位映射 `floor((2·avg_rank−1)·n_groups/(2·n))`（clip
  `[0, n_groups−1]`）得分档——与 kernel `evaluate_factor` 的 decile **同公式**；
  `direction=1` 时 D1 = signal 最高档（D_g ↔ 升序 decile 的 `n_groups−g`），
  `direction=-1` 时 D1 = signal 最低档（D_g ↔ decile `g−1`），两者都是"最佳档"。
  无并列且 N 整除 `n_groups` 时与旧 `(rank−1)·n_groups//N` **逐值等价**（唯一值面板
  回归承诺）；重并列/不整除时可能跳档（空档由 `empty_groups` 披露，与 kernel
  R03-I3 语义同源）。
- **期数口径**：signal/forward 为 null 的行不参与分档与收益（期内部分行 null 的期
  仍计入，组内等权平均忽略 null）；某期**全部**行无效（头部 ts 窗口未满/尾部无未来
  收益）则该期不计入 `periods`——与桥接评估的 `n_weeks` 口径一致
  （`bt["periods"] == evaluation["n_weeks"]`）。
- **档空期**：某期某档无股票 → 该档收益记 0（fill_null(0)，净值保持前值，不跳变）。
- 年化：期收益均值 × `periods_per_year`（weekly=52 / daily=252）；年化波动：
  std × √`periods_per_year`；夏普 = 年化收益/年化波动（vol=0 时记 0.0 退化）；
  最大回撤 = 净值峰值到谷值最大跌幅；胜率 = 期收益 > 0 比例。

返回结构：

```python
{
  "n_groups": 10,
  "periods": 98,                      # 回测期数 = 有效评估期数（= 评估 n_weeks）
  "net_values": {                     # 各档净值序列 + long_short（每期一点，长度 = periods）
    "D1": [1.0, 1.01, ...], ..., "D10": [...],
    "long_short": [1.0, 1.02, ...],   # D1 − D10 净值差（非组合净值）
  },
  "summary": {                        # 每档 + long_short 的摘要指标
    "D1": {"annual_return": ..., "annual_vol": ..., "sharpe": ...,
           "max_drawdown": ..., "win_rate": ...},
    "long_short": {...},
  },
  "dates": ["2024-01-05", ...],       # 净值序列对应日期
}
```

边界：空面板或过滤后无有效行（signal 全 null）→ `periods=0`、`net_values={}`、
`summary={}`（不崩溃、不产出平值假净值）；单期面板正常返回（各序列长度 1）。

**空档检测（R03-I3）**：`degenerate_decile_groups(decile_returns) -> list[int]` 返回
kernel `decile_returns.groups` 中 `mean_ret` 缺失/非有限的组号（重并列/离散信号
经 average-rank 对称分位映射跳档，某些 decile 全期无成员——D2 后 `layered_backtest`
采用同一 average-rank 分档，其 `empty_groups` 与 kernel 跳档同源、会同时披露）。
`app.evaluate.evaluate_run` 检测非空时在
`evaluation.decile_returns.degenerate_groups` 落标记并生成 notes（CLI `run` 打印
`提示:`；`show` 打印 `警告:`，文案含降组数/换评估口径指引）；正常面板不产生该键
（summary 逐字节不变）。

CLI 消费：`factorlab run` 默认调用并把结果写入
`summary.json.evaluation.layered_backtest`（`--no-backtest` 关闭、`--groups` 调档数）。

### `factorlab.adapters.read.adjust.view_prices / total_return`

价格视图（输入 raw 价格面板，含 `adj_factor` 列；输出含 scaled 价格列）：
`view_prices(df, view="qfq", asof=None)`，`view ∈ raw|qfq|hfq|pit_qfq`：
- RAW 原样；QFQ `×adj/adj[latest]`（最新因子基准）；HFQ `×adj`（连续价格）；
  PIT_QFQ `×adj/adj[asof]`（研究日视角防未来，必须给 `asof`）。
- QFQ/PIT_QFQ 按 code+date 排序后计算，latest 语义基于日期而非行序；跨 code 独立。
- 停牌补全行的 adj 为 null：QFQ 的 latest 与 PIT_QFQ 的 asof 基准跳过 null
  （窗口末行/截止日补全行不污染整组），补全行价格保持 null。

`total_return(close, adj)`：HFQ 收益 `close[t]×adj[t]/(close[t-1]×adj[t-1])-1`
（含分红再投资的真实收益；除权日上 RAW 收益率 ≠ QFQ/HFQ 收益率，用 total_return）。

### `factorlab.adapters.read.adjust` 审计三查（AdjustmentAudit）

`FactorFn = Callable[[pl.DataFrame], pl.DataFrame]`——输入价格面板
（date/code/价格列），输出 (date, code, signal)；输出缺列时抛 `ValueError`。
`AuditReport(check, passed, details)` dataclass，`details` 含审计指标。

- `lookahead_check(factor_fn, df, asof) -> AuditReport`
  未来信息泄漏：asof 截断重算 vs 全量重算，仅对比 `date <= asof` 的行；
  截断后值变化（含一侧为 null）的行即潜在泄漏（`affected_rows`）。
- `scale_invariance_check(factor_fn, df) -> AuditReport`
  价格尺度不变：RAW vs QFQ 因子值最大绝对差 `< 1e-6` 通过。收益率类因子天然
  不变；**跨除权日**时朴素 RAW 收益率与 QFQ 不同（RAW 除权跳变）——审计对比应
  使用无除权事件的面板或声明口径的因子。
- `adjustment_sensitivity_check(factor_fn, df, views=("raw","qfq","hfq")) -> AuditReport`
  复权口径切换敏感性：各视图因子值相对 raw 的最大绝对差（`max_abs_diff`）。

### 数据平台旧写路径（M3b）——已退役（2026-09-17，Plan P T11）

`factorlab.adapters.{fetcher,mirror_db,rebuild,refresh}` 与 CLI
`factorlab data rebuild|update|refresh|verify` 随 teajoin 源退役**删除**（历史实现
见 git 历史；teajoin 使用指南存档 `teajoin-guide.md`，不再维护）。现行数据链：
夸克网盘（唯一外部源）→ `platform/tools/pan_update/`（`make data-update`）→
转换/灌入 → CH；消费侧统一 `FACTORLAB_DATA_BACKEND=ch`（见 §8 数据平台（网盘更新链）
与 `data-ops-playbook.md`）。duckdb 后端保留为**测试/历史只读库**（`open_read`
工厂与双腿测试设施不变；`settings.platform_db` 指向历史库路径）。

### `factorlab.adapters.read.verify` 读面列命名纪律（M5/G4，双重锁的数据侧）

- `ENGINE_SURFACE_TABLES`：引擎读面表 frozenset（**10 表**：daily/daily_basic/
  adj_factor/index_daily/stock_basic/trade_cal/stock_st/stk_limit/suspend_d/
  **moneyflow**——moneyflow 自 T7（Plan P）起经 `load_daily` LEFT JOIN 供给公式，
  纳入读面纪律；`fundamentals` 等暂无 `load_daily` 供给的表不受纪律约束）。
- `validate_surface_columns(tables: dict[str, list[str]]) -> list[str]`（纯函数，
  无 DB）：逐引擎读面表检查列名——禁止引擎内部保留名（`__factorlab_*` 前缀 /
  `in_universe` 精确名——注入/join 与引擎内部列碰撞毒化面板）与未来前缀列
  （`forward_*/future_*` 前缀 / `target`/`label` 精确名——读面按构造即 PIT，
  未来数据只由评估运行时内存计算或研究侧落库）。违例给表名/列名 + 修法指引。
- `validate_engine_surface(rd) -> list[str]`：对 Rd 句柄（duckdb|ch 双腿同一
  函数）逐读面表实探列名检查（缺表 → 空列集，不报错）。
- 旧 `verify_all`/`compare_sample`/`PlatformDB.integrity_check` 六规则随旧写路径
  退役删除；CH 侧对账统一走 `platform/tools/ch_ingest/reconcile.py`（`make reconcile`，
  daily 层 + moneyflow/fundamentals 人工核对口径见 §8）。

### Canonical research identifier（M6-07B4）

**背景**：vendor source（旧 teajoin/Tushare 路径，已退役）曾返回历史遗留
别名/实体标识，超出 canonical 六位 A 股证券代码域（实测：`T600018.SH`/
`TS0018.SH`——上港集箱退市残留）。这些行**不映射、不合并、不删除、不猜测关系**
——M6 无 verified corporate-action/entity-lineage 模型。

**Canonical research universe v1**（唯一权威：`factorlab.core.domain.codes`）：

```
ts_code 匹配 ^\d{6}\.(SH|SZ|BJ)$  且  symbol == ts_code 前六位
```

- `is_canonical_stock_code(ts_code) -> bool`：ts_code 形态判断（None/非 str → False）。
- `CANONICAL_TS_CODE_PATTERN`：Python re 与 DuckDB `regexp_matches()` 共用同一
  pattern 常量——读路径/universe 代码不得独立重写该正则。

**Source partition**（`partition_stock_basic_source`/`fetch_stock_basic_*`）随旧源
退役删除（2026-09-17 Plan P T11）；其 fail-fast 语义（canonical 校验、L/D 分区、
quarantine 审计）以 git 历史为准，CH `stock_basic` 灌入侧（`ch_ingest`）沿用
canonical-only 口径。

- `factorlab.adapters.catalog`（M5 活文档数据体；R8 从包顶层归位到 adapters）：`build_catalog()`（组装目录 dict——
  常量/registry 同源 + `validate_catalog` 通过才返回）、`validate_catalog(cat)`
  （描述完整性门：空字段/占位式 STUB_WORDS 拒绝并点名 json 路径）、
  `catalog_json()`（确定性 JSON 字符串）、`render_catalog_markdown()`（目录正文）。
  CLI 入口 `factorlab catalog dump/docs` 即其薄封装（见 §1 同小节）。

## 4.0 读路径双后端（duckdb|ch）与 intraday 读接口

### `factorlab.ports.read`：Rd 句柄与 open_read 工厂

三层读路径：公开读函数（单写，共享 polars/校验）→ 模块内
`_IMPL[rd.backend]` 编译函数对（`_xxx_duckdb` / `_xxx_ch`：SQL 文本 + 参数 +
1-3 行 decode）→ Rd 句柄（执行 + 目录探测）。读函数体内无 if/else。

- `class Rd`：`backend: Literal["duckdb","ch"]` + `query_df(sql, params) ->
  pl.DataFrame` / `query_rows(sql, params) -> list[tuple]` / `command(...)` /
  `tables() -> set[str]` / `columns(table) -> set[str]` / `close()`。
- `DuckDBRd`：平台库文件**只读连接**（文件缺失 → `FileNotFoundError`）；打开即
  SET memory_limit/threads pragma（历史逐函数 SET 的收敛点）。可包外部连接
  （引擎链，`_owns=False` 时 close 不动外部连接）。
- `ChRd`：无状态包 `ch_source` 客户端单例；SQL 表一律带 `{settings.ch_database}.`
  前缀（临时库测试靠 monkeypatch ch_database 生效）；连接失败首次查询抛
  `RuntimeError`。
- `open_read(data_backend=None, db_path=None) -> Rd`：data_backend None →
  `settings.data_backend`。duckdb 腿用 db_path（默认 settings.platform_db）；
  ch 腿忽略 db_path。

**签名迁移约定（本文件所有读路径 API 已统一）**：参数一律 `rd` 读句柄；
`db_path`/连接位置参数已废弃（无兼容层）。`RunContext` 保留 db_path、新增
`data_backend: Literal["duckdb","ch"] | None = None`（None → settings）。
测试双后端化：`env` fixture 参数化（duckdb 腿 = 改造前行为的回归网，硬门槛）。

### `factorlab.config.settings` 数据后端字段

`data_backend`（默认 `"duckdb"`，env `FACTORLAB_DATA_BACKEND` 覆盖）与
`ch_host/ch_port/ch_user/ch_password/ch_database`（env `FACTORLAB_CH_*`）。
ch 读路径统一客户端设置 `join_use_nulls=1`：LEFT JOIN 一律 NULL-extension，
与 duckdb 语义对齐（服务器默认 0 时未匹配行填类型默认值——PIT 骨架的
is_st/is_listed 判定与未匹配 code 的 NULL 形态会失真；见 ch_source 模块注释）。

### `factorlab.adapters.intraday`：bars_1m / tick 读接口（仅 ch）

生产分钟/逐笔数据仅存 ClickHouse（bars_1m ~1.85B 行、tick_trades/orders/
snapshots 共 ~14B 行）；duckdb 平台文件无 intraday 表 → duckdb 后端读接口
**显式 ValueError**（"bars_1m/tick 数据仅 ClickHouse 后端提供"），非底层表
缺失错误。

- `load_bars_1m(rd, code, *, day=None, date_start=None, date_end=None, cols=None) -> pl.DataFrame`
  1 分钟线（bars_1m）：datetime/trade_date/code/minute_index/session_type/
  open/high/low/close/amount/volume（OHLC Float32、volume Float64 原样，
  上游 1m 事实库语义读侧不解释）。排序 datetime。
- `load_bars_1m_codes(rd, codes: list[str], *, date_start, date_end, cols=None)
  -> pl.DataFrame` 多 code 分钟线批读（引擎分钟面/批算共用入口）：单条 SQL
  返回，与 load_bars_1m 同契约（列白名单/排序 (code, datetime)/decode/raw 与
  单位语义），差异：codes 混合 6 位或 ts_code 均可（任一 6 位无 stock_basic
  映射 fail fast）；**date_start 与 date_end 都必填闭区间**（无单边开窗——批读
  防全表扫描）；输出 code 一律 6 位。
- `load_bars_1m_coverage(rd, *, date_start, date_end, codes=None) -> pl.DataFrame`
  **分钟覆盖只读聚合**（R03-I6 静态池生成/审计辅助；不参与 run 链）：每 code
  一行 `code`(6 位)/`covered_days`(有 bars 的交易日数)/`first_date`/`last_date`/
  `min_rows_per_day`/`max_rows_per_day`，按 code 排序；与 trade_cal 交易日数
  对比即得「窗口全覆盖」集；codes None=全市场（6 位/ts_code 混合，未知 6 位
  fail fast）；闭区间必填；仅 ch（duckdb 显式 ValueError）。**口径警示**：按
  全窗覆盖选样生成静态池本身含前视（用未来存活信息选宇宙），只应作为研究
  便利并知情披露；PIT 安全的 run 级口径是 `FACTORLAB_MINUTE_UNCOVERED=drop`
  （见 §4 分钟覆盖口径节）。
- `load_tick_trades(rd, code, ...)` 逐笔成交（tick_trades）：time_ms（当日毫秒
  数，00:00 起）、trade_no UInt64、bs UInt8、price_x10000 Int32（元 = ÷10000）、
  volume UInt32、ask_seq/bid_seq UInt64。排序 (time_ms, trade_no)。
- `load_tick_orders(rd, code, ...)` 逐笔委托（tick_orders）：order_no/
  exch_order_no UInt64、order_type/bs String（上游语义原样）、price_x10000
  Int32、volume UInt32。排序 (time_ms, order_no)。
- `load_tick_snapshots(rd, code, ...)` 秒级快照（tick_snapshots）：默认投影
  核心列（price/volume/amount/n_trades/iopv/trade_flag/bs/cum_*/OHLC/
  prev_close/wavg_ask/bid/ask_total/bid_total），排除 10 档盘口与指数统计列；
  `cols` 全列名（66 列镜像生产 DDL）可请求。排序 time_ms。

共用合约：code 接受平台 6 位纯数字（经 stock_basic.symbol 解析为 ts_code，
未知 → `ValueError("未知 code ...")`）或带后缀 ts_code；输出 code 一律 6 位
纯数字（与 daily 读路径一致）。时间窗 `day='YYYY-MM-DD'` 快捷（= 单日闭区间）
或 `date_start/date_end` 闭区间（单边可开）；**完全不限制 → ValueError
（防全表扫描）**。`cols` 输出列白名单（顺序即输出顺序），未知列 ValueError。
`datetime` 统一 **naive Asia/Shanghai 墙钟 ms**（stored epoch = 源 wall；
arrow 读回带服务器 tz → `convert_time_zone("UTC")` 后剥）；bars_1m 的
`datetime` = **bar 起点**（left edge，连续竞价 bar 覆盖 [datetime, +1min)；
R03-M5 tick 对拍修正——15:00 竞价 bar 与 time_ms=15:00:00 成交 delta=0，
探针 governance/evidence/verification/R22/R03/misc/probe_m5_bar_time_labeling.{py,txt}）。
空结果（当日无数据）返回同投影空 frame 不抛。生产真数据 e2e 见
tests/test_intraday_prod_e2e.py。

### `factorlab.adapters.batch_flock.BatchFlock().run(tasks, worker, *, workers=1, stall_s=None, lock_path=None, state_path=None, success_marker=None) -> BatchReport`（R9）

P-5 批算编排真实现（`factorlab.ports.batch.BatchOrchestrator`；`Task(key, payload)` / `Result(key, status, metrics, error)` / `BatchReport(done, failed, skipped, results)` 在端口模块定义）：

- **失败记账不中断**：单元异常 → `Result(status="failed", error="<类型>: <消息>")`，其余照跑；
- **断点**：`state_path`（JSON，`{"done": [...]}` 与裸列表两种历史形态都读）里的 key 直接 `skipped`；
  每完成一个就原子写回（tmp+fsync+replace+目录 fsync）并**保留既有 key**；
- **单写者**：`lock_path` 被占 → `BatchLocked`，且**一个任务都不跑**（调用方惯例退出码 3）；
- **停滞看门狗**：`stall_s` 秒内无任何完成 → 在飞单元记为 `failed("stall: …")` 并**立即**返回，
  在跑的子进程 SIGKILL 回收（不挂死、不静默吞任务）；
- **`_SUCCESS`**：仅当 `failed == 0`（空批视为无失败）才落 `success_marker` 空文件，有失败**不落**。

`worker` 会在子进程里执行 → 必须是**模块级可 pickle** 的函数。

**R10 扩展缝**（三份产量循环实测共用的最小集，全部可选、缺省不变）：
`max_inflight`（在飞上限，缺省 = `workers`）、`mp_context`（如 `"spawn"`——fork 会连父进程的
大缓冲一起复制，16GB 目标机直接爆，产量循环必须 spawn）、`initializer`/`initargs`（worker 预热，
如载入只读 manifest）、`stall_policy`/`stall_strikes`（`"fail"` 缺省 | `"requeue"` 把在飞单元
退回队列重试）、`on_result(task, result_or_exc)`（父进程**逐结果回调**，按完成顺序；失败传异常；
回调自身抛错 → 该单元记 failed。**"worker 算、父进程写"的数据流靠它**，否则只能把整张表塞进
`metrics`）。**R14 再补三条产量循环专有缝**：`throttle()`（派单闸门：返回 False 则本轮不派新单——16GB 目标机按 `MemAvailable` 低水位派单）、`on_tick`/`on_tick_s`（等结果期间的周期回调——runbook 的 rss/内存审计）、`pool_hook(pool)`（池一创建即回调，含停滞重建后的新池——审计要按 worker pid 读 RSS 时据此拿到池）。派单为 **FIFO（任务序）**，与契约桩一致；停滞判定按"距上次完成的时间"（周期回调会切短单次等待，按次判定会误报）。协议参数名与真实现的漂移由 `tests/test_ports_contract.py` 的签名比对守住。

已采用（**三份编排样板全部切换**）：`converters/convert_tick_to_parquet`、
`lob_fact/extract_sz_cancels`（R10）、`lob_fact/pipeline/run_lob_batch`（R14）——切换前后
真实数据**内容逐值等价**（详见 `governance/evidence/verification/R10/`、`R14/`）。

## 4.1 Domain contracts（M6-01）

统一研究语义层（`factorlab.core.domain`）——Signal / Label 领域契约与信号时间语义。
**已接线**：run_factor 输出 canonical SignalArtifact/LabelArtifact（M7-05），
strategy/execution 链程序化消费（真实信号链端到端见
tests/test_execution_signal_chain.py）；CLI 面（factorlab run/list/corr/svd/serve）
仍只跑研究诊断、不直接驱动执行链（M8 无 CLI——不发明）。

### 时间语义（`factorlab.core.domain.timing`）

```python
SignalTiming(information_cutoff, available_at, default_earliest_execution)
DEFAULT_EOD_SIGNAL_TIMING   # CLOSE / AFTER_CLOSE / NEXT_OPEN
```

含义：**使用 t 日完整 OHLCV 的日频 EOD 信号，在 t 日收盘后才可获得
（AFTER_CLOSE），因此默认最早只能在 t+1 open 执行（NEXT_OPEN）**。
对象 frozen 不可变；本阶段不实现 calendar/execution timestamp 计算。

### 领域对象（`factorlab.core.domain.frames`）

| 对象 | 契约 |
|---|---|
| `SignalMeta` | name / frequency（当前仅 `1d`）/ timing / adjustment——frozen |
| `SignalArtifact` | frame + meta；必需 `date`(pl.Date) / `code`(pl.String) / `signal`(numeric)；`(date, code)` 唯一；允许额外非未来列 |
| `LabelArtifact` | frame；必需 `date` / `code` + 至少一个 `forward_return_<N>d`（任意 horizon） |

**SignalArtifact 显式禁止 future-return / label 字段**：`forward_*` / `future_*`
前缀与 `target` / `label` 精确字段一律拒绝（`ValueError`），重复 `(date, code)`
直接失败（不静默去重），dtype 错误直接失败（不自动 cast）。

LabelArtifact 供因子研究评估使用（可合法包含未来信息），**不得进入未来
Strategy Runtime**。Signal / Label 边界是 M6 最重要的 domain invariant。

## 4.2 PIT Universe（M6-02）

两阶段 Universe 模型：**Candidate Universe（数据加载候选集）→ PIT Eligibility（逐日 membership）**。
Universe membership ≠ tradability（M6-02 不实现 can_buy/can_sell——M8 Execution 职责）。

| API | 语义 |
|---|---|
| `resolve_codes()` | **legacy/static**：全期共用一组静态代码（含最新 ST 快照过滤与 date.start 一次性 min_list_days）——候选语义，不用于历史 PIT |
| `resolve_candidate_codes(spec, rd, override=None)` | 候选代码集：复用 override/ref/codes/rules 解析；rules 模式**只应用 exchange 与证券标识合法性**——exclude_st/min_list_days 属动态 PIT 条件，禁止提前应用。**M6-07B4**：rules 候选额外要求 canonical research identifier（`regexp_matches(ts_code, '^\d{6}\.(SH\|SZ\|BJ)$')`，pattern 单一权威来自 `domain.codes`）——legacy vendor aliases（如 `T600018.SH`）即使后缀匹配 .SH 也绝不进入候选。`rd` 读句柄（duckdb\|ch） |
| `resolve_universe_frame(spec, rd, dates, *, override=None, candidate_codes=None)` | date×code PIT membership——接受显式日期集（chunk 友好，不要求全历史生成）。ch 腿：arrayJoin 展开 + stock_basic LEFT JOIN（join_use_nulls=1 NULL-extension，与 duckdb 一致） |
| `align_to_universe(raw, universe)` | **Universe 驱动**的 active LEFT JOIN raw：raw 不能决定日期是否存在（某日 raw 完全无行 → active date/code 仍输出、行情 null）；universe 外排除。raw 至少 date/code；universe 至少 date/code/in_universe(Boolean)；code 严格 pl.String（前导零证券代码，整数无法无损表示）；duplicate/dtype/缺列 fail fast |

UniverseFrame schema：`date(pl.Date) / code(pl.String) / in_universe / is_listed / list_days / is_st / exchange`，
`(date, code)` 唯一，按 date/code 稳定排序。

PIT 语义：
- **listing**：`is_listed = list_date <= t AND (delist_date IS NULL OR t < delist_date)`（`t < delist_date` 平台语义）
- **list_days**：`date − list_date`（**自然日**年龄，非交易日数）；**pre-list（date < list_date）→ list_days = null**
- **ST coverage**：以 `min/max(stock_st.trade_date)` 为 coverage（v1 contract，内部 gap 的精确 provenance 留给 Data Coverage Registry）——coverage 内：当日快照出现 → true、缺席 → false；**coverage 外：is_st = null（unknown ≠ false）**；`exclude_st=true` 且请求日期落在 coverage 外 → **ValueError（fail fast，错误含 requested date 与 coverage 区间）**；缺 stock_st 表：exclude_st=true → ValueError、false → is_st=null
- **ST 显式降级（R03-I1，仅"缺表"一种 unknown）**：`exclude_st=true` 且库中**无** `stock_st` 表时，默认仍 **ValueError（fail fast，不把 unknown 当非 ST）**；只有显式设置 `FACTORLAB_ST_DEGRADE=allow`（`settings.st_degrade`，默认 `"fail"`）才降级为**无 ST 口径**——`warnings.warn` 响亮告警（文案含"ST 未知按非 ST 处理，结果为无 ST 口径"）、`is_st=null`（unknown ≠ false 语义保留）、`in_universe` 不做 ST 过滤；`run_factor`/`run_factor_minute`/`resolve_universe_frame` 调用方可用 `st_degrade_active(spec, rd, override=...)` 查询本 run 是否降级，run 摘要恒写 `st_degrade: true/false`（审计，不静默）。空 `stock_st` 表、请求日期在 coverage 外（后两种 unknown）**不受开关影响，仍 fail fast**。**挖矿口径**：CH 当前无 `stock_st`，全市场挖矿/复跑须显式 `FACTORLAB_ST_DEGRADE=allow` 接受无 ST 口径（挖矿 spec 保持库规范 `exclude_st: true`，不写"无 ST 影子 spec"）；有真实 `stock_st` 后应关开关按标准 ST 过滤复跑——降级结果与 ST 过滤结果口径不同，不得混比。**R29 当前裁决（2026-09-16）**：本机无 `stock_st` 历史源（CH 无表、`data/raw` 无快照、历史外部源 2026-09-17 已退役）——保留本开关为现行口径，不伪造 ST 数据；触发条件 = 外部 ST 历史源到位 → 建表灌入（沿本节 coverage 契约）→ 关开关按标准 ST 过滤复跑，届时解除降级口径。
- **exchange**：ts_code 后缀（.SH→SSE / .SZ→SZSE / .BJ→BSE）；默认池 SSE+SZSE，不意外纳入 BSE
- **范围（R37，2026-09-20 用户裁定）**：universe 解析默认排除 `.BJ`——`rules.exclude_bj`
  默认 true、仅显式 false 才纳入（未显式 `exchanges` 时默认交易所补 BSE；显式 exchanges 为准）；
  `.BJ` 一律不出现在 UniverseFrame（含显式 `candidate_codes` 旁路；显式 false 才保留并按
  exchange 规则判 `in_universe`）；codes/ref 分支同口径（ref 文件顶层 `exclude_bj: false` 放行）。
  范围外数据保留在盘、不删除不改写、不纳入研究读取（spec
  `2026-09-20-dq-scope-cut-addendum.md` §4；裁定源 `governance/workspace/pending-items.md` #24）
- 显式 codes 同样尊重上市/退市 PIT 状态（不自动增加 exclude_st/min_list_days 规则）
- 输入校验：dates 仅接受 datetime.date / ISO `YYYY-MM-DD`（非法格式、重复日期 fail fast）；candidate_codes 重复 fail fast；输出前主动验证 (date, code) 唯一
- **delist_date 保护**：`stock_basic.delist_date` 是语义关键稀疏字段——`build_final_db` 的 sparsity pruning 不得物理删除（PROTECTED_SPARSE_FIELDS，仅保护显式字段，不关闭整体 pruning）；旧 DB 无 delist_date 列时仍可运行，但 **delisting PIT is incomplete**（不伪造退市日期）。**R21 起**：ch 生产库 `stock_basic.delist_date` 已灌入（Nullable(Date)，代理 = 最后交易日 + 1 天，来源 = 退市股文件 in-file code 权威集合，断流兜底）；平台侧列兼容 Date 与 String 两形态（ch 编译器统一 `toString(toYYYYMMDD(...))`，R01-DATA-I4），且运行期有 staleness gate 兜底（见 §4.7）
- **suspend_timing 保护（M8-02B0）**：`suspend_d.suspend_timing` 是 execution-critical sparse data 且受 final-DB sparsity pruning 保护——null = 停复牌事件无具体日内时间区间，non-null = 存在日内 temporal evidence（决定 open suspension semantics，M8-02B）；约 99% null 是其数据本身语义，不是无价值缺失。**restoration 使用既有 frozen staging（`data/rebuild_staging.duckdb`，DATA_CUTOFF 2026-08-14）重建 candidate final 后 backup + 原子替换，无任何 source refresh 发生**

**PIT invariant**：membership at t 不能依赖 t 之后的数据（ST/listing/delisting 均 PIT；
平台库 stock_basic 缺 delist_date 列时退市信息不可用，is_listed 只基于 list_date——
此时由运行期 staleness gate 兜底：listed 但最后非空 close 早于面板末日 >250 交易日
（`adapters/read/staleness.py`，`run_factor` 面板加载后调用）→ **fail loudly**，
不静默 forward-fill 死价格；R01-DATA-C1）。

## 4.3 Universe-Aware Signal/Label Runtime（M6-03）

`run_factor()` 拆成两条独立 runtime（M6-01 domain contract 正式接线）：

```
Listed Market History → Signal Runtime → SignalArtifact
Listed Market History → Label Runtime  → LabelArtifact
        （PIT UniverseFrame 在两条路径入口：listed skeleton + active mask/keys）
```

- **Signal Runtime**（`engine.compute._compute_signal`）：candidate codes →
  PIT UniverseFrame → load listed market → `align_to_listing`（is_listed skeleton，
  停牌日保留 null）→ fill → 复权视图 → **universe-aware formula** →
  filter(in_universe=true) → process chain → SignalArtifact。**绝不计算 forward returns**。
- **Label Runtime**（`engine.compute._compute_labels`）：listed market →
  `compute_forward_returns` → active-at-t keys → LabelArtifact。t 的 label 只取决于
  t 是否 active——**t+h 的未来 universe membership 不参与 censoring**。
- **legacy panel** = signal LEFT JOIN labels + close（CLI/eval 兼容视图，行为不变）。

### Cross-sectional universe masking（`ops.universe_masking`）

CS/GP 算子的**数据参数**在 AST 层包 `if_else(__universe_active, arg, None)`：
TS/TA 仍见完整 listed history，CS/GP 只见当日 active 横截面。数据参数位置由
`_CS_GP_MASK_ARGS` 显式声明（cs_rank 等单参数；cs_resid(y,x) 双参数全 mask；
gp_rank/gp_mean 的 group key 不 mask）；**无法确认 mask 语义的 CS/GP
算子 fail fast（ValueError 含 operator name）**。mask 列 `__universe_active`
为内部保留列（来源 = PIT in_universe，用户不得定义），最终 SignalArtifact 不含。

### formula future guard

公式显式引用 `forward_*` / `future_*` / `target` / `label` → ValueError
（"future/label inputs are forbidden in factor formula"）——不等到 load_daily
unknown column。

### FactorResult

```python
@dataclass
class FactorResult:
    spec: FactorSpec
    signal_artifact: SignalArtifact | None  # 多输出（outputs != [signal]）为 None
    label_artifact: LabelArtifact
    panel: pl.DataFrame        # legacy compatibility view（多输出含全声明列）
    summary: dict
    signals: dict[str, pl.DataFrame] = {}   # M2 多输出逐输出 frame（date/code/<o>）
```
summary 新增 `candidate_count / signal_rows / label_rows / runtime_semantics`；
`universe_count` 保留兼容。M6-03 不落盘 signal.parquet/labels.parquet
（M6-05）；chunk label 尾部缺失保持现状（M6-04）。M2 多输出 summary 以
`outputs` + `signals` 块替代 signal_rows/signal_null_ratio（panel 无 signal
字面列）。

### M6-03A hardening（masking boundary）

- **import alias**：masker 与 `validate_partition_calls` 一致解析 alias
  （`from polars_ta.prefix.wq import cs_rank as cs_r` → canonical `cs_rank` 查
  metadata）；**不改写用户 callable**（`cs_r(if_else(...))` 保持）。注：无分区
  前缀的 alias（`as r`）是 expr_codegen 按名前缀分区的平台限制（非 masking 语义）。
- **registry alias**：metadata 一律经 canonical `OperatorDef.name` 查询——
  `factor_op(aliases=...)` 的 alias 不会绕过或误判 mask metadata。
- **keyword arguments**：CS/GP 的 keyword invocation（`cs_rank(x=close)`、
  `gp_rank(key=industry, x=close)`）→ fail fast（M6 v1 positional-only，
  masking 无歧义）；TS/TA/elementwise keyword 不受影响。
- **保留名空间（M1 收拢，常量单点定义 `factorlab/engine/reserved.py`）**：
  `__factorlab_*` 前缀 + `in_universe`（PIT 标记列）为平台内部保留名——
  **绑定与读取双门 fail fast**：绑定门（Assign/AnnAssign/FunctionDef/ClassDef/
  参数/import alias）与读取门（任何 Load 位置，含 def 体）都在
  `compute_formula` 顶部**无条件**生效（此前绑定门只在 masked 路径校验；
  M1 起 universe_mask=None 直调同样封），并在 `run_factor` 打开数据库前
  前置执行。内部 mask 列更名为 `__factorlab_universe_active`。

## 4.4 Exact Chunked Labels（M6-04）

chunked run 与 non-chunked run 的 forward labels 在研究样本内部**逐 cell 完全一致**
（right lookahead 只进 Label Runtime）。

每块双窗口：

```
Signal: [ left warmup | output chunk ]         ← 结束于 chunk_end（禁止传 label_end）
Label:                [ output chunk | right lookahead ]  ← 结束于 label_end
         chunk_start     chunk_end       label_end
```

- `label_lookahead_end(cal, chunk_end, horizon)`：chunk_end 向后 horizon 个交易日
  （截断到研究 calendar 最后一天）；chunk_end 不在 calendar / horizon<0 /
  calendar 为空 → fail fast
- **lookahead 可跨内部 chunk boundary，不可跨研究 sample boundary**——最后一块
  label_end = sample 最后一天（5d/20d 尾部 null 保持合法）
- Label Runtime：date_start=chunk_start（无左侧 warmup——forward 只需 t 与 t+h）、
  date_end=label_end；**Signal Runtime 仍只看 <= chunk_end**
- 每块输出双边裁剪 [chunk_start, chunk_end]——lookahead rows 不进任何输出
- 未来 membership 不 censor label（t+h ST/inactive 不影响 t 的 label——listed
  skeleton 承载未来市场历史）
- `DEFAULT_FORWARD_HORIZONS = (1, 5, 20)` 为 horizon 唯一来源（forward.py）

## 4.5 Versioned Result Artifacts（M6-05）

结果目录正式契约（`adapters/parquet_artifacts.py` + `adapters/strategy_artifacts.py`
+ `adapters/results_fs.py` 统一 I/O；原 `artifacts.py` 单模块已随 WS4 分层退役）：

```
runs/platform/<factor>/   # = 默认 <results_dir>/<factor>/（R24）
├── signal.parquet      ← legacy 单输出（outputs == [signal]）正式 signal artifact
├── signal__<o>.parquet ← M2 多输出布局（format v2）：每声明输出一个正式 artifact
├── labels.parquet      ← FactorEvaluator 使用的未来标签 artifact（evaluation-only）
├── panel.parquet       ← legacy compatibility view（CLI/eval/Web 兼容，非正式输出）
└── summary.json        ← manifest（最后写入 = core artifacts 完成标记）
```

主从关系：`SignalArtifact → signal.parquet`；`LabelArtifact → labels.parquet`；
`SignalArtifact + LabelArtifact + 兼容字段 → panel.parquet`（signal 绝不从 panel
派生）；**M2 多输出**：`outputs × N → signal__<output>.parquet × N`——多输出目录
**绝不写单列 signal.parquet**（不提供"signal = 某输出"的隐式别名，loader 无歧义）。

- **版本**：`ARTIFACT_FORMAT_VERSION = 1`（legacy 单输出 layout，行为不变）；
  **`MULTI_ARTIFACT_FORMAT_VERSION = 2`**（M2 多输出 layout：root 增 `outputs`
  声明列表，artifacts 条目 `signal__<output>` × N，无单列 signal 条目）——均为
  结果目录 layout 版本，整数可比较；Signal/Panel `schema_version = 1`
  （单 artifact 契约）；**Labels `LABEL_SCHEMA_VERSION = 2`**——horizons 固定
  `(1, 5, 20)`（D9/D11 起 forward 含 1d），v1→v2 迁移见下
- **文件名常量**：SIGNAL_FILE/LABELS_FILE/LEGACY_PANEL_FILE/SUMMARY_FILE（单一来源）
- **signal manifest** 含 SignalMeta（timing 以 Enum.value JSON 化：information_cutoff
  = close / available_at = after_close / default_earliest_execution = next_open——
  来自 SignalArtifact.meta.timing，非硬编码）；labels manifest 的 horizons 来自
  DEFAULT_FORWARD_HORIZONS；panel manifest 标记 `role: legacy_compatibility_view`
- **loaders**：`load_signal_artifact(result_dir)` / `load_label_artifact(result_dir)`——
  验证 format/schema version、manifest 文件名 == 平台固定名、M6-01 validator 复验
  磁盘内容。**绝不 fallback 到 panel.parquet**；旧结果目录（无 versioned manifest）
  明确报错（legacy result directory does not contain versioned Signal/Label artifacts）
- **label schema v1→v2 迁移（R30 fix 波）**：`LABEL_SCHEMA_VERSION = 2`——
  label schema v1 老产物（horizons=(5, 20) 时代）与过渡期 v1 产物
  （schema_version=1 但已含 1d 列）**均不可读**；`load_label_artifact` 报错文案
  指明 `v1→v2 迁移`，处置 = **重跑 run 按 v2（horizons=(1, 5, 20)）重生成**
  labels.parquet。writer 侧同门 fail fast（`validate_label_schema`：非
  `(1, 5, 20)` → `Label schema v2 要求 horizons == (1, 5, 20)`，零文件写入）。
  D7 口径：不 silent migration、不留兼容层、不重算历史 summary；
  存量产物按 §4.5 迁移处置（挖矿在途批次随重跑自然升级）
- **M2 多输出目录（v2）loader 行为**：per-output loader（`signal__<output>` 读取）
  在后续里程碑提供——本里程碑对 v2 目录明确报错（supported version 提示含
  "多输出布局（v2）：per-output loader 在后续里程碑提供，请以单输出
  outputs: [signal] 重跑"），不猜测主信号、不 silent fallback
- 单文件 atomic（temp + os.replace）；目录级事务不实现（见风险）

## 4.6 Semantic Guards（M6-06）

M6 边界 fail-fast invariants（不再新增计算能力）：

- **SignalArtifact**：future data forbidden（forward_*/future_*/target/label）——保持；
  合法扩展列（raw_signal/coverage/quality_flag）允许
- **LabelArtifact v2 contract（label schema v2）**：只允许
  `date / code / forward_return_<N>d`——signal/close/open/future_price/
  __factorlab_*/任意普通列 → ValueError；任意 horizon 仍合法（domain 不固定——
  label schema v2 的 (1, 5, 20) 在 artifacts loader 层；v1 旧契约 (5, 20)
  产物按 §4.5 迁移重跑）
- **core persistence 拒绝内部列**：signal/labels/panel 含 `__factorlab_*` →
  write_factor_artifacts 写文件前 fail fast（零文件写入——暴露 runtime 泄漏）
- **manifest integrity**：loader 验证 manifest rows/columns（含顺序）/horizons 与
  磁盘 parquet 实际一致；meta structural validation（缺字段/非法 Enum → 清晰
  ValueError 非裸 KeyError）；manifest 类型（rows 非负 int、columns list[str]、
  horizons positive strictly-increasing）
- **Signal/Label key 对齐**：`validate_signal_label_alignment()`（行数 + date/code
  键 + 顺序——Polars-native equals）——persistence 写文件前执行；不自动 sort/inner join
- **bundle loader**：`load_factor_artifacts(result_dir) -> FactorArtifactBundle`
  （signal + labels + alignment）——integrity/evaluation API，**不是 strategy-safe**
  （含未来标签）；Strategy consumer 只用 `load_signal_artifact()`（只加载 signal，
  不加载 labels）；bundle 不加载 panel
- **`load_signal_artifact()` 不加载 labels**（性能/职责隔离）

注意：M6-06 验证 manifest/parquet **semantic consistency**，不提供 cryptographic
integrity 或 immutable run identity（hash/data snapshot 属后续 reproducibility
里程碑——同改 parquet+manifest 的攻击无法检测，这是正常边界）。

## 4.7 Production Data PIT（M6-07）

**M6-07A 审计**：MARKET_DATA_COVERAGE_GATE=READY（2015-01-05→2026-08-14，daily/adj_factor 内部无缺日）；SAFE_EXCLUDE_ST_START=20160809（manifest completed 6450 天无 gap）；delist_date CASE C（final/staging 均缺失——TARGETED STOCK_BASIC REFRESH REQUIRED）；stock_st raw 10000 组重复（M6-07B 修复）。

**Gate 状态（M6-07B1 修正——行情覆盖完整 ≠ 完整 PIT universe 可运行）**：
- `MARKET_DATA_COVERAGE_GATE` = **READY**（2015-01-05→2026-08-14——纯行情覆盖）
- `FULL_HISTORY_PIT_GATE` = **R21 解除（ch）**——ch 生产库 `stock_basic.delist_date`
  已灌入（329 codes，代理 = 最后交易日 + 1；R01-DATA-C1），退市股 `is_listed`
  在退市后为 false，不再 forward-fill 死价格。缺列旧库（如未重建的 duckdb 平台库）
  由运行期 staleness gate fail loudly 兜底（>250 交易日断流且 listed）；恢复路径 =
  `data rebuild`（duckdb；已退役）或重灌 stock_basic（ch）
- `ST_AWARE_GATE` = **NOT_READY**（code-level duplicate-ST repair complete；
  production smoke pending stock_basic migration / token availability）

**M6-07B PIT 数据修复**：resolve_universe_frame 的 ST join 改为唯一 (trade_date,
ts_code) projection（raw stock_st 重复行不膨胀 UniverseFrame——is_st 由存在性决定，
不物理删 raw payload）；stock_st ingestion 用 dedup=True（retry 幂等，其他日频表
dedup=False 不变）；stock_basic fetch 显式字段（STOCK_BASIC_FIELDS 含
delist_date/list_status）+ fetch_stock_basic_all（L/D 合并、ts_code unique fail
fast、D 行缺 delist_date fail fast）+ migrate_stock_basic_pit_fields（定向迁移：
ALTER ADD COLUMN + upsert keys=ts_code，保留原字段）。真实迁移依赖
FACTORLAB_TEAJOIN_TOKEN（**历史：该 token/源已于 2026-09-17 Plan P T11 退役**）。

**M6-07B2 source integrity**：validate_stock_basic_source（纯 validator，唯一正式
入口）——list_date 非空、endpoint status 分区（L endpoint 全 L / D endpoint 全 D）、
list_status 仅 L/D、日期真实日历有效（YYYYMMDD）、delist_date >= list_date、
ts_code 匹配 ^\d{6}\.(SH|SZ|BJ)$、symbol == ts_code 前六位、ts_code
unique——全部 fail fast 不自动修复。production stock_basic migration requires
validated L/D source: non-null valid list_date, D delist_date, correct status
partition, identifier/date temporal consistency.

**M6-07B1 migration hardening**：two-phase（Phase-1 schema 事务外幂等补
list_status/delist_date；Phase-2 同一 connection 事务：UPDATE 已有行 PIT fields
（不覆盖 name/industry 等）、INSERT source 新 code、不删旧 code、validation
（uniqueness/before-preservation/source-completeness/D delist full-match/
list_status full-match）、COMMIT/ROLLBACK——不调用 db.upsert 不开第二写连接）；
fetch_stock_basic_all 收紧：L/D 必须非空（D 空更可能代表权限/API/schema 问题）、
list_date/delist_date YYYYMMDD 校验、symbol 非空。

**M6-07B4 quarantine legacy aliases**：vendor `stock_basic` 实测含历史别名
`T600018.SH`/`TS0018.SH`（上港集箱退市残留；冻结库同存、daily 均无行情）。
canonical research identifier 谓词收口到 `factorlab.core.domain.codes`
（`is_canonical_stock_code` / `CANONICAL_TS_CODE_PATTERN`——Python 与 DuckDB
SQL 共用）；`partition_stock_basic_source` 显式分区 canonical/quarantined：
canonical 走完整 validator（不弱化，canonical D 缺 delist 仍 BLOCK）；
非 canonical **退市** alias（suffix 合法 + symbol==ts_code 去后缀）进
quarantined（D 允许 delist=null）；其余形态 fail fast。**禁止** alias→canonical
映射/静默丢弃/硬编码白名单。`fetch_stock_basic_source` 暴露 quarantine（审计
可见），`fetch_stock_basic_all` = canonical-only 兼容 API；rules-based
resolve_codes/resolve_candidate_codes 加 canonical predicate——legacy aliases
绝不进 candidate_codes/UniverseFrame.code。冻结库中 T600018.SH/TS0018.SH
行保留为 inert（不删除；universe 不可选）。

## 4.8 Numeric Determinism（M6-07C2G/I）

**两层契约**：

1. **STRUCTURAL_EXACT**（严格位级相等）：schema/dtype/rows/(date,code) keys/key
   order/null mask/NaN/±Inf mask/labels/timing/PIT/canonical/ST filtering——
   以及非 reduction signal path（`signal = close`、QFQ 复权、labels）。
2. **FLOAT_REDUCTION_EQUIVALENT**（算子/负载特定数值等价）：连续 reduction
   的有限值对满足 `ULP_DISTANCE <= 4` **且** `abs(a-b) <= 8·EPS_FLOAT64·max(1,|a|,|b|)`
   （AND Gate）。当前绑定 M6 参考 `ts_mean(close, 20)`（全量 11.4M+ 行验证
   max ULP=4、scaled violations=0）。**bitwise mismatch count 本身不是
   reduction failure**（诊断指标）。

**Float64 ULP primitive**（单一权威，`factorlab.core.numerics`）：sign-aware 单调
IEEE bit 映射（**压缩零**：+0.0/-0.0 映射到同一序值——ULP=0 是映射本身性质；
零邻域 ULP(-min_subnormal, ±0.0)=1 且 ULP(±0.0, +min_subnormal)=1）；相邻
可表示 float64 → 1。QA comparator
（`factorlab.core.qa.numeric_determinism`）与 stable rank 共用，禁止两套 ULP 定义。

**cs_rank v2（stable dense rank，M6-07C2I/J）**：canonical operator 为
`cs_rank` v0.2.0，实现 = 平台 stable dense rank（`factorlab.core.ops.stable_rank`
的 `cs_stable_rank`——registry 中 `get_op("cs_rank")` 即该实现，vendor
polars_ta 的 0.1.0 cs_rank 不再占用 canonical 名；legacy exact tie 通过
`cs_rank(..., tie_ulps=0)` 显式获得）。Float64
近 tie（数值间隔 <= 4 ULP）按**组 anchor** 规则归为一个 dense level：
- anchor = 组内第一个（排序序）值；后续值 vs anchor ULP <= 4 才加入当前组
  （**anti-chaining**：A、A+4、A+8 → [A,A+4]、[A+8]，非传递合并）
- `cs_rank(x, True, 0)` 显式 legacy exact-bit tie 语义（迁移选项）
- 非 Float 输入 exact tie（不 fuzzy）；null → null；+0.0/-0.0 同组
- pct=True：level / max(K-1, 1)（0..1）；pct=False：1..K（UInt32）
- 动机（C2H）：数学真实 tie 被 rolling 路径 1 ULP 假拆分 → dense unique +1 →
  denominator 变化 → 全截面 normalized rank 平移（2022-12-06 单日 4,859 行）
- **4 ULP 不是全平台通用容差**——它绑定 M6 numerical contract + cs_rank v2；
  其他 discontinuous 算子（cs_quantile/cs_qcut/gp_rank 等）未自动获得该
  contract（后续 discontinuous-operator audit）。

**执行模式角色**：CHUNK = production execution mode（全历史）；FULL =
bounded reference/debug mode（受限窗口独立实现对照）。full-history FULL
不要求在当前 16 GiB reference machine（8 GiB 固定 pagefile，CommitLimit
≈23.87 GiB）完成。

**M6 已验证结果**：F1 全历史 120/60 结构 exact + ≤4 ULP；F2/F3 全历史
120/60 strict exact（stable rank 下 F2 亦 exact）；bounded F2/F3
FULL/120/60 strict exact；labels strict exact；F2_ST violations=0。

## 4.9 DQ 读取门（require_dataset；R37 范围门）

`factorlab.adapters.read.health.require_dataset(dataset="ashare_daily", as_of, *,
accept_quality, max_staleness, completeness_required, root, override_reason,
strict)` → `DatasetGate`。

读取 `data/health/<dataset>/<as_of>.json` 判定该分区是否 research-ready
（fail-closed；**只读 health 证明**，不重跑行级校验/不重算 OHLC）：

- `DataReadable = HealthValid ∧ FreshEnough ∧ Complete`：默认仅接受 `PASS`；
  `DEGRADED` / `UNKNOWN(LEGACY_UNVERIFIED)` 必须显式 opt-in（`accept_quality`
  + `override_reason`，自动写 Experiment Manifest 五字段）；`FAIL` 不可 opt-in；
  `completeness.status` 与 `freshness` 独立检查（不靠 coverage 推）；
  `strict=True`（正式 OOS/验收/基准）只接受 PASS；
- **范围门（R37，2026-09-20 用户裁定）**：`as_of < factorlab.core.scope.MIN_TRADE_DATE`
  （1996-01-01）→ `DatasetQualityError(status="OUT_OF_SCOPE")`，**无论 health 文件
  是否存在**；不属 `UNKNOWN`/`LEGACY` 过渡条款、无 opt-in 通道。范围外数据保留在盘、
  不删除不改写，只不纳入研究读取（口径与裁定源：`knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md` §3、
  `governance/workspace/pending-items.md` #24）；
- 拒绝异常带结构化上下文 `dataset/partition/status/freshness/guidance`
  （CLI/门面按字段渲染友好报错，不解析文案）；
- 策略链消费：`factorlab.app.strategy.run.run_strategy` 组合前对信号帧应用同一
  scope 谓词（`factorlab.core.scope.filter_frame`；历史产物兼容——不重算信号也不
  交易 BJ/1996 前证券），随即 `run_backtest` 按决策窗口末端过读取门（五字段进
  manifest）。

## 5. M7 Portfolio Construction

### 架构边界（最重要）

```
Factor Research Runtime
    │
    ├── SignalArtifact
    └── LabelArtifact
              │
              X
              │
Strategy Runtime
    │
    └── 只允许 SignalArtifact
```

**Strategy Runtime 永远不能消费 LabelArtifact / forward_return_* / legacy
panel**。未来策略回测必须从 SignalArtifact 开始。现有
`factorlab.core.eval.layered.layered_backtest` 继续保留——它直接使用
forward_return_*，定位为 **FactorEvaluator / Research Diagnostic**（NOT
Strategy/Execution/Portfolio Backtest）。

### M7-01 Domain Contracts

```
SignalArtifact
    │
    ▼
StrategySpec
    │
    ▼
PortfolioConstructor  [M7-02 ✓]
    │
    ▼
TargetPortfolio
    │
    ▼
Execution Runtime     [M8 ✓——M8-06B orchestration / M8-06C persistence，
                       见下方 M8-06B/06C 条目]
```

**StrategySpec**（`factorlab.core.strategy`，与 FactorSpec 严格分离）：

```
name               ^[A-Za-z_][A-Za-z0-9_]{0,63}$
signal_name        期望消费的 SignalArtifact.meta.name（M7-02 实际检查）
direction          ±1（signal 越大越优 / 越小越优；bool 拒绝）
selection          SelectionSpec（v1: top_k）
weighting          WeightingSpec（v1: equal_weight）
gross_exposure     0 < x <= 1（finite、非 bool；剩余为隐式现金）
rebalance_frequency "daily" only（调仓日历语义属 M7-03）
extra="forbid" + frozen——target/forward_return/label/commission/slippage
                   全部 fail fast
```

SelectionSpec：`k` strict int >= 1（"30"/1.5/True/False 拒绝）；
`tie_breaker="code_asc"`（cutoff 处相同 signal 按 code 升序——输入行序
不影响 Top-K）；`null_policy="drop"`（null 不进入 ranking）；`
on_insufficient` ∈ {use_available（全部可用）/ all_cash（显式 0 仓位）}。

**TargetPortfolioMeta**（`factorlab.core.domain.portfolio`，dataclass frozen）：
`strategy_name / source_signal_name / source_timing（复用 M6 SignalTiming——
不新建 Strategy/PortfolioTiming）/ gross_exposure / frequency="1d"`。

**TargetPortfolio**（dataclass frozen）：

```
frame：严格 3 列 decision_date(pl.Date) / code(pl.String) / target_weight(pl.Float64)
      ——禁止 signal/rank/forward_return_*/label/close/execution_price 等附加列
decision_dates：tuple[datetime.date,...] 严格递增唯一——与 sparse frame 分离
      （某日 0 rows = 显式 all-cash target，≠ 无决策）
meta：来源元数据
```

校验：`(decision_date, code)` 唯一（不 dedup）；code 必须
`is_canonical_stock_code`（复用 domain.codes，CASH/alias 拒绝）；target_weight
finite 且 0 < w <= 1（zero position 不创建 row）；frame date ⊆ decision_dates；
frame 必须按 (decision_date, code) 稳定排序（validator 只检查不自动重排——
artifact diff/hash/reproducibility 依赖稳定顺序）；**gross exposure invariant**：
有仓位日 sum(weight) ≈ meta.gross_exposure（1e-12，只检查不 renormalize）；
all-cash 日（0 rows）豁免。

**时间语义**：`decision_date = t` = 使用 t 日可得 SignalArtifact 的理想目标
组合。默认 EOD 信号（information_cutoff=CLOSE、available_at=AFTER_CLOSE、
earliest_execution=NEXT_OPEN）下**不代表 t close 成交**——最早 t+1 open
执行；execution_date 解析属 M8。

### M7-02 PortfolioConstructor

```python
construct_target_portfolio(
    signal: SignalArtifact,
    spec: StrategySpec,
) -> TargetPortfolio
```

流程：

```
SignalArtifact(date, code, signal)
    │
    ├── validate signal_name / frequency / canonical code / non-finite
    ├── per-date null drop
    ├── direction sort（±1）
    ├── code_asc exact tie break
    ├── top_k / insufficient（use_available / all_cash）
    └── equal_weight（gross_exposure / M）
    ▼
TargetPortfolio
```

- **Strategy Runtime 边界**：只接受 SignalArtifact（LabelArtifact/DataFrame/
  FactorResult/panel 一律 TypeError——无 DataFrame shortcut）；spec 必须为
  StrategySpec（dict 不自动转换）
- **输入边界全量校验**：signal.meta.name == spec.signal_name（不匹配 fail）；
  SignalMeta.frequency == "1d"（与 rebalance_frequency=daily 兼容）；全部
  code 必须 canonical（非法 alias 即使永不入选也 fail）；NaN/±Inf → fail
  （non-finite 无策略语义，非 null_policy 范畴）
- **decision_dates（M7-02 daily v1）= SignalArtifact.frame 中出现过的全部
  signal dates**（ascending unique）——不是交易所 calendar；M7-03 才引入
  独立 Rebalance Schedule
- **ranking**：null drop → direction 排序（+1 高→低 / -1 低→高）→ 相同
  signal 按 code ASC（exact tie，输入行序不影响）；ULP near-tie 不合并
  （M6 cs_rank tie_ulps=4 是版本化算子语义，不推广为 Strategy 算子——
  stable Top-K 语义单独评估）
- **insufficient**：N >= k → 选 k；0 < N < k + use_available → 选 N；
  N < k + all_cash → 0 rows（决策存在）；N = 0 → all-cash
- **equal_weight**：每支 = gross_exposure / M（Float64，无 residual
  correction——TargetPortfolio 1e-12 gross tolerance 吸收）；gross < 1 →
  隐式现金（不创建 CASH row）
- **输出**：sparse positions（0 weight 不建 row）、严格三列
  decision_date/code/target_weight、按 (decision_date, code) 稳定排序
  （与 ranking order 不同——ranking 只决定谁入选）
- **top-k ≠ execution**：今日目标 Top-30 不代表明天一定能全部买到
  （T+1/停牌/涨跌停/整手/费用/滑点属 M8）
- **source timing 原样传播**：TargetPortfolioMeta.source_timing =
  signal.meta.timing（不重建默认，未来不同 timing 也继承实际值）

### M7-03 Rebalance Scheduler

```
SignalArtifact
    │
    ▼
Rebalance Scheduler（daily / weekly / monthly）
    │
    ▼
RebalanceSchedule（decision_dates）
    │
    ▼
PortfolioConstructor
    │
    ▼
TargetPortfolio
```

**精确定义**：

- **daily**：每个 available signal date 都是 decision date
- **weekly**：每个 ISO calendar week（**ISO year + ISO week**——跨年正确，
  如 2020-12-31 与 2021-01-01 同属 ISO 2020-W53）的**最后 available signal
  date**——不要求 Friday（缺周五选该周最后 available）；partial first/last
  week 包含（v1 deliberate）
- **monthly**：每个 calendar (year, month) 的**最后 available signal date**
  ——不硬编码月末日历日；partial month 包含

**observed-signal-domain contract**：M7-03 v1 scheduler 只基于
SignalArtifact 提供的 observed dates（不读取 exchange calendar/DB）——
partial first/last periods 是 deliberate 行为，非错误。schedule 只由
日期域 + StrategySpec.rebalance_frequency 决定（signal 数值/code/全 null
均不影响）。

**NO DECISION vs ALL CASH**（M7 核心语义）：

```
date ∉ decision_dates
    = NO DECISION（该日无策略决策）

date ∈ decision_dates 且 0 positions
    = EXPLICIT ALL CASH（策略明确决定全部现金）
```

**constructor 接线**：`construct_target_portfolio` 内部调用
`build_rebalance_schedule(signal, spec)`——decision_dates 唯一权威来自
scheduler（M7-02 独立 date logic 已删除）；只处理 scheduled dates（非
decision 日不 ranking、不 carry）；scheduled 日全 null / insufficient 按
policy（all_cash/use_available）——**绝不回退到前一天**。

**TargetPortfolioMeta 区分**：

```
frequency          = signal.meta.frequency（"1d"——信号频率，不变）
rebalance_frequency = spec.rebalance_frequency（daily/weekly/monthly——调仓频率）
```

**execution separation**：weekly/monthly decision schedule ≠ execution
schedule——decision=t（默认 EOD：after close 可得、最早 t+1 open 执行），
实际 next open 由 M8 解析。weekly/monthly 不意味着固定持有期；TargetPortfolio
不在非 decision 日 forward-fill。

### M7-04 Strategy Artifact Persistence

```
SignalArtifact（source only——不复制，只记录 SignalMeta provenance）
    │
    ▼
StrategySpec / RebalanceSchedule / TargetPortfolio
    │
    ▼
strategy result dir
├── target_portfolio.parquet      （TargetPortfolio.frame 直接落盘）
├── rebalance_schedule.parquet    （decision_dates——core artifact）
└── strategy_manifest.json        （最后写 = 完成标记）
```

- **Schedule 是 core artifact**：TargetPortfolio.frame 是 sparse positions——
  显式 all-cash decision date（0 rows）必须由 rebalance_schedule.parquet
  单独恢复，否则丢失
- **strategy-safe**：Strategy loader 不加载 LabelArtifact / labels.parquet /
  panel.parquet（无关文件被忽略）；无 factor fallback（target 缺失即 fail）
- **provenance 边界**：记录 source SignalMeta（name/frequency/adjustment/
  timing）；**不绑定 source SignalArtifact byte identity**（hash/run-id 未
  实现——不伪装更强 provenance）；不保存 source 绝对路径
- **R21 覆盖写恢复语义（R01-M8-I1）**：写新 bundle 前先把旧
  `strategy_manifest.json` 原子失效（rename 为 `.stale` tombstone，load 检测
  到 tombstone 即明示拒绝并报告上一 bundle 已失效）；每文件写入后记录
  sha256，manifest **最后写**；load 侧交叉校验 sha256/列契约/nav 与
  nav_series 逐行一致（不一致 fail loudly，绝不拼接"新数据 + 旧 manifest"
  的混合 bundle）。失败后**不自动回滚**旧 bundle——恢复 = 重跑写
  （R01-M8-I1 验收语义）
- **版本语义**：STRATEGY_ARTIFACT_FORMAT_VERSION（目录布局）与
  TARGET_PORTFOLIO/REBALANCE_SCHEDULE/STRATEGY_SPEC SCHEMA_VERSION 独立于
  M6 factor artifact 版本
- **atomic（M7-04A）**：三个 core 文件全部走 sibling-temp + os.replace——
  失败的 temp 写入**绝不替换已存在的合法 final 文件**（target/schedule/
  manifest 一致）；writer 失败清理 temp 且不吞异常；manifest 最后写（缺失 =
  incomplete directory，loader 识别）；目录级事务未实现（partial write
  可能残留 target/schedule——无 manifest 即不可加载）
- **load provenance closure（M7-04A）**：bundle loader 重建 source SignalMeta
  并 cross-validate 完整链——source_meta.name == spec.signal_name ==
  schedule.source_signal_name == target.meta.source_signal_name；
  source_meta.frequency == target.meta.frequency；source_meta.timing ==
  target.meta.source_timing（valid-but-inconsistent tamper 全类检测；
  source_signal 结构/类型（name/frequency/adjustment/timing root/字段）严格校验）
- **round-trip**：spec/schedule/target（frame/decision_dates/meta）严格一致；
  tamper 检测覆盖 version/filename/rows/columns/dtype/spec/meta/timing
- 不复制 M6 summary.json；不保存 StrategySpec YAML 副本（manifest 已完整
  JSON 持久化）

**TargetPortfolio = 理想目标权重**（Strategy Decision Artifact），不是
actual holdings：T+1/停牌/涨跌停/整手/费用/滑点/部分成交/现金余额全部属
M8 Execution Runtime。现金是隐式 residual（cash = 1 - securities weight
sum），不创建 CASH pseudo-security。

### M7-05 Canonical Security Identity Handoff

```
M6 compute internals:
    symbol（"000001"——内部计算 key，非跨 runtime public identity）

        ↓  artifact boundary canonicalization

M6 formal artifacts（Signal/Label/legacy panel）:
    canonical ts_code（"000001.SZ"——is_canonical_stock_code）

        ↓

M7 / M8:
    canonical ts_code only（Strategy/Execution canonical guard）
```

- **边界位置**：run_factor 在 signal/labels chunk concat 完成后、
  SignalArtifact/LabelArtifact/legacy panel 构造前，执行一次
  symbol→ts_code canonicalization（resolve_canonical_code_map——每 run 只
  解析一次，不重复查 stock_basic）
- **mapping 唯一数据来源**：stock_basic.symbol ↔ stock_basic.ts_code——
  **禁止 prefix/exchange 启发式推断**（canonical identity 是 reference
  data）。完整性（N 输入 → N 映射，缺失 fail）、唯一性（同 symbol 多
  ts_code fail）、canonical 验证（is_canonical_stock_code +
  is_canonical_stock_row：symbol == ts_code[:6]）
- **legacy vendor alias（T600018.SH / TS0018.SH）**：不映射、不合并、不
  drop——到达 artifact handoff 即 fail fast（M6-07B4 quarantine contract）
- **M6 内部仍使用 symbol namespace**（load_daily/resolve/UniverseFrame/
  align/compute 不动——大规模改造无必要）；只有正式 artifact 输出
  canonical ts_code
- **summary.codes** 与 signal.parquet.code 同一 canonical namespace
  （candidate_count/universe_count 数值不变）
- **旧 M7-05 前的 factor artifact 目录**（含 6 位 symbol code）不静默迁移

## 6. M8 Execution Runtime

### M8-02 Execution Calendar + Market Open Snapshot

```
TargetPortfolio
    │ decision_dates + source_timing.default_earliest_execution
    ▼
trade_cal（唯一 calendar truth——复用 factorlab.adapters.read.calendar.trading_calendar）
    ▼
ExecutionSchedule（decision_date → execution_date）
    ▼
execution_date + canonical codes
    ▼
daily / stk_limit / suspend_d（raw evidence，精确 ts_code）
    ▼
MarketOpenSnapshot
```

**两层严格分开**：

```
trade_cal determines when market should open（calendar truth）
daily/stk_limit determine whether the backtest has execution evidence（data availability）
```

例：decision 2026-08-14 → trade_cal next open 2026-08-17 可成功解析；但
daily.max = 2026-08-14 → MarketOpenSnapshot(2026-08-17) 必须 fail
（"outside available daily market-data coverage"）——禁止 fallback 到最后
已知价/丢弃/假装停牌。

**ExecutionSchedule**（domain）：decision_date(Date)/execution_date(Date)/
execution_timing(String = ExecutionTiming.value)；三列 **non-null**；
decision unique、execution > decision、decision 与 execution 序列**严格
递增**（相邻 <，不依赖 sorted 表达 strict——duplicate execution date
fail）；空 typed 合法。

**resolve_execution_schedule(target, rd)**：timing 权威 =
target.meta.source_timing.default_earliest_execution（NEXT_OPEN/NEXT_WINDOW/
NEXT_CLOSE 日期解析相同——均为严格 > decision 的第一开放日；NEXT_WINDOW 的
分钟窗口是 ExecutionSpec 层成交配置（见「R22 分钟窗口执行」），日程仍是下一
开放日；NEXT_CLOSE market snapshot/fill 未实现）；decision date 必须 open（fail，不自动取周一）；
无下一开放日 fail whole（不 drop trailing）；空 target → 空 schedule；
all-cash decision 仍产生 execution event；一次 calendar 加载 + bisect。
`rd` 读句柄（duckdb|ch，M6 双后端化后 db_path 参数已废）。

**MarketOpenSnapshot**（domain）：execution_date + 严格 9 列
code/open/pre_close/up_limit/down_limit/has_daily/has_limit/
has_suspend_record/is_suspended_at_open（code String、价格 Float64、
证据 Boolean——is_suspended_at_open 为 M8-02B1R 增补的 suspension
证据列）；code canonical unique 排序；
四个 evidence flags（has_daily/has_limit/has_suspend_record/
is_suspended_at_open）全部
**non-null Boolean**（无第三 "unknown" 状态——数据覆盖 uncertainty 由
global coverage gates 单独表达；null 穿透三值逻辑会绕过 conditional
invariants）；has_daily=True → open/pre_close 非空有限 >0（False →
null）；has_limit 同理（down <= up）；**evidence ≠ fillability**——has_daily/has_limit/has_suspend_record/
is_suspended_at_open 都是 market-data evidence，不是 can_buy/can_sell
（M8-04 才定义 fill rules）；价格是 **raw daily.open**（禁止 qfq/hfq
复权价作成交价）。

**load_market_open_frame / load_market_open_snapshot**：精确 ts_code
查询（无六位启发式）；skeleton 由 requested codes 驱动（rows == codes，
无数据证券保留 has_*=False）；daily/stk_limit duplicate fail、suspend_d
DISTINCT collapse；daily/stk_limit 全市场日期覆盖 0 行 fail、suspend_d
0 行合法；缺表/缺字段 fail fast（**WS4：suspend_d 表可选**——表不存在
不再 fail，事件证据全 False；停牌 = 缺行推断，见 M8-06B）；执行价格
Float64（不沿用 M6 float32 research 设置）。

**load_adj_event_window(rd, *, start_date, end_date, codes)**（WS5 新增；
CA Gate 事件源）：只读加载 [start_date, end_date] 自然日历日闭区间 ×
canonical codes 的除权事件行（表契约 `adj_event(ts_code, trade_date)`——
研究侧由用户 K 文件事件列组派生：红利∨送股∨转增∨配股 ≠ 0 的行）。表缺失
→ typed empty frame（fail-closed 表格检查在 backtest CA Gate 层）；表在但
缺列 → fail fast；codes canonical + unique（duplicate fail）；end < start →
ValueError；输出 code String / trade_date Date、(code, trade_date) 稳定排序。

**load_adj_detail_window(rd, *, start_date, end_date, codes)**（R07-DATA-I8
新增；CA 调整量事件源）：只读加载同窗口 × codes 的除权**明细**行（表契约
`adj_detail(ts_code, trade_date, div_cash, div_bonus, div_transfer,
rights_num, rights_price)`，CH 由 `ch_ingest/adj_backfill.py` 从 daily_fact
除权 7 列全量派生；单位：div_cash 元/10股、div_bonus/div_transfer/
rights_num 股/10股、rights_price 元/股；非事件列 NULL）。同 adj_event 守卫
（表缺失 → typed empty 7 列；缺任一明细列 fail fast；codes canonical +
unique；end < start ValueError；(code, trade_date) **重复 fail**——重复明细
静默去重会二次入账）。输出 code String / trade_date Date / 5 个 Float64 明细
列（NULL 保留，NaN 归一为 NULL 与 CH Nullable 腿一致）、稳定排序。


### 架构边界

```
TargetPortfolio（ideal weights，M7）
    │
    │ ideal weights——weight space
    ▼
Execution Runtime
    ├── calendar resolution       [M8-02 ✓]
    ├── target→orders             [M8-03 ✓]
    ├── A-share fills             [M8-04 ✓]
    ├── accounting                [M8-05 ✓]
    ├── orchestration             [M8-06B ✓ run_backtest（见 M8-06B 条目）]
    └── artifact persistence      [M8-06C ✓ save/load_backtest_result]
    ▼
PortfolioState（actual cash/share inventory）
```

**TargetPortfolio ≠ PortfolioState**：前者是 desired weight space（策略希望
达到什么权重），后者是 actual cash/share inventory（账户实际持有什么）——
中间必须经过 Execution Runtime（真实 A 股存在 execution date/open price/
现金约束/整手/T+1/停牌/涨跌停/费用/部分成交，ideal ≠ actual 是独立 domain）。

### M8-01 Execution Domain Contracts

**ExecutionSpec**（`factorlab.core.execution.spec`，extra=forbid + frozen）：

```
initial_cash   positive finite float（bool/string 拒绝；int 规范化 float）
**不拥有 per-security quantity rules**（M8-01B：全局 lot_size 已移除——
  SecurityQuantityRule 是唯一数量权威；传入 lot_size 即 extra=forbid fail）
无成本参数（commission/stamp_tax/slippage 属 M8-05 Cost Model）
不重复时间语义（最早执行时点复用 M6 SignalTiming.ExecutionTiming）
```

**Per-Security Quantity Rules（M8-01B）**：

| Rule                | Buy                    | Sell                                    |
| ------------------- | ---------------------- | --------------------------------------- |
| ROUND_LOT_100       | >=100 and multiple 100 | round lot + one whole odd-lot remainder |
| STAR_MIN_200_STEP_1 | >=200, step 1          | >=200; holdings <200 must sell all      |
| BSE_MIN_100_STEP_1  | >=100, step 1          | >=100; holdings <100 must sell all      |

规则依据：SSE Trading Rules (2026) / SZSE Trading Rules (2026) / BSE
Trading Rules (2026)。**证券分类来自 stock_basic.market（+ ts_code suffix
一致性校验），不是 code-prefix 板块推断**。`resolve_security_quantity_rules`
read-only 解析（缺失/重复 reference、unknown market、impossible market/
suffix 组合 fail fast）；`is_valid_buy_quantity` / `is_valid_sell_quantity`
是 pure quantity 合法性（SELL 含零股/不足最小单位余额全量卖出语义；不接收
sellable_quantity——T+1 限制在 M8-03 单独处理；不做数量投影）。

**PortfolioState**（`factorlab.core.domain.execution`，dataclass frozen）：

```
as_of_date   datetime.date（datetime.datetime/str 拒绝）
phase        PortfolioStatePhase ∈ {PRE_EXECUTION, POST_EXECUTION}——
             同一天开盘交易前/后不是同一 state（直接影响 cash/quantity/
             sellable_quantity）
cash         finite >= 0（可用于证券交易的账户现金；可提现/结算未区分）
positions    sparse holdings——严格 code(String)/quantity(Int64>0)/
             sellable_quantity(Int64 ∈ [0, quantity])；code unique + canonical
             + 稳定排序；无日期列（共享 as_of_date/phase）；空 typed frame
             合法（cash-only state）；0 quantity 行拒绝
```

quantity = 当前持有股数；sellable_quantity = 当前时点依法可提交卖出的股数
（A 股 T+1 基础）——**transition 已实现（M8-04E
advance_to_next_trading_day）**：隔夜 sellable += 当日 actual BUY filled
股数（provenance-aware——只释放成交源股数、非整仓放行；POST(d) → PRE
(次日 open) 每轮恰一次，重复调用 ValueError）。不保存 weights/price/
market_value/cost（估值属 M8-05）。

**OrderBatch**（dataclass frozen）：

```
decision_date    datetime.date（未来对应 TargetPortfolio.decision_dates）
execution_date   calendar resolver 解析出的真实交易日期（> decision_date——
                 默认 t close decision → after_close → next trading day open；
                 M8-01 不负责计算）
execution_timing 复用 M6 ExecutionTiming（NEXT_OPEN/NEXT_WINDOW/NEXT_CLOSE
                 枚举——不新建；M8-03 v1 规划路径只接受 NEXT_OPEN 日程
                 timing，见 M8-03「规划路径 schedule timing」条目）
orders           strict code(String)/side(String "buy"/"sell"——
                 OrderSide.value)/quantity(Int64>0)；code unique（净订单，
                 buy/sell 同 code 必须先 net）；稳定排序；空 typed batch 合法
                 （目标==当前：execution event 存在但 0 orders）
```

不携带 target_weight/signal/price/cost（share-space action）。**不定义
Fill/Trade/PnL/NAV/MarketSnapshot**（M8-04/05 之前锁定 contract 过早）；
不读取行情/DB；不实现任何执行算法（resolve/generate/execute/fill/mark）。

### M8-03 Net Order Planning

```
TargetPortfolio
        +
PRE_EXECUTION PortfolioState
        +
ExecutionSchedule
        +
MarketOpenSnapshot
        +
SecurityQuantityRules
        │
        ▼
planning equity @ raw open
        │
        ▼
ideal target shares（floor(target_value / open)）
        │
        ▼
delta shares
        │
        ├── SELL
        │    └─ sellable cap（T+1）
        │       └─ quantity projection
        │
        └── BUY
             └─ quantity projection
                └─ proportional funding scale（sell-first funding）
        │
        ▼
OrderBatch（每 code 至多 1 行，code ASC）
```

**construct_order_batch(target, schedule, state, snapshot, quantity_rules, *,
decision_date) -> OrderBatch**（`factorlab.app.backtest.orders`）——单 execution event
planner（一次调用 = 一个 decision_date → 一个 OrderBatch；完整历史循环属
M8-06）：

- **没有 ExecutionSpec 参数**：调仓时刻的现金 authority 是
  `PortfolioState.cash`（M8-01B 后 ExecutionSpec 只剩 initial_cash——每次
  调仓重读会重置账户现金到初始值）
- **type guards 显式**：5 个对象参数必须各自类型（dict/DataFrame/None
  拒绝）；decision_date 必须 `datetime.date`（`datetime.datetime`/str 拒绝）
- **cross-object invariants**：schedule.decision_date 序列 ==
  target.decision_dates（数量/顺序/日期严格一致——missing/extra/不同顺序
  fail）；schedule 全部 execution_timing ==
  target.meta.source_timing.default_earliest_execution.value；selected
  decision ∈ target.decision_dates 且 schedule 中恰 1 行；
  schedule.execution_date == snapshot.execution_date == state.as_of_date；
  state.phase 必须 PRE_EXECUTION
- **规划路径 schedule timing 仍仅接受 NEXT_OPEN**：v1 只接受 schedule/target
  authority = `ExecutionTiming.NEXT_OPEN`（市场数据对象是
  MarketOpenSnapshot）；NEXT_CLOSE → `NotImplementedError`（禁止拿
  daily.open 冒充 next_close 成交/规划价）。**R22 分钟窗口不改变本层**：
  NEXT_WINDOW run 的 execution date 仍是下一开放日，规划参考价改由
  `run_backtest` 显式传 `planning_prices`（窗口首分钟 open；缺任一 planning
  code → fail fast，不发明价格）——OrderBatch/FillBatch 的
  execution_timing 仍为 NEXT_OPEN（窗口属于该 next-open execution event 内；
  见「R22 分钟窗口执行」）
- **planning universe = current ∪ target**（code ASC）；snapshot 与
  quantity_rules 必须**精确覆盖**（missing/extra 均 fail）；空 universe
  （空持仓 + 显式 all-cash）→ 空 OrderBatch（execution event 仍存在）
- **显式 all-cash ≠ 无决策**：selected target slice 0 rows = 显式 all-cash
  target（target shares = 0，无需 target weights/equity）；decision 不在
  decision_dates 中则 fail
- **planning equity = state.cash + Σ(current quantity × raw open)**——只用于
  weight → share sizing，不落盘、不构成 NAV/PnL/valuation artifact（正式
  accounting 属 M8-05）；target value = weight × planning_equity；
  ideal target quantity = `math.floor(target_value / raw open)`
- **target quantity 是 position-space integer**：ideal target holding 本身
  不需要满足 buy-order minimum（例如 STAR 数学 target 150 股合法，但从 0
  持仓 BUY 150 不合法 → 不下单）；target holdings ≠ order quantity validity
- **SELL**：desired_sell = −delta；sell_limit = min(desired_sell,
  sellable_quantity)（T+1 cap 位于 M8-03——只读取不改变
  sellable_quantity，隔夜 transition 仍属后续任务）；`_project_sell_quantity`
  取 <= limit 的最大合法 SELL（ROUND：整手或一次完整零股 remainder 中取
  最大；STAR：H<200 只能全量 L>=H→H；BSE 同理阈值 100）；**不 oversell
  target**（STAR H=250 target=100 → SELL 0，不得 SELL 200）
- **BUY**：desired_buy = delta；`_project_buy_quantity` 取 <= desired 的
  最大合法 BUY（ROUND：floor/100×100；STAR >=200 step1；BSE >=100 step1）；
  先算 provisional buys → provisional notional
- **sell-first funding**：buy_budget = state.cash + planned sell proceeds
  （仅规划层 funding assumption——**不代表 sell 已成交**）：
  > M8-03 buy budget may credit planned sell notional for deterministic
  > sell-first sizing. This does not assert those sells fill. M8-04 must
  > recompute actual cash after realized sells before accepting buy fills.
- **funding insufficient → proportional scale**：funding_scale =
  buy_budget / provisional_notional（禁止按 code 顺序抢现金）；每 code
  scaled_cap = floor(provisional × scale) 后**再次 quantity projection**
  （ROUND provisional=500 scale=0.75 → cap=375 → 最终 300）；残余现金
  **不 redistribute**（不二次分配/不按 code 填手数/不贪心补仓——避免隐含
  priority）；最终 Σ(final buy × open) <= buy_budget（float 容差
  1e-10×max(1,budget) 内显式验证，超限 RuntimeError——不允许
  silently negative planned cash）
- **evidence 边界**：`has_daily` 是 **sizing price evidence**（非
  can_trade）——非 all-cash 的整个 planning universe 必须 has_daily=True
  （缺 open 无法反推 target shares/equity/sell funding），失败文案是
  "missing sizing price evidence"（**不是**"停牌/无法交易"）；all-cash
  target 允许 has_daily=False（target shares=0，SELL intent 仍由
  holding/sellable/quantity rule 决定，能否成交属 M8-04）；
  `has_limit`/`has_suspend_record`/`pre_close` **不参与规划**（订单不变，
  M8-04 才定义 fillability）
- **输出**：严格 OrderBatch schema（code/side/quantity，Int64）；每 code
  至多 1 行（不会 BUY+SELL 同现）；quantity=0 不写行；code ASC；metadata
  = selected decision_date / schedule row execution_date / ExecutionTiming；
  empty OrderBatch 合法（target 已匹配/T+1 全锁/delta 低于最小单位/无现金/
  all-cash 无持仓）；纯 runtime（不访问 DB / SignalArtifact / StrategySpec）

**三个层级必须严格分开**：

```
TargetPortfolio（desired weights）≠ OrderBatch（desired legal share
instructions）≠ Fill（what actually trades——M8-04）
```

### M8-02B1R Suspend Timing Grammar（`factorlab.core.execution.suspension`）

`suspend_timing` 使用 **circular wall-clock interval model**（纯解析 utility，
不接入 MarketOpenSnapshot——integration 属 M8-02B）：

```
source grammar:
  NULL（absence；production absence 表示为 NULL only——''/空白均拒绝）
  <time>-<time>[,<time>-<time>]*
  time = H:MM | HH:MM | H:MM:SS | HH:MM:SS（分钟/秒两位；hour 0..23 /
         minute 0..59 / second 0..59）
```

interval 三类语义（**start >= end 不是 malformed**——source adjudication
修正后）：

```
start <  end → SAME_SESSION  [start, end)
start >  end → WRAPPED       circular：second >= start OR second < end
                             （跨 session/day boundary 的 time-of-day
                              coverage；不表示"回当日早上"，与 calendar
                              resolver 无关——不解析下一交易日是哪天）
start == end → FULL_CYCLE    任意合法 second 均覆盖（如开盘起的持续停牌
                             source 形式）
```

- 输出 seconds since midnight（如 09:30=34200、13:00=46800）；open
  reference 固定 09:30:00（`timing_covers_open` 默认 34200）
- parser 不 merge/不排序/不 dedup intervals、不补 gap；**Parser normalizes
  only into in-memory second offsets. Raw suspend_timing bytes/text are not
  rewritten.**
- **Malformed interval is source QA failure, not interpreted as absence and
  not guessed.**——runtime 对 grammar 外值 fail fast（ValueError）
- production 全量审计：2,636 non-null rows 全部可解析（same-session 2,837 /
  wrapped 1 / full-cycle 2 segment；1 interval 2,434 / 2 intervals 200 /
  3 intervals 2）

### M8-02B Open Suspension Evidence（`is_suspended_at_open`）

> **WS4 关闭注记（2026-09-07）**：本节 suspend_d 事件证据语义是
> suspension 模块（`factorlab.core.execution.suspension`）现行实现——表在即按
> 本节契约推导证据；但 **runtime 数据路径不再要求 suspend_d 表**（WS4
> 停牌 = 缺行推断：持仓缺行冻结 / 目标缺行跳过，见 §M8-02 loader 条目与
> M8-06B 条目）。表缺省时 has_suspend_record / is_suspended_at_open 恒
> False（loader 可选项，不再 fail）。

```
suspend_d raw events
        │ suspend_type + suspend_timing
        ▼
generalized circular timing parser（factorlab.core.execution.suspension）
        │
        ▼
09:30 temporal evidence
        │
        ▼
MarketOpenSnapshot.is_suspended_at_open（第 9 列，Boolean non-null）
```

**三层语义严格区分**：

```
has_suspend_record     = date-level raw event presence（当天任何 suspend/resume
                         record → True）
is_suspended_at_open   = 09:30 temporal suspension evidence（NEXT_OPEN 与
                         R22 NEXT_WINDOW 共用的日级证据：窗口路径 suspend →
                         跳过该 code；不是 can_trade / Fill——M8-04 才定义
                         tradability）
```

事件语义（frozen production contract）：

```
no raw event         → record False / open False
S + NULL             → record True  / open True（非日内限定停牌 evidence）
R + NULL             → record True  / open False
S + timing           → record True  / timing_covers_open(...)（SAME_SESSION/
                       WRAPPED/FULL_CYCLE 语义见 M8-02B1R）
```

**不支持的 source 结构 → fail fast（不是自动 precedence）**：

```
R + non-null suspend_timing → ValueError（production R+timing=0，source
                              contract 未证明 intraday resumption）
同一 date/code dedup 后 >1 distinct event → ValueError（production max
                              distinct=1；exact duplicate 允许 collapse）
suspend_type ∉ {S,R}（含 null/''/whitespace）→ ValueError（不 strip）
suspend_timing non-null 无法 parse → parser ValueError 向上穿透（不降级
                              presence-only）
```

**关键边界**：

```
has_suspend_record ≠ is_suspended_at_open
is_suspended_at_open=True → has_suspend_record=True（implication；converse
  不成立——R/NULL、later S 合法）
has_daily 不与 open-suspension 绑定：日内开盘停牌后复牌的证券可以
  has_daily=True 且 is_suspended_at_open=True（daily.open 可能代表复牌后
  第一笔成交，不是 09:30 可成交价）
production formal-gate 实测：open_suspended=True AND daily row exists = 1,075
  → **daily row exists ≠ 09:30 executable**
```

data loader（`load_market_open_frame`）——**WS4 修订**（与 §M8-02 loader
条目同步）：读面最低表 = daily/stk_limit/trade_cal——suspend_d **不再被
要求**（停牌 = 缺行推断）。表存在时仍按本节的 protected-field 契约读取
（trade_date/ts_code/suspend_type/suspend_timing 四列缺列 → fail fast；
事件行经 `_derive_suspend_evidence` 推导——时间 grammar 唯一 authority
是 suspension.py，不在 SQL 重写 temporal semantics，不存在退回
presence-only DISTINCT 的路径）；表不存在 → 事件证据全 False、不 fail。
skeleton 仍为 requested-code 驱动（rows == len(codes)、code ASC、空 codes
→ typed empty 9 列）。

### M8-04B Conservative Open Fillability（`assess_open_fillability`）

```
OrderBatch + MarketOpenSnapshot
        ↓
OpenFillAssessment（market eligibility，非成交）
        ↓
（M8-04C Funding + FillBatch ✓——cost-aware actual fills）
```

**assess_open_fillability(orders, snapshot) -> OpenFillAssessment**：
每条订单严格按序判定（顺序不可更改）：

```
1. suspension      is_suspended_at_open=True → BLOCKED_SUSPENSION
                   （决定性 market evidence——不需要 daily/limit 即可 blocked；
                    suspension 优先于 limit）
2. daily evidence  非 suspended + has_daily=False → ExecutionDataQualityError
                   （missing executable open price evidence——DATA UNKNOWN
                    ≠ TRADE REJECTED，不模拟 no-fill）
3. limit evidence  非 suspended + has_limit=False → ExecutionDataQualityError
                   （缺失 ≠ 无涨跌幅限制——M8-04A：92,147 daily rows 无 join）
4. outside limit   open > up 或 open < down → ExecutionDataQualityError
                   （raw Float64 比较，无 tolerance/epsilon/tick clipping）
5. adverse limit   BUY @ open==up → BLOCKED_LIMIT_UP
                   SELL @ open==dn → BLOCKED_LIMIT_DOWN
6. FILLABLE @ open interior / BUY @ dn / SELL @ up → FILLABLE（price = raw open）
```

**conservative modeling assumption**（不是历史事实）：

```
BUY at exact upper-limit open → modeled as no fill
SELL at exact lower-limit open → modeled as no fill

without order-book / queue-position evidence,
the platform does not reconstruct historical
limit-price queue fills.
```

favorable-side equality 明确：

```
SELL at upper limit → FILLABLE
BUY at lower limit → FILLABLE
```

**数据 Gate**：

```
missing/invalid execution evidence → ExecutionDataQualityError（继承 ValueError）
never → BLOCKED_*
MarketOpenSnapshot 的 execution-data quality invariant（has_daily=True 但
open/pre_close 非法；has_limit=True 但 up/down 非法）升级为
ExecutionDataQualityError——覆盖 M8-04A 的 open=0 / dn=NULL / BSE dn=0 证据；
结构错误（schema/dtype/duplicate/canonical/order/null-Boolean/implication）
保持普通 ValueError（program/domain construction error）
```

**OpenFillAssessment domain**：严格 5 列 code/side/quantity/disposition/
fillable_price（String/String/Int64/String/Float64）；与 OrderBatch 逐行
(code, side, quantity) 完全对应（rows == orders.height、code unique + ASC）；
disposition ∈ OpenOrderDisposition 四类；FILLABLE ↔ price non-null finite >0；
BLOCKED_* ↔ price null。**架构边界**：

```
OpenFillAssessment = market eligibility
不是 actual fills / cash settlement / portfolio mutation（M8-04C/05）
不接收 PortfolioState / quantity rules / DB
不做 partial / probabilistic fill / fee
```

### M8-05A Execution Cost Contracts（`factorlab.core.execution.costs`）

**为什么先于 M8-04C**：实际 SELL 成交后得到多少钱、BUY 实际有多少钱可用、
BUY 到底成交多少股——没有成本 authority 时 gross proceeds ≠ net available
cash。

**ExecutionCostSpec**（`factorlab.core.execution.spec`，frozen + extra=forbid，
嵌套于 ExecutionSpec.cost_model）：

```
commission_rate      0 <= r < 1（broker commission）
minimum_commission   finite >= 0（比例佣金被启用后的下限；rate==0 时不生效）
stamp_tax_sell_rate  0 <= r < 1（仅 SELL）
transfer_fee_rate    0 <= r < 1（BUY+SELL 均按名义金额；v1 不做 SH/SZ/时代
                     条件化——历史费率需版本化时升 CostModel v2）
slippage_bps         finite >= 0（10_000 bps = 100%；无上限）
```

**默认 zero-cost**：

```
default ExecutionCostSpec is zero-cost
zero default does NOT claim real A-share transaction costs are zero.
```

真实费率 broker-dependent / 历史变化 / market-rule dependent——生产 backtest
必须显式配置（M8-06 Gate 再 enforce；当前不 enforce nonzero）。旧调用
`ExecutionSpec()` / `ExecutionSpec(initial_cash=...)` 继续合法（zero-cost）；
root 直接传 commission/slippage/stamp_tax 仍 extra=forbid fail（必须嵌套）。

**compute_execution_cost(side, reference_price, quantity, spec) ->
ExecutionCostBreakdown**（pure；side 必须 OrderSide 实例；不接收
code/market/date/DB；不 import duckdb/polars）：

```
reference price → slippage（BUY ×(1+bps/1e4)、SELL ×(1-bps/1e4)）
→ execution price → gross notional = price × quantity
→ commission（rate==0 → 0；否则 max(gross×rate, minimum_commission)）
→ stamp（仅 SELL）→ transfer（BUY/SELL）
→ total fees → effective cash delta（BUY：-(gross+fees)；SELL：+(gross-fees)）
```

**slippage adjusts execution_price, not fee**（先 slippage 后 gross——fees
基于 slipped notional）。v1 为 **security-agnostic / time-invariant /
continuous Float64** 成本模型：

```
v1 uses continuous Float64 monetary arithmetic
（尚未建模 broker-specific fee rounding——无分位取整、无 Decimal authority）
```

pathological config（SELL execution_price <= 0 即 slippage >= 10000 bps、
SELL total_fees >= gross）→ ValueError（不输出 zero/negative proceeds）；
BUY 允许 fees > notional（minimum commission 小交易），cash requirement =
notional + fees。**ExecutionCostBreakdown**（frozen dataclass）：
gross_notional / commission / stamp_tax / transfer_fee / total_fees /
effective_cash_delta / execution_price——monetary result，不携带
code/date/strategy/quantity。

### M8-04C Cost-aware Realized Funding + FillBatch（`realize_open_fills`）

```
OrderBatch + OpenFillAssessment + PRE_EXECUTION state + snapshot +
quantity_rules + ExecutionCostSpec
        ↓
FillBatch（sparse actual fills——filled_quantity > 0 才有一行）
```

**FillBatch 是 sparse actual fills**：

```
FillBatch contains actual positive fills only.
blocked / funding-zero → absence from FillBatch
（原因由 upstream assessment/order pair 解释，不重建 blocked_reason）
```

严格 12 列：code/side/order_quantity/filled_quantity/reference_price/
execution_price/gross_notional/commission/stamp_tax/transfer_fee/total_fees/
effective_cash_delta；reference_price = assessment.fillable_price（= raw
open）；fees 直接来自 ExecutionCostBreakdown（唯一成本 authority，不手写
第二份公式）；gross = execution_price × filled；total = 三者精确和。

**Realization 顺序**：cross-object validation → quantity-rule/inventory
revalidation（所有订单含 blocked——市场 blocked 不掩盖 state/order
corruption）→ FILLABLE SELL full fill → actual net proceeds → available
cash → FILLABLE BUY candidates → **迭代 cost-aware funding** → FillBatch →
final cash safety（cash_after >= 0 严格，无 tolerance/clamp）。

**Realized SELL funding**：

```
BUY funding uses only actual net proceeds from FILLABLE SELLs.
（不是 M8-03 planned sell notional；blocked SELL 提供 0 现金）
SELL proceeds are credited before BUY funding
within the modeled NEXT_OPEN execution event——
deterministic accounting convention，不是交易所微观顺序声明
```

**Cost-aware BUY affordability** 包括 slippage + commission + transfer fee
（禁止 gross-only funding）。资金不足 → **global simultaneous scaling**：
每轮 scale = budget/current_total → 每 candidate cap = floor(q×scale) →
`project_buy_quantity`（rules.py 唯一 projection authority，M8-03 共用）→
重算 total cost → repeat 直到 cash-feasible（minimum commission 使 cost(q)
非齐次，可能需多轮；无 progress → RuntimeError；全投影 0 即移除；**无
greedy redistribution / 无 code-order favoritism**；残余现金不二次分配）。

**Slippage legal-bound Gate**：slippage 产生的 execution_price 必须
down <= price <= up（越界 → 普通 ValueError：raw market 数据合法，问题在
cost/slippage 配置——**不是 ExecutionDataQualityError**；禁止 clipping，
bounded-at-limit slippage model 尚未实现）。

**模型边界**：

```
M8-04C assumes market-FILLABLE SELLs fill in full.
BUY partial fills in M8-04C arise only from
account funding constraints,
not historical queue/liquidity reconstruction.
FillBatch does not mutate PortfolioState——
cash/quantity/sellable 迁移属 M8-04D。
```

### M8-04D Same-day POST_EXECUTION PortfolioState Transition（`apply_fill_batch`）

```
PRE_EXECUTION PortfolioState + FillBatch
        ↓
POST_EXECUTION PortfolioState（新 immutable state）
```

**Transition equation**：

```
POST cash = PRE cash + Σ FillBatch.effective_cash_delta
（与 M8-04C 同一 Float64 表达；无 Decimal/round/fsum 分支；
 empty FillBatch → cash 不变；finite >= 0 严格，无 tolerance/clamp——
 不一致 → ValueError "FillBatch is not cash-consistent ..."）
```

**BUY inventory**（A 股 T+1 核心）：

```
BUY: quantity += fill
     sellable_quantity 不变（当天新买入不可卖）
```

**SELL inventory**（卖掉的是可卖库存）：

```
SELL: quantity -= fill
      sellable_quantity -= fill
```

**Sparse holdings**：

```
quantity == 0 → position row removed（full liquidation 删除行）
全卖光 → typed empty positions（code String / quantity Int64 / sellable Int64）
sell-all-sellable-but-not-holding → quantity > 0、sellable = 0（position 保留）
```

**T+1 / overnight 边界**：

```
same-day BUY shares are NOT sellable in POST_EXECUTION state.
M8-04D does NOT release T+1 inventory.
POST_EXECUTION(D) → PRE_EXECUTION(next trading day) 属 M8-04E ✓
（advance_to_next_trading_day：T+1 释放 + 停牌复牌可卖）。
```

**Authority boundary**：

```
FillBatch is the sole actual-fill authority.
M8-04D does not recompute fillability, execution cost,
funding, or market prices（不 import cost/market/rules/DB/strategy）。
只消费 state.cash + state.positions + fills.frame.filled_quantity/
effective_cash_delta；不做 side netting / 不重新聚合；输出经真正
PortfolioState validator 构造；输入零修改。
```

### M8-04E Overnight T+1 Inventory Release（`advance_to_next_trading_day`）

```
POST_EXECUTION PortfolioState @ D
        + same-day FillBatch @ D
        + trade_cal
        ↓
PRE_EXECUTION PortfolioState @ next trade_cal open day
```

**Provenance rule**：

```
overnight release quantity = same-day actual BUY filled_quantity
（blocked / funding-zero BUY 不在 FillBatch → 不释放；partial BUY 只释放
 filled 部分；SELL fill → release contribution 0）
```

**Provenance-aware（禁止全量释放）**：

```
M8-04E does NOT set sellable_quantity = quantity.

因为 PortfolioState does not encode the reason for every unavailable
share——只有 FillBatch 携带 same-day T+1 acquisition provenance；
其它 unavailable inventory 必须保留（如 POST 1500/600 + BUY 200 →
 NEXT 1500/800，剩余 700 继续 unavailable）。
```

**State equation**（BUY code）：

```
next_quantity  = post_quantity（隔夜不增减股份）
next_sellable  = post_sellable + same_day_buy_filled
next cash      = post cash（无利息/结算费/分红）
POST_EXECUTION(D) → PRE_EXECUTION(next trade_cal open date)
```

无 same-day BUY 的 holding：quantity/sellable 严格不变。每个 BUY fill
要求 code 存在于 POST state 且 unsellable capacity
（post_quantity - post_sellable）>= filled（否则 state/fill pair 不一致 →
ValueError）。

**Calendar authority**：`factorlab.adapters.read.calendar.trading_calendar` 是唯一
authority（禁止自写 trade_cal SQL / weekday arithmetic / timedelta）；
当前 date 必须 is_open=1；下一开放日严格 > 当前（无则 ValueError——
不保持原日期）。

**Calendar/data separation**：

```
rolling state to a future trade_cal open date
does not imply market data exists for that date.
（production 验证：trade_cal 解析 2026-08-14 → 2026-08-17 成功，但
 MarketOpenSnapshot(2026-08-17) 在 frozen DATA_CUTOFF=2026-08-14 下
 仍按 coverage Gate fail——不刷新数据）
```

### M8-05B Execution Accounting + Point-in-Time NAV Kernel

**两条独立但可交叉验证的 accounting primitive**（`factorlab.core.execution.accounting`
/ `factorlab.core.execution.valuation`，domain 在 `factorlab.core.domain.accounting`）：

```
A. summarize_execution_accounting(pre_state, fills, post_state)
   → ExecutionAccountingSummary（realized accounting）
B. value_portfolio(state, marks: PortfolioMarkSnapshot)
   → PortfolioValuation（point-in-time account equity）
```

**A. ExecutionAccountingSummary**（frozen；execution_date + cash_before/
buy_gross/sell_gross/commission/stamp_tax/transfer_fee/total_fees/
net_cash_delta/cash_after）：

```
cash_before = PRE state.cash（唯一 authority）
net_cash_delta = Σ FillBatch.effective_cash_delta（唯一 authority——
  与 M8-04D 同一 Float64 reduction path，禁止 Decimal/round/clamp）
cash_after = POST state.cash
**cash bridge 严格**：POST cash == PRE cash + Σ delta（否则 ValueError
  "POST cash is inconsistent with PRE cash + FillBatch effective_cash_delta"）
buy/sell gross 与四项费用直接聚合 FillBatch 列；total_fees 按固定顺序
  = commission + stamp_tax + transfer_fee（禁止按 rates 反算——不接收
  ExecutionCostSpec）
PRE/POST phase 必须；三日期对齐；FillBatch timing = 仅 NEXT_OPEN（v1 唯一
可入账 execution event 标记——NEXT_WINDOW 的窗口成交同样经 NEXT_OPEN-stamped
FillBatch 聚合入账，见「R22 分钟窗口执行」）；不含 position valuation
```

**B. PortfolioMarkSnapshot**：显式 per-share valuation mark authority
（code/mark_price 两列；canonical unique ASC；finite > 0；typed empty
合法）。**PortfolioValuation**：frame 四列 code/quantity/mark_price/
market_value；position MV = quantity × mark（exact）；total MV = frame sum；
**NAV = cash + total MV——货币金额（如 1,112,200 RMB-like units），
不是 normalized cumulative NAV index**（normalized/unit_nav/returns 属
M8-06）。

```
value_portfolio：date 对齐；**exact coverage**（set(marks) ==
  set(positions)——missing/extra mark 均 ValueError，kernel 不默默忽略）；
  资产所有权基于 quantity（sellable_quantity 不参与估值）；
  no qfq/hfq/adj_factor valuation——**实际账户 shares × qfq 单位无资产语义**；
  overflow/non-finite → ValueError；不做 tolerance repair
```

**价值中性证明**（production 实测）：

```
ZERO_COST_ZERO_SLIPPAGE_EXECUTION_VALUE_NEUTRAL
PRE NAV == POST NAV == 1,112,200（相同 raw-open reference marks、
  zero cost、zero slippage 下，交易只改变 cash/holdings composition，
  不改变 account equity——同一 execution instant 的 PRE/POST，不是日收益）
EXECUTION_FEE_DRAG_RECONCILED：execution_price == reference_price 且
  fees > 0 → PRE NAV - POST NAV == total_fees
```

**边界声明**：

```
- PortfolioValuation assumes marks are share-unit-consistent with
  PortfolioState.quantity（caller 提供；kernel 不负责 price sourcing /
  stale-price / suspension valuation policy / corporate-action 修正）
- realized/unrealized PnL 未实现（PortfolioState 无 cost basis / lot
  ledger）；normalized NAV / daily return / drawdown / Sharpe 未实现
- **M8-06 Gate**：full-history NAV production-ready 前，M8-06 必须显式
  address/gate corporate-action position continuity（split / stock
  dividend / share consolidation / rights issue）
```

### M8-06B Backtest Runtime（`run_backtest`）

```
run_backtest(target, execution_spec, rd, *, marks=MarksPolicy.OPEN_BASED,
             decision_range=None) -> BacktestResult
```

- **纯 orchestration**：每 decision 编排已关闭 primitives（schedule →
  snapshot → orders → assessment → fills → POST → accounting → NAV →
  overnight advance）；零新 execution math；不接收 StrategySpec/SignalArtifact
- **execution_spec 必须显式传入**（cost model 显式选择 Gate）
- **MarksPolicy v1 = OPEN_BASED + 停牌冻结（WS4：缺行 = 停牌）**：POST
  holdings 以 execution date 的 raw open 标记（integration 层构造
  PortfolioMarkSnapshot）。**持仓 code 当日缺 daily 行 → 停牌冻结**：mark
  沿用该 code 最近一次真实 open（run 内 mark_map 携带，无历史表查询；
  多日停牌逐日沿用；复牌日真实 open 恢复）；**目标 code 当日缺行 → 隔夜
  停牌**：该 order 编排层跳过（不生成订单）、run 继续——整轮再无
  "缺 open → fail run"路径。既无 open 亦无先前 mark（结构上不可能）→
  防御性 ExecutionDataQualityError（不发明估值）
- **每 event sanity**：slippage-free 时 POST NAV == PRE NAV - total_fees
  （zero-cost → value-neutrality，同 basis open marks）
- **execution 间隔 > 1 交易日**：隔夜 advance 后 re-date；期间无 fills，
  除 CA 事件调整（下方 CA Gate）外 cash/quantity/sellable 不变
- 全链 fail fast；memory-only runtime object（无 persistence/DB 写入）；
  ExecutionArtifact/NavSeries/BacktestResult 见 domain/backtest.py
  （artifact = primitive 输出快照，cash bridge invariant 校验；
  NavSeries per-event 严格递增、nav == cash + market_value exact）
- **R21 trailing unresolved 语义（R01-M8-I5，m8-06a §6.3）**：最后一个
  execution 后无下一开放日 → **合法终止**（不 fail 全 run、不 drop 中间
  结果）：`BacktestResult.trailing_unresolved=True`，`final_state` = 最后
  一个 POST_EXECUTION 快照；**非最后** decision 的未决 advance 仍是硬错误
  （`ExecutionDataQualityError`）。decision_range 之外的尾部未决不参与本次
  run 的编排（R01-M8-I4）

**CA Gate（WS5 → R07-DATA-I8 v2；事件源 = `adj_event` + `adj_detail`）**：
懒性触发——仅"多事件 + 持仓（held(PRE) 非空）"run 武装（单事件/空仓 no-op）。
每相邻执行日窗口 **(prev_exec, exec] 左开右闭**（右端闭 = 除权 exec 当日零点
生效、隔夜持仓断链；左端开 = prev_exec 当日买入已以 post-CA 价建仓——买入日 =
事件日豁免，B6）内、held(PRE) 命中事件行 → **execution date 开盘前调整**
（调整后 PRE 状态才进入 orders/fills/NAV）：常见除权事件下**单 run 连续多年
可跑**，NAV 连续（Sharpe/回撤可直接计算，无分段重基）。

调整口径（V1，设计决策见证据 `R24/17-r07-fixes/ca/README.md`）：
- **资格股数 = PRE 持仓**（跨 exec 间隔无成交，PRE 持仓 == 除权前收市持仓 =
  登记日持仓）；同事件先按该基数计现金、再缩放股数（不重复放大）。
- **现金分红** div_cash（元/10股）：`cash += 资格股数 × div_cash/10`
  （入账时点 = 下一次 execution date 开盘前，V1 近似）。
- **送股/转增** div_bonus/div_transfer（股/10股）：`quantity` 与
  `sellable_quantity` ×= 1+(b+t)/10，**不足 1 股向下取整（floor 舍去）**；
  Decimal 精确缩放（float 朴素乘法在 100×1.01 等组合会少 1 股）；新股份
  随除权日到账可卖。
- **配股** rights_num ≠ 0（股/10股）：V1 **不参与**——shares/cash 不变 +
  `CorporateActionWarning`（除权价格落差自然计入 NAV = 未参与的真实成本；
  不按 rights_* 参与）。
- **多事件**按 (code, trade_date) 复合（送转后再分红用调整后股数）。
- NAV 连续性：调整后 marks 用当日 raw open（除权日价格已反映除权）× 新股数
  + cash；每 event 仍过 POST NAV == PRE NAV - fees（同 basis）sanity。
- 会计手算样例 / 4 年连续 NAV/Sharpe/回撤产物 / 存根必败：
  `governance/evidence/verification/R24/17-r07-fixes/ca/`。

**CA Gate fail-closed（保持不加宽）**——以下任一情形 → `ExecutionDataQualityError`：
- armed 且缺 `adj_event` 表（文案含"缺 adj_event 表"；空表 = 干净 run 通过）
- 命中事件但缺 `adj_detail` 表 / (code, date) 无对应明细行 / 明细全 0-NULL
  （两源不一致）——不得静默按无事件放行
- div_cash < 0 / div_bonus+div_transfer < 0（缩股）/ rights_num < 0
- 事件命中**停牌持仓**（exec 当日无 daily open；冻结 mark 为除权前 basis，
  调整后无法估值=不发明价格；WS4 × CA 交叉，B12）
- 明细非有限值；事件 code 不在 PRE 持仓（结构错误 fail fast）

**CA Gate 分段工作流（R03-I8，2026-09-16；R07-DATA-I8 后退役）**：R03-I8
口径属"CA handling 未落地"期——已支持事件（分红/送转/配股不参与）落地后，
全窗 run 直接产出连续 NAV，**分段重基不再需要**（
`test_b13_supported_event_full_run_passes_missing_detail_fails` 锁：有明细
全窗通过；缺明细仍 fail-closed）。仍未闭合的角落（停牌 × CA 等，见上）
按 fail-closed 拒绝（分段只搬走不适用的拦截，不修复会计正确性）。历史事实
仍成立：分段 run 是独立 run，每段从 `initial_cash` + 空仓位开始，段间持仓/
资金连续性丢失（`test_b14_segment_restart_drops_positions_and_cash_continuity`
锁）；若 caller 仍用分段（诊断等），段间拼接必须显式重基、边界 return 无
定义，不得当连续绩效。

### R22 Minute-Window Execution（`NEXT_WINDOW` + `minute_window`）

日频 EOD 信号 + 次日**分钟窗口内成交**（R22，2026-09-15；设计
`knowledge/design/workspace/2026-09-15-minute-execution/design.md`）。与
NEXT_OPEN 的关系：**日历语义不变**（decision t → 第一开放日 t+1 执行），
变化只在执行日**开盘后窗口内的成交仿真**与 marks 口径。

```
ExecutionSpec（L5）
├── execution_timing: ExecutionTiming   # NEXT_OPEN（默认）| NEXT_WINDOW
└── minute_window:   MinuteWindowSpec   # NEXT_WINDOW 必填；NEXT_OPEN 禁止携带
```

- **枚举**：`ExecutionTiming.NEXT_WINDOW = "next_window"`（`core/domain/timing.py`；
  NEXT_OPEN 默认行为逐值不变——digest ZERO-DIFF 硬门）。`NEXT_CLOSE` 仍显式
  拒绝（NotImplementedError；config/schedule/orders/fills/accounting/
  execution_store 各处保持不动）。
- **配置联动校验（fail fast）**：`execution_timing=NEXT_WINDOW` 必须提供
  `minute_window`（缺 → ValueError，"禁止隐式默认窗口"）；`NEXT_OPEN`/
  `NEXT_CLOSE` 携带 `minute_window` → ValueError（仅 NEXT_WINDOW 使用）。
- **MinuteWindowSpec**（frozen + extra=forbid）：
  - `start`/`end`：minute_index 闭区间 0..239（0=09:25 开盘集合竞价、
    239=15:00 收盘集合竞价；240 网格契约见 `bars_1m` 条目）；`end >= start`；
    非法类型/越界 → ValueError。
  - `price_basis` ∈ {`vwap`(默认), `open`, `close`, `twap`, `mid`}——本模块是
    口径唯一权威：`vwap` = 该分钟 amount/volume；`twap` = (O+H+L+C)/4；
    `mid` = (H+L)/2；数据不足的分钟跳过（不发明价格）。
  - `slices`：可选分批 `SliceSpec(start/end/weight)`；缺省 = 单切片
    [start, end] 权重 1；提供时须非空、按 start 严格升序、互不重叠、落在
    窗口内、权重和 = 1（容差 1e-9）、每片 weight > 0；每片目标量 =
    floor(weight × 总量)，尾差归最后一片。
  - `participation`：默认 0.10，0 < r <= 1；单分钟成交量上限 =
    floor(r × minute.volume)；超出部分顺延下一分钟；窗口结束仍未成完按
    `fallback`。
  - `trigger`：可选 `TriggerSpec(mode="limit"|"vwap_offset", ref, offset_bps)`；
    `None`（缺省）= 必成交（窗口内按参与率成交）。`limit`：基准 ref ∈
    {`pre_close`, `window_open`, `window_vwap`}，买 limit = ref×(1+offset/1e4)、
    卖 limit = ref×(1−offset/1e4)，**逐分钟重判**（买 `low<=limit` 命中、卖
    `high>=limit` 命中），成交价 = 买 `min(limit, open)` / 卖 `max(limit, open)`
    （限价或更好；价格回到 limit 才继续成交，绝不追价）。`vwap_offset`（及
    `limit`+ref=`window_vwap`）：limit 随**截至上一分钟**的窗口累计 VWAP 滚动
    （第一分钟无累计 → 不触发；全程不看未来）。缺基准价（ref=pre_close 无
    pre_close、窗口内无任何 open）→ ValueError——不发明触发价。
  - `fallback` ∈ {`none`(默认), `close`}：窗口结束仍有剩余 → `none` 如实
    不成交（记入 unfilled 明细）；`close` 用**最后窗口有价分钟 close** 一次性
    补足（不参与率约束、`fell_back=True`；该分钟一字封板则不补）。
- **窗口成交语义**（`simulate_window` / `realize_window_fills`）：
  - 一字板：某分钟 `open==high==low==limit_up` → BUY 跳过；`limit_down` →
    SELL 跳过；部分封板（high≠low）不拦。日级 `stk_limit` 仍为前置闸门
    （has_limit=True 时 up/down 必须合法；has_limit=False = 无限制，不拦）。
  - 停牌：执行日 daily 缺行沿用 WS4（持仓冻结 / 目标行跳过）；分钟缺行 → 该
    分钟跳过（盘中临停 V1 只表现为缺行——精确临停规则库属 V2）。
  - T+1 / 资金：SELL 先于 BUY；买入现金不足按比例缩减分钟成交量（floor，
    0 量分钟丢弃），严格 cash >= 0；成交/成本经 `compute_execution_cost`
    （每 code 聚合一次，滑点叠加成交价；has_limit 时仍做 up/down 边界
    Gate）。
  - 规划参考价：窗口首分钟 open（非 `snapshot.open`；缺任一 planning code →
    ExecutionDataQualityError fail fast，不发明价格）。
- **marks（`MarksPolicy.WINDOW_END_BASED`）**：NEXT_WINDOW run **无论 marks
  入参**均以执行日**窗口末分钟 close**估值；该 code 当日窗口无有效分钟
  close（缺行 / close 全 null）→ 沿用 run 内上次 mark（停牌冻结语义同
  OPEN_BASED）；`WINDOW_END_BASED` 与 NEXT_OPEN
  组合 → ValueError（NEXT_OPEN 必须 OPEN_BASED）。每 event sanity 与
  NEXT_OPEN 不同（mark 与成交价口径不同）：`POST NAV == PRE NAV +
  Σfilled×(mark − execution_price) − total_fees`（买 +、卖 −；rel 1e-9）。
- **执行日程/原语不动**：schedule/OrderBatch/FillBatch 的 `execution_timing`
  仍为 NEXT_OPEN（窗口属于该 next-open execution event 内）；accounting
  （M8-05B）继续只接受 NEXT_OPEN-stamped FillBatch——NEXT_WINDOW 不放松
  原语级 NEXT_OPEN-only 约束。
- **分钟数据仅 CH**：`load_execution_window`（唯一窗口读取入口）duckdb 后端
  → NotImplementedError（duckdb 平台库无 bars_1m）；契约校验 fail fast：
  (code, minute_index) 重复 / volume·amount null 或 <0 / session_type
  ∉ {0,1,2} → ValueError；OHLC 允许 null（该分钟由窗口引擎跳过）。运行需
  `FACTORLAB_DATA_BACKEND=ch`。
- **内存护栏建议（R05-C1）**：窗口读取随 universe × 窗口宽度增长（全市场
  多年回测按日分块自然增长）；重任务显式设 `FACTORLAB_MAX_MEMORY`（16GB 机
  推荐 8GB），不与 LLM 服务/多 agent 会话并发（见 §1「进程内存护栏」）。
- **返回与持久化**：`run_backtest(..., execution_spec.execution_timing=
  NEXT_WINDOW)` 返回 `WindowBacktestResult`（BacktestResult 扩展：
  `window_fills` 逐 event 分钟成交明细 + `execution_spec` 原样携带）；M8-06C
  持久化 **schema v2** = v1 布局 + `window_fills.parquet` +
  manifest.execution_spec（NEXT_OPEN v1 布局字节不变；spec 非 NEXT_WINDOW
  拒绝写 v2；load 缺 execution_spec / 未知版本 → fail fast，无 silent
  migration）。
- **策略 YAML（Plan S）**：`execution.timing: NEXT_WINDOW` +
  `minute_window: {...}` 原样映射 `ExecutionSpec`（`core/strategy/spec_io.py`
  透传 + pydantic 校验；NEXT_WINDOW 缺 window 在加载期拒绝）。
- **验收锚**：`tests/test_window_spec.py`、`test_minute_window.py`、
  `test_read_minute_window.py`、`test_window_fills.py`、
  `test_backtest_window_runtime.py`、`test_execution_store_window.py`、
  `test_minute_execution_e2e.py`（CH 真段：20 只 × 2024-01，含真实一字
  涨停日 BUY 不成交）；证据
  `governance/evidence/verification/R22/minute-execution/`。

### `factorlab.app.strategy`：策略侧报告（E3 成本后净值 / E4 容量代理）

评估指标 v2（R30）增强项的**策略层**交付（design §2b/§3；D11 因子侧纯净——
**不进因子评估 summary**，因子评估保持零成本统计口径）。纯函数、无 I/O，经
`factorlab.app.strategy` 导出：

- **E3 `cost_net_report(returns, turnover, cost_rate=0.0, periods_per_year=252) -> dict`**
  （`factorlab.app.strategy.cost_net`，R30 Task 8）：策略收益/换手序列 →
  成本后净值报告。口径 `net_t = gross_t − cost_rate × turnover_t`（与 R9 分层
  成本模型同式；`cost_rate` = 每单位**单边换手**的买卖总成本，如 A 股约
  0.0007），成本后净值 = `(1+net_t)` 连乘；返回 `cost_rate` / `periods` /
  `annual_turnover` / `total_cost` + `gross`/`net` 摘要（annual_return /
  annual_vol / sharpe / max_drawdown / win_rate）+ `net_nav` 序列。
  `cost_rate=0.0`（缺省）= 与 gross **逐值一致**（零行为变化）；输入长度不一致 /
  非有限 / `cost_rate` 越界 → `ValueError`。用途：不跑 M8 全套（涨跌停/停牌/
  CA）时对任意策略层收益序列做成本敏感性对照；**不得**写回因子评估
  `layered_backtest`（因子侧保持零成本理想口径）。
- **E4 `capacity_proxy(avg_amount, one_side_turnover, participation_rate=0.1) -> dict`**
  （`factorlab.app.strategy.capacity`，R30 Task 9）：容量代理 =
  `avg_amount × participation_rate / one_side_turnover`。`avg_amount` 为标的
  **日均成交额**（元，调用方从 daily 读面注入）；`participation_rate` 缺省
  **0.1**（ADV 参与率上限，公开经验值）——公式/系数/单位随结果字段披露
  （`formula`/`unit`/`layer`，可审计）。单边换手为 0 时容量无上界，显式
  `ValueError`（不伪造成大数）；ADV ≤ 0 / 换手 ∉ (0,1] / 参与率 ∉ (0,1]
  同样 fail loud。
- **边界（因子侧纯净）**：E3/E4 只存在于策略侧 `factorlab.app.strategy`；
  `factorlab.core.eval.*`（kernel / ic_series / layered）结果不含成本后净值/
  容量字段——`test_strategy_cost_net.py` / `test_strategy_capacity.py` 断言因子
  评估 summary 不出现对应字段（双向锁定）。
- **接线状态（R30 fix 波，2026-09-17）**：E3/E4 **当前无调用方**——纯函数已
  实现并导出，等 M8 / 策略报告接线（设计已豁免：策略层不在本次产品入口
  范围，interface 仅锁函数契约）。因子侧产品入口只接 E1（见 §4 eval 段）与
  E2（`evaluation.ic_decay`）。

### M8-06C Artifact Persistence Layer（`save_backtest_result` / `load_backtest_result`）

```
save_backtest_result(result, output_dir, *, created_at=None) -> ArtifactManifest
load_backtest_result(artifact_dir) -> BacktestResult
```

- 文件系统 only（parquet + manifest.json；目录由调用方显式提供——不猜测
  路径/不建 registry/不写 DB）
- 固定结构：artifacts/（execution_artifact / orders / assessment / fills /
  accounting / valuation / state / positions 各自独立 parquet——每 primitive
  输出单独保存，禁止合并后重算）+ state/final_state.parquet +
  nav/nav_series.parquet + manifest.json
- **R22 schema v2（NEXT_WINDOW）**：v1 布局 + `window_fills.parquet`（逐 event
  分钟成交明细）+ manifest.execution_spec（serialized ExecutionSpec）；NEXT_OPEN
  产物仍为 v1 字节布局；v2 仅接受 execution_timing=NEXT_WINDOW + minute_window
  （否则拒绝写）。详见「R22 分钟窗口执行」。
- **round-trip stable**：artifacts / nav_series / final_state 保存→加载后
  逐字段一致（load 只反序列化 + domain validator 检查——不重算
  NAV/accounting/fills）
- manifest：schema_version "1" / artifact_type / created_at（可显式注入——
  确定性输出）/ runtime_version / artifact_count / columns（每文件列契约）；
  **未知版本 fail fast——无 silent migration**。**R21 严格校验（R01-M8-I6/I7）**：
  `artifact_count` 必须为 int 且等于实际加载数（bool 拒绝）、columns 与固定布局
  全等、created_at/runtime_version 非空、日期范围与产物首末 execution date 一致、
  `execution_timing` 持久化并在 load 时读取（缺字段 legacy 显式拒绝——不静默
  硬编码 NEXT_OPEN）；每文件 sha256 与磁盘内容交叉校验
- error contract：目录缺失 / manifest 缺失 / 缺文件 / 缺列 / dtype 不匹配 /
  manifest 字段不满足上述严格校验 → ValueError（不自动修复）
- 支持 empty BacktestResult（typed empty parquet）
- BacktestResult / primitive / runtime 零修改

**组合配方（真实信号链，WS6——tests/test_execution_signal_chain.py 双腿
e2e 点亮）**：M8 无 CLI（不发明），"因子信号 → 执行"的装配序列 =
`run_factor`（M6 engine，读 daily/adj_factor/stock_basic/trade_cal，输出
canonical SignalArtifact）→ StrategySpec + `construct_target_portfolio`
（M7-02）→ `build_rebalance_schedule`（M7-03）→
`write_strategy_artifacts`/`load_strategy_artifacts`（M7-04 持久化，
round-trip 校验后消费 **loaded** bundle）→ `run_backtest(bundle.target,
execution_spec, rd)`（M8-06B）→ `save_backtest_result`/
`load_backtest_result`（M8-06C）。decision 日 target 权重来自真实 signal
排序（测试锁成员关系翻转）；干净窗口（无停牌/无除权事件）不触发冻结/CA
路径——纯信号链回归锚；stub 替换（硬编码 constant target）后测试必败。

## 7. 测试

运行：

```powershell
python -m pytest
```

当前覆盖 Spec 校验、AST 白名单、算子插件生命周期、最小计算路径、polars_ta 算子族、
平台薄封装、分区校验与防未来函数、CLI smoke、数据审计与读路径
（adjust/audit、读面列纪律 `test_column_discipline.py`、外部源清理守卫
`test_dataiface_clean.py`）、分层回测（`tests/test_layered.py`：分档/方向翻转/净值数学/long-short/摘要/
无效周排除/全 null 空回测）、run 参数与 list/show（`tests/test_cli_run.py`、
`tests/test_cli_list_show.py`）、真实平台库集成（`tests/test_e2e_m4.py`：
run → 周频评估 + 分层回测，回测期数 = 评估周数；`tests/test_e2e_free_form.py`：
free-form 端到端——A 股日频版 RunLength 思路因子 vol_run_energy（def 内窗口算子 +
params 替换 + run --set 变体，n_weeks > 50）），真实 results 目录 Web 冒烟
（`tests/test_e2e_web.py`：列表含因子名/详情含图表数据/旧因子降级/缺失 404）。
数据更新链（网盘 pan_update/转换/灌入）测试在 `platform/tools/`（`make test-research`）。

## 8. 数据平台（网盘更新链）

**现行链路（Plan P，2026-09-17 起）**：唯一外部源 = 夸克网盘分享
（PWD/PASSCODE 单点 `platform/tools/pan_update/config.py`；cookie 仓根
`quark_cookies.txt`，chmod 600、gitignored，`QUARK_COOKIE_FILE` 可覆盖）。
四类别（日K/分钟/日线资金/财报）经 `make data-update` 一键：
`sync`（分享树遍历 → 差集下载；**超分享直链上限的文件默认走转存回退**——用账号 cookie
转存到自有盘 `factorlab_tmp` 再自取直链下载，校验后清副本；`--no-transfer` 关闭后记
**manual_required**，写清单告警、不 fail 整链）→ `build`（类别阶段链：转换/灌入 CH）→ `verify`
（`platform/tools/ch_ingest/reconcile.py` 全库对账，rc=0 为一致；`all` = 幂等全链）。
工具/手册：`platform/tools/pan_update/README.md`；运维经验见
`data-ops-playbook.md`（《网盘数据更新手册》）；定时器
`governance/ops/install_pan_timer.sh`（user systemd `pan-data-update.timer` 每日
08:10，失败回退 crontab；日志 `runs/platform/logs/pan_update-*.log`；状态
`data/raw/pan_state.json`、flock `data/raw/pan_update.lock`）。
**内存护栏**：入口 `FACTORLAB_MAX_MEMORY=8GB`；stage 默认
`MALLOC_ARENA_MAX=2`（40 核 glibc arena VA 事故对策，T10）。

### 类别 → 目标端（T5-T8）

| 类别 | 网盘路径（`level2_detail/` 下） | 本地 raw | 转换 | 目标端 |
|---|---|---|---|---|
| 日K | `日K线数据---复权因子-经典技术指标--bs点缠论划线/`（全量 `19910101至*` + 增量 `YYYY-MM-DD至*` + `退市股/`） | `data/raw/daily/` | `ashare_ingest/import_daily.py` | `data/fact/daily_fact/daily_fact.parquet` → CH `daily` 层 5 表 + `stk_limit` + `adj_detail/adj_event`（`ch_ingest` 脚本） |
| 分钟 | `A股分钟线/<年>/<月>/<YYYYMMDD>.zip` | `data/raw/minutes/` | `converters/convert_minutes_to_parquet.py`（7z 魔数兼容） | `data/fact/bars_1m/` → CH `bars_1m`（月分区） |
| 日线资金 | `日线资金--每日沪深京个股日线数据和资金流数据/<年>/<MM>.zip` | `data/raw/fund_flow/` | `pan_update/parse_fund_flow.py`（`zj.xls` 个股 + `hyzj/gnzj.xls` 板块 GBK TSV → fact；`gn_detail.csv` 概念成分快照 → fact） | CH **`moneyflow`**（个股，脚本内 zip→CH）+ **`moneyflow_sector` / `concept_members`**（板块/成分，fact→CH）（`ch_ingest/ingest_moneyflow.py`，TRUNCATE+INSERT 幂等） |
| 财报 | `财报报表---有史以来--每周更新/`（周更 `*更新简化个股基本面数据.xlsx`；大件 `*_financial.parquet`/zip 超直链 → 转存回退，`--no-transfer` 时 manual） | `data/raw/financial/` | `pan_update/parse_fundamentals_xlsx.py`（openpyxl 快照） | `data/fact/fundamentals/fundamentals_snapshot.parquet`（旧版留 `.prev`）→ CH **`fundamentals`**（`ingest_fundamentals.py`，全量替换） |

超限大文件（`sync` 取链 HTTP 400 `download file size limit`）：默认 `--transfer`
自动回退——转存自有盘 `factorlab_tmp` → 自取直链 → `.part` + size 校验 + `os.replace`
→ 删网盘副本（不可逆；`--keep-drive-copy` 保留）；失败（task 超时/风控/校验不符）
记 `failed`（exit 1）且**不删副本**（下次同名同 size 复用）。回退不可用（cookie 缺失
等）或 `--no-transfer` → manual_required。人工处理：浏览器下载/转存后放入对应 raw
目录，`pan_update` 下次按本地命名登记并接续（`sync._adopt_local`）。
取链/下载两个实测硬闸（2026-09-17）：取链须官方客户端 UA（Chrome UA → 400 code
23018）；下载须客户端 UA + Range 分块 + 4 连接并行（整文件 GET 被 CDN 限速
~0.1MB/s，分块并行 ~8MB/s）。

### 分钟月分区提交语义（A3，2026-09-17）

`converters/convert_minutes_to_parquet.py` 生产模式以**月**为事务单元：`_SUCCESS`
只是提交边界，已提交月能否跳过 = 产物校验（schema/rows/row_groups）**+ 源归档
清单比对**（`_state/…/_daily_manifest.parquet` 逐日记录源 zip 的 name/size/sha256；
比对取轻量可靠者 = **name+size**，不重读内容）：

- 源清单与回执逐 (name,size) 一致 → `skipped`（幂等，产物不重写）；
- 源含回执全部归档（同名同 size）且另有**新增** → 判 stale，清理该月产物重转吸收
  （同月新增日不再被既有 `_SUCCESS` 钉死）；
- **同名 zip size 变化**（源被替换）→ 同判 stale 重转；
- 回执中某归档在当前源**缺失**（源回退）→ `SourceRollbackError` **fail loud**：
  不自动重转（重转会抹掉已提交历史日 = 不可逆数据损失），保留产物、非零退出；
  人工确认源后需先删该月产物再跑；
- 回执 manifest 缺失/不可读 → 保守判 stale 重转（不用旧产物冒充已提交）。

**CH 灌入侧月断点指纹（A4，2026-09-18）**：`ch_ingest` 的 bars_1m 月断点
（`state.json` 的 `bars_1m_<yyyymm>`）记**同月回执的源 zip name+size 摘要**
（`ch_source.source_fingerprint`，读 A3 同一份 `_state/…/_daily_manifest.parquet`，
不自创 digest 源）。`ingest_bars.run_pool` 判定：

- 断点缺（新月）→ 灌入并记指纹；
- **指纹变化**（转换器重转吸收同月新增日/同名替换）→ 该月 `DROP PARTITION` 重灌
  并更新指纹（转换器重转 → CH 自动跟随，`reconcile` 不再报 CH≠源）；
- 指纹一致 → 幂等跳过（不重灌，不重写）；
- **旧布尔断点迁移** → 首跑只回填当前指纹、**不重灌**（81 个存量月 ≈18.7 亿行
  不可全量重灌）；存量偏差由 `make reconcile` 暴露后经
  `ingest_bars.py --force YYYYMM[,YYYYMM...]` **点名重灌**（无视指纹的逃逸口）；
- 失败分区不写指纹 → 下次按同样判定重试。

tick 3 表断点仍为布尔 `true`（tick 回执是另一份
`tick_fact/_manifest/conversion_manifest.parquet`，未纳入本轮；残余见
pending-items A5）。

### CH 消费侧（读面）

- **moneyflow（T7）**：18 列经 `load_daily(cols=[...])` 按 `(trade_date, ts_code)`
  LEFT JOIN 供给公式（缺行 → null）：`main_net_inflow`、`auction`、
  `super_in/super_out/super_net/super_net_pct`、`big_in/big_out/big_net/big_net_pct`、
  `mid_in/mid_out/mid_net/mid_net_pct`、`small_in/small_out/small_net/small_net_pct`。
  单位=元（源 亿/万 归一）、占比=百分数、缺失=NULL。列映射单点
  `adapters/read/source.py::_MONEYFLOW_MAP`；moneyflow 已纳入
  `read/verify.ENGINE_SURFACE_TABLES` 读面列纪律（§4）。
- **fundamentals（T8）**：**当期快照**（`updated_date` 为行键，同一 code 可有多行），
  非历史 PIT 序列——不能回溯"某历史日已知财务值"；平台当前**无 `load_daily`
  消费方**。列（30）：`ts_code, updated_date, report_period, list_date, market,
  industry, sw_industry, sw_sub, total_shares(万股), float_a_shares(万股), eps,
  total_assets, current_assets, fixed_assets, intangible_assets, shareholders,
  current_liab, long_liab, capital_reserve, net_assets, revenue, operating_cost,
  op_profit, invest_income, op_cashflow, total_cashflow, inventory, total_profit,
  net_profit, undist_profit`（金额=元）。PIT 历史待多期快照累积/人工
  `*_financial.parquet`（默认转存回退可自动补齐）。
- **moneyflow_sector（R30 项 2）**：行业/概念**板块**资金流。列：
  `trade_date, board_type('industry'|'concept'), board_code('BK####'), board_name`
  + 与 `moneyflow` **同套 18 项**资金指标（同名列/同单位：金额=元、占比=百分数、
  缺失=NULL）。来源 `hyzj.xls`（行业）/`gnzj.xls`（概念），与个股同分享树；
  解析侧对「增仓占比排名」变体（20260903/0904/0908/0909）与个股串档
  （20260422）**显式跳过并 warning**（不入库）。覆盖：**2026-02-04 起**
  （147 日 / 77,524 行 / 558 板块；industry 18,480 + concept 59,044）。
  平台读面为通用 `open_read`（`Rd.query_rows/query_df`，无专用 load 函数）：
   ```python
   from factorlab.app.bootstrap import open_read
   rd = open_read("ch")   # 生产 FACTORLAB_DATA_BACKEND=ch
   rd.query_rows("SELECT trade_date, board_code, board_name, main_net_inflow "
                 "FROM moneyflow_sector WHERE board_type='concept' "
                 "AND trade_date=(SELECT max(trade_date) FROM moneyflow_sector) "
                 "ORDER BY main_net_inflow DESC LIMIT 5")
   ```
- **concept_members（R30 项 2）**：概念成分**每日快照**（PIT 成员；列
  `trade_date, board_code, board_name, ts_code`）。`ts_code` 带后缀
  （200/201→SZ、900→SH 的 B 股亦覆盖）；`board_name` 可为空串（源 BK1753
  20260728 实测），板块改名逐日如实。来源 `gn_detail.csv`（utf-8-sig）。
  覆盖：**2026-05-19 起**（87 日 / 7,281,555 行 / 964 板块；每日
  81,219..85,972 行）。判定"某日某概念成分"须按 `trade_date` 取当日快照
  （非长表 PIT 区间）。
- **index_daily**：CH 仍为空表（R29 裁决；`idx_ret` 恒 NULL）；网盘指数目录的
  `截止_*_指数…_日线.zip`（100MB）超直链上限，补数路径待人工核对（pending #25）。
- **对账**：`make reconcile` 覆盖 daily 层 + `moneyflow`/`moneyflow_sector`/
  `concept_members`/`fundamentals`（源帧 vs CH 行数/日期/天数/关键列/键/BK 码格式/
  每日行数分布，失败 exit≠0；R30 项 2 实测全库一致：moneyflow_sector 77,524 /
  concept_members 7,281,555，见 `governance/evidence/verification/R30/moneyflow-sector/`）。

### 旧平台（M3b，teajoin）——已退役（2026-09-17，Plan P T11）

数据平台层原以 teajoin（Tushare 兼容代理）为数据源、落地本地 DuckDB 库
（`factorlab data rebuild|update|refresh|verify`）。随唯一外部源收敛到夸克网盘，
`adapters/{fetcher,mirror_db,rebuild,refresh}` 与上述 CLI 已**删除**（历史实现见
git 历史；使用指南存档 `teajoin-guide.md`）。duckdb 后端仅保留为测试/历史只读库
（双腿测试设施不变）。

### 汇总速查

- `platform/tools/pan_update/`：`sync|build|publish|verify|all [--categories a,b]
  [--dry-run] [--prune] [--workers N] [--transfer|--no-transfer] [--keep-drive-copy]`
  （`publish` = build 同义；超限大件默认转存回退 `transfer.py`；详见工具 README）。
- `factorlab.adapters.read.adjust`：`view_prices`（raw/qfq/hfq/pit_qfq 价格视图）、
  `total_return`（HFQ 含分红再投资收益）、审计三查（`lookahead_check` /
  `scale_invariance_check` / `adjustment_sensitivity_check`）。

## 9. 挖矿服务（生产执行入口；R39，2026-09-21）

> **状态（2026-09-21）：容器化挖矿服务已退役**——生产路径 = 研究工作流（Prefect
> `make xpipe`）+ 宿主 `factorlab`/`flab` CLI；本节为 R39 历史交付留档（部署件不删）。
> 锁箱硬门在 execute 层，与执行形态无关，见 §10。

**定位**：正式/生产作业跑在**按 git ref 冻结的镜像** + 常驻作业服务内（SQLite 队列 /
FastAPI `127.0.0.1:8787`），与开发双向不干扰（开发改工作区/宿主 venv 不影响作业结果；
挖矿限额下 dev 基准劣化 ≤20%）。研究侧统一经客户端提交；宿主 `flab`/`factorlab` 直跑仅
dev/应急。设计与验收：`knowledge/design/platform/specs/2026-09-21-factorlab-service-design.md`、
`governance/evidence/verification/R39/`。

### 部署与运维

- 构建：`make svc-image REF=<sha|HEAD> [STABLE=1]`（`git archive` → `factorlab-svc:<sha>`，
  同时更新 `<results>/.service/image.json`）。
- 安装/启停：`governance/ops/install_svc.sh {install|status|uninstall}`（systemd user
  `factorlab-svc.service`，Restart=always + linger = 开机自启）。
- 容器（`governance/ops/service/run-service.sh`）：`--network host --user 1010:1010
  --cpus=8 --memory=16g --pids-limit=256 --memory-swappiness=0`；挂载
  `results`/`factor`/`experiments`（rw）、`composites`/`strategy`/`dossiers`/`index`/`lab`（ro）、
  专属缓存 `<results>/.service/cache`、`data/health`（ro）；不挂源码。
- **OOM 语义（部署修正）**：本机 cgroup v1 swap 不计限额，须 `--memory-swappiness=0`
  才能得到「限额触顶 → 内核 OOM 杀失控进程」语义（否则换页抖动、API 卡死；R39 实测）。
- 版本可证：`/version` → `image_ref/git_sha/uv_lock_hash/built_at`（构建时注入）。

### API

鉴权：token 文件（`~/.config/factorlab/service_token`）存在 → 需 `Authorization: Bearer`；
不存在 → 仅本机可用。错误信封 `{"error": {code, message}}`（404/409/422/429/401）。

| 端点 | 语义 |
|---|---|
| `POST /jobs` | 提交作业（校验白名单；返回 202 + `job_id`） |
| `GET /jobs` | 列表（`status`/`type`/`limit` 过滤，倒序） |
| `GET /jobs/{id}` | 详情（含 `dataset_version`、`image_ref`、时间戳） |
| `GET /jobs/{id}/log?tail=N` | 日志尾部（text/plain） |
| `GET /jobs/{id}/result` | CLI JSON 信封 + `service` 段（image/git/dataset_version） |
| `POST /jobs/{id}/cancel` | 取消（queued 立即；running → SIGTERM→10s→SIGKILL） |
| `POST /queue/pause` / `POST /queue/resume` | 停/启出队（running 不受影响） |
| `GET /health` / `GET /version` | 存活（busy/queue_depth/sqlite）与版本 |

作业类型与参数白名单（冻结）：`factor_run`（spec/set/universe/output_dir/profile）、
`compose`（spec）、`strategy_run`（doc/signal/accept_quality/override_reason）、
`factor_admit`（spec/scales/wait）。路径经 resolve 后必须落在研究产物区白名单根内；
`FAIL` 质量 opt-in 需 `override_reason`；逃逸/非白名单/非法参数 → 422 不排队。

### 客户端（研究侧推荐入口）

`$QUANTRESEARCH_ROOT/lab/platform_client.py`（纯 stdlib）：
`health/version/submit/get/jobs/wait/log/result/cancel/pause/resume/submit_and_wait`；
`submit_and_wait(type, **params)` = submit → wait →（成功时）result，返回
`{job_id, job, result}`。`spec`/`output_dir` 传**相对研究产物区根**的路径（容器内根
`/quantresearch`）；`output_dir` 必须在 `<root>/results/` 下。冒烟脚本
`$QUANTRESEARCH_ROOT/scratch/20260921_service_smoke.py --spec factor/<族>/<名>.yaml`。

### 状态与记录

- 状态机：`queued → running → {succeeded|failed|cancelled}`；服务重启时 running →
  `interrupted`（**不自动重跑**）；`pause` 后不出队。
- `dataset_version`：claim 当刻从 health 最新分区冻结入记录（`/jobs/{id}`、`/result.service`、
  SQLite 三处一致）。
- CH 账号（L2）：服务以只读账号 `svc` 连 CH（`readonly=2`、`max_threads=8`、8GB 内存、
  600 查询/时；凭据 `~/.config/factorlab/service.env`，0600 不入 git）；`INSERT/DDL` 被拒。
  配置方式：`users.xml` + `SYSTEM RELOAD USERS`（本机无 SQL RBAC 存储）。
- 磁盘预检：`run-service.sh` 启动时检查产物区余量（`FACTORLAB_SVC_DISK_MIN_GB`，缺省 20GB）。

### 已知限制（L3 建议）

作业与服务同容器：失控**非平台子进程**可能短暂拖慢 API（平台 8GB 作业守卫为一线防线）；
彻底隔离需作业独立容器/子 cgroup（L3）。

## 10. 锁箱纪律（Rolling Lockbox；R40）

**定位**：全库单一时间锁箱——锁箱窗口 `[window_start, window_end]` 内的数据是未观测测试集；
一切触碰锁箱的评估**自动登记**（append-only 台账），**终评**另受"每候选每窗唯一 + 每窗配额
M=20"硬门约束。设计：`knowledge/design/platform/specs/2026-09-21-lockbox-discipline-design.md`；
实施计划：`knowledge/design/platform/plans/2026-09-21-lockbox-discipline.md`；
验收证据：`governance/evidence/verification/R40/`。

**部署形态（2026-09-21 切换）**：R39 Docker 挖矿服务已退役（`governance/ops/service/`
留档），生产路径 = 研究工作流（Prefect）+ 宿主 `factorlab`/`flab` CLI。锁箱硬门位于
**execute 层**（`factorlab.app.run` / composite / strategy / admit / ref add），与执行形态
无关——宿主、工作流、容器内同样生效。

### 10.1 窗口

- **滚动 12 个月**：`roll(as_of)` 取 `as_of` 之前最近一个完整日历季末 `Qe`，
  `window_id=f"{Qe.year}Q{Qe.quarter}"`；`window_start` = 首个 ≥ `(Qe − 1 年 + 1 日)` 的
  交易日（交易日历）；`window_end` = 最新数据日（随数据自然生长）。
  例：2026-09-21 roll → `2026Q2`、`window_start=2025-07-01`；2026-10-01 roll → `2026Q3`、
  `window_start=2025-10-09`（2025-10-01~08 休市，首个交易日为 2025-10-09；旧窗解封并入 IS）。
- **`is_end`** = `window_start` 的前一交易日（`lockbox status --json` 直接输出）；
  挖矿 spec 写 `date.end = is_end` 即不碰箱。
- **幂等与倒退**：同一 `window_id` 重复 roll 不变更任何行（`--quota-final` 可显式改配额）；
  窗口倒退 → `LOCKBOX_ROLL_BACKWARD`。跨季未 roll 时任何评估 → `LOCKBOX_WINDOW_STALE`
  （防静默解封）。
- 角色判定：面板整段 `< window_start` → `is`（不要求 flag、不登记）；整段
  `≥ window_start` → `lockbox`；跨边界 → `mixed`（后两者 = 碰箱）。

### 10.2 CLI

```bash
# 状态：单行 JSON（含 is_end/配额/已用/剩余）；未初始化 → {"initialized": false}，exit 1
factorlab lockbox status [--json]
# 季度滚动（幂等；拒绝倒退；--as-of 供验收/复现注入——指过去=正常对齐，指未来=制造 stale）
factorlab lockbox roll [--as-of YYYY-MM-DD] [--quota-final N]
```

> `flab` = `factorlab research` 门面，**没有 `lockbox` 子命令**；锁箱运维入口是顶层
> `factorlab lockbox …`。

run 家族统一参数（Typer，`factorlab` 与 `flab` 同源）：

| 参数 | 语义 |
|---|---|
| `--lockbox exploration\|final` | 碰箱评估必需；缺失 → `LOCKBOX_INTENT_REQUIRED`（拒跑在开库/重链前，零产物） |
| `--lockbox-reason TEXT` | 与 `--lockbox` 配对、必填非空 → 否则 `LOCKBOX_REASON_REQUIRED` |

覆盖命令：`factorlab run`、`factorlab compose`、`factorlab research factor run`
（= `flab factor run`）、`factorlab research strategy run`（均带 `--lockbox`/`--lockbox-reason`）；
固化路径 `factorlab research factor admit`、`factorlab research factor ref add` 只带
`--lockbox-reason`（用于补登记），且要求已存在对应 **final** 登记，缺失 →
`LOCKBOX_FINAL_REQUIRED`（走同一唯一性/配额）。

### 10.3 错误码

| code | 触发 |
|---|---|
| `LOCKBOX_NO_CALENDAR` | 交易日历无 `window_start` 之后的交易日（响亮失败，不静默放行） |
| `LOCKBOX_EMPTY_DATA` | 最新数据日早于窗口起点 |
| `LOCKBOX_NO_STATE` | 碰箱但锁箱未初始化（先 `factorlab lockbox roll`；IS 运行不需要） |
| `LOCKBOX_WINDOW_STALE` | state 窗口 ≠ 当前季度窗口（跨季未 roll） |
| `LOCKBOX_ROLL_BACKWARD` | roll 窗口早于 state（拒绝倒退） |
| `LOCKBOX_INTENT_REQUIRED` | 碰箱无 `--lockbox` |
| `LOCKBOX_REASON_REQUIRED` | `--lockbox` 无理由/空理由 |
| `LOCKBOX_FINAL_DUPLICATE` | 同 `(window_id, fingerprint)` 已有 final 登记（登记层严格；同候选 guard 重跑复用不报错） |
| `LOCKBOX_QUOTA_EXCEEDED` | 窗口 final 数 ≥ `quota_final`（缺省 M=20） |
| `LOCKBOX_FINAL_REQUIRED` | `admit`/`ref add` 引用的评估窗口碰箱但无 final 登记 |

### 10.4 产物声明 `summary.sample`

碰箱评估的 `summary.json` 追加：

```json
"sample": {"role": "is|mixed|lockbox", "window_id": "2026Q2",
            "window_start": "2025-07-01", "window_end": "2026-09-17",
            "access_id": "01M3..."}
```

`is` 仅含 `role`；碰箱时 `access_id` 与 `lockbox_access` 登记一致，评估结束回填
`result_ref`。评估段与分层回测块记录 `date_start/date_end`（样本区间可追溯）。

**研究工作流（xscore pipeline）语义对齐**：流水线 **config = 候选**——flow 开始按同一
窗口/角色判定并**钉死候选身份**（`artifact_sha256 = panel 文件签名`、config 部分 = config
**文件内容 sha**，对齐 spec_fingerprint；收尾复用起点 fp，不重算）。无 state 或
`FACTORLAB_LOCKBOX=0|off|false` → 不读/不写台账（`unknown`/`[]`）；碰箱即幂等登记
**final**（命中复用 `access_id`；配额不足 `LOCKBOX_QUOTA_EXCEEDED`，报错指引
`factorlab lockbox status`）；stale（跨季未 roll）→ `LOCKBOX_WINDOW_STALE` fail-fast
（指引 `factorlab lockbox roll`），panel 缺失 → 拒以 `artifact_sha256=missing` 登记。
`access_id` 写入 run 级与 campaign 级 manifest 的 `access_ids`（campaign = 既有 ∪ 新 id），
`window_id`/`sample_role` 同步为本次真实值，收尾把 `result_ref` 回填为 run 的 out 目录；
流水线自身不 `roll`、不初始化台账（见 `research/tools/xscore/pipeline/README.md`）。

### 10.5 台账（`<research_root>/data/ledger.sqlite`，WAL；append-only）

- `lockbox_state`（单行）：`window_id/window_start/quota_final/rolled_at`——状态推进只经 roll。
- `lockbox_access`：`access_id(ULID)`、`ts_utc`、`window_id/window_start/window_end`、
  `kind(exploration|final)`、`fingerprint`、`artifact`、`params`、`command`、`result_ref`、
  `reason`、`actor`、`tool`。只增不改不删（触发器强制；唯一允许回填的列 = `result_ref`）。
- **终评唯一 + 配额**：`(window_id, fingerprint)` 唯一（指纹 = `sha256(canonical({kind,
  primary_artifact_sha256, params, window_id}))`，改参=新候选）；每窗口 final 计数 ≤ M
  （缺省 20，roll 时 `--quota-final` 可改）。探索不限额。
- 写入方只有平台（execute 层 guard）、研究工作流（xscore pipeline 的 final 登记）与研究侧
  `lab/lockbox.py`；禁手改（同 ledger 纪律）。
- 库路径/env：`FACTORLAB_LOCKBOX_DB` 覆盖（缺省 `<research_root>/data/ledger.sqlite`）；
  `FACTORLAB_LOCKBOX=0|off|false` 关闭硬门（直接 IS 放行、不读 state、不登记）——
  仅供 CI/离线基线，生产不设或设 1。

### 10.6 门与迁移

- **G-LOCKBOX**（`make gates`）：档案/manifest 样本声明字段齐全与格式（与 `window_id`
  季号一致）；宿主段与台账交叉核对 `access_id` 存在、kind=final；`lockbox/mixed` 另要求
  **至少一条 id 的窗口与 manifest 一致**（campaign 并集含历史窗 id 不判违规）；
  `is/legacy/unknown` 可挂历史真实 final id（引用仍须核验）。负向自检（缺字段/空 id/
  幽灵 id/探索冒充终评/锁箱仅旧窗 id/档案缺声明）。
- **G-ANNOTATE**：新/更新档案必须含 `sample_role`（缺 → 红）。
- **迁移（只管以后）**：存量 spec（含 175 个 `end=2026-07-31`）在锁箱初始化后被判定碰箱：
  要么收紧窗口 `date.end = is_end`（`lockbox status --json` 输出），要么显式
  `--lockbox exploration|final --lockbox-reason …` 意图；存量档案/`_oos2026`/参考库 OOS
  理由不追溯补登（grandfather）。
