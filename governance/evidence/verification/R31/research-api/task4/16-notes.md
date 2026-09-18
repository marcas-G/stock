# R31 Task 4（factor 组：run 过闸 + admit 一键冗余检验）证据说明

计划：`knowledge/design/platform/plans/2026-09-18-research-api.md` Task 4；
spec：`knowledge/design/platform/specs/2026-09-18-research-api-design.md` §3 factor/§4 错误码/§5 重命令过闸。

## 交付物

- `platform/src/factorlab/research/factor.py`（新）：17 个命令
  （lint/run/list/show/export/corr/resic/svd/ref.list|add|remove/admit/op.list|doc|add|remove/catalog）。
  只装配：lint 复用 `surfaces.cli.main._lint_one`；run 复用新抽出的
  `surfaces.cli.main.execute_run`（CLI `factorlab run` 计算主体）+ `_guard.guard_heavy`；
  list 复用 `collect_result_rows`；corr/resic/svd 复用 `app.analysis.*`；
  op/catalog 复用 adapters；ref 写 = 本模块安全写。
- `platform/src/factorlab/research/registry.py`：`ParamSpec.positional`（spec §3 的
  `flab factor run <spec.yaml>` 位置参数写法；required 位置参数真必填）。
- `platform/src/factorlab/research/cli.py`：`factor` typer 组（含 ref/op 二级命令名映射）。
- `platform/src/factorlab/surfaces/cli/main.py`：抽 `execute_run` + `collect_result_rows`
  （CLI 行为不变；门面不复制 run/list 装配）。
- `platform/tests/test_research_factor.py`（新，38 用例）。

## 证据

- `01-red-collection.txt`：先红——模块不存在，collection ImportError。
- `02-green.txt`：38 passed。
- `03-mutation-skip-guard.txt`：`_guard_env` 跳过 guard_heavy → 4 用例失败
  （过闸断言/GuardError 映射 x2/真闸槽释放）。
- `04-mutation-admit-verdict.txt`：`_admit_verdict` 恒返 可加入 → 冗余/重复两判决失败。
- `05-mutation-show-stub.txt`：`factor_show` 硬编码存根 → 缺失/损坏/CLI exit9 失败。
- `06-mutation-ref-comment-loss.txt`：`_insert_reference_entry` 丢注释（模拟整库 dump）
  → 注释保留断言失败。
- `07-platform-full.txt`：平台全量 3348 passed, 15 skipped（EXIT=0；较 Task 3 基线 +38）。
- `08/09`：真 CH 冒烟（flab 安装版）——`flab factor list --json` 95 因子、单行 JSON；
  `flab factor show amihud_illiq_turn_20d --json` version=2 freq=daily、artifacts.summary。
- `10/11/12`：`flab factor lint <真实 spec>` exit 0；`flab factor ref list --json` 真库
  只读 27 成员（daily 10 + minute 17）；describe 含 17 个 factor.* 命令。
- `13-test-research.txt`：platform/tools 665 passed；research/tools 1 failed——预存红：
  `research/factor/momentum_20d/turnrank_top2.yaml` 在库但其档案不在 HEAD（挖矿在途，
  本任务未触碰该树；与 Task 3 证据同因）。
- `14-governance-ops.txt`：82 passed。
- `15-gates.txt`：EXIT=1，全部 BAD 均为 `platform/tools/lob_fact/pipeline/{event_daily,panel_batch}.py`
  在途未跟踪文件（G-READ/分区字面量），与本任务无关；G-BOUNDARY 绿（未动 research/ 树）。

## admit 判决口径（plan Task 4）

metrics 走 D10 `incremental_diagnostics`（corr_max/r2_lib/resic_t/retention）；
verdict：corr_max≥0.95 → 重复；r2_lib≥0.8 且 |resic_t|<2（含 NaN）→ 冗余；否则可加入。
合成面板三判决：独立（vs[1]，fwd 由候选驱动）→ 可加入；0.93·base+0.368·noise → 冗余；
base+1e-6·noise → 重复。三测试均断言数值来源（corr_max/r2_lib/resic_t 组合），
硬编码 verdict 必败（见 04 突变）。
