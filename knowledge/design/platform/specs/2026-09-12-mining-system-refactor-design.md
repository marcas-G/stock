# 策略挖掘系统 · 深度重构 + 端口化设计（P0-P4）

> 状态：**WS0-WS8 完成（2026-09-12），P8 Mission satisfied**；剩余项见 pending-items #11。设计方法 = 《递归式需求驱动系统工程开发手册
> （NASA SE × V-Model）》P0-P4 递归分解；本文即 workspace `governance/workspace/pending-items.md` #5
> 「仓库内部重构」递归子树的 P0-P4 文档。
> 执行计划与阶段门见 workspace `docs/verification/WS*/` 证据目录与同批 plan 文档。

> **R04 勘误（2026-09-16）**：本文路径为 2026-09-12 快照；lob_fact 的 `config.py` 现落位
> `research/tools/lob_fact/core/config.py`；P8 判定归档于
> `docs/verification/archive/2026-09-workspace-cleanup/final/03-p8.md`。正文不改。
>
> **R27 勘误（2026-09-16）**：工具归位后 `config.py` 落位 `platform/tools/lob_fact/core/config.py`
> （旧勘误行中的 `research/tools/…` 以本行为准）。正文不改。

## 0. SoI 与设计输入

**System-of-Interest**：策略挖掘系统 = `quant-platform-main`（平台）+ `quant-platform-research`
（研究工具）的统一整体——同一 git 仓库的两个 worktree + 一份共享纯计算核。
**使命（用户原话）**："这个系统目前只有策略挖掘这一个目标"——从行情/订单簿事实中
发现、实现、评估、验证可交易的因子与策略。

实测事实（2026-09-12，全部可复现）：

| 事实 | 数值/位置 |
|---|---|
| 平台规模 | src 86 py / 15,501 行；tests 93 文件 / 2436 collected |
| 错误文案契约 | `pytest.raises` 692 处，其中 `match=` 442 处 |
| **两副本漂移** | `git diff main research -- src/ tests/` = **5 文件**：minute_gate.py（−4/+1 缺修复）、conftest.py 与 test_e2e_web.py（Windows 硬编码路径）、test_minute_gate.py（−57 缺测试）、test_strategy.py（+377 平台 tests/ 装研究侧测试） |
| research 侧内核解析 | pytest `pythonpath=["src"]` → `import factorlab` 落 `quant-platform-research/src`（实测） |
| 研究侧重复面 | 路径常量 6 套 25 处；tick 月表读 ≥6 份；10 档列契约 4 处；编排样板 3 份；时间解析 2 套；板块分类 2 处 |
| 核心→外围渗漏 | `engine/compute.py:381/:958/:984`、`engine/minute.py:316/:346` 函数内 import artifacts；`compute.py:35` 顶层拖 polars_ta；`cli/main.py:170-222` 自建评估装配；web/artifacts 两份 `_load_summary`；eval 两份 panel 读 |
| 运行约束 | 16GB 无页面文件；磁盘 96%（338G 可用，禁复制类操作）；CH 8 表 5.81B 行在线 |

## 1. P0 — Problem Definition

**P0-Q1 核心问题**：研究者需要从事实库稳定地发现/实现/评估/验证因子与策略，但由于链路横跨
两个 worktree、两套解释器、**同一份平台源码的两份物理副本（且已漂移）**、六套路径常量、
六份 tick 读取、三份编排样板，目前无法可靠地：①判定研究结论用的是哪个版本的计算内核；
②判定同一因子的评估口径哪个权威；③判定改一处会不会断链。

**P0-Q2 后果**：结论不可比（最严重——research 副本缺 minute_gate 折日修复，同 spec 可能两侧
产出不同）；口径分叉无单点（CLI 自建装配 vs run 链，修一侧不传导）；核心不可单测
（import compute 即拖入 duckdb/CH/写盘）；搬家成本不可枚举；跨工具耦合脆弱
（`sys.path` + 模块级注册表）。

