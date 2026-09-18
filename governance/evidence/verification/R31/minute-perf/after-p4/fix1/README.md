# R09 复评修复波：F1（chunk-workers 预算按 chunk_days 校准）+ F3/F5

- 日期：2026-09-19；评审 verdict = 需修复（F1 Important；F2/F3 Minor；F5 观察）。
- 本目录 = F1 修复的真实 CH 复验 + 突变证据；F2 台账行/interface 契约更新见
  提交与 `knowledge/contracts/interface.md`。
- 复现命令：

```bash
# 1) chunk20 + N=2 @ 8GB 护栏 → 读盘前拒绝（真实 CLI；下方 reject-chunk20-n2/）
BENCH_OUT=governance/evidence/verification/R31/minute-perf/after-p4/fix1/reject-chunk20-n2 \
BENCH_CHUNK_DAYS=20 BENCH_CHUNK_WORKERS=2 BENCH_FACTORS=am_pm_vol \
  bash governance/evidence/verification/R31/minute-perf/bench.sh
# 2) chunk10 + N=2 @ 8GB 护栏 → 放行并完成折日（下方 allow-chunk10-n2/）
BENCH_OUT=governance/evidence/verification/R31/minute-perf/after-p4/fix1/allow-chunk10-n2 \
BENCH_CHUNK_DAYS=10 BENCH_CHUNK_WORKERS=2 BENCH_FACTORS=am_pm_vol \
  bash governance/evidence/verification/R31/minute-perf/bench.sh
# 3) 突变（门恒放行 / 估值不回 chunk_days / 调用点常量块长）：
bash governance/evidence/verification/R31/minute-perf/after-p4/fix1/mutation_f1.sh
```

## 1. F1 根因与修复（方案①：按生效 chunk_days 比例估算）

- 根因：预算门固定 `N × 3.6GB/chunk`，该常量只在 10 日/块实测校准
  （P2/P3 同窗峰值 3.0–3.6GB）；且预算门在 `chunk_days` 解析之前调用 →
  默认 `--chunk-days 20` 低估 ~2×。实测：8GB + chunk20 + N=2 放行后峰值
  10.37GB、看门狗 8.3GB 中止（干净但标准护栏配方不成立）。
- 修复公式：`est = N × 3.6GB × max(chunk_days, 10)/10`，其中
  `chunk_days = ctx.chunk_days or MINUTE_DEFAULT_CHUNK_DAYS(20)`，由调用方
  **在 chunk_days 解析之后**传入（`run.py` 已把解析提前到预算门前）。
  下限 10 日 = 校准块长（更小块固定开销不降，最保守）。
- 校准来源：10 日/块 3.0–3.6GB（R09-PERF P2/P3 bench）；R04-P1 20 日/块
  实测 6.95GB（`R23/perf/p1-minute-before-chunk20.log`）与 7.2GB 线性外推
  同量级且外推在上侧（保守）。默认块长 N=2 → 14.4GB 估算。
- 实现：`platform/src/factorlab/app/memory.py`（`minute_chunk_worker_peak_bytes`
  + `guard_minute_chunk_workers(chunk_days=...)`）；`app/run.py`（解析前移 +
  透传）；契约 `knowledge/contracts/interface.md` §1；CLI 帮助与 `RunContext`
  docstring 同步（改动行，main.py 的在途 DQ hunk 不混入提交）。

## 2. TDD 红→绿

- 红（修复前）：
  - `tests/test_memory_guard.py` 4 条新单测：chunk20+N2 拒绝 / chunk10+N2 放行 /
    <10 日保底 / N=1 零行为——旧签名直接 `TypeError: unexpected keyword
    chunk_days`；
  - `tests/test_minute_engine.py::test_minute_chunk_workers_default_chunk20_n2_refused_under_8gb`
    ——**DID NOT RAISE**（旧门按 7.2GB 放行，复现 F1 缺陷本身）。
- 绿（修复后）：
  - 定向 `tests/test_memory_guard.py test_minute_fold.py test_minute_engine.py
    test_ch_read_tuning.py test_cli_run.py`：**171 passed**（含 F5 新测试）。
  - 平台全量 `pytest -q`：**3514 passed, 15 skipped, 1 failed**；唯一失败
    `test_regression_152.py::test_sample_value_regression` 为**并发在途
    DQ-M1 读取门**（dirty `main.py` 缺省 `dataset="ashare_daily"`，
    2024-03-29/2026-07-31 health 分区 MISSING），与本改动路径无关。
- 突变 `mutation_f1.txt`（3 处，全部被杀）：
  - 门恒放行 → 3 条 F1 测试失败；
  - 估值恒定 3.6GB（忽略 chunk_days）→ chunk20 单测 + 引擎测试失败；
  - 调用点传常量 `chunk_days=10`（调用顺序漂移）→ 引擎默认 chunk20 测试失败；
  - 恢复后 85 passed。

## 3. 真实 CH 复验（commit `cb2444d6` + 本修复工作树，heavy.sh 8GB）

- **reject-chunk20-n2/**（默认块长 + N=2）：rc=1，打开 DB 前拒绝，外部峰值 RSS
  **344MB**（未加载数据）。日志：
  `chunk_workers=2 × chunk_days=20 预估峰值 14.4GB（单 chunk 7.2GB = 3.6GB ×
  max(chunk_days, 10)/10 …）`。
- **allow-chunk10-n2/**（chunk10 + N=2）：预算门放行，折日全窗完成并落
  compute 产物（`summary.json` 无 `evaluation` 键——评估段被拒），外部峰值
  RSS **6042MB（≈5.9GiB）< 8GB**——与 P4 基线 N=2 同因子 6222MB 同量级；
  rc=1 发生在 **evaluate 读取门**（同一并发在途 DQ-M1：`dataset=ashare_daily
  partition=2024-03-29 MISSING`，`run.log` 末段），非内存/预算门问题；
  parquet 按证据库惯例不入库（可复现）。

## 4. F2 / F3 / F5 处置

- **F2**：`governance/evidence/reviews/findings.md` R09-PERF-I1 行 ulp 改
  per-factor（autocorr ≤3.3e-7、vol_price_corr ≤2.5e-6）；只动该行，R08 在途
  hunk 未混入提交（staged blob = HEAD + 本行）。
- **F3**：`adapters/ch_read.py::ClickHouseRead.close` 补生命周期说明（线程级
  客户端：worker 线程随池退出回收、主线程实例跨 run 复用/进程退出回收；显式
  关闭的边界问题），注释级最小改动、无行为变化 → 无需新测试。
- **F5**：落地执行期异常回退（`core/engine/compute.py`：`try_fused` 抛非
  `FactorDSLError` 异常 → `RuntimeWarning` + 回退旧 codegen；公式/数据错误不
  掩盖）。TDD 红→绿：`test_minute_fold.py::test_fused_execution_error_falls_back_to_legacy`
  （先 DID NOT WARN，后与旧路径逐 cell 相等）。

## 5. 未竟 / 残余

- allow 复验因并发在途 DQ 读取门在 evaluate 段 rc=1（计算/折日已全部完成，
  `summary.json` 为 compute 标记、无 `evaluation`）；内存门放行 + 折日完成 +
  峰值 6042MB 已由 `/usr/bin/time -v`、`summary.json`（panel_rows=281338）与
  `run.log` 锁定。
- 全量 1 红同为该在途读取门；待 DQ-M1 落定后复跑应为 0 红。
