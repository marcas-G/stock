# 策略配置化实施计划（Plan S）——六层漏斗 + YAML + L5

> **实施状态（2026-09-16 收口）**：**已完成**——实现 `7e03acb`/`126def4`（契约+YAML 加载器 / 运行器 /
> 研究入口 / 档案+策略索引门 / 首例 / L5 `max_hold`）；验收 `governance/evidence/verification/R28/`（R06 复查复核通过）。
> 计划步骤已按 R28 验收证据补记完成；后续维护以现行代码与文档为准。

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 策略像因子一样"一处定义（YAML）、机器可跑、人可阅读、可版本化"：L0-L5 六层漏斗中，
L3 由因子提供、L4 走 M7、L5 走 M8；策略 YAML 声明六层映射并一键跑出回测与证据。

**Spec:** `docs/reviews/2026-09-16-strategy-decomposition/design.md`（六层规范 §1-§2、缺口 G1-G5、
D4 草案 §6；决策 D1/D2/D4 已拍板）。
**关联**：`r05-usage-2026-09-16/strategy-backtest-manual.md`（回测链机制）；
`r04-efficiency-2026-09-16/tools-migration-plan.md`（`strategies` 工具留 research 的边界面）。

**Architecture（已核实的平台现状）**：
- M7 契约 `StrategySpec`（`core/strategy/spec.py:79`）**只允许** name/signal_name/direction/
  selection(top_k)/weighting(equal_weight)/gross_exposure/rebalance_frequency(daily|weekly|monthly)，
  `extra=forbid` 且**明令禁 execution 字段** → 策略 YAML 必须由加载器**组合两份 spec**：
  `StrategySpec`（L4）+ `ExecutionSpec`（L5，`core/execution/spec.py:229`，含 timing/cost/minute_window），
  不得合并 schema。
- M8 **无 CLI（勿发明）**；链 = `load_signal_artifact → construct_target_portfolio →
  run_backtest → save_backtest_result`（见 factorlab-backtest skill、`tests/test_execution_signal_chain.py`）。
- 落点分层：新契约（纯 pydantic）在 `core/strategy/`；YAML 读入按 `core/spec.py:190 load_spec` 先例；
  运行器在 `app/`；研究侧薄入口与 L5 规则在 `research/tools/strategies/`。

## Global Constraints

- **TDD**：先失败测试后实现；测试须能识别存根（硬编码返回值必败）。
- **core 纯净**：契约模型不得含 IO；YAML 读入与 `load_spec` 同模式同位置（若架构门拒绝，移 `adapters/`，团队按门定夺）。
- **零回归**：`NEXT_OPEN` 既有链逐值不变（M8 回归 307+122 passed 基线）；`StrategySpec` 既有测试不动。
- **一次提交一棵树**（platform / research / docs 分提）；证据落 `docs/verification/R28/`（按轮次顺延）。
- **内存纪律**：重任务带 `FACTORLAB_MAX_MEMORY=8GB`；分钟链默认 20 日/块。
- **CA Gate**：多窗口被拦是已知行为（R03-I8），首例选干净窗口并在档案记录窗口筛选过程。

## 文件结构（落点）