**P0-Q3 为何现有方式不足**：现有手段 = 分支纪律 + 人工同步。但纪律只约束"谁提交什么"，
**不约束"两份副本何时同步"**——实测 main 的 `319fa3e`（Windows 路径修复）与 minute_gate 修复
从未到达 research。且平台内部以"模块级函数 + 单例 settings + 模块级注册表"组织，需要换后端/
换写盘目标/换编排时**没有可替换的缝**。

**P0-Q4 解决判据（写进 P8）**：研究者从任一入口得到**同一内核**产出的面板与结论，且能一句话
回答"这份结论用的哪个内核版本"；替换任一外围（后端/落盘/编排/评估内核）只改装配点一处；
可观测：全仓 `import factorlab` 解析到唯一物理路径；`core/` 内 I/O 关键词 = 0；路径字面量
仅出现在单点文件；同 spec 在两 worktree 下结果逐字节一致。

**P0-Q5 范围**：In——核心/端口/适配器/装配/界面五层的归属重划与物理落位；6 条端口化；
跨 worktree 单副本；重复面收敛；旧引用全量重指。Out——Web 功能重构（只接口化）；data
rebuild/refresh 行为（只搬家）；平台缺陷修复（会改数值，见 §7）；死代码专程清理；GP 搜索器
实现（只预留端口）；ashare 内部重构；CH 数据重灌。

## 2. P1 — Stakeholder & Context

- **研究者 gaolei**（唯一人类干系人）：结论可信（内核版本可追溯）、改一处不断链、16GB 内跑得动。
- **未来协作代理**：文档契约准确（interface.md 的模块路径 == 实现路径）、grep 路径真实存在。
- **GP 搜索器（未来）**：无文件系统、无数据库的纯核入口（REQ-F-009）。
- **ashare_alpha3（下游）**：ch_source 接口稳定。
- **外部实体**：CH 127.0.0.1:8123（在线）；teajoin（token 过期）；quark 网盘；
  emb（3.11）与平台 `.venv`（3.13）双解释器；quant_core_shim（评估契约锚点）。
- **异常场景语义**：CH 不可达 → duckdb 单腿 + ch 腿 skip（不假通过）；内存压力 → 分块
  （与不分块逐字节一致）；批算中断 → 断点续跑 + 失败记账 + 退出码非 0；并发写 → flock 单实例门；
  磁盘紧张 → 只同盘 rename 或源码改动；artifact 校验失败 → **零文件写入**。

## 3. P2 — Requirements

### REQ-F（功能）

| ID | Statement | 验证 |
|---|---|---|
| REQ-F-001 | 从任一已注册数据源读日频/分钟/逐笔/订单簿事实，**同一次调用语义在 duckdb/ch 双腿一致** | Test（双腿参数化） |
| REQ-F-002 | 把公式/spec 计算为逐日面板（daily 与 bars_1m 双作用域） | Test |
| REQ-F-003 | 对面板产出 IC/resIC/分层/相关性评估 | Test |
| REQ-F-004 | 信号→目标组合→调度执行→NAV/fills/positions | Test |
| REQ-F-005 | 运行产物落为**版本化 artifact**（schema 版本号） | Test（往返+版本逐字） |
| REQ-F-006 | 断点续跑、单实例、停滞看门狗方式编排批算 | Test（编排契约） |
| REQ-F-007 | CLI 与 Web 两界面暴露能力 | Test（e2e） |
| REQ-F-008 | 外部源→事实库→CH 灌入 + 对账证据 | Test（reconcile 全库一致） |
| REQ-F-009 | 提供**无文件系统、无数据库**的纯计算入口（GP 消费面） | Test（隔离 import） |

### REQ-Q（质量）

