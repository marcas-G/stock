# R36 自托管 CI 首跑发现（2026-09-19，selfhosted CI）

> 背景：上线自托管 runner（`gpu-server-1`）执行"宿主数据面"验证
> （`.github/workflows/selfhosted-verify.yml`：平台全量 + CH 集成 + 工具/研究/治理）。
> 首跑排障中逐项隔离出本缺陷：hosted CI 无 CH/无线程 env，长期未覆盖该路径。

## R36-CI-I1（I，open）cash bridge 校验用精确相等，遇浮点求和顺序变化即失败

**现象**
- `POLARS_MAX_THREADS=8`（= `governance/ops/heavy.sh` 默认注入值）下，
  `platform/tests/test_run_strategy.py` 与 `platform/tests/test_research_strategy_report.py`
  共 20 条红：`ValueError: artifact cash bridge 破坏：post.cash != pre.cash + Σ delta`。
- 同一测试无该 env 时全绿（本机复现 A/B 对照见 `probe/output.txt`）。

**复现**
```bash
bash governance/evidence/reviews/r36-2026-09-19-selfhosted-ci/probe/repro_cash_bridge.sh
```

**根因证据（插桩输出）**
```
[PROBE-BAD] pre=70.5442530000746 post=775.7201355000889 delta=705.175882500027(float)
            pre+delta=775.7201355001016 post-diff=-1.2732925824820995e-11
```
- `platform/src/factorlab/core/domain/backtest.py:130` 的不变量校验为**精确 `!=`**：
  `post_state.cash != pre_state.cash + delta`（`delta` 来自 polars `sum()`）。
- `POLARS_MAX_THREADS` 改变 polars 并行归约的求和顺序 → 两侧浮点结果相差 1e-11 量级，
  精确相等必然随机/环境相关地失败；与账实正确性无关。

**修复请求**
1. 校验改**容差比较**（相对 epsilon，如 `abs(diff) <= 1e-6 * max(1, abs(pre), abs(post))`，
   具体阈值由团队定），或两侧经同一归约路径计算；
2. 全库排查同类"浮点精确相等"不变量（backtest/accounting 及相关 dataclass 校验）；
3. 回归测试覆盖：同一场景在 `POLARS_MAX_THREADS∈{1,8}` 下都必须通过
   （测试断言"两种线程数下校验均绿"，而非只测默认 env）。

**证据文件**
- `governance/evidence/reviews/r36-2026-09-19-selfhosted-ci/probe/repro_cash_bridge.sh`（A/B/C 对照）
- `governance/evidence/reviews/r36-2026-09-19-selfhosted-ci/probe/probe_diff.py`（插桩打印原式差值）
- `governance/evidence/reviews/r36-2026-09-19-selfhosted-ci/probe/output.txt`（原始输出）