| 动作 | 路径 | 职责 |
|---|---|---|
| 新建 | `platform/src/factorlab/core/strategy/doc.py` | `StrategyDoc`：YAML 文档契约（组合 StrategySpec + ExecutionSpec + date/universe_override/regime/rules） |
| 新建 | `platform/src/factorlab/core/strategy/spec_io.py` | `load_strategy_doc(path) -> StrategyDoc`（YAML 读入 + fail-fast；按 `core/spec.py` 先例） |
| 新建 | `platform/src/factorlab/app/strategy/run.py` | `run_strategy(doc, rd) -> StrategyRunResult`（链 assembly + 持久化） |
| 修改 | `platform/src/factorlab/core/strategy/__init__.py` | 导出 `StrategyDoc`/`load_strategy_doc`（`run_strategy` 走 `app`） |
| 新建 | `platform/tests/test_strategy_doc.py` | 契约 + 加载器测试（含未知键/类型/边界） |
| 新建 | `platform/tests/test_run_strategy.py` | 运行器端到端（合成数据，逐值断言 + 禁止行为断言） |
| 新建 | `$QUANTRESEARCH_ROOT/strategy/low_lottery_top30_weekly.yaml` | 首例策略 spec |
| 新建 | `research/tools/strategies/run_strategy.py` | 研究侧薄入口（参数=spec 路径；打印 run 摘要） |
| 新建 | `research/tools/strategies/l5_rules.py` | L5 规则层（V1：`max_hold` 强制换出） |
| 新建 | `$QUANTRESEARCH_ROOT/dossiers/strategies/_template.md` | 策略档案模板（从 `crash_bottom_leader_strategy.md` 提炼） |
| 新建 | `research/tools/factor_lib/build_strategy_index.py` | `$QUANTRESEARCH_ROOT/index/strategies.md` 生成 + `--check`（与因子索引同模式） |
| 修改 | `Makefile` / `scripts/gates.sh` | `index` 目标与 G-INDEX 扩展策略索引 |
| 新建 | `$QUANTRESEARCH_ROOT/index/strategies.md` | 生成产物（机器生成，不进人读改） |

---

## 接口契约（用户可见；实现须与此一致）

**1) 策略 YAML——用户唯一要写的东西（六层声明）**

```yaml
name: low_lottery_top30_weekly     # StrategySpec.name
signal: max_effect_20d_high        # L3：已入库因子名（StrategySpec.signal_name）
direction: -1                       # ±1：信号越大越优 / 越小越优
regime: {mode: signal_gate}         # L2：本期唯一合法语义（D1）
portfolio:                          # L4：M7
  top_k: 30
  weighting: equal_weight
  gross_exposure: 1.0
  rebalance_frequency: weekly       # daily | weekly | monthly
execution:                          # L5：M8（ExecutionSpec 字段 1:1，extra=forbid 复用）
  timing: NEXT_OPEN                 # NEXT_OPEN | NEXT_WINDOW（后者必带 minute_window）
  initial_cash: 10000000.0
  cost_model: {commission_rate: 0.00025, minimum_commission: 5.0,
               stamp_tax_sell_rate: 0.0005, transfer_fee_rate: 0.00001,
               slippage_bps: 5.0}
  # minute_window: {...}            # timing=NEXT_WINDOW 时必填（2026-09-15-minute-execution 接口）
rules: {stop_loss: null, take_profit: null, max_hold: null}   # L5 路径依赖；V1 仅 null（Task 6 放开 max_hold）
date: {start: "2025-03-01", end: "2025-03-31"}   # 回测窗口（decision 过滤）
universe_override: null            # L1：可选 codes 列表；null = 随因子
```

字段 → 契约映射（加载器职责，**不做 schema 合并**）：

| YAML | 产出 |
|---|---|
| `name` / `signal` / `direction` / `portfolio.*` | `StrategySpec`（`top_k→selection.k`；`weighting→WeightingSpec`；`gross_exposure`/`rebalance_frequency` 直通） |
| `execution.*` | `ExecutionSpec`（原样 `model_validate`，pydantic 既有校验全复用） |
| `date` | `DateRange(start,end)`，`start<=end` |
| `regime` / `rules` / `universe_override` | 文档级字段（V1：regime 仅 `signal_gate`；rules 仅 null；universe_override = null \| list[str]） |

**2) 平台 API（Python）**