| ID | 指标 | 水平 | 验证 |
|---|---|---|---|
| REQ-Q-001 | 无 duckdb/clickhouse_connect/requests 的进程内 import `core.*` 全成功；core 内无 `open(`/写盘/读盘 | =0 | Test（隔离 import + AST 门） |
| REQ-Q-002 | 每条端口 ≥2 实现（真+内存桩）被同一契约测试 parametrize | 6 条全覆盖 | Test |
| REQ-Q-003 | 绝对路径字面量各自只出现于唯一单点文件 | 散落 =0 | Test（grep 门） |
| REQ-Q-004 | 层间依赖方向违规边 | =0 | Test（AST 门） |
| REQ-Q-005 | 单月 bars 批算峰值 RSS | ≤7GB | Analysis |
| REQ-Q-006 | `match=` 字面量 **442** 处、`pytest.raises` **692** 处 | 逐字不变 | Test（diff 门） |
| REQ-Q-007 | 位级：chunk 三测试 + 双跑 bitwise + catalog diff==0 + artifact 版本号逐字 | 全绿 | Test |
| REQ-Q-008 | research 分支 `src/`、`tests/` 条目数 | =0 | Test（git 门） |
| REQ-Q-009 | 读路径双腿参数化保持；ch 不可达 skip | duckdb 腿恒可跑 | Test |
| REQ-Q-010 | 旧模块路径在 src/tests/docs/skills/tools 出现次数 | =0（无兼容层） | Test（grep 门） |
| REQ-Q-011 | 运行产物记录 `factorlab.__version__` + 源码 HEAD + `__file__` | 三字段齐 | Inspection |

### DER（派生需求）

DER-001 核心层不得依赖持久化（落盘经写端口，`core/` 无 open/write_parquet/read_parquet）·
DER-002 跨 worktree 共享核心不得复制（research 无 src/tests；平台文档副本同为漂移类，一并退役）·
DER-003 注册表由装配点唯一负责（`app.bootstrap.install_operators`；core 只提供 registry 对象）·
DER-004 逐日动态池仍是层间唯一通道（不变）· DER-005 事实库列契约单点（`core/factio/schema.py`）·
DER-006 编排语义单点（flock/断点/看门狗/_SUCCESS 只在编排端口）· DER-007 时间解析单点
（显式位宽参数）· DER-008 路径单点（`core/factio/paths.py`；研究侧经 `tools/_env.py` 转发）·
DER-009 运行参数不得经全局单例传递（`RunOptions` 值对象；settings 只在装配点读一次）·
DER-010 研究→平台依赖走单一注入点 `tools/_env.py` + 运行时落位断言 ·
DER-011 搬家不得改错误文案（搬迁提交中 tests/ 的 match= 行零变化）。

## 4. P3 — Logical Decomposition

LF-01 Acquire Facts · LF-02 Compute Factor Panel（双作用域）· LF-03 Derive Labels ·
LF-04 Resolve Universe · LF-05 Materialize Evidence（校验先于 I/O）· LF-06 Evaluate Factor ·
LF-07 Construct Target Portfolio · LF-08 Simulate Execution · LF-09 Orchestrate Batch ·
LF-10 Convert Raw To Fact · LF-11 Ingest & Reconcile · LF-12 Report & Inspect ·
LF-13 Guard Purity（静态+运行门）。

**主链**：LF-01 →（LF-02 ‖ LF-03）→ LF-05 → LF-06 → LF-07 → LF-08 → LF-05 → LF-12；
**支撑旁路**：LF-10 → LF-11 → LF-01；LF-09 横切长任务；LF-13 横切全部。

**状态**：NORMAL_OPERATION｜SAME_SEMANTICS（后端切换）｜DEGRADED_SINGLE_BACKEND（CH 不可达）｜
CHUNKED（内存压力，产出位级一致）｜RESUMABLE（中断续跑）｜CALIBRATION_FROZEN（lob 校准常量）。

**失败语义**：见 §2 异常场景；共性 = Detect → Classify → Contain → Report（点名）→ Result
（原子性/单腿可用/skip 非假通过）。

## 5. P4 — Architecture

### 5.1 分层（依赖方向可静态强制）

