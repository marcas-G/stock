# 研究员接口封装（Research API / flab）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AI 研究员提供统一门面（`flab` CLI + `factorlab.research` Python 面）：全库数据查询、因子计算/评估/冗余检验、策略回测、报告与一条链 study，统一 JSON 契约与稳定错误码。

**Architecture:** 新门面包 `platform/src/factorlab/research/`（registry 单点 + envelope + 各组门面函数，只装配不实现业务），CLI 挂载 `factorlab research` 命令组，`flab` 短入口脚本注入环境与重任务闸。

**Tech Stack:** Python 3.13 / typer（现有 CLI 框架）/ polars / pytest（CliRunner、双后端 `env` fixture）。

**Spec:** `knowledge/design/platform/specs/2026-09-18-research-api-design.md`（执行者必读）

## Global Constraints

- **只装配不实现**：门面调用既有 app/adapters 函数；业务逻辑不得进 `research/`。
- **stdout 仅一个 JSON**；日志/进度 → stderr + artifacts.log；`--pretty` 供人。
- **错误码**（exit）：USAGE=2 / LINT=3 / MEMORY_GUARD=4 / DEAD_SIGNAL=5 / RUN_FAILED=6 / STRATEGY_FAILED=7 / DATA=8 / NOT_FOUND=9 / INTERNAL=10 / BUSY=11。
- **重命令强制过闸**：`factor run`/`strategy run`/`study run` 经 `_guard.py`（flock 2 槽非阻塞→BUSY；`--wait` 阻塞；avail<8GB→MEMORY_GUARD；注入 `FACTORLAB_MAX_MEMORY=8GB`、`FACTORLAB_MIN_AVAILABLE_MEMORY=6GB`、`OMP/POLARS_MAX_THREADS=8`、nice）。
- **大数据**：结果 >200 行默认落 `runs/research/<command>/<ts>/data.parquet`，JSON 回 path+head(5)+schema；`--out/--limit/--inline` 覆盖。
- **默认值**：后端 ch、结果目录 `runs/platform`（策略 `runs/platform/strategies`）、不交互、幂等。
- TDD：先红后绿、突变可杀存根；一次提交一棵树；证据落 `governance/evidence/verification/R31/research-api/`。
- 平台全量/`make test-research` 不回退；`make gates` 仅预存红可接受；不动挖矿/reviewer 在途文件。

---

### Task 1：契约层（registry + envelope + describe + CLI 挂载）

**Files:**
- Create: `platform/src/factorlab/research/__init__.py`、`registry.py`、`envelope.py`、`cli.py`
- Modify: `platform/src/factorlab/surfaces/cli/main.py`（挂 `research` 子命令组）
- Test: `platform/tests/test_research_contract.py`

**Interfaces:**
- Produces: `Envelope`（dataclass：ok/schema_version/command/data/artifacts/warnings/error）、`ok(command, data=None, artifacts=None, warnings=())`、`fail(command, code, message, hint=None, log=None)`、`EXIT_CODES: dict[str,int]`、`COMMANDS: dict[str, CommandSpec]`、`CommandSpec(name, params, defaults, description, examples, output_schema)`、`register(spec, handler)`、`dispatch(argv) -> int`。

- [ ] **Step 1: 写失败测试**（envelope 信封与错误码映射；registry 注册/describe 输出；CLI `factorlab research describe --json` 仅一个 JSON）

```python
def test_envelope_ok_single_json(capsys):
    from factorlab.research.envelope import ok, emit
    assert emit(ok("version", {"version": "0.1.0"})) == 0
    out = capsys.readouterr().out.strip()
    doc = json.loads(out)
    assert doc["ok"] is True and doc["schema_version"] == 1 and doc["error"] is None

def test_error_exit_code():
    from factorlab.research.envelope import fail, emit, EXIT_CODES
    assert emit(fail("factor.run", "BUSY", "闸满", "稍后重试或 --wait")) == EXIT_CODES["BUSY"]
```

- [ ] **Step 2: 跑测试确认失败**：`cd platform && .venv/bin/python -m pytest tests/test_research_contract.py -q` → ImportError/FAIL
- [ ] **Step 3: 实现** `envelope.py`（json.dumps ensure_ascii=False；错误→退出码表，未知码归 INTERNAL；`--pretty` 由 env/参数控制）、`registry.py`（有序 dict + describe 文档生成 + handler 签名 `fn(args: Namespace) -> Envelope`）、`cli.py`（typer/argparse 薄壳，把 `research` 挂到现有 app；handler 异常→`INTERNAL` 信封）。
- [ ] **Step 4: 跑测试转绿** + 手动 `factorlab research describe --json | python -m json.tool` 检查。
- [ ] **Step 5: 提交** `feat(research): 契约层（envelope/registry/describe + CLI 挂载）`