```python
from factorlab.config import settings
from factorlab.core.strategy import StrategyDoc, load_strategy_doc
from factorlab.app.strategy import run_strategy

doc: StrategyDoc = load_strategy_doc(
    settings.research_root / "strategy/<name>.yaml"
)
assert isinstance(doc.strategy, StrategySpec)     # property：YAML 映射构造（逐字段）
assert isinstance(doc.execution, ExecutionSpec)   # property：execution.* 直通
result = run_strategy(doc, rd)                    # rd = open_read(data_backend=...)
result.nav_series, result.out_dir, result.backtest, result.target
```

`run_strategy` 内部链（不发明新语义，全部复用既有入口单点）：
`load_signal_artifact(results_dir / doc.strategy.signal_name)` → 按 `doc.date` 过滤 signal frame →
`construct_target_portfolio` → `build_rebalance_schedule` → `write_strategy_artifacts(out_dir)` →
`run_backtest(target, doc.execution, rd)` → `save_backtest_result(out_dir)`。
**默认目录**：results 根 = `settings.results_dir`；`out_dir` 默认 =
`settings.results_dir / "strategies" / doc.strategy.name`（与因子结果目录隔离）。
错误透传（CA Gate / `ExecutionDataQualityError` / fail fast），不吞、不自动重试。

**3) 研究侧 CLI（用户实际测试入口）**

```bash
FACTORLAB_DATA_BACKEND=ch FACTORLAB_MAX_MEMORY=8GB \
  platform/.venv/bin/python research/tools/strategies/run_strategy.py \
  $QUANTRESEARCH_ROOT/strategy/low_lottery_top30_weekly.yaml [--dry-run] [--out-dir DIR]
```

- `--dry-run`：只打印六层解析结果（**不触 CH**，供秒级自检）；
- 正常：打印 NAV / 决策数 / 成交事件 / 落盘路径；缺数据或被 CA Gate 拦截 → 非零退出并原样报错。

---

## Task 1: `StrategyDoc` 契约 + YAML 加载器

- [x] **Step 1: 写失败测试**（`platform/tests/test_strategy_doc.py`）
  - 合法 YAML（完整六层）→ `doc.strategy` 是 `StrategySpec`、`doc.execution` 是 `ExecutionSpec`、
    `doc.date` 窗口、`doc.regime.mode == "signal_gate"`；断言 `doc.strategy.name == "low_lottery_top30_weekly"`、
    `doc.execution.execution_timing` 映射正确（**不是**检查 `len(doc) == 3` 这类格式断言）。
  - 未知顶层键（如 `formula:`）→ `ValueError` 且消息含键名（extra=forbid 语义）；
  - `direction: 0` / `true` → 拒绝；`portfolio.top_k: "30"` → 拒绝（strict int）；`gross_exposure: 1.5` → 拒绝；
  - `execution.timing: NEXT_WINDOW` 无 `minute_window` → 拒绝（复用 ExecutionSpec 既有校验）；
    携带完整 `minute_window` → 通过（**V1 即支持分钟执行配置**，与 2026-09-15-minute-execution 对齐）；
  - `rules.stop_loss` 非 null → `NotImplementedError`（V1：L5 复杂规则未落地，见 Task 6 解除）；
  - `universe_override: ["000001.SZ", ...]` → 通过且原样保存；`rebalance_frequency: weekly` 生效；
  - 缺文件/SyntaxError → 明确报错含路径。
- [x] **Step 2: 跑测试确认失败**（`cd platform && .venv/bin/python -m pytest tests/test_strategy_doc.py -q`）
- [x] **Step 3: 实现**
  - `doc.py`：`DateRange`、`RegimeSpec(mode: Literal["signal_gate"])`、`RulesSpec(stop_loss/take_profit/max_hold: ... | None)`
    （全部 `extra="forbid", frozen=True`）；`StrategyDoc(strategy, execution, date, universe_override, regime, rules)`
    的 `model_validator(mode="after")` 做跨字段约束（rules 非 null → V1 `NotImplementedError`）。
  - `spec_io.py`：`yaml.safe_load` + `path` 注入错误信息；与 `core/spec.py::load_spec` 同模式。
