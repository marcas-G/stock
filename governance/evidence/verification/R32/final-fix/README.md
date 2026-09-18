# R32 终审修复波（Plan DQ-M1 两个 Important）

日期：2026-09-19 ｜ 分支：`restructure/monorepo`
来源：Plan DQ-M1 全分支终审 N1/N2。TDD：先红后绿（red/green 日志逐条留证）。

## N1：阶段链缺 backend 默认 → 裸 `make data-update` 必败

- 修复：`platform/tools/pan_update/cli.py::_stage_env()` 一行——缺省
  `env.setdefault("FACTORLAB_DATA_BACKEND", "ch")`；调用方显式设置优先（不覆盖）。
- 测试（`platform/tools/pan_update/tests/test_cli.py`）：
  - `test_stage_env_defaults_backend_ch`——env 未设时 stage runner 收到 `ch`（red→green）；
  - `test_stage_env_respects_explicit_backend`——显式 `duckdb` 不被改写。
- 验收证据：
  - `n1-red.log`：实现前 1 failed（缺键，`None == 'ch'`）；`n1-green.log`：2 passed；
  - `n1-probe.log`：真实 `_stage_env()` + 真实 `stages.run_cmd` 子进程打印
    `FACTORLAB_DATA_BACKEND='ch'`；显式覆盖 → `'duckdb'`；`N1 PROBE OK`；
  - `n1-dry-run.log`：`cli.py build --categories daily --dry-run` → 6 步链计划
    （轻量路径，未真跑 CH）。

## N2：用户入口无 DEGRADED/UNKNOWN opt-in 通道

- 修复（spec §7 语义）：
  1. `factorlab run` 加 `--accept-quality TEXT`（逗号分隔，默认空=PASS-only）与
     `--override-reason TEXT`，透传 `execute_run → evaluate_run`；
     `flab factor run` / `flab strategy run` 同参数（registry 单点，describe 自动可见），
     分别透传 `execute_run` / `run_strategy`（`research/factor.py`、`research/strategy.py`）。
  2. 非空 `accept_quality` 缺 `--override-reason` → exit 2（门面 USAGE）；
     未知状态 / `FAIL` → 同样拒绝且不进入计算。
  3. CLI 捕 `DatasetQualityError` → 友好报错（exit 1）：结构化
     dataset/partition/status/freshness（`health.py` 的异常新增字段）+ 所需旗标指引，
     不再裸 traceback；门面映射为 `DATA` 信封（含旗标 hint）。
  4. DEGRADED/LEGACY opt-in 经既有 `require_dataset → write_quality_manifest`
     自动写 Experiment Manifest（五字段 + override_reason 落地）；CLI 成功路径回显
     manifest 路径。
- 测试（真实门，不使用 conftest autouse 假 gate；health 样本复用 T9 证据
  `m1-e2e/health-root`，仅按面板分区改写 partition/freshness）：
  - `test_cli_run.py`：`test_run_quality_default_rejects_degraded_friendly` /
    `test_run_quality_stale_pass_rejects_with_freshness` /
    `test_run_quality_opt_in_degraded_runs_and_writes_manifest`（manifest 五字段+reason）/
    `test_run_quality_opt_in_missing_reason_exit2` /
    `test_run_quality_fail_flag_rejected_exit2` /
    `test_run_quality_unknown_non_legacy_rejected` /
    `test_run_quality_legacy_unknown_opt_in_writes_manifest`；
  - `test_require_dataset.py`：`test_resolve_accept_quality_cli_semantics` /
    `test_quality_error_exposes_dataset_partition_status_freshness`；
  - `test_research_quality_optin.py`（新）：门面 factor.run/strategy.run 参数校验
    （USAGE）与透传、`DatasetQualityError → DATA`。
  - 口径说明：`UNKNOWN` 仅 `verification_state=LEGACY_UNVERIFIED` 可 opt-in（设计
    §7 表 + §8 过渡条款；非 LEGACY 的 UNKNOWN 拒绝）；`FAIL` 任何情况不可 opt-in。
- 验收证据：
  - `n2-red.log`：实现前 17 failed；`n2-green.log`：17 passed；
  - `n2-real-gate-matrix.log`：7 条 CLI 真实门用例逐条 PASSED；
  - `cli-surface.log`：`factorlab run --help` 两个旗标 + `research describe` 的
    factor.run/strategy.run 参数与默认值（None）。

## 回归

- `mandated-command.log`：`POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest
  platform/tools/pan_update/tests platform/tools/data_quality/tests
  platform/tests/test_require_dataset.py platform/tests/test_cli_run.py -q`
  → **390 passed**（本工作树修复前实测基准 379；本轮 +11）。
- `related-suites.log`：新增门面测试 + research/strategy/architecture/契约等
  → **192 passed**。
- `full-platform-suite.log`：`cd platform && .venv/bin/python -m pytest -q`
  → 3568 passed / 15 skipped / **1 failed**：`test_regression_152.py::
  test_sample_value_regression`（值级漂移 7.6e-7 > 1e-9）。
- `pre-existing-regression152.log`：把本轮 4 个源文件 stash 回 HEAD 后单跑同一测试
  → **同样失败、漂移值逐位相同**（`reversal_20d.ic.mean: -0.040132795234204814 !=
  -0.040133558743973084`）——**与本修复无关的存量数据面漂移**（基线档 R22 口径）。
- `gates.sh` 结构门另有两处存量红（未触碰文件）：G-BOUNDARY 命中
  `platform/tests/conftest.py:174` 注释行（HEAD 即存在）；G-INDEX 因子索引不一致
  （工作区在途挖矿文件 `research/factor/*` 未重生成索引，HEAD 工作区既有）。

## 复现

```bash
# N1
git stash push -- platform/tools/pan_update/cli.py && \
  POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest \
  platform/tools/pan_update/tests/test_cli.py -q \
  -k "stage_env_defaults_backend_ch or stage_env_respects_explicit_backend"; git stash pop
platform/.venv/bin/python governance/evidence/verification/R32/final-fix/probe_stage_env_backend.py

# N2（真实门矩阵）
POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest \
  platform/tests/test_cli_run.py platform/tests/test_require_dataset.py \
  platform/tests/test_research_quality_optin.py -q -k "quality or resolve_accept"
```