### Task 2：`flab` 短入口 + 重任务闸 `_guard.py`

**Files:**
- Create: `governance/ops/install_flab.sh`、`platform/src/factorlab/research/_guard.py`
- Test: `platform/tests/test_research_guard.py`；shell 冒烟入证据

**Interfaces:**
- Consumes: Task 1 的 envelope。
- Produces: `guard_heavy(argv, *, wait: bool) -> tuple[dict, Path | None]`（获得槽位返回注入 env；失败抛 `GuardError(code, message, hint)`）；`install_flab.sh` 生成 `~/.local/bin/flab`。

- [ ] **Step 1: 写失败测试**：mock flock 文件占用 → `GuardError(code="BUSY")`；`--wait` 阻塞后获得；avail 低（mock `psutil.virtual_memory`）→ `MEMORY_GUARD`；断言 env 注入值（8GB/6GB/8 线程）。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现** `_guard.py`（flock 两个槽文件 `~/.cache/factorlab/heavy.{1,2}.lock`，非阻塞 LOCK_EX|LOCK_NB；预检 `psutil.virtual_memory().available < 8GB`；env dict 返回）与 `install_flab.sh`（写脚本：`exec env FACTORLAB_DATA_BACKEND=ch "$ROOT/platform/.venv/bin/factorlab" research "$@"`，chmod +x，`--dry-run` 可打印）。
- [ ] **Step 4: 绿 + 安装 `bash governance/ops/install_flab.sh` 并 `flab describe --json` 冒烟**。
- [ ] **Step 5: 提交** `feat(research): flab 短入口 + 重任务闸`

### Task 3：data 组（全库读取）

**Files:**
- Create: `platform/src/factorlab/research/data.py`
- Test: `platform/tests/test_research_data.py`
- 数据源复用：`adapters/read/{source,calendar,adjust}.py`、`adapters/intraday`、`app/bootstrap.open_read`

**Interfaces:**
- Produces: `result_frame(df: pl.DataFrame, *, command, artifacts_dir, out=None, limit=None, inline=False) -> Envelope`；各命令 handler `data_tables/data_schema/data_status/data_calendar/data_daily/data_minute/data_tick/data_daily_basic/data_adj/data_limit/data_stock_basic/data_universe/data_moneyflow/data_sector/data_members/data_fundamentals`。

- [ ] **Step 1: 写失败测试**（MemoryRead/`env` 双腿）：`data_daily` 必须真实走 ReadPort（spy 记录 SQL/表名，禁止硬编码）；>200 行落 parquet 且 JSON 只含 head；`--limit/--inline` 行为；`data_tables` 用 CH `system.tables`（ch 腿）或 MemoryRead 表清单；缺失表→`DATA`。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现**：通用 `_frame_env`（polars→head/path 逻辑）；每 handler：装配 codes/date→既有 load 函数；daily 用 `view_prices(view=...)`；minute/tick 仅 ch（duckdb 腿→`DATA`+hint）；universe 调 `tools/universe_stages` 产物或 `app` PIT 读取（以现树为准，先探索再写）；`tables/status` 查 `system.columns` + 各表 max(trade_date)。
- [ ] **Step 4: 绿 + registry 注册全部 data 命令（describe 可见）+ 提交** `feat(research): data 组（全库读取 + 大数据落盘契约）`

### Task 4：factor 组（含 admit 冗余检验）

**Files:**
- Create: `platform/src/factorlab/research/factor.py`
- Test: `platform/tests/test_research_factor.py`

**Interfaces:**
- Consumes: Task 2 `guard_heavy`。
- Produces: `factor_lint/factor_run/factor_list/factor_show/factor_export/factor_corr/factor_resic/factor_svd/factor_ref_*/factor_admit/factor_op_*/factor_catalog`；`factor_admit` 返回 `{verdict: 可加入|冗余|重复, corr_max, r2_lib, retention, resic, 建议}`。

