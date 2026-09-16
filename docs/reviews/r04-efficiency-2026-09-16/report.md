# R04 功能性 Review：高效与整洁提案（2026-09-16）

- **范围**：全仓（平台/研究工具/文档证据/数据布局/顶层目录结构）
- **方法**：5 路并行只读审视（算得快 / 迭代快 / 更整洁 / 存储布局 / 顶层结构），全部要求实测数字；
  快照 HEAD 约 `0d2bb33..47fe5be`（团队并发提交中，数字为快照值）
- **产出**：本报告为**改进提案**（非缺陷台账）；缺陷类发现仍入 `findings.md`
- **红线**：只读审视，未改任何仓库文件；所有测量脚本与原始输出在 `/tmp/opencode/reviewer-r04-*/`

---

## §0 十个最高收益事实（实测）

| # | 事实 | 数字 | 来源 |
|---|---|---|---|
| 1 | 日频全窗 run 的 **universe 解析占 52%**，且 signal/label 两次输入完全相同、各解析一遍 | 49.7s 全窗中 27.9s | perf P2 |
| 2 | 分钟链非分块 **峰值内存 34.95GB**（16GB 机直接 OOM）；分块后 6.95GB 且不变慢 | 127.5s → 124.7s / RSS 5× | perf P1 |
| 3 | **全库 lint 552s → 单进程批跑 4.1s（~100×）**（现在 159 个 spec 各起一个 CLI 进程，启动主导） | 552s vs 4.1s | flow P1 |
| 4 | M8 回测逐 decision 重复查 schema/日历/覆盖率：**356 次 CH 查询 / 21 个决策**（`system.tables` 100 次） | 4.42s/21 决策，随决策线性涨 | perf P4/P10 |
| 5 | 平台测试全量 **967s**，其中 `test_regression_152.py` 占 352s（36%，6 个全窗 spec 串行） | 无 xdist | perf P7 |
| 6 | CH 列存全默认 LZ4；换 **ZSTD 同数据省 33~45%** | 可回收 **≈72 GiB**（最大单项） | storage P5 |
| 7 | 僵尸文件：`_staging` **550M/11,411 文件**、`1m_features/output` 244M、`.bak-R21` 435M、pycache 12.9M、根 `results/` 空壳 | 立即回收 ~1.25G | tidy P2/storage P1 |
| 8 | 代码克隆：平台 6 组、研究工具 17 组、测试助手 20 组（AST 逐字克隆下界）；真死符号 10 个 + **1 个真 NameError** | `verify_cancels_sample.py:86` | tidy P0/P3 |
| 9 | 文档数字全面滞后：spec **152→159**、族 **14→15**、平台基线 **2570→2976**；活文档断链 10 处；`!src/factorlab/data/` 陈旧例外 | 台账口径漂移已真实发生 | tidy P1 |
| 10 | 顶层"同一类东西三处"：设计/计划散落 `platform/docs/superpowers`、`research/docs/superpowers`、`docs/reviews/2026-09-15-*`；契约/档案/证据/脚本各有归属不清 | 结构提案 A（见 §5） | structure F1-F8 |

---

## §1 算得快（性能）

| # | 提案 | 现状 → 预期 | 成本 | 风险 | 文件 |
|---|---|---|---|---|---|
| P1 | **分钟链默认自动分块**（~20 交易日） | RSS 34.95GB→~7GB；16GB 可跑；速度不变 | 极小 | 低 | `app/run.py`、CLI、interface.md |
| P2 | **universe 复用**：signal/label 日历相同则解析一次；分块按并集切片 | 全窗 −14s（−26%） | 小 | 低-中 | `app/run.py` |
| P3 | 分钟解码 `str.split(".").list.first()` → `str.slice(0,6)` | 1.46 亿行 −10~15s（5.7×） | 极小 | 低 | `adapters/intraday.py:128`、`read/source.py` |
| P4 | `ClickHouseRead` 按 (db,table) **缓存 schema**（现每次 query 都探） | M8 356 查询大降 | 极小 | 低 | `adapters/ch_read.py:124` |
| P5 | `listed_codes_at` 循环外提集合；池 YAML memoize | −0.5~3s/run | 极小 | 极低 | `app/run.py`、`read/universe.py` |
| P7 | 测试并行/分层：6 spec 并行 subprocess + `-m not integration`/xdist | 循环 16min→~2min | 小-中 | 中 | `test_regression_152.py`、pyproject |
| P9 | universe 下推 CH（只取 5.5K+866 行，本地 cross join） | 与 P2 合并 −25s（−46%） | 中 | 中 | `read/universe.py` |
| P10 | M8 **批量预载** calendar/coverage/market/rules/adj_event | 多年窗口分钟→秒级 | 大 | 中 | `app/backtest/*` |
| P12 | 分钟 `day_*` 聚合下推 CH（1.46 亿行→60.9 万行） | 分钟进秒级 | 高 | 高 | `engine/minute.py`、`intraday.py` |
| P14 | (数据版本,窗,宇宙,复权) 级**面板缓存** | 挖矿会话 −40~50% | 高 | 中 | `app/run.py` + cache 层 |

