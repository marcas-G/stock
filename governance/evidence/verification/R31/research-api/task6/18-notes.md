# R31 research-api · Task 6 证据（study 一条链 + health + 手册 + E2E 终验收）

日期：2026-09-18 ｜ 计划：`knowledge/design/platform/plans/2026-09-18-research-api.md` Task 6
平台提交：`d80b900 feat(research): study 一条链 + health 通用命令（手册/示例漂移门）`
文档提交：`ec2d9ea docs(agent): 研究员一页手册 + AGENTS 登记 flab 入口`

## 1. 产出

- `platform/src/factorlab/research/study.py`：`study.run`（factor run →（admit）→
  strategy run → report URL；`--skip-admit/--against/--wait`；失败传播稳定错误码且
  artifacts 保留已完成步骤）、`study.list`（历史记录含失败，落 `runs/research/study/`）。
- `platform/src/factorlab/research/health.py`：`health`（CH 连通真查询 / psutil 内存 /
  磁盘 / heavy 闸槽真探 flock / daily 新鲜度；后端不可达 → `DATA`）。
- `research/__init__.py` / `research/cli.py`：study 组与 health 挂载。
- 手册 `knowledge/handbooks/research-agent-manual.md`（41 行 ≤1 屏，10 条常用示例）；
  `AGENTS.md` 登记「研究员入口=flab」；仓外用户级
  `.claude/skills/factorlab-{dsl,data,evaluate,backtest,ch-pipeline}/SKILL.md`
  各加「研究员入口（flab）」节。
- 测试：`tests/test_research_study.py`（17）、`tests/test_research_health.py`（8）、
  `tests/test_doc_paths_exist.py` 扩展（+5：手册存在/≤1 屏、命令存在、组覆盖、
  registry 示例可解析、CLI help 顶层组一致）。

## 2. TDD 红 → 绿

- `01-red-collection.txt`：干净 HEAD worktree 拷贝新测试 → `ImportError: cannot import
  name 'study'/'health'`（2 collection errors）。
- `01b-red-doc.txt`：手册缺席 → 3 failed（exists/命令存在/组覆盖）。
- `02-green.txt`：33 passed（study 17 + health 8 + 文档门 8）。

## 3. 突变（存根可杀；逐条改实现→跑测试→复原）

| 证据 | 突变 | 结果 |
|---|---|---|
| `03-mutation-skip-admit.txt` | `study_run` 无视 `--skip-admit` 恒跳过 admit | 8 failed（链序/判决/记录/CLI） |
| `04-mutation-health-slots-stub.txt` | health 硬编码 `slots_free=2` | 2 failed（占用槽探测 duckdb|ch） |
| `05-mutation-doc-drift.txt` | 手册追加不存在命令 `flab factor ghost` | 1 failed（防漂移门） |

## 4. E2E 真 CH 终验收（`flab`，6 命令全链；`e2e/run_e2e.sh` 可复跑）

`07-e2e-timing.txt`：**6/6 PASS，exit 0，单 JSON，总耗时 32s**（16:16:47→16:17:19）。

| # | 命令 | 结果（原始输出在 `e2e/0N-*.json`） |
|---|---|---|
| 01 | `flab health` | backend=ch，daily max=2026-09-17，behind=0，avail 80.5GB，闸 2/2 空 |
| 02 | `flab data daily --codes 600176.SH ...` | 13 行（date/code/open/high/low/close/…） |
| 03 | `flab factor run e2e_factor.yaml`（120 codes，2026-04~07） | IC=0.0371，coverage=92.57%（9071/9799），n_weeks=77；`.err` 含 `[guard] slot=1/2` |
| 04 | `flab factor admit e2e_factor.yaml` | verdict=**可加入**，corr_max=0.5595，r2_lib=0.4752，resic t=0.564（16 周） |
| 05 | `flab strategy run low_lottery_top30_weekly.yaml` | signal=max_effect_20d_high，5 events，fills=176，NAV 9992407→10214807（+2.23%） |
| 06 | `flab report url e2e_research_api_probe` | `http://127.0.0.1:8000/factor/e2e_research_api_probe` |

study 真链（非必做，补证 Task 6 核心）：`08-study-run.json` = `study.run` exit 0，
steps=[run, admit, strategy, report]，verdict=可加入，strategy `e2e_probe_strategy`
9 events（NAV −14.29%），report URL 同上；`09-study-list.json` n=1。
E2E 资产：`e2e/e2e_factor.yaml`（探针 spec）、`e2e/e2e_strategy.yaml`、`e2e/run_e2e.sh`。

## 5. describe / CLI help / 手册 三处一致（`check_consistency.py`）

`18-consistency.txt`：**6/6 PASS** —— registry=describe=**49 条**，help 覆盖 8 个顶层组，
手册 45 条命令 ⊂ registry、≥10 示例、覆盖 7 个关键命令；`describe.exit_codes == EXIT_CODES`。
原始输出：`16-describe.json`（49 命令/10 错误码）、`17-help.txt`。

## 6. 终验收数字

| 门 | 结果 | 证据 |
|---|---|---|
| 平台全量 | **3420 passed / 15 skipped / 0 failed**（805.79s；基线 3390 + 本任务 30） | `13-platform-full.txt` |
| `make test-research` | platform/tools 665 passed；research/tools 1 failed（**预存红**）；governance/ops 82 passed | `14-test-research.txt`、`14b-*`、`14c-*` |
| `make gates` | 全部绿，除 14 处 BAD = 2 个**未跟踪在途** `platform/tools/lob_fact/pipeline/{event_daily,panel_batch}.py` | `15-gates.txt`、`15b-*` |
| G-LINT（gates 内） | 225 spec 全过 | `15-gates.txt` |

预存红判定：干净 HEAD worktree（5cddaef）跑
`research/tools/factor_lib/tests/test_index.py` → 3 failed（含工作树唯一失败的
`test_every_yaml_has_mirror_doc_and_name_matches`）；gates 的 BAD 行逐条指向
`?? ` 未跟踪文件（`git ls-files` 为空），均为挖矿/reviewer 在途，本任务零触碰。

## 7. 未竟 / 说明

- 挖矿在途文件与 reviewer 台账改动未纳入本任务提交（按约束不动）。
- 纯净 HEAD 上 `make test-research` 的失败早于本任务存在（因子档案镜像缺口，
  属挖矿循环收尾项，不在 R31 范围）。
- skill 文件在仓外 `~/.claude/skills/`（非 git），本任务已加「研究员入口」节，
  内容以本目录 `17-help.txt` / `16-describe.json` 为权威口径。