- [ ] **Step 1: 写失败测试**：`factor_run` 必须过闸（mock guard 记录）、产物含 `evaluation.version/frequency`（真库/固定 kernel 至少断言新增产物时间戳）；`factor_admit` 合成面板（独立→可加入；近亲→冗余）三条判决；`factor_lint` 坏 spec→`LINT`；缺失产物→`NOT_FOUND`。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现**：直接调用 `surfaces/cli/main.py` 已实现的 lint/run/list/show/corr/resic/svd/ref/op/catalog 逻辑函数（不为门面重写；抽公共 `_call_cli_handler` 或直接 import 其内部函数，以现树为准）；`admit` = lint→(缺产物则 run)→`resic --against reference`+`corr --against reference`→verdict 规则（r2_lib≥0.8 且 resic 不显著→冗余；corr_max≥0.95→重复；否则可加入——阈值调用现有 D10 口径函数）。
- [ ] **Step 4: 绿 + 注册 + 提交** `feat(research): factor 组（run 过闸 + admit 一键冗余检验）`

### Task 5：strategy + report 组

**Files:**
- Create: `platform/src/factorlab/research/strategy.py`、`report.py`
- Test: `platform/tests/test_research_strategy_report.py`

**Interfaces:**
- Produces: `strategy_lint/run/list/show/export/capacity/cost`；`report_list/show/url/serve`。
- Consumes: `app/strategy/run.run_strategy`、`adapters/execution_store`、`surfaces/web`。

- [ ] **Step 1: 写失败测试**：strategy run 过闸；无信号产物→`NOT_FOUND`+hint（先 `flab factor run`）；`strategy_capacity/cost` 读回测产物返回字段；`report_url` 不启服务返回路径；`report_serve` 用 CliRunner 起停或以 `create_app` 断言（不长跑）。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现**（装配默认值；capacity/cost 调已含的 `capapcity_proxy`/`cost_net_report`；report url 基于 `runs/platform/strategies/<name>`）。
- [ ] **Step 4: 绿 + 注册 + 提交** `feat(research): strategy/report 组`

### Task 6：study 一条链 + 手册/skills + E2E 验收

**Files:**
- Create: `platform/src/factorlab/research/study.py`、`knowledge/handbooks/research-agent-manual.md`
- Modify: `AGENTS.md`、`.claude/skills/factorlab-*/SKILL.md`（指向 flab）
- Test: `platform/tests/test_research_study.py`、`test_doc_paths_exist.py`（手册命令必须存在于 registry）

**Interfaces:**
- Produces: `study_run(factor_yaml, strategy_yaml=None, against="reference", skip_admit=False) -> Envelope`（artifacts: run_dir/summary/strategy_dir/report_url/log）；`study_list`。

- [ ] **Step 1: 写失败测试**：链序（mock 各组记录调用顺序 run→admit→strategy→report）；任一步失败→对应错误码且 artifacts 含已完成步骤；文档漂移测试（手册内每个 `flab ...` 命令在 registry）。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现 + 手册（≤1 屏，10 示例）+ skills/AGENTS 更新**。
- [ ] **Step 4: E2E 真 CH**：`flab data daily` → `flab factor run`（小 universe 真 spec）→ `flab factor admit` → `flab strategy run`（已有策略 yaml）→ `flab report url`；原始输出落 `governance/evidence/verification/R31/research-api/`。
- [ ] **Step 5: 全量验收**：平台全量、`make test-research`、`make gates`（仅预存红）、describe/help/手册三处一致性；提交 `feat(research): study 一条链 + agent 手册 + E2E 证据`

## Self-Review（对 spec 覆盖）

§3 命令面 → Task 3/4/5/6 全覆盖（data 14/ factor 13/ strategy 7/ report 4/ study 2/ 通用 3）；§4 契约 → Task 1；§5 默认与安全 → Task 2；§6 可发现性 → Task 1 describe + Task 6 手册；§7 测试 → 各 Task + Task 6 E2E；§8 顺序一致。类型一致性：`Envelope`/`GuardError`/`result_frame`/`study_run` 命名跨任务一致。

## 风险

| 风险 | 处置 |
|---|---|
| 现有 CLI 逻辑难以函数级复用 | Task 3/4 先探索 `surfaces/cli/main.py` 抽函数；实在不行以子进程调用既有 CLI 并在门面层解析 JSON（不许复制业务代码） |
| data 组表多、单文件过大 | `data.py` 超 600 行则按 `data_bars.py`/`data_fund.py`/`data_meta.py` 拆分 |
| E2E 依赖真 CH/数据 | 小 universe + 现有 spec；CH 不可达时 skip 并留证 |
