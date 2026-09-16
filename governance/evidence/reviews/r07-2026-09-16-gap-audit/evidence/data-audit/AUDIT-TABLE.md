# R07 数据/运行侧缺口专项审计（只读实测）

- 审计时间：2026-09-16 18:29–18:37（CST）；审计员：R07 data-gap 专项（只读）
- 环境：CH server 26.3.22 @127.0.0.1:19000（db=factorlab）；平台 venv Python 3.13；
  `FACTORLAB_DATA_BACKEND` 未设置（默认 duckdb）；`FACTORLAB_ST_DEGRADE`/`FACTORLAB_MINUTE_UNCOVERED`
  未设置（默认 fail）；所有重任务显式 `FACTORLAB_MAX_MEMORY=8GB`。
- 约束遵守：CH 只读查询/小样本；无全市场长跑；最重的动作是 `make reconcile`（11.8s）；
  未与其他 agent 并发重任务。因子探针运行产物落 `runs/platform/r07_*`（gitignore 内）。
- 严重度：C=关键（工作流被卡且无可靠绕行）/ I=重要（有绕行或口径受损）/ M=次要/文档类。

## 审计表（逐条实测）

| # | 缺口 ID | 现状实测（命令+结果） | 用户影响（被卡工作流） | 文档/台账覆盖 | 严重度 | 证据 |
|---|---|---|---|---|---|---|
| 1 | 平台库 `factorlab.duckdb` 不存在（pending #1） | `find` 全仓无 `*.duckdb`；`platform/data/` 目录不存在；默认 backend=duckdb（`config.py:19`）。`factorlab list/show` 不触库（正常，exit 0）；`factorlab run` 默认后端 exit 1：`错误: 数据库不存在: data/factorlab.duckdb（可运行 data refresh 或检查路径）`。teajoin token 无（无 `.env`、env 未设、默认 ""）→ `data rebuild/refresh` 均不可用 | 不显式 `FACTORLAB_DATA_BACKEND=ch` 时**任何触库命令立刻失败**；决策点（CH 是否唯一后端）仍悬挂 | 已覆盖：data-map B2/B3、pending #1、AGENTS「已知的别踩」 | I | `01-duckdb-missing.txt`、`01b-duckdb-run-default.txt` |
| 2 | `stock_st` 表缺失，`exclude_st` 阻断 | `SELECT count() FROM factorlab.stock_st` → 表不存在（13 表中无）。默认跑 `exclude_st:true` spec → exit 1：`错误: exclude_st 需要 stock_st 表（平台库由 data rebuild 生成）——不能默认所有股票非 ST`。`FACTORLAB_ST_DEGRADE=allow` → `STDegradedWarning` + run exit 0 + `summary.st_degrade: true`（实测 r07_st_probe，n_weeks=2）。**164/174 个在库 spec 含 `exclude_st: true`（94%）** | 全市场因子跑默认被 fail-fast 挡；必须全局带 `FACTORLAB_ST_DEGRADE=allow`，结果口径为「无 ST 过滤」，不可与 ST 过滤结果混比；真实 ST 数量/影响面无法量化（无名称源） | 已覆盖：interface.md:1495-1496、R03-I1、AGENTS「已知的别踩」 | I | `02-stock-st-failfast.txt`、`02b-stock-st-degrade.txt`、`02c-st-degrade-summary.txt` |
| 3 | `index_daily` 0 行（000852.SH 依赖） | 全表 `count()=0`（非仅 000852.SH；0 行/0 code）。`idx_ret` 读路径为 LEFT JOIN 恒 NULL。对照实验：`signal = idx_ret + log(circ_mv)` → **`signal_null_ratio=1.0`、n_weeks=0、exit 0**；对照 `log(total_mv)` → null_ratio=0.0、n_weeks=2。7 个 `crash_bottom_leader/*` spec 用 idx_ret 全部死信号 | `crash_bottom_leader` 策略族不可复跑（dossier 已声明）；任何指数依赖研究在本数据面拿不到输入，且**不报错、静默全 null** | 已覆盖：dossier 文首状态块（R01-STRAT-I6）、ch README、ddl.sql 注释 | I | `03-index-daily.txt`、`08c-idx-circmv-impact.txt`、`10-affected-specs.txt` |
| 4 | CA Gate 拦停连续回测（R03-I8） | 真实最小复现（CH）：目标 600519.SH（真实事件 2026-06-26），决策 06-24→exec 06-25 买入、决策 06-29→exec 06-30 持仓跨窗 → `ExecutionDataQualityError`，文案含 code/事件日/decision_range 指引。`decision_range` 分段两段均可跑（exit 0），但每段从 initial_cash+空仓重启（测试 `test_b14` 锁连续性丢失）。事件密度：2024 年 4784 次/3981 code、2025 年 4925 次/3794 code；30 只持仓年化约 30+ 个事件窗口 → 多年连续 run 必撞 | **多年连续策略回测不可行**（设计行为，fail-closed）；分段可做段内诊断，但段间持仓/资金连续性丢失，重基后不得当连续 NAV 算 Sharpe/回撤；「多年回测」用户需求未满足 | 已覆盖且最新：interface.md §6 R03-I8 段（含分段工作流代码）、closeout design §8 | C | `04-ca-gate-repro.txt`、`04b-adj-event-density.txt` |
| 5 | 分钟覆盖/幸存者偏差（R03-I6） | 默认 fail：rules 池 2 日窗口 → `168 个 (code,date) 日线在而分钟无行` exit 1（84 只/日）。`FACTORLAB_MINUTE_UNCOVERED=drop` → `MinuteUncoveredWarning` + exit 0 + `summary.minute_uncovered` 完整审计（mode/dropped_code_days=168/dropped_codes=84/dropped_dates=2/日期区间/前 5 样本）。已入库分钟因子档案：`bars1m_2024h1`（5207 只）/`bars1m_2023_2025`（4852 只，含 161 BJ）静态池，档案 §4B 记录真实 run 的 drop 审计（14,529 code-day/4,844 只/5 日）并标注「全市场」结论条件于该池 | 分钟因子可跑（需显式 drop + 接受口径），但**宇宙为静态覆盖池**，幸存者偏差结构性存在；结果不可标注为真正「全市场」 | 已覆盖：interface.md:793-802、R03-I6、两张因子档案 §4B/风险节 | I | `05a-minute-uncovered-fail.txt`、`05b-minute-uncovered-drop.txt` |
| 6 | 数据时效 + reconcile | `daily/daily_basic/adj_event/stk_limit/adj_detail/trade_cal` 最新均 **2026-08-21**（今天 2026-09-16，滞后 26 自然日/约 18 交易日）；`stock_basic.list_date max=2026-07-30`。`make reconcile` **全绿 exit 0**（daily 18,124,805 双侧一致；stk_limit 期望=实际 17,854,764；adj_event 57,173；bars_1m 80 分区、tick 3×13 分区全一致），耗时 11.8s | 一切「最新数据」研究止于 8-21；spec 默认 end=2026-07-31 不受影响；回测末端要再让 7 交易日缓冲。时效等待新全包属正常节奏，但**仓内无 freshness 检查/gate**，只能手查 CH | 部分覆盖：ch-pipeline 技能（等新全包）、data-map A5/A8 更新方式；pending 无此项 | I | `06a-freshness.txt`、`06b-reconcile.txt` |
| 7a | pending #3：`20260817.7z` 未解包 | 归档在（5,632,765,477 B），`data/raw/20260817/` 不存在；`7z l` → 23,586 文件/7,863 目录、**解包后 49.8 GB**；磁盘可用 330G（条件满足）。`universe_paths.ticks_root()` 指向缺失目录 | layer3 tick 管线不可端到端（MIGRATION_GAP 表列「not runnable」）；日常日线挖矿不受影响 | 已覆盖：pending #3、data-map A11、MIGRATION_GAP、preflight 报错点名路径 | I | `07-pending-3-7z.txt` |
| 7b | pending #4：fundamentals 缺源 | `data/fact/fundamentals/` 不存在；`datapaths.fundamentals()` 仍指向 `fundamentals_pti.parquet`；`check_inputs.py` 实测 `fundamentals_pti=False`（exit 1）；`import_fundamentals.py` 需外部 `--fin-parquet`（Windows TDX 财务导出），工作区无源。pending 所述的「config.yaml `fundamentals_pti` 引用」已不在新 config（改由 datapaths 单点） | universe_stages layer1（单快照/历史批量）不可端到端；不影响平台因子链 | 已覆盖：pending #4、MIGRATION_GAP「Executable status」表、preflight | I | `07-pending-4-9.txt`、`11-pending-2-6-and-gitignore.txt` |
| 7c | pending #9：A9 golden 无生成链路 | `v4_top300.parquet` 在（6.8M）；**源脚本已归档** `universe_stages/references/v4_jqdata_final_original.py`（R20 收编）+ MIGRATION_GAP 明确「formulas recovered and ported」，但 README:51、`universe_paths.golden_universe()` docstring、data-map A9 仍写「生成链路未留存」；本机无 jqdata → `--from-golden` 历史批量仍 blocked | 不影响已有 golden 的读取；影响「重建股票池」路径与 provenance 一致性 | **部分失真**：源头已补（R20），登记文字未同步 | M | `07-pending-4-9.txt` |
| 7d | pending #10：ddl.sql vs CH schema 未 diff | 本次实跑逐列 diff（13/13 表、含同行多列解析）：列清单+类型**全部一致，NO DRIFT**（bars_1m 11 列、tick_snapshots 65 列、stock_basic 6 列含 R21 ALTER 的 delist_date 均吻合） | 当前无 schema 漂移风险；建议把「灌库前 ddl diff」固化为脚本/gate | 已覆盖：pending #10（本次即其启动条件的一次执行） | M | `07-pending-ddl-diff.txt` |
| 附1 | pending #2：tick 面板批量产出 | `panel_1s` 2 个 parquet（20250812、20260803）+ `panel_1m` 2 个 + 1 个 run json；`run_lob_batch.py` 在 | tick 因子面板尚不可全史批算；属未排期项，当前无人被卡 | 已覆盖：pending #2 | M | `11-pending-2-6-and-gitignore.txt` |
| 附2 | pending #6：30 天归档到期 | 3 个批次在（S1 3.9G / S3 912M / R24-ashare-alpha3 13M），到期 2026-10-12/10-16，今日未到期；policy TTL 单点齐全 | 到期清理程序未启动（未到时候） | 已覆盖：archive-policy.md、pending #6 | M | `11-pending-2-6-and-gitignore.txt` |