**快速项 P1-P5（几乎零风险）**；结构性 P9/P10/P12/P14 需设计与双腿对拍。
附：eval 4.96s 中 layered 2.23+rust 1.92+align 0.81（P13 矩阵化可 −2~3s）。

## §2 迭代快（工作流/门/证据）

| # | 提案 | 现状 → 预期 | 成本 | 风险 |
|---|---|---|---|---|
| P1 | **`factorlab lint` 多路径/`--all` 单进程** + Makefile 一行 | 552s→4-8s（~100×） | 0.5d | 低 |
| P2 | `make gates` 增加 **annotate --check + 批量 lint**（消灭"忘跑门"） | 19s→~25s，全库门一次过 | 0.5h | 无 |
| P3 | **`scripts/check_reviews.py` 台账门**：ID 唯一/状态词表/引用路径存在/统计=实计 | 防"11/37 vs 13C+50I"类口径漂移；7 死链 | 0.5d | 低 |
| P5 | **`factorlab archive <spec>`**：模板+summary 自动填档案（front matter/§1/§3/§4 15+ 字段），`--check` 校验 | 每轮省 10-20min 手抄 | 1-2d | 低-中 |
| P6 | `scripts/ledger_draft.py`：从 `git log`（RXX-YY 标记）生成 fixed-claimed 草稿 | 台账回填 10-20min→数分钟 | 0.5d | 低 |
| P7 | `gen_minute_pool.py --start/--end/--out` 参数化 + 默认写仓内 `_pools/` + `--check` | 分钟链可复制；池已入仓（本次先手工落位） | 0.5-1d | 低 |
| P4/P9 | 证据索引生成器（`R*/**` 自动 README + finding 关联）；测试分层（durations 存档→分档） | 371 文件可检索；测试分档 | 1-2d | 中 |

## §3 更整洁（重复/死代码/漂移）

| # | 提案 | 计量 → 预期 | 风险前置 |
|---|---|---|---|
| P0-1 | 修 `verify_cancels_sample.py:86` 真 NameError | 5 分钟 | 无 |
| P1-1 | 数字对齐：152→159 / 14→15 / 2570→2976 / 392G 去写死 | 文档 6 处 | 无 |
| P1-2 | 修 10 处活文档断链（spec 加勘误行不改正文） | 10 处 | 无 |
| P1-3 | 删两处 `!src/factorlab/data/` 陈旧例外 | 2 个 .gitignore | 无 |
| P2-x | 僵尸清理：`_staging` 550M、`.bak-R21` 435M、smoke results、`make clean` 扩项、`1m_features/output` 83 月分区 | **回收 ≈1.25G + 11.4k 文件** | 逐项确认零引用 |
| P3-1 | 删 10 个死符号（逐符号 grep 已核） | 10 符号 | 跑目标测试 |
| P3-2 | 安全解析单点化：`_sha256_file`×3 / alias 提取×5 / `_strict_int`×2 | 安全逻辑分叉风险 | 单测 |
| P3-5 | pyflakes/ruff 报告制入 `make gates`（先 report 后 enforce） | 防再生 | 低 |
| P4-1 | `uv lock`/冻结（pending #18：20 声明 vs 72 安装） | 环境可复现 | 中 |

## §4 存储与数据布局

| # | 提案 | 数字 | 说明 |
|---|---|---|---|
| P1/P2 | 僵尸+备份清理 | ~1.0G 立即 | 见 §3 |
| P3 | `_archive` TTL 清理（2026-10-12 到期，3 决策点） | ≤4.8G | 需过 pending #6 决策 |
| P4 | `platform/results` 预算+索引（当前 2.57G/20 run，覆盖同名文件天然有界 ≈19G） | 0 立即 | 建 `runs/README.md` 指回档案；**不可删 signal/labels**（loader 禁 fallback） |
| P5 | **CH 全表 ZSTD**（夜间分批重写 190G，限线程） | **≈72 GiB** | codec 默认 LZ4；磁盘只剩 334G |
| P6 | 7z 解包策略：extract→convert→删解包目录（净增 ~5-8G，而非 ~50G） | 5.3G 输入 | 排 P5 后 |
| P8 | parquet 副本削减（fact 214.9G 理论可删） | 储备项 | 重建代价高，本轮不做 |