- [x] **Step 4: 跑测试确认通过** + `pytest tests/test_strategy_spec.py tests/test_portfolio_constructor.py -q`（零回归）
- [x] **Step 5: 提交** `feat(platform): 策略文档契约与 YAML 加载器（Plan S Task 1）`

## Task 2: 策略运行器（app）——一键链

- [x] **Step 1: 写失败测试**（`platform/tests/test_run_strategy.py`，合成数据参照 `test_execution_signal_chain.py`）
  - 断言真实行为：按 `doc.strategy.signal_name` 从 results 单点读 SignalArtifact → `construct_target_portfolio`
    收到 `direction/k/gross_exposure/frequency` 逐值一致 → `run_backtest` 收到 `ExecutionSpec` 逐值一致 →
    产物经 `save_backtest_result` 可 `load` 回读且 `nav_series` 与返回一致；
  - 禁止行为断言：monkeypatch 的记录器证明**调用发生过**（不是返回硬编码）；`NEXT_OPEN` 默认路径与
    既有链 sha256 对照无差异（抽既有 fixture）；
  - 窗口过滤：先按 `doc.date` 过滤 signal frame，构造的 target 只含窗口内 decision；
  - 落盘：`out_dir` 默认 `settings.results_dir/"strategies"/<name>`，`write_strategy_artifacts` +
    `save_backtest_result` 往返可读（`load_strategy_artifacts`/`load_backtest_result`）；
  - `StrategyRunResult` 字段：`out_dir / target / backtest / nav_series / signal_name / decision_count`；
  - CA Gate 失败时原样抛 `ExecutionDataQualityError`（不吞、不自动重试）。
- [x] **Step 2: 跑测试确认失败**
- [x] **Step 3: 实现** `app/strategy/run.py`：`run_strategy(doc, rd, results_dir=None) -> StrategyRunResult`
  （`open_read` 由调用方注入或内部 bootstrap；results 单点用 `adapters.results_fs`/`panel_store`）。
- [x] **Step 4: 跑测试确认通过** + `pytest tests/test_execution_signal_chain.py tests/test_backtest_runtime.py -q`
- [x] **Step 5: 提交** `feat(platform): run_strategy 运行器（信号→组合→回测→持久化）`

## Task 3: 研究侧薄入口

- [x] **Step 1: 写失败测试**（`research/tools/strategies/tests/test_run_strategy_cli.py`）
  - `--dry-run` 打印解析后的六层映射（含 date/rules），exit 0 且**不触碰 CH**（断言 `open_read` 未被调用）；
  - 缺参数/坏 YAML → exit≠0 且错误可读；
  - 真跑标记 `integration`（CH 后端），只做 1 个干净窗口。
- [x] **Step 2-4: 红 → 实现 `run_strategy.py`（薄封装 `load_strategy_doc` + `run_strategy`）→ 绿**
- [x] **Step 5: 提交** `feat(research): 策略 YAML 薄入口 run_strategy.py`

## Task 4: 策略档案 + 索引门（D4 建档）

- [x] **Step 1: 写失败测试**（`research/tools/factor_lib/tests/test_strategy_index.py`）
  - 由 `$QUANTRESEARCH_ROOT/strategy/*.yaml` + `$QUANTRESEARCH_ROOT/dossiers/strategies/*.md` 生成 `$QUANTRESEARCH_ROOT/index/strategies.md`；
    字节级一致：重生成 → `--check` 通过；手改索引 → `--check` exit≠0；
  - spec 缺档案（或档案缺 spec）→ 门红并列出缺失名（不是静默跳过）；
  - 档案 front matter 必须含 spec 路径与回测窗口字段（模板约定）。
- [x] **Step 2-4: 红 → 实现 `build_strategy_index.py` + `Makefile index` 与 `gates.sh` G-INDEX 扩展 → 绿**
- [x] **Step 5: 提交** `feat(research): 策略索引生成与 --check 门 + 档案模板`

