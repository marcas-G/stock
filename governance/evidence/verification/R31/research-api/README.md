# R31 research-api 证据索引（研究员接口封装 / flab）

日期：2026-09-18 ｜ 计划：`knowledge/design/platform/plans/2026-09-18-research-api.md`
Spec：`knowledge/design/platform/specs/2026-09-18-research-api-design.md`

## 交付提交（按序）

| 任务 | 实现提交 | 证据提交 | 内容 |
|---|---|---|---|
| Task 1 契约层 | `a8e564a` | `75b7a47` | envelope/registry/describe + CLI 挂载；门精度修复 `c26f50d` |
| Task 2 flab+闸 | `c15d95f`、`3ece594` | `c8ad0b9` | `flab` 短入口 + `_guard`（flock 2 槽/BUSY/--wait/内存预检/env） |
| Task 3 data 组 | `039851f` | `425bf5c` | 全库读取 + >200 行落盘契约 |
| Task 4 factor 组 | `c3ece34` | `8553130` | run 过闸 + admit 一键冗余检验 |
| Task 5 strategy/report | `c23c2f6` | `5cddaef` | 策略回测 + E3/E4 + report url/serve |
| Task 6 study/health/手册/E2E | `d80b900`、`ec2d9ea` | 本目录 | study 一条链 + health + 一页手册 + E2E 终验收 |

## 证据目录

- `task1-2/`：契约层 + flab/闸（红/绿/突变/describe/门精度）。
- `task3/` `task4/` `task5/`：各组红/绿/突变 + 真 CH 冒烟 + 平台全量 + gates。
- `task6/`：study 17 + health 8 + 文档门 5（红/绿/突变）、E2E 计时、三处一致性、终验收。
- `e2e/`：真 CH 六命令原始输出（`01..06 *.json/*.err`）+ study 真链（`08/09`）+
  资产（`e2e_factor.yaml`/`e2e_strategy.yaml`/`run_e2e.sh`，一键复跑）。

## 终验收数字（Task 6）

| 项 | 数字 | 证据 |
|---|---|---|
| 平台全量 pytest | **3420 passed / 15 skipped / 0 failed**（13:25） | `task6/13-platform-full.txt` |
| `make test-research` | platform/tools 665 ✓；research/tools 1 ✗ **预存红**；governance/ops 82 ✓ | `task6/14-*.txt` |
| `make gates` | 除 2 个未跟踪在途 lob_fact 文件（14 BAD）外全绿；G-LINT 225 ✓ | `task6/15-*.txt` |
| E2E（真 CH，`flab`） | **6/6 PASS，exit 0，单 JSON，32s** | `task6/07-e2e-timing.txt`、`e2e/0*` |
| study 真链 | exit 0，steps=[run,admit,strategy,report]，verdict=可加入 | `e2e/08-study-run.json` |
| describe/help/手册 | registry=describe=49，help 8 组，手册 45 命令，6/6 PASS | `task6/16..18-*` |

预存红归因：干净 HEAD（5cddaef）worktree 复跑 `research/tools/factor_lib/tests/test_index.py`
即 3 failed（含工作树唯一失败项）；gates 的 BAD 行逐条指向 `??` 未跟踪的
`platform/tools/lob_fact/pipeline/{event_daily,panel_batch}.py`（挖矿在途，本任务零触碰）。

## 一句话验收

`flab health` → `data daily` → `factor run`（过闸，IC/覆盖率真实）→ `factor admit`
（对参考库 corr+resic → verdict）→ `strategy run`（过闸，M7/M8 真链）→ `report url`，
六命令全 exit 0 单 JSON；`flab study run --strategy` 一条链同样真跑通过。