## 新发现缺口（不在原清单/台账内）

| ID | 发现 | 实测证据 | 影响 | 建议严重度 |
|---|---|---|---|---|
| R07-D1 | **`daily_basic.circ_mv` 全空，但源列 `float_shares` 存在且可派生**：CH 后 5 列（circ_mv/pe_ttm/pb/dv_ratio/volume_ratio）non-null=0/18,124,805；源 parquet `float_shares` non-null=16,873,795（= total_shares 覆盖），`ingest_daily.py` 已加载该列（算 turnover_rate）但未派生 circ_mv。**10 个在库 spec 直接消费 circ_mv，覆盖 4 个族**（crash_bottom_leader 7、size 2、reversal_20d 1）→ CH 上全部静默产出全 null（`signal_null_ratio=1.0`, exit 0）。README/DDL「无数据源」的表述与源侧事实不符 | `08-new-gap-hunt.txt`、`08c-idx-circmv-impact.txt`、`10-affected-specs.txt`、`12-source-float-shares.txt`；`ingest_daily.py:172-175` | size/reversal/crash 族的 CH 复跑不可行（现状只有历史快照）；对用户「小市值/市值加权」类需求是死路，且**无报错** | I |
| R07-D2 | **报错指引缺替代路径**：① duckdb 缺失报错只提示 `data refresh`（而 token 无、refresh 不可用），不提 `FACTORLAB_DATA_BACKEND=ch`（run.py:463/814）；② stock_st 缺失报错不提唯一绕行开关 `FACTORLAB_ST_DEGRADE=allow`（universe.py:642）。两处文档均写有替代方案，唯独报错文案没有 | `01b-duckdb-run-default.txt`、`02-stock-st-failfast.txt`、`09-newfind-guidance.txt` | 新用户/新 agent 首次失败时被指向不可行路径，徒增排查 | M |
| R07-D3 | **`index_daily` 补数路径是死引用**：ddl.sql:70 与 ch README 写「可选：sina 拉 000852.SH 填充（ingest_index_sina.py）」，全仓**不存在** `ingest_index_sina*` 文件（`find`=0）；现有 `import_index.py` 生产的是 A10 的 000905.SH → `data/ref/000905.SH.parquet`，与 CH `index_daily` 无接线 | `09-newfind-guidance.txt`、`08-new-gap-hunt.txt` | idx_ret 缺口「看起来有修复路径」实际无脚本；补数需从零写工具 | M |
| R07-D4 | **文档行数/表数漂移（agent 面）**：data-map A5 daily_fact「18,162,795 行」vs 实际 18,124,805（差 37,990）；B1「tick_orders … 等 8 表」vs 实际 13 表；ch-pipeline 技能 daily 18,162,795 / stk_limit 17,889,079 / bars_1m 1,854,876,240 vs 实际 18,124,805 / 17,854,764 / 1,853,379,840（源 2026-09-15 21:21 重灌后未同步）。R21 档案 README「不可复跑事实」表未列 `circ_mv`/`index_daily` 空数据面；另 `import_index.py:2` docstring 仍写旧输出名 `benchmark_daily_pre.parquet`（实际默认 `data/ref/000905.SH.parquet`） | `06a-freshness.txt`、`08b-rowcount-drift.txt`、`09-newfind-guidance.txt` | 会被 agent 当成 sanity 基线；对账/脚本若写死旧数会误报 | M |
| R07-D5 | **证据目录 `evidence/data/` 被 `.gitignore` 命中**：`.gitignore:17` 的 `data/` 规则匹配任意层级名为 data 的目录 → 本目录全部证据 `git check-ignore` 命中（feature/eng 不受影响）。除非 `git add -f`，本审计证据不会入库 | `11-pending-2-6-and-gitignore.txt` | 证据可能在提交/清理时丢失；建议 force-add 或把目录改名（如 `data-gap/`） | M（流程） |
| R07-D6 | **死列组合静默全 null 无 loud fail**：`idx_ret`+`circ_mv` 全空时 `factorlab run` exit 0、`n_weeks=0 ic_mean=nan`，与「因素无效」在输出上不可区分（需另查 `signal_null_ratio`）。空面板语义为设计行为，但数据面缺列场景建议加显式告警 | `08c-idx-circmv-impact.txt` | 用户可能把数据缺列误读为因子无信号 | M |

