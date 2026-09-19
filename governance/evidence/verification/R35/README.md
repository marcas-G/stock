# R35 台账复查取证（只读复跑）—— findings 13 行未闭环项

- 日期：2026-09-19
- 审计对象：`governance/evidence/reviews/findings.md` 13 行 = R07 fixed-claimed 8 + R09 fixed-claimed 3 + R08 open 2
- 审计 HEAD：`011ca16`（工作区含在途 composite 改动；修复 commit 均已验为 HEAD 祖先，见 `_inventory/commit-inventory.txt`）
- 方法：逐行读团队「修复说明」→ 复跑其声称的最小命令/检查 → 原始输出落本目录；CH 只做限流小查询（max_threads=2）；未做全量套件/全史/3 年重跑；发现注入探针用后已删（`skill: verification-before-completion`）
- 结论词：**一致** / **不一致** / **证据缺失**（open 行另给事实）

## 声称 vs 实测

| ID | 声称（团队） | 复跑（本审计） | 实测 | 结论 | 证据 |
|---|---|---|---|---|---|
| R07-MIG-I1 | 5919337+ad43a87：extcnt 旧坐标/索引重生/snapshot；`gates --structure` G-LEGACY+G-INDEX+G-ANNOTATE 全绿 | `bash governance/ops/gates.sh --structure`；`build_index.py --check`；`git show 5919337` | gates exit 0、结构门全绿（G-LEGACY 双段✓、G-INDEX✓、G-ANNOTATE 231 份✓、G-LINT 239/0）；extcnt.md:85 `platform/results`→`runs/platform` 已改为 runs 路径；索引 check exit 0 | **一致** | `R07-MIG-I1/` |
| R07-MIG-I2 | 79bbc79+458030b：模板旧结果根修正；存量 156 文件/163 处替换，具体指针修前 164 行/156 文件→0；残余为 README 行级豁免 | 读 `_template.md`；按门同款 PCRE 全量扫 `knowledge/dossiers/factors/**`；`git show 79bbc79` | `_template.md:75` 已是 `runs/platform/<name>/summary.json`、:3 已改 knowledge 路径；裸 `results/<name>` 命中 **0**；`platform/results` 仅 README:16,23 两条（门行级豁免的历史事实行）；`_mine_round_*` 已改 `runs/platform/_mine_rounds/`；458030b 已改 strategies 工具默认 `--panel` | **一致** | `R07-MIG-I2/` |
| R07-GATE-I3 | 83bf9a2：G-LEGACY tracked+untracked 双扫、裸 results/ PCRE 负向后顾、补 lib/档案模式、整档豁免改行级；TDD 注入 RED | 读 `gates.sh:45-131`；注入 A（untracked `platform/results`+`research/tools/lib/`+`docs/factors`）与 B（untracked 裸 `results/probe_b/…`）+ 负向探针；`gates --structure` 两跑 | 注入 A/B 均被捕获（exit 1，G-LEGACY 3 处红，A、B 各自命中）；负向探针 0 误报；删除探针后 exit 0、G-LEGACY 双段回绿 | **一致** | `R07-GATE-I3/injection-red.txt`、`injection-clean.txt` |
| R07-DATA-I4 | 18e8531：ingest_daily 派生 `circ_mv=close×float_shares`；CH 重灌非空 0→16,873,795；抽样 rel=0 | grep 派生代码；CH `count(circ_mv)`、逐日非空；源 parquet 3 码逐值对拍 | 派生代码在 `ingest_daily.py:82`（NaN→NULL）；CH 现任期 total=18,226,404 / circ_mv 非空=16,975,394（含修复后新增数据，> 声称值）；600519/300842/000001 @2026-09-17 逐值 rel=0；固定证据 04 与声称完全一致；reconcile 证据 exit 0（全库一致） | **一致** | `R07-DATA-I4/` |
| R07-CONTRACT-I5 | 73f30f6：interface.md 增「R22 Minute-Window Execution」小节、修 4 处 NEXT_OPEN-only；防漂移测试过 | `grep -c NEXT_WINDOW`；读 :2277/:3004/:3197 上下文；`pytest tests/test_doc_paths_exist.py` | NEXT_WINDOW **20 处**、小节在 :3129；残余 2 处 NEXT_OPEN 语句为精确限定（“NEXT_WINDOW 不放松原语级约束”），非误导；`test_doc_paths_exist.py:93-96` 显式防漂移断言，11 passed | **一致** | `R07-CONTRACT-I5/` |
| R07-STRAT-I6 | 56f5f9e+9e1fdaf：`factorlab lint` 自动识别策略文档严格校验；universe_override 运行链消费 | `factorlab lint research/strategy/*.yaml`；构造 unknown key / NEXT_WINDOW 无窗口两个反例；跑 test_cli_lint_strategy + `-k universe_override` | 真实策略 2 份均 `OK` exit 0；unknown key exit 1（extra=forbid）；NEXT_WINDOW 无窗口 exit 1（明示禁止隐式默认窗口）；测试 11 passed；override 运行链在 `app/strategy/run.py:70-86`（canonical 过滤/空交集 fail fast），override 测试 8 passed | **一致** | `R07-STRAT-I6/` |
| R07-LINT-I7 | 16fbc84：arity 静态校验 `ts_cum_count(close,5)` lint exit 1、`ts_cum_count(close)` 过 | 两个 spec 写 /tmp 后 lint；查生成表与 semantics | bad → exit 1「参数过多：最多 1 个」；good → exit 0；`_generated_ta_ops.py:277` 记录 (`ts_cum_count`,1,1)，`semantics.py:248-262` `_check_arity` 实装 | **一致** | `R07-LINT-I7/` |
| R07-DATA-I8 | 377432c+b87ddd2：load_adj_detail_window + apply_corporate_actions（现金/送转/配股 warning），快照后订单前；4 年 53 决策连续 NAV | grep 符号/接线；跑 CA 相关测试；查契约 §6 与 4 年证据 | 符号与接线在（backtest.py:61/265，快照后调用）；`test_corporate_actions.py`+`test_backtest_ca_gate.py` 48 passed、`test_backtest_ca_multi_year.py` 3 passed；interface §6 CA Gate v2 在 :3083；证据 multi_year_metrics.json（53 决策/4 事件/sharpe 5.60/maxdd −2.57%）、stub_kill 手算对比存在 | **一致** | `R07-DATA-I8/` |
| R09-PERF-I1 | 2b4daae：minute_fold 融合路径；after 对拍 bit-exact/ulp；fold 64.7→27.3s 等 | 读实现/接线/测试；`pytest tests/test_minute_fold.py`；核对 R31 after/after-p3 对拍与计时 | `minute_fold.py`(656 行) 存在，`compute.py:375-377` 调用 `try_fused`；34 passed（含 not-stub/bit-exact 断言）；R31 after 对拍：am_pm_vol/vol_asym bit-exact、autocorr ≤3.28e-7、vol_price_corr ≤2.42e-6（与声称 ≤3.3e-7/≤2.5e-6 相符）；after fold vol_price_corr 27.3s（R09 原 64.7/27.3 记于报告） | **一致**（平台全量套件按约束未复跑） | `R09-PERF-I1/` |
| R09-PERF-I2 | 7760357：条件外提 `filter(cond).max/min` 单次 agg；at_minute(k) 双防线；P3 vs P2 max\|Δ\|=0 | 读实现；跑 test_minute_fold/test_catalog；核对 after-p3 对拍与同进程 fold 数 | 实现为 `col.filter(cond).max/min.over()`、`day_first/last` 单次 sort_by（minute_fold.py:580-604）；P3 vs head 全因子 bit_exact、at_minute vs day_max(if_else) 0/281338；同进程 fold lunch_jump 20.69→16.23、close_auction_premium 19.0→11.87、open_minute_mom 14.63→12.27（与声称相符）；catalog.md:61/interface:980 已同步；test_catalog 28 passed | **一致**（平台全量套件按约束未复跑） | `R09-PERF-I2/` |
| R09-PERF-M3 | df0c3b0：`--profile`/`FACTORLAB_PROFILE=1` 分段计时；tests 9 条 | `factorlab run --help` 查 `--profile`；跑 test_profile_timing；小窗 smoke（heavy.sh + --profile） | help 有 `--profile`（stderr 摘要 + summary.runtime.profile）；`app/profile.py` 在；9 passed；smoke 被读取门拦截（partition UNKNOWN 不可 opt-in，R31-DQ-I1 独立 open 项）→ 未能活体输出分段（非本修复回退） | **一致**（活体 smoke 受独立 open 项阻断） | `R09-PERF-M3/` |
| R08-MET-I1（open） | 无修复说明（团队未回填） | 读当前产物/R30 Task 11 证据 | R30 Task 11 已执行 D7 处置：重跑 39（含 `intraday_high_time` → **version=2 / daily / n_weeks=116**，weekly.parquet 117 日期即逐日面板）、删 16 目录（manifest）、17 个在途例外未动（**明示含 `max_effect_20d_high`，等挖矿收尾**）；当前 101/136 summaries 有 `evaluation.version=2`；`max_effect_20d_high` 仍 v1 旧口径（coverage 1.0/896750、无 version、mtime 09-16） | **open 行；R30 已落地处置但未全闭环**（max_effect_20d_high 按在途例外保留） | `R08-MET-I1/` |
| R08-DATA-I2（open） | 无修复说明（团队未回填） | 查 sidecar/工具/测试；CH 抽 5 只退市股；读 R30 Task 12 证据 | sidecar `delisted_adj_factor.parquet` 83,967 行/180 码（meta：5 码漂移拒绝）；`delisted_adj_backfill.py` + 19 测试在；CH 抽样退市码 adj_factor 非空（000005=524、002231=987、300379=957、600355=1027、600811=787），000005 2022-01-04 值与 sidecar 一致；R30 前后：面板窗 NULL 41,984→157、signal null 3.35%→2.42%、reconcile exit 0 | **open 行；R30 Task 12 已落地** | `R08-DATA-I2/` |