```
surfaces(cli/web) → app(装配) → ports(契约) → core(纯核)
                      ↑
                adapters(I/O) → ports → core
门：core ✗→{ports,adapters,app,surfaces}；adapters ✗→{app,surfaces}；surfaces ✗→adapters 实现细节
```

### 5.2 六条端口（`ports/`，零实现；每条 ≥2 实现）

| # | 端口 | 核心方法 | 实现 | 消费者 |
|---|---|---|---|---|
| P-1 | `ReadPort` | `query_df/query_rows/command/tables/columns/close` + `backend`（方法名与现 `Rd` 逐字一致，`runtime_checkable`） | `DuckDBRead`/`ClickHouseRead`/`MemoryRead` | app.run、core 数据装配、测试 |
| P-2 | `ArtifactWritePort` | `write_run/write_multi/read_run`（6 版本常量逐字不变；校验先于 I/O；失败零写入） | `ParquetArtifactWriter`/`InMemoryArtifactWriter` | app.run/evaluate |
| P-3 | `PanelStorePort` | `load_panel/list_factors/has_panel` | `ParquetPanelStore`/`DictPanelStore` | app.evaluate/results_index、web |
| P-4 | `FactSourcePort` | `connect/insert_arrow/reconcile/exists` | `ClickHouseFactWriter`/`DryRunFactWriter` | app.ingest（研究侧） |
| P-5 | `BatchOrchestrator` | `run(tasks,worker,*,workers,stall_s,lock_path,state_path,success_marker)→BatchReport` | `FlockProcessPoolOrchestrator`/`InlineOrchestrator` | 全部研究批算工具 |
| P-6 | `EvalKernelPort` | `evaluate(panel,factor_name,direction,target)→dict` | `RustICKernel`（quant_core）/`PyICKernel` | app.evaluate |

### 5.3 新增 `core/factio/`（跨 worktree 共享的事实访问纯核）

`paths.py`（唯一绝对路径源；env 可覆盖）· `schema.py`（10 档快照等列契约 + POLARS_TA_WQ_MODULE）·
`tick_month.py`（tick 月表读单函数）· `timeparse.py`（显式位宽）· `boards.py`（板块分类）。
约束：仅依赖 polars/pyarrow（emb 3.11 可 import）；`factorlab/__init__.py` 保持 22 字节不加依赖。

### 5.4 关键架构决策：跨 worktree 共享核 = 方案 B'

**research 分支退役平台副本（src/ tests/ 平台文档）**；研究侧 `tools/_env.py` 单点注入
`../quant-platform-main/src` 并**运行时断言** `factorlab.__file__` 落位；`platform_head()` 记录
main HEAD 进运行产物。解释器映射（刻意不统一）：T1（1m_features/strategies，需完整 factorlab）
= 平台 venv；T2（lob_fact/converters/ch_ingest/quark_download，只需 core.factio）= emb 注入；
T3（ashare）= 只依赖 editable。**WS1 是最后一次 main→research 合并**。

决策对比：合并单 worktree（与分支纪律正面冲突 + 需归并 414 文件历史；留退出路径）·
独立纯核包（93 测试 + interface.md 全量跨包改名，改动面翻倍）· symlink（git 不跟踪目录级
符号链接内容，迁移即断）——均否决，选 B'。

### 5.5 递归判断（手册 §9 判据）

需下一层 P0-P4 的新 SoI：`core.engine`（多责任+不变量）、`core.factio`（契约即正确性）、
`adapters.read`（多后端+连接状态）、`ports.batch`（失败语义/资源门）、`tools.lob_fact`
（多责任+校准冻结生命周期）、`tools.ch_ingest`（8 表+派生+对账）。
Leaf 直接 P5：`app.evaluate`、`surfaces.web`（保守）、`tools.converters`（拆分后）、`quark_download`。

## 6. 重复面收敛（DER-005..008）