## §5 顶层目录结构（"打散重整"提案）

**病灶**：不在三棵树边界（有门保护），而在**树内 docs 职责重叠 + 证据/产物/脚本游离**：
设计/计划同类东西 3 处（platform/docs/superpowers、research/docs/superpowers、docs/reviews/2026-09-15-*）；
根级 3 份 agent 文档 × 每树 3 份且内容漂移；`scripts/` 无公约地位；证据双轨（verification 564 + reviews 48）；
运行产物两个根（根 `results/` 空壳 + `platform/results/` 2.6G）；`projects/ashare_alpha3` 过渡态无收尾。

**方案 A（推荐，代码树零位移）**：

```
stock/                    # 根 = 治理薄层（白名单定稿）
├── platform/             # 引擎（src/tests/kernels 不动；只留 README + scripts/gen_op_catalog）
├── research/             # 研究（factor/tools 不动；只留 README）
├── knowledge/            # 【新】文档与知识唯一入口
│   ├── contracts/        #    ← platform/docs 4 契约
│   ├── design/{platform,research,workspace}/  # ← 三处 superpowers/评审设计
│   ├── dossiers/         #    ← research/docs/{factors,strategies,playbook}
│   ├── handbooks/  index/
├── governance/           # 【新】治理与证据
│   ├── ops/              #    ← scripts/
│   ├── workspace/        #    ← docs/{data-map,directory-conventions,pending-items,...}
│   └── evidence/{verification,reviews}/   # ← docs/verification + docs/reviews
├── runs/platform/        # 【新，本地】运行产物唯一根 ← platform/results
├── data/  projects/  _archive/   # 本地过渡
```

- 迁移映射 14 项、需同步的门/测试/脚本/技能清单（含仓外 4 个用户级 skill）已在结构提案中逐条列出；
- **明确不动**：platform/src、research/factor、research/tools（R19/R20 红线）、data/、历史冻结文档正文；
- 方案 B（`code/` 五区制）留作下一战役（`parents[N]` 根推导 10+ 处风险）；
- 待拍板：**D1** 证据位置、**D2** 两树 docs 留指针壳还是删、**D3** 契约归属、**D4** 是否立项方案 B、**D5** `projects/ashare_alpha3` 归档或删。

## §7 决策记录（用户拍板 2026-09-16）与执行清单

**已拍板**：

| 决策 | 结论 |
|---|---|
| D3 契约位置 | **迁 `knowledge/contracts/`**（同步改 G-COPY 门判据与 5 个测试路径） |
| D1 证据位置 | **`governance/evidence/{verification,reviews}`** |
| D5 projects 处置 | **归档到 `_archive/`**（连 manifest，随后按 TTL 处理） |
| 快速项 | **授权开发团队执行 §6 本周项** |

**仍待定**：D2（两树 `docs/` 留 3 行指针壳 vs 删除，建议留壳）、D4（方案 B `code/` 五区制是否在 P3 后立项）。

**快速项执行清单（已授权，团队按此接管；完成后在本报告或台账回填 commit+证据）**：

| # | 项 | 验收锚点 |
|---|---|---|
| Q1 | 分钟链默认自动分块（§1 P1）**+ 硬内存护栏（RSS 看门狗/上限，超限干净中止）**（因 R05-C1 事故升级为 P0；长窗未分块应拒绝或显式确认） | 16GB 机全 H1 分钟 run 峰值 RSS < 8GB，IC 与分块前 delta=0；超限进程被中止且报错清晰 |
| Q2 | universe 复用 +（可选）CH 下推（§1 P2/P9） | 全窗日频 run wall 下降 ≥20%，信号逐值不变 |
| Q3 | CH schema 缓存（§1 P4） | M8 21 决策的 `system.tables/columns` 查询次数 ≈1 |
| Q4 | 分钟解码 `str.slice(0,6)`（§1 P3） | 解码耗时下降、值不变 |
| Q5 | lint 批跑（`--all`/多路径）+ `make gates` 并入 lint/annotate（§2 P1/P2） | 全库 lint ≤10s；`make gates` 覆盖 lint+annotate |
| Q6 | 僵尸清理 + 死符号 + NameError + 文档数字/断链（§3 快速项） | 回收 ≥1.2G；pyflakes 真问题清零；152→159 等数字对齐；10 断链修复 |
| Q7 | 台账门 `check_reviews.py`（§2 P3） | 口径/ID/引用路径自动校验入 `make gates` |

