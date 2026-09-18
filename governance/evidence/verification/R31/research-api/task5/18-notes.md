# R31 research-api · Task 5 证据（strategy + report 组）

日期：2026-09-18 ｜ 计划：`knowledge/design/platform/plans/2026-09-18-research-api.md` Task 5
平台提交：`c23c2f6 feat(research): strategy/report 组（run 过闸 + E3/E4 + report url/serve）`

## 1. 产出

- `platform/src/factorlab/research/strategy.py`：`strategy.{lint,run,list,show,export,capacity,cost}`
  （registry 单点注册；run 过 `_guard.guard_heavy`；capacity/cost 真调
  `capacity_proxy`/`cost_net_report`）。
- `platform/src/factorlab/research/report.py`：`report.{list,show,url,serve}`（url 不启服务；
  serve 委托 `create_app` + `uvicorn.run`）。
- `platform/src/factorlab/app/strategy/turnover.py`：`target_one_side_turnover`
  （0.5×Σ|Δw| 组合层换手单点，w₋₁=0 含建仓期；E3/E4 caller 契约）。
- `research/__init__.py` / `research/cli.py`：strategy/report 组注册与 CLI 挂载。
- 测试：`tests/test_research_strategy_report.py`（37 例，env duckdb|ch 双腿）+
  `tests/test_strategy_turnover.py`（5 例）。

## 2. TDD 红 → 绿

- `01-red-collection.txt`：实现文件缺席 → `ImportError: cannot import name 'strategy'`
  （collection error）。
- `01b-red-turnover.txt`：turnover 单点缺席 → 5 failed（ModuleNotFoundError）。
- `02-green.txt`：42 passed（24s）。

## 3. 突变（存根可杀；逐条改实现→跑测试→复原）

| 证据 | 突变 | 结果 |
|---|---|---|
| `03-mutation-skip-guard.txt` | `_guarded_env` 不调 `guard_heavy` | 4 failed（真链×2 + BUSY/MEMORY_GUARD×2） |
| `04-mutation-capacity-stub.txt` | capacity 返回硬编码 123 | 2 failed（duckdb|ch） |
| `05-mutation-cost-zero.txt` | cost 恒 `cost_rate=0` | 2 failed（net 不低于 gross） |
| `06-mutation-report-url-base.txt` | `report url` 忽略 `--base` | 1 failed（自定义 base） |

## 4. 真 CH 冒烟（`~/.local/bin/flab`，FACTORLAB_DATA_BACKEND=ch）

复用仓库既有策略产物 `runs/platform/strategies/low_lottery_top30_weekly`
（signal=max_effect_20d_high，2025-03-10~2025-04-01，5 event）：

- `07` `flab strategy list --json`：exit 0，n=1，nav 9992407→10214807（+2.23%）。
- `08` `flab report list --json`：exit 0，n=95（真 CH 因子摘要）。
- `09` `flab strategy show low_lottery_top30_weekly --json`：spec/nav/turnover/cost 齐。
- `10` `flab strategy cost ... --cost-rate 0.0007`：gross 年化 23.15% → net 21.72%。
- `11` `flab strategy capacity ...`：真 daily amount 读数（72 标的；mean≈4.45e7 元，
  单边换手 0.3933，min≈4.93e6 元）。
- `12` `flab report url max_effect_20d_high --json`：静态 URL，未启服务。
- `16` `flab describe --json`：strategy.*（7）+ report.*（4）全部可见。
- `17` `flab strategy lint research/strategy/low_lottery_top30_weekly.yaml`：OK。

## 5. 回归 / 门

- `13-platform-full.txt`：**3390 passed, 15 skipped**（13:33）。
- `14-test-research.txt`：**预存红**（非本任务）：`test_every_yaml_has_mirror_doc...`
  缺 `knowledge/dossiers/factors/momentum_20d/turnrank_top2.md`——该文件
  `git cat-file HEAD:...` 不存在（HEAD 即红），由挖矿在途树状态造成；本任务未动
  research/ knowledge/（`git diff --stat -- research knowledge` 空）。
- `15-gates.txt`：**仅预存红**——G-LEGACY（本任务文案裸 `results/`）已修并转绿
  （G-LEGACY/G-INDEX/G-LINT/G-TOPO 全 ✓）；剩余红 = 在途未提交
  `platform/tools/lob_fact/pipeline/{event_daily,panel_batch}.py`（`??` 状态，
  直读 parquet/`year=` 字面量），与 Task 5 无关。

## 6. 未竟（滚动到 Task 6）

- `strategy run` 真 CH E2E（小 universe 全链）留待 Task 6 的 E2E 验收统一跑。
- `report serve` 未长跑（测试注入 `uvicorn.run` 断言 `create_app`，符合计划"禁长跑"）。
- `strategy show/cost/capacity` 未含 PIT 池/分钟执行口径的额外视图（按 spec §3 不要求）。