路径常量 6 套→`factio.paths`；tick 月表读 ≥6 份→`factio.tick_month.read_tick_table`；
10 档列契约 4 处→`factio.schema`；编排样板 3 份→`ports.batch`+`adapters.batch_flock`；
时间解析 2 套→`factio.timeparse`（width=9|2 显式）；panel 读 2 份/因子枚举 2 份/summary 加载 2 份
→P-3 与 `app.results_index`；评估装配 2 份→`app.evaluate`；板块分类 2 处→`factio.boards`；
polars_ta 包名→`factio.schema.POLARS_TA_WQ_MODULE`；`cvt.SCHEMAS[...]` 副作用注册→显式 `schema=` 参数。

## 7. 范围外（登记 workspace pending-items，不静默消失）

1. **平台缺陷修复**（`spec.target` 未接线、`ts_RSI` float32 崩溃、`if_else` 白名单）——会改数值，
   与本轮"位级不变"门冲突；是使命最高价值后续项，收口后单独立项（带 TDD）。
2. 死代码 `eval/metrics.py::coverage_report`；Web 功能重构；data rebuild 行为；GP 搜索器实现；
   ashare 内部重构；CH schema 漂移核对；单 worktree 合并（退出路径已定义）。

## 8. 验证（P7）与收口（P8）

V1 平台 2436 失败=0 · V2 研究 ≥183+N · V3 catalog diff==0 · V4 位级三测试+双跑+搬家前后 sha256 ·
V5 442/692 diff 门 · V6 env 双腿 · V7 纯核隔离 import · V8 依赖方向 =0 · V9 单副本 ls-tree=0 ·
V10 旧路径零残留 · V11 路径单点 · V12 CH reconcile 全库一致 · V13 1m check-day max|Δ|=0 ·
V14 lob 金样 pins · V15 编排契约 · V16 RSS ≤7GB · V17 数据零变更 + df 对照。

P8 判定：同一内核三入口逐字节一致；"用哪个内核"可答（REQ-Q-011）；
"所有规格满足但使命失败"候选 = 平台缺陷未修（登记为独立子树，不构成静默缺口）。

## 9. 风险（P7 口径，摘要）

R1 端口化破坏双腿参数化（端口自身 parametrize；不接受 skip 当绿）· R2 搬家改位级
（搬迁批禁改计算表达式；diff 只许 import 行）· R3 catalog 脱钩（先锁输出快照）· R4 文案被改
（442 diff 门）· R5/R6 用错内核（B' + 落位断言根治）· R7 数据误动（只碰 git 源码）· R8 磁盘
（禁复制）· R9 import 大面积红（每批 collect-only 先行）· R10 注册时机（幂等 + 只读断言）·
R11 settings 改写（独立提交 + 红测试先行）· R13 不统一解释器（刻意）· R14 半成品运行
（干净门 + platform_head）。

## 10. 实施计划

WS0 基线冻结 + 本文落盘 → WS1 跨 worktree 收敛（B'）→ WS2 端口纯增量 → WS3 core 搬迁 5 批
（含切除 artifacts 依赖）→ WS4 adapters 搬迁 7 批 → WS5 app/surfaces 与评估装配单点 →
WS6 研究侧重排 + 编排单点 → WS7 文档/skills/安装面 → WS8 P7/P8 收口。
各阶段门、证据与提交边界见 workspace `docs/verification/WS*/`（随实施同步落盘）。
≈21 天保守工作量；检查点 WS1 / WS3-4 / WS6 / WS8。

## 11. 验证记录

### POST-WS8 缺陷修复（2026-09-14）：能力实演暴露两处缺陷 — 已修（commit ce3ffcc）
P8 之后按用户要求做**能力实演**（真 CH 端到端跑因子 + 库级分析），暴露两个测试套件
未覆盖的缺陷——两处都不是"重构把对的改错了"的臆断，均有定位证据：