**结构性项（排在当前实施波之后）**：§1 P7/P10/P12/P14、§2 P5/P4、§4 P5（CH ZSTD ≈72GiB）、§5 目录重整（方案 A，需冻结窗口）。

**目录重整的可执行计划**：`docs/reviews/r04-efficiency-2026-09-16/structure-plan.md`（12 个 Task：P0 冻结/基线/引用清单 → P1 搬迁七步（契约/设计/档案/治理/证据/脚本/技能）→ P2 产物与 agent 文档 → P3 过渡区 → 全量验收；含精确 `git mv` 命令、门/测试/脚本同步清单、风险表与验收标准）。

**工具归属决策（2026-09-16，修订结构计划红线）**：见同目录 `tools-reorg-decisions.md` ——
8 项数据生产线工具（converters/quark_download/ch_ingest/ashare_ingest/universe_stages/1m_features/lob_fact/lib）
迁 `platform/tools/`；`strategies`、`factor_lib` 留 research；T1/T2 合并试点通过
（全量 research/tools 在平台 venv 下 **372 passed / 49.1s**，emb 不再是工具必需）。
**可执行迁移计划**：同目录 `tools-migration-plan.md`（TM1 单解释器化 → TM2 搬迁 → TM3 _env 退役 → TM4 验收）。

## §6 建议执行顺序（综合）

1. **本周（快速收益，≤2 人日）**：§1 P1-P5（分钟 OOM + universe 复用 + schema 缓存 + 解码）→ §2 P1/P2（lint 批跑 + 门补 lint/annotate）→ §3 快速清理（僵尸 1.25G + 死符号 + NameError + 数字/断链对齐）→ §2 P3（台账门）；
2. **近期（结构性，需设计与对拍）**：§2 P5/P6（archive 助手 + 台账草稿）→ §1 P7/P9/P10（测试分层、universe 下推、M8 预载）→ §4 P5（CH ZSTD，夜间）；
3. **战役级（需冻结窗口 + 用户决策）**：§5 目录重整（方案 A，P0 冻结→P1 搬迁→P2 收口→P3 过渡区收尾），与团队现有实施波（开放算子/分钟执行）错峰。

**协调提醒**：团队正在实施开放算子（df6851f/c7b3d66）与分钟执行方案，R04 多项改动触及 `app/run.py`、
`read/universe.py`、`cli/main.py` 等同一批文件——建议快速项**插空合流**，结构性项排在当前波次收口后。

---

## 附：方法与局限

- 五份完整分报告（含原始命令与输出）：`/tmp/opencode/reviewer-r04-{perf,flow,tidy,storage,structure}/`；
- 各分报告均独立声明局限（快照性、争用负载、样本外推、未跑全量测试等）；
- 本轮仅产出提案；实施与否/顺序由用户与开发团队决定。

---

## §8 执行回填：工具迁移 + 单解释器化（R27，2026-09-16）

`tools-migration-plan.md`（TM1–TM4）已执行，单提交 `04e9f9e`（跨树单提交例外，信息已注明）：

- **TM1/TM3**：Makefile 研究测试收敛为平台 venv 单解释器；`emb` 退役为工具解释器；
  `_env.py` 退役 T1/T2 表述、保留落位断言（`platform/tools/_env.py`，`parents[1]`）。
- **TM2**：8 项数据生产线工具 + `lib/` + `_env.py`（170 文件）`research/tools/` → `platform/tools/`；
  `strategies/`、`factor_lib/` 留 research；G-TOPO/G-CONTRACT/G-READ 门双树判据 +
  G-COPY 归位例外（`scripts/{check_tool_layering,check_dataiface,gates}.sh`、
  `platform/tests/test_architecture.py` 同步）；仓内引用清扫（活文档）；
  仓外 `factorlab-ch-pipeline` skill 同步（`factorlab-{data,dsl,evaluate,backtest}` 零引用）。
- **TM4 验收**：`platform/tools` **337**、`research/tools` **35**（清单逐一相同 = 372）；
  平台全量 **3099 passed / 13 skipped**；门全绿（**除 G-INDEX**——迁移前 HEAD 即红，
  并发挖矿未入库 yaml 使索引成超集，与迁移无关）；convert_tick 金样**内容逐值等价**；
  `make reconcile` **全库一致**；`data/` 零写（元数据 hash 相同）。
- **证据**：`docs/verification/R27/`（命令 + 原始输出 + 门结果 + 偏差 D1–D6；含
  `research/tools` 2 个失败为挖矿在途所致的定位证明）。
- **残余**：`compact_lob.py` 直跑自举漏网（遗留，非本轮）；`platform/tools` 是否并入
  平台 testpaths 留结构计划。
