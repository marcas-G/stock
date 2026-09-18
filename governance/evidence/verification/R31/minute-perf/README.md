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
# after 复测（P2 落地后同窗同口径）：
BENCH_OUT=governance/evidence/verification/R31/minute-perf/after \
  bash governance/evidence/verification/R31/minute-perf/bench.sh
# 数值硬门（旧/新同一次读盘逐 cell 对拍 + f64 oracle）：
governance/ops/heavy.sh platform/.venv/bin/python \
  governance/evidence/verification/R31/minute-perf/verify_fold_parity.py
# polars/codegen 能力 spike 与突变检验：
platform/.venv/bin/python governance/evidence/verification/R31/minute-perf/spike/spike_polars.py
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

---

## R09-PERF-I1（P2）：分钟折日物化共享（2026-09-18）

实现（平台 `core/engine/minute_fold.py`，`compute_formula(scope="bars_1m")`
codegen 前接管；提交 `2b4daae feat(engine): 分钟折日物化共享融合路径
（R09-PERF-I1）`）：

- **聚合检测/调度**：AST 收集全部 day_*（含嵌在算术/变量链中的），按依赖分
  pass；每 pass 用同链 codegen 把聚合参数物化一次 → 组内 over 广播
  （sum/mean/max/min 单 over；day_first/day_last 同旧实现双 over）→ 结果列供
  后续 pass/最终输出复用；最终公式死赋值剪枝。
- **im_\* 共享**：保留 over 路径，但 bars 预排序一次（已物理有序零代价，
  `struct.is_sorted` 探测）、经 `seq_*` 物理序变体去 order_by、重复子表达式
  CSE 临时列只算一次。
- **完整回退**：分析期不支持形态（跨层/未知调用、非简单赋值、保留前缀
  `factorlab_fold_`/`factorlab_cse_` 名字冲突、缺网格列、依赖环）→ build_plan
  返回 None，逐字节回退既有 codegen 路径；spike 依据
  [`spike/README.md`](spike/README.md)（含 polars 能力 / 嵌套 over 归约敏感 /
  突变检验）。契约注记：`knowledge/contracts/interface.md` 分钟链一节。

### after 复测（commit `48858f2`；`after/timings.md` + `after/env.txt`）

产物同 before 口径（保留 `summary.json` 含 `runtime.profile`、`run.log`、
`time.txt`、`exit_code`；原始 parquet ~50MB 不入库，按上方命令可复现）。

| 因子 | before fold | after fold | 倍率 | before 总墙钟 | after 总墙钟 | 峰值 RSS |
|---|---|---|---|---|---|---|
| am_pm_vol | 18.19s | 14.11s | 1.29× | 55.6s | 46.7s | 3.4GB |
| vol_asym | 20.45s | 21.70s | 0.94× | 52.2s | 53.0s | 3.2GB |
| autocorr_micro | 31.24s | 23.02s | 1.36× | 63.4s | 53.2s | 3.1GB |
| vol_price_corr | 64.66s | **27.29s** | **2.37×** | 101.2s | 63.1s | 3.6GB |

- **目标达成**：R09 最病态 vol_price_corr fold 64.7s→27.3s（≥2×）；总墙钟
  101.2s→63.1s（1.60×）；read 段不变（P4 范畴），RSS 无回退。
- vol_asym +1.2s（-6%）：融合预排序检查/额外 codegen 开销；同量级噪声带
  （同步长跑 read 段自身波动 ±3s）。autocorr 收益来自旧路径把 `im_delay`
  内联进 `day_sum` 的嵌套归约被物化共享替代。

### 数值硬门（同一次读盘旧/新逐 cell 对拍；`after/fold_parity.json`）

`verify_fold_parity.py`：同一 chunk 打桩跑旧路径（`try_fused→None`）与融合
路径 + f64 oracle（bars f32 精确上转后融合计算），整段 281338 行对齐比较：

| 因子 | 输出 dtype | bit-exact | max\|Δ\| | 旧 vs f64 oracle | 新 vs f64 oracle | null 掩码 |
|---|---|---|---|---|---|---|
| am_pm_vol | f64 | **True**（0/281337 差异） | 0 | 0 | 0 | 一致 |
| vol_asym | f32 | **True**（0/280481 差异） | 0 | 0.742 | 0.742 | 一致 |
| autocorr_micro | f32 | False | 3.28e-07 | 1.237e-05 | 1.237e-05 | 一致 |
| vol_price_corr | f64 | False | 2.42e-06 | 5.071e-05 | 5.091e-05 | 一致 |

- 纯逐行参数形态（am_pm_vol/vol_asym）**逐 cell bit-exact**；
- 旧路径将 im_delay/day_mean 内联进 day_* 聚合的**嵌套 over** 形态
  （autocorr/vol_price）：新旧差 ≤ 3.3e-7 / 2.4e-6，且**小于旧路径自身对
  f64 oracle 的 f32 舍入量级**（1.2e-5 / 5.1e-5）——差异为 f32 归约计划
  敏感的末位舍入（spike ③ 定位），非语义/错值；null 掩码严格一致。

### 门结果（2026-09-18，改动树）

- **新测试**（TDD 红→绿）：`platform/tests/test_minute_fold.py` 17 条——plan
  分 pass/回退、4 因子对拍（2 bit-exact + 2 ulp）、手算竖例、乱序确定性、
  嵌套 day 双 pass、非存根锁（≥2 次 codegen + `factorlab_fold_arg_` 临时列）、
  CSE 只算一次、多输出。
- **现有分钟测试**：`test_minute_ops/gate/engine/coverage/read_minute_window`
  77 passed（含并发在途 `im_cummax` 新增测试）。
- **平台全量**（融合首版 join 广播）：`3447 passed, 15 skipped, 1 failed`
  ——`test_catalog.py::test_markdown_docs_file_committed_and_fresh` 为**预存红**
  （并发挖掘在途 `im_cummax` 未同步 `knowledge/contracts/catalog.md`，diff 仅
  3 行 op 清单；本改动不触注册面）。最终版 over 广播实现的全量复跑见提交/门记录。
- **make gates**：预存红（在途 lob_fact 工具链）不受本改动影响。
- **突变检验**：`spike/mutation.txt`——聚合错值 7 failed、优化器存根 2 failed、
  CSE 关闭 1 failed；恢复 17 passed。

### 与计划的偏差 / 限制

- **bit-exact 范围**：嵌套 over 参数形态仅达 ulp 级（任务书允许"证明仅 ulp 级
  且记录"）；要逐 bit 复现须保留旧路径"逐 day_sum 重算嵌套表达式"的病态计划，
  与优化目标冲突（spike ③ 有归约敏感定位 + f64 oracle 尺度对照）。
- **vol_asym 轻微回归**（-1.2s/6%，噪声带）与 am_pm_vol 的融合收益有限：
  简单两聚合形态旧路径已接近最优；融合主力收益在病态共享形态。
- after 在 `commit 48858f2`（挖掘/reviewer 在途提交推进了树，dirty_files=22；
  minute 链功能无相关改动），before 在 `df0c3b0`；bench 同窗同口径可比。