1. **process 处理器注册丢失**（WS3/WS5 包重排的 regression）：run 链不再 import 旧
   `factorlab.process.__init__`（注册副作用所在）→ 实跑 CLI 即
   `KeyError: 未知处理器: winsorize（可用: ）`。套件掩盖机制 = 导入顺序依赖
   （test_process 先 import）。修复 = 装配点显式化（`install_processors` /
   `_ensure_assembly`，docstring 之后调用）+ **注册缺失即 RuntimeError 守卫**
   （不做静默 no-op）+ 3 个子进程回归用例（含守卫红绿）。
   架构含义：DER-003「注册即装配」原只覆盖算子族，本轮补齐 process 族——**注册副作用
   不得作为隐式契约**（No Hidden Design）。
2. **process 链 NaN 毒化**（旧缺陷，非重构引入；refactor 前后 diff 可证 body 未动）：
   polars `NaN > 0` 为 True → 截面含任一 NaN 时 std=NaN 被判有效 → 整截面 NaN、IC 归零
   （真实数据 332 只退市/无数据股触发）。修复 = 四个截面处理器统一 `fill_nan(None)`
   （NaN ≡ 无效观测，输出 null，评估层照常过滤）+ 4 个用例。

门：平台全量 **2469 passed / 13 skipped**（基线 2462 → 2469 = +7 新增用例，逐项登记）；
真 CH 复验 momentum_demo n_weeks 0→25、ic_mean=0.0347；三因子库级 list/corr/svd/resic 全通。
教训（记为下轮回归清单）：**"测试全绿"不等于"链路能跑"**——注册/装配类副作用缺陷与
NaN 语义类缺陷都在单元测试的盲区，实跑门（真数据 + 全链）应作为收口标准动作。

3. **CLI `op list` 空注册表**（同上缺陷类，同日第二例 · commit 944cfb6）：`factorlab op list`
   打印 `[]`——CLI 进程从不 import 算子族实现模块，`@factor_op` 装饰器副作用未发生。
   危害被放大：`catalog dump` 的未知算子关闸指引正是"对照「注册清单」（catalog dump /
   factorlab op list）改正名字"，空表反噬写因子的 AI。修复 = `app.bootstrap.ensure_assembly`
   **单点装配** + CLI 组回调调用（全部子命令与未来新增命令自动覆盖）+ 子进程回归用例。
   **根因归纳**：同一缺陷类在 3 天内出现 3 次（process 处理器 / NaN 语义 / CLI op list），
   前两次是"装配副作用充当隐式契约"、第三次是"语义前提未写进处理器契约"——**凡契约靠
   副作用或默认语义成立，就必须有显式装配点 + 实跑门**（本 spec §5.1 依赖方向门之外，建议
   下一轮补一条"注册面完整性门"：任一入口调用后，其声明依赖的注册表必须非空）。

4. **插件算子"可注册不可用"+ 分区前缀陷阱**（同日第 4 处，能力追问暴露）：`factorlab op add`
   成功后 `op list`/`op doc` 可见，但 run 链报 `未知算子`——`discover_plugins` 只在 op 子命令
   被调（同 1/3 的装配缺陷类）；补齐后发现第二层：生成代码以裸名字调用算子，插件名字不在
   exec 作用域 → `NameError`。修复 = 装配单点补插件发现 + 加载器登记来源模块
   （`registry.mark_source_module`）+ 引擎注入 import 头（无插件时零注入，位级门前提）。
   **陷阱（实测两例）**：分区语义与窗口预热**都据名字前缀**识别（`ts_` → `.over(asset,...)`；
   裸名 → 元素级 = 行序窗口跨 code 块；`_ts_window_days` 对裸名返回 0）——裸名插件在真实
   日频主链上**可能恰好逐位相同**（满载历史裁掉污染行，实测一例 sha256 相同），属加载策略
   巧合而非保证。故加**注册期命名门**（`plugin_naming_error`：ts/ta 必须 `ts_`、cs 必须
   `cs_`、el 免前缀、gp 暂不支持），并把两例泄漏固化为证据测试
   `tests/test_plugin_partition_prefix.py`。已装违规插件不阻断 CLI（discovery 告警 + 禁用，
   避免堵死 `op remove` 修复路径）。