## Task 5: 首例落地与端到端证据

- [x] `$QUANTRESEARCH_ROOT/strategy/low_lottery_top30_weekly.yaml`（`max_effect_20d_high`、direction=-1、
  top_k=30、等权、weekly、NEXT_OPEN、默认 A 股成本；date 用干净窗口 2025-03）；
- [x] `$QUANTRESEARCH_ROOT/dossiers/strategies/low_lottery_top30_weekly.md`（模板：假设/规格全文/窗口筛选过程
  （CA Gate 记录）/结果 NAV 与回撤/迭代历史/风险）；
- [x] 生成索引 → `--check` 绿；真跑一次（真 CH + 内存护栏）留原始输出；
- [x] **实际测试验收（用户可自己跑）**：`run_strategy.py $QUANTRESEARCH_ROOT/strategy/low_lottery_top30_weekly.yaml`
  → 打印 NAV/决策数/成交事件；与手工 demo 对照同窗口参数一致
  （基准见 `r05-usage-2026-09-16/strategy-backtest-manual.md`，防"跑通但口径漂移"）；
- [x] 证据落 `docs/verification/R28/strategy-first-example/`（命令 + 输出 + 门结果）；
- [x] 提交 `feat(research): 首例策略配置化（low_lottery_top30_weekly）`

## Task 6: L5 规则层（V1：研究侧）

- [x] **Step 1: 写失败测试**（`research/tools/strategies/tests/test_l5_rules.py`）
  - `max_hold=60`：目标组合历史中连续持有超 60 交易日的 code 在调仓日被强制换出（合成 3 只 × 100 日，
    断言被换出的 code/日期逐值；硬编码返回必败）；
  - `max_hold=None` → 输出与输入 target 逐值相同（零行为变化）；
  - `stop_loss/take_profit` 非 null：本版明确 `NotImplementedError`（含"平台化另立"指引）。
- [x] **Step 2-4: 红 → 实现 `l5_rules.py`（基于目标组合历史的近似；文档注明"非成交明细级"）→ 绿**
- [x] **Step 5: 文档化边界**：design.md §4-G3 更新为"V1 近似（调仓日粒度）+ 平台化触发条件"；
  提交 `feat(research): L5 持有上限规则（V1 近似）+ 边界文档`

## Task 7: 后置与触发条件（登记，不在本计划实施）

| # | 事项 | 触发条件 |
|---|---|---|
| G1 | regime 多输出（`outputs: [signal, regime]`） | 首个需要段界显式化的策略上库前 |
| G3+ | stop_loss/take_profit 平台化（连续/日内） | V1 近似的偏差在复盘中证实有实质影响 |
| G4 | crash_bottom 收敛 M7/M8 | `index_daily`(000852.SH)/`stock_st`/duckdb 任一恢复后 |
| — | M8 无 CLI 的入口策略（要不要 `factorlab strategy run`） | 用户明确要求命令行一键；否则维持研究侧薄入口 |

## Self-Review（对 spec 覆盖）

- design.md §1 六层 → Task 1（声明面）/Task 2（执行面）/Task 6（L5）；
- D4 §6 草案 → Task 1/3/4/5（路径按现目录；**若 R24 目录重整先落地，坐标系切到
  `knowledge/dossiers`+`knowledge/index`，本计划 Task 4/5 同步**）；
- G5 可交易性分工 → 本计划不改口径，档案模板中固定记录"池=研究口径/执行=成交口径"；
- 与 `2026-09-15-minute-execution` 的接口：Task 1 接受 `NEXT_WINDOW + minute_window`，运行器直通。

## 未竟/后置

- 策略索引与因子索引的生成器**暂不合并**（`factor_lib` 留 research，见 tools 迁移决策）；
- 策略 spec 的 lint（如 direction 与因子档案 direction 对齐校验）先放运行器期检查，
  独立 `factorlab lint --strategy` 待 Team 决定。
