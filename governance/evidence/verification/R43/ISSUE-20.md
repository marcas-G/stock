# Issue #20 / R36-CI-I1 处理与验证记录

复核日期：2026-09-25。根因是 `POLARS_MAX_THREADS=8` 改变浮点归约顺序后，execution accounting 与回测 artifact 使用精确浮点相等校验 cash bridge；约 `1.27e-11` 的归约噪声即可使策略运行失败。

## 修复

- 在 `platform/src/factorlab/core/domain/accounting.py` 增加共享 `cash_bridge_matches`，使用 `math.isclose(rel_tol=1e-12, abs_tol=1e-9)`。
- execution accounting 与 `ExecutionArtifact` 共用该比较，避免一处通过、另一处因精确相等拒绝。
- 容差只用于比较；不改写现金、不做 round/clamp。明显账差仍然拒绝，现金状态自身的 finite/non-negative 检查仍严格执行。
- 同步 `knowledge/contracts/interface.md` 的 cash bridge 契约。

## 验证

- `governance/ops/heavy.sh platform/.venv/bin/python -m pytest -q platform/tests/test_execution_accounting.py platform/tests/test_backtest_runtime.py`：**39 passed**。原始输出：[issue-20-cash-bridge-final.log](issue-20-cash-bridge-final.log)。包括归约噪声可通过及 artifact 明显账差拒绝两项回归。
- `governance/ops/heavy.sh bash -c 'unset FACTORLAB_MAX_MEMORY; exec platform/.venv/bin/python -m pytest -q platform/tests/test_run_strategy.py platform/tests/test_research_strategy_report.py'`：**85 passed**。原始输出：[issue-20-strategy-tests.log](issue-20-strategy-tests.log)。子进程清除该测试入口的 `FACTORLAB_MAX_MEMORY`，因为现有测试断言进程环境未设置该值；heavy wrapper 其余资源护栏仍生效。
- 较早的宽选测试记录为 **121 passed、2 failed**；两处失败都断言 `settings.max_memory is None`，而 heavy wrapper 注入了 `8GB`。该环境差异及重跑方式保留在 [issue-20-cash-bridge-tests.log](issue-20-cash-bridge-tests.log)，不属于 cash bridge 失败。

## 结论与范围

定向回归确认现金桥比较容忍有限浮点归约噪声，并继续拒绝明显不一致。修复已提交于 `7d3a2b9`；本轮 MR 会携带该提交及本验证记录，待 GitHub issue 回填后收口。