5. **`lint` 假报错 + Web 集成测试假失败**（同日第 5/6 处，用户"写因子→跑结果"追问暴露 ·
   commit e83fb2d）：lint 拿**未替换**文本过 AST 门 → 文档化 `params`/`${name}` 模板假报
   "语法错误"（全库 152 spec 中 15 个，写因子第一条命令即误报）；修法 = 与引擎同序
   （`substitute_params` → `validate_formula`）+ 校验范围补全池公式与宏体。
   Web 集成测试 fixture 只判 `results/` 目录存在却断言历史档因子 → 用户首跑自己的因子
   即由 skip 变 fail（404）；修法 = 按**具体产物**判 skip。
   归纳（新增一类）：**门本身也会假失败**——"真错与假错无法区分"与"用户正常操作触发
   假红"都是交付级缺陷，门必须与实现同序、按具体前提判定。

   终态门：平台全量 **2486 passed / 13 skipped**（基线 2462 → 2486 = +24，逐项登记）。

### WS6（2026-09-12）：研究侧重排 — PASS（部分；剩余项 pending #11）
lob_fact 包化（core/store/pipeline/diag；`store` 而非 `io`——stdlib `io` 在 sys.modules 必然遮蔽）；
路径单点（`core/config.py` 由 `core.factio.paths` 派生，经 `tools/_env` 落位断言）；
时间解析单点（`qa/streams.hms_to_ms` 委托 `factio.timeparse`）；tick 读单点
（4 个诊断读点 → `adapters.tick_read`；cancels 契约入 `factio.schema`）；
6c：`extract_sz_cancels` 去 `cvt.SCHEMAS` 全局注册 → `MonthWriter(schema=)`。
未做（登记 pending #11）：P-5 编排真实实现与三样板切换、ch_ingest 拆分、
生产读点（run_lob_batch/factor_panel）收敛、catalog 拆分——均需字节级重跑门配套。
门：lob 183 passed；1m check-day max|Δ|=0。

### WS7（2026-09-12）：文档面收口 — PASS
interface.md 64 处路径重指 + `tests/test_doc_paths_exist.py`（全路径解析 + 负向自检；
抓出两条无后缀漏网）；CLAUDE.md 架构分层节 / AGENTS.md 速览；factor-mine skill 清 Windows 残留。
门：全量 2462 passed/13 skipped。

### WS8（2026-09-12）：P7/P8 收口 — PASS
V 链全绿：V1 2462/13 · V2 研究 183 + T1 24（emb skip 不假通过）· V3 catalog diff==0 ·
V4 位级 66 · V5 **旧 match= 行零改动**（+9 全为新增测试）· V7/V8/V9 架构门 17 · V10 代码旧路径 0 ·
V11 路径单点（仅 notes/ 历史豁免）· V12 CH 全库一致 · V13 1m max|Δ|=0 · V14 金样 183 ·
V17 数据零变更。P8 判定 Mission satisfied（`docs/verification/final/03-p8.md`）。
过程中修复：T1 测试 importorskip 目标随迁址失效导致的**假 skip**（终验抓出）。

### WS5（2026-09-12）：app 层与装配单点 — PASS
注册单点 `core/ops/registration.ensure_all_ops_registered`（幂等；`app.bootstrap.install_operators`
为文档化入口）；`RunContext.max_memory` → `DuckDBRead` → `open_read` 贯通、**CLI 去 settings 全局改写**；
`app/evaluate.py`（唯一评估装配 `evaluate_run`+`publish_run`，CLI 内联段上移）；`cli/ web/` → `surfaces/`；
P-6：`evaluate_factor_weekly` 归位 `adapters/rust_ic.py` + `RustICKernel`（EvalKernelPort）。
附带修复：测试注册表跨文件污染（test_ops 泄漏 + conftest 全 suite 隔离）——顺序相关假失败根治。
门：每批全量；终态 2460 passed/13 skipped。证据：workspace `docs/verification/WS5/`。

