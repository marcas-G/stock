# R31 分钟链性能（R09-M3：分段计时 + 基线复测）

对应评审：`governance/evidence/reviews/r09-2026-09-17-minute-perf/report.md`
（§1④ 无逐阶段计时；§2.4 建议 `--profile`）与 `evidence/timings.md`（R09 实测）。

## 交付

1. **M3 分段计时**（平台）：`factorlab run --profile`（或 env
   `FACTORLAB_PROFILE=1`）→ stderr 各段墙钟/峰值 RSS + `summary.runtime.profile`
   （append-only；默认关闭零行为变化）。实现 `platform/src/factorlab/app/profile.py`，
   接线 `app/run.py`（日频/分钟链）、`app/evaluate.py`（评估/分层回测）、
   `surfaces/cli/main.py`；测试 `platform/tests/test_profile_timing.py`（9 条，
   含"不调 evaluate/backtest 无对应段"禁止行为断言与折日断线变异检出）。
   契约：`knowledge/contracts/interface.md` §1 `--profile`。
   提交：`df0c3b0 feat(engine): run --profile 分段计时（R09-M3）`。
2. **基线复测（before）**：`bench.sh`（可复现；`BENCH_OUT` 可指 after 目录）+ `before/`。

## 复现

```bash
# 从仓库根；before 证据即本目录（默认输出 before/）
bash governance/evidence/verification/R31/minute-perf/bench.sh
# after 复测（P2/P3 落地后同窗同口径）：
BENCH_OUT=governance/evidence/verification/R31/minute-perf/after \
  bash governance/evidence/verification/R31/minute-perf/bench.sh
```

口径：`--chunk-days 10 --profile`，25min/因子超时；env `FACTORLAB_DATA_BACKEND=ch
FACTORLAB_MAX_MEMORY=8GB FACTORLAB_ST_DEGRADE=allow FACTORLAB_MINUTE_UNCOVERED=drop`；
经 `governance/ops/heavy.sh`（flock 2 槽 + oom_score_adj=700）。

## before 结果（2026-09-18，commit `df0c3b0`）

明细见 [`before/timings.md`](before/timings.md)（每因子分段墙钟/外部峰值 RSS/
面板行数/窗口）；环境与树状态见 [`before/env.txt`](before/env.txt)。

要点（窗口 2024-01-02..2024-03-29，58 交易日，7.32e7 分钟行；未触发超时）：

| 因子 | 总墙钟 | read_data | fold | label+evaluate+backtest+persist | 峰值 RSS |
|---|---|---|---|---|---|
| am_pm_vol（简单 `day_sum(if_else)`） | 55.6s | 51.8s | 18.2s | 1.6s | 3.4GB |
| vol_asym（病态） | 52.2s | 48.6s | 20.4s | 1.5s | 3.1GB |
| autocorr_micro（病态） | 63.4s | 59.3s | 31.2s | 1.8s | 3.1GB |
| vol_price_corr（病态） | 101.2s | 97.6s | 64.7s | 1.4s | 3.6GB |

- **折日（fold）是主耗时**：占 read_data 的 35–66%，占全链 35–64%——与 R09 §1①
  一致；评估/分层/落盘合计 <2s，占比 <3%（R09 §1③ 的 daily 评估叠加在本窗不显著）。
- **病态形态排序**：vol_price_corr（4 个产品级 day_sum 串联）> autocorr_micro >
  vol_asym > am_pm_vol，与 R09 §1② 形态分类一致。
- 折算 R09 原窗（×12.5，723/58 交易日）：约 11.6/10.9/13.2/21.1 min——
  vol_price_corr >15min，与 R09 "病态 ≥15min 超时"吻合。

## 门结果（2026-09-18，基线树 `df0c3b0`）

- **平台全量**：`cd platform && .venv/bin/python -m pytest -q` →
  `3430 passed, 15 skipped`（15:08，0 failed）。
  （首跑经 `governance/ops/heavy.sh` 注入 `FACTORLAB_MAX_MEMORY=8GB` 导致 18 条
  策略测试对 `settings.max_memory is None` 的断言红——为运行环境注入而非代码
  回归：不注入 env 单跑该 18 条 61 passed；上表为直接跑结果。）
- **`make gates`**：红在 `platform/tools/lob_fact/pipeline/{event_daily,panel_batch}.py`
  （G-CONTRACT `year=` 字面量 / G-READ 未登记直读）——**预存红**（R31 挖掘在途工具，
  commit `108f73b`；本 R09-M3 改动不触 `platform/tools`）。
- **M3 单测（TDD 红→绿）**：`tests/test_profile_timing.py` 初始
  `ModuleNotFoundError: factorlab.app.profile`（红）→ 实现后 9 passed；折日接线
  变异（`fold` span 置空）→ 分钟链测试 FAILED（可检出），恢复后绿。

## 与计划的偏差 / 限制

- **窗口缩短**（R09 全窗 → Q1 2024）：病态因子在 723 日窗 ≥15min 超时，无法在
  25min/因子预算内完成；缩短窗口保证 before/after 同窗可比，折算说明见
  `before/timings.md`。窗口内 4 因子均未超时（超时路径未实测，脚本保留 1500s
  timeout + 会如实记录 rc=124）。
- **原始 parquet 未入库**（panel/weekly/signal/labels 共 ~50MB）：证据保留
  `summary.json`（含 `runtime.profile` 与 evaluation）、`run.log`（stderr 人读
  分段）、`time.txt`（外部峰值 RSS）、`exit_code`；完整产物按上节命令可复现。
- bench 运行期树上有挖矿/reviewer 在途改动（`env.txt` `dirty_files=20`），
  与性能路径无关；基线树为 `df0c3b0`（M3 仅计时，无优化）。