## 与文档结论的对照（哪些登记属实、哪些已失真）

- 属实且登记完整：#1（duckdb 决策点）、#2（stock_st）、#3（index_daily）、#4（CA Gate）、#5（分钟覆盖）、#6（时效）、#7a（7z）、#7b（fundamentals）、#7d（ddl diff 本次零漂移）。
- 部分失真：#7c（golden 源脚本已归档，登记文字未同步）；pending #4 的 config 指针已迁移到 datapaths。
- 登记缺失/需新增：R07-D1…D6。
- 未复现 R03-I6 之外的分钟问题；R03-I7（陈旧尾 bar 守卫）未在本次清单内，未测。

## 证据文件清单（本目录）

| 文件 | 内容 |
|---|---|
| `01-duckdb-missing.txt` | 库缺失、list/show 不触库、默认后端与路径解析 |
| `01b-duckdb-run-default.txt` | 默认后端 run 的失败文案（exit 1） |
| `02-stock-st-failfast.txt` | CH 表清单 + exclude_st 默认 fail-fast |
| `02b-stock-st-degrade.txt` | `FACTORLAB_ST_DEGRADE=allow` 警告 + 成功 run |
| `02c-st-degrade-summary.txt` | `summary.st_degrade=true` 审计字段取证 |
| `03-index-daily.txt` | index_daily 0 行（全表/000852 两次查询） |
| `04-ca-gate-repro.txt` | 真实事件最小复现 + decision_range 分段可跑 |
| `04b-adj-event-density.txt` | 2018-2026 年 adj_event 事件密度（多年阻断量化） |
| `05a-minute-uncovered-fail.txt` | 分钟缺口默认 fail（168 code-day，12.6s） |
| `05b-minute-uncovered-drop.txt` | drop 模式警告 + `summary.minute_uncovered` 全字段 |
| `06a-freshness.txt` | CH 各表最新/最早日期与行数 |
| `06b-reconcile.txt` | `make reconcile` 全绿 exit 0（11.8s） |
| `07-pending-3-7z.txt` | 7z 现状 + 解包体积 49.8GB + 目录结构 |
| `07-pending-4-9.txt` | fundamentals 缺源、golden 归档与登记矛盾 |
| `07-pending-ddl-diff.txt` | ddl.sql vs CH schema 13/13 NO DRIFT |
| `08-new-gap-hunt.txt` | 空列 null 率 + 消费面 grep |
| `08b-rowcount-drift.txt` | system.tables 行数 vs 文档声明（漂移量化） |
| `08c-idx-circmv-impact.txt` | idx_ret/circ_mv 全 null 对照实验（null_ratio=1.0） |
| `09-newfind-guidance.txt` | 报错指引缺口的代码定位 |
| `10-affected-specs.txt` | circ_mv/idx_ret 受影响 spec 全清单 + 测试定位 |
| `11-pending-2-6-and-gitignore.txt` | pending #2/#6 快照 + evidence/data gitignore 冲突取证 |
| `12-source-float-shares.txt` | 源 parquet float_shares 覆盖率（circ_mv 可派生证据） |

## 未做/边界

- 未跑全市场多年回测（约束）；CA Gate 影响用「真实最小复现 + 事件密度」量化。
- 未解包 7z、未灌 stock_st/index_daily/circ_mv（只读审计，补数属后续轮次）。
- 未修改任何仓内既有文件；因子探针产物落 `runs/platform/r07_*`（gitignore）；本目录证据受
  `data/` 规则屏蔽，提交需 `git add -f`（见 R07-D5）。