### WS3（2026-09-12）：core 层搬迁 — PASS
3a domain/numerics/qa/factor/spec → 3b ops → 3c-1 engine → 3c-2 持久化/rd 依赖切出
（run_factor/run_factor_minute/6 个装配 helper → `app/run.py`；对齐校验 → `core/engine/alignment.py`；
列契约 → `core/factio/schema.py`）→ 3d eval/strategy/process.registry/execution 纯子集 →
3e factio 扩展（paths/timeparse/boards/tick_month）+ `adapters/tick_read` + `adapters/plugins`。
**纯核双门**（静态 AST + 隔离运行）入 `tests/test_architecture.py`。附带真实缺陷修复：
`load_daily_fill_state` 两腿补 ORDER BY（全量套件实测行序 flaky）。门：collect 2469；
全量 2456 passed/13 skipped；catalog 每批重生成。证据：workspace `docs/verification/WS3/`。

### WS4（2026-09-12）：adapters 层（I/O 收敛）— PASS（4g 推迟）
读侧：`data/*` → `adapters/{duckdb_read,ch_read,read/*,intraday,fetcher,rebuild,refresh,mirror_db}`；
`Rd`→`ReadPort`、`open_read`→`app/bootstrap.py`；`factorlab.data` 退役。写侧：artifacts/strategy_artifacts/
execution_store/process_ops → `adapters/`。面板/摘要单点：`adapters/{panel_store,results_fs}.py`
+ 四消费点接线（correlation/cross_section/web/parquet_artifacts）。门：每批全量 2456 passed/13 skipped。
4g（catalog 拆分）推迟至 WS7（纯位移、低净值）。证据：workspace `docs/verification/WS4/`。

### WS2（2026-09-12）：端口层建立（纯增量）— PASS
六条端口 Protocol 落位（`src/factorlab/ports/{read,write,panel_store,source,batch,eval_kernel}.py`）
+ `RunPayload`/`ReconcileReport`/`Task`/`Result`/`BatchReport` 载荷 + 六条内存桩
（tests/_doubles.py，不进 src）+ `tests/test_ports_contract.py` 8 项（形状/行为/负行为）。
门：纯增量（`git status` 仅 3 个新增路径，零既有文件改动）；平台 2435 passed / 13 skipped
（= 前值 2427 + 8 契约测试，skipped 不变）；DuckDBRd 直接 `isinstance(..., ReadPort)` 通过。
其余端口真实实现随 WS4/WS5/WS6 接入同一契约测试的 param 列表。
证据：workspace `docs/verification/WS2/`。

### WS0（2026-09-12）：基线冻结 — PASS
平台 2423 passed / 13 skipped；研究 183 passed；位级 66 passed；catalog 27 passed；
692/442 计数、漂移面 5 文件、df、RSS（2020-01 单月 3.87GiB）落证。
证据：workspace `docs/verification/WS0/`。

### WS1（2026-09-12）：跨 worktree 收敛（DER-002）— PASS
`git merge main`→research 后 `git rm -r src tests`（190 文件）+ 退役平台手册 4 份 +
`tools/_env.py` 单点（强制提权队首 + 落位断言）+ T1 接入 + 策略工具迁 `tools/strategies/`。
门：平台 2427/13（=基线+4 架构门）；研究 183+1skip（T1 24 用例在平台 venv 全过，
emb 下 importorskip 干净 skip）；单副本 `ls-tree -r research` src/tests = 0；
`1m_features check-day 2020-01-02` 经共享核 + 真实 CH 逐值一致（max|Δ|=0）；
692/442 不变。过程发现并修复：`_env` 优先级守卫太弱（editable .pth 在尾部 →
强制提权）、架构门首版漏 `-r` 假绿（修正后如实红→绿）、shell 门路径假阳性。
证据：workspace `docs/verification/WS1/`；提交 main `34f3919`、research `feff0f8`/`9cba8dc`/`8e01168`。