## 异常清单（供 reviewer）

1. **台账门解析 bug（新发现）**：`check_reviews.py:333` 用裸 `split("|")`，不识别行内 `\|` 转义。`R09-PERF-I2` 行修复说明含 `max\|Δ\|=0`，被切成 9 个 cell，复查列被解析成杂散 `Δ\` → `--closure` 把它当“已复查”。实际未复查 fixed-claimed = **11**（R07 8 + R09 3），门报 10（R07 8 + R09 2）。证据：`_inventory/ledger-parse.txt`、`_inventory/closure.txt`。
2. **R08 两行台账仍 open，但 R30 Task 11/12 实际已落地修复**（fix 说明列为空）；本条只给事实，回填属 reviewer。
3. **`max_effect_20d_high` 仍是旧口径 v1 产物**（无 version、coverage 1.0/896750）——R30 明示“挖矿在途例外，等批次收尾”，非静默遗漏；下游引用该 summary 时需注意。
4. `R30/eval-v2-task14-12-11/36-task11-spotcheck.txt` 末尾带一段未捕获的 polars `DuplicateError` traceback（`len` 列重名）；traceback 前的数值有效，但证据文件不整洁。
5. R07-DATA-I4 数据边界：CH 有 3,914 行（0.023%）`circ_mv > total_mv`，头部样本为 2001 年 `total_mv=0` 的老行（600120.SH）。不推翻派生正确性（逐值对拍 rel=0），但下游若做 `circ_mv>total_mv` 断言需排除。
6. 审计在**脏工作区**（在途 composite；`strategy_artifacts.py:82` 有 LSP 未定义 `SignalTiming`）上进行；已验证 16 个修复 commit 均为 HEAD 祖先，复跑命令按现树执行。
7. R09-PERF-M3 活体 smoke 被读取门（UNKNOWN legacy 不可 opt-in）拦截，与 `R31-DQ-I1`（open）一致；profile 行为由 help + 9 条单测 + R31 既有 timings 证据支撑。
8. R07-MIG-I2 残余 2 处 `platform/results`（dossiers README:16,23）为门行级豁免的历史事实行，与声称“残余为 README 明示不改写的历史行”一致（非差异）。

## 方法学备注

- 未复跑：平台全量 pytest（声称 3447/3470 passed）、全史 reconcile、3 年分钟回测、19 条 backfill 测试（避免 CH 写路径）；以上均以 R31/R30 原始证据 + 定点测试/单查询替代，逐条注明。
- CH 查询均 `max_threads=2` 且限定分区/单列，最重一条为 `daily_basic` 单列全表 count（返回即验证）。
