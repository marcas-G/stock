# R21 / EVAL — R01 严格评审评估子系统修复证据

- 范围：R01-EVAL-C1/C2 + I1–I9（11 项），输入台账 `docs/reviews/findings.md`（只读）。
- 纪律：每 finding 先写失败测试（RED，`red_*.txt`），最小实现后 GREEN；修复前/后各跑一遍
  `/tmp/opencode/reviewer-eval/probe_*.py`（输出在 `before/`、`after/`，文件头含命令）。
- 未跑平台全量（按任务边界）；相邻消费者（CLI corr/svd/resic、run 评估、web、architecture 门）
  已含在 `green_all_eval.txt`。

## 复跑命令

```bash
cd platform
.venv/bin/python -m pytest -q tests/test_layered.py tests/test_ic_series.py \
  tests/test_quant_core_shim.py tests/test_correlation.py tests/test_web.py \
  tests/test_e2e_web.py tests/test_cli.py tests/test_cli_web.py tests/test_cli_list_show.py \
  tests/test_eval_rust_ic.py tests/test_eval_alignment.py tests/test_cross_section.py \
  tests/test_cli_resic.py tests/test_results_single_point.py tests/test_outputs_multi.py \
  tests/test_architecture.py tests/test_input_surface.py tests/test_pool_formula.py
for p in layered turnover_nan correlation web kernel_adversarial contract cross_section sign_smalln; do
  .venv/bin/python /tmp/opencode/reviewer-eval/probe_$p.py
done
```

## 逐 finding

| finding | 根因 | 改动文件 | 测试（新增/更新） | 证据 |
|---|---|---|---|---|
| C1 layered NaN | 过滤只查 `is_not_null`；polars rank 把 NaN 当最大、NaN 均值传播 | `core/eval/layered.py` | `test_layered_backtest_nan_signal_excluded_from_top_decile`、`..._nan_forward_not_poisoning_nav_tail`、`..._matches_kernel_on_nan_panel` | `red_batchA.txt`/`green_batchA.txt`；`after/probe_layered.txt` |
| C2 weekly_ic NaN | 只 `drop_nulls`，NaN 参与秩相关 | `core/eval/ic_series.py` | `test_weekly_ic_nan_rows_excluded_matches_kernel`、`..._infinite_signal_excluded`、`..._constant_week_null_like_kernel_stat` | 同上；`after/probe_turnover_nan.txt`（weekraw=1.0==kernel） |
| I1 Web 忽略 target | `weekly_ic(panel)` 用默认 5d | `surfaces/web/app.py` | `test_factor_detail_ic_curve_uses_spec_target`、`..._5d_target_regression`、`..._target_column_missing_degrades` | `red_web.txt`/`green_correlation_web.txt`；`after/probe_web.txt`（matches 20d） |
| I2 伪 Spearman | `np.argsort(argsort())` ordinal 秩，NaN 排末位，常量→1.0 | `app/analysis/correlation.py` (`_spearman`) | `test_rank_correlation_average_rank_on_ties`、`..._nan_pairwise_deletion`、`..._zero_variance_is_nan_not_one`、`..._degenerate_pair_count_not_1` | `red_correlation.txt`/`green_correlation_web.txt`；`after/probe_correlation.txt`（MATCH True×2） |
| I3 日频 vs 周频 | 未 `align_weekly`，按 date（日）逐日算 | `correlation.py` `_join_panels` | `test_factor_correlation_aligns_weekly_not_daily`（10 日=2 周） | 同上；after/probe case4 n_weeks=2 |
| I4 护栏抽样偏置 | `_r < limit` 取帧序前 N 行 | `correlation.py` `_join_panels` | `test_guard_subsampling_covers_head_and_tail` | 同上；after/probe case5 kept 1/5/9/13/17 |
| I5 Web 缺标量 500 | 模板 `signal_null_ratio * 100` 未防 None | `templates/factor.html`（+app `_num` NaN→None） | `test_factor_detail_missing_top_level_scalars_degrade` | `red_web.txt`/`green_correlation_web.txt` |
| I6 periods≠n_weeks | layered 计所有含有效行的周；kernel 要 ≥2 股 | `layered.py`（`MIN_STOCKS=2` + 周过滤） | `test_layered_backtest_periods_matches_kernel_min_stocks`、`..._two_stock_week_counted_like_kernel`、`..._min_stocks_constant_matches_kernel` | `red_batchA.txt`/`green_batchA.txt`；`after/probe_layered.txt`（2 vs 1 → 1 vs 1） |
| I7 t/sign 分母含退化周 | shim 用 n_weeks（含退化）做 t/sign 分母 | `kernels/quant_core/.../__init__.py` | `test_degenerate_weeks_excluded_from_t_and_sign_denominators` | 同上；`after/probe_kernel_adversarial.txt`（t 182.24 → 141.16 = n_ok=3；sign 0.6 → 1.0） |
| I8 decile ordinal tie | ordinal rank 让并列随行序漂移 | 同上（average rank + `floor((2r−1)·10/(2n))`） | `test_decile_ties_row_order_invariant_numeric`、`..._heavy_ties_deterministic_nan_spread` | 同上；`after/probe_kernel_adversarial.txt`（A/B same=True）；`after/probe_contract.txt`（§4.3 向量仍逐字段一致） |
| I9 测试盲区 | turnover 只查范围；web 只查字符串 | `tests/test_quant_core_shim.py`、`test_correlation.py`、`test_web.py`、`test_e2e_web.py` | `test_turnover_exact_value_and_row_order_invariant`、`..._nan_when_buckets_insufficient`、`test_factor_detail_chart_values_numeric`、e2e 逐点数值断言 | `red_batchA.txt`/`green_*`；after 各 probe |

## 关键 before/after（probe）

- `probe_layered` (2)：D10 净值 `nan` → `1.0`；D1 `1.1881` → 有限有效值；`(1)` periods 2 vs kernel 1 → 均 1。
- `probe_turnover_nan` (2)：`weekly_ic` raw mean 0.788 vs kernel 1.0（DIVERGES）→ 1.0 == kernel（same）。
- `probe_web`：chart matches 5d=True/20d=False → 5d=False/20d=True。
- `probe_correlation`：case1 ties 0.778≠0.8 → 0.8 精确；case2 NaN 0.583≠0.846 → 0.846；case3 1.0 → nan；case4 n_weeks 10 → 2；case5 前 5 行 → {1,5,9,13,17}。
- `probe_kernel_adversarial`：t 182.24 → 141.16（n_ok）；sign_consistent 0.6 → 1.0；tie A/B `same? False` → `True`（spread 均 NaN，确定性）。
- `probe_contract`：vector1 §4.3 逐字段仍 ok（t/spread/groups 全等）；vector2 差异为既有 1-based 勘误解释（见 §4.2 注），非本轮回归。

## 契约勘误建议（不改 platform/docs，按任务边界只记录）

1. **§3.1 t_stat 分母**：现文 "`t_stat` 的分母 n = **n_weeks（含退化周）**（实测口径；pearson 同）"
   与同节 "IC 统计基础：仅使用 IC 可计算周" 矛盾。统计正确方向 = 分母 `n_ok =` IC 可计算周数
   （退化周不进分母）；`sign_consistent` 分母同（§3.1 与 §6 两处均需改）。
   `n_weeks` 字段本身保持"≥MIN_STOCKS 含退化周"不变（§3.1 实测确认）。
2. **§3.1 decile 公式**："`ordinal_rank × 10 // (n_stocks_week + 1)`" 需改为
   **average rank 对称分位映射** `floor((2·avg_rank − 1) · 10 / (2·n_stocks_week))`，clip [0,9]：
   并列同档、行序无关；无并列且 n=10 时与旧式等价 → §4.3 三组向量逐字段不变（已实测）。
3. **§3.1 周 IC 注释**："与平台 weekly_ic 同源" 仍成立；但需注明两边都按 `is_finite`
   剔除 NaN/inf，且 weekly_ic 对退化周记 null（MIN_STOCKS=3 与 kernel 2 的分歧按原文保留）。
4. **correlation 文档口径**：`interface.md` §CLI corr 的"周度横截面秩相关均值"现与实现一致
   （本轮把实现改成真周频，`align_weekly` 快照）；`cross_section.py` 的"与
   factor_correlation 同口径"引用随之成立，无需改文档。

## 未解决 / 存疑点

- **I3 取 (a) 周频**：`factor_correlation` 行为变更（日频→ISO 周最后交易日快照），
  `factorlab corr`、`factorlab svd`、Web 相关热力图消费同一路径；`n_weeks` 从"日数"变"周数"，
  消费方如需日频冗余诊断需新增显式接口。
- `rank_corr/pearson` 均值分母从"全局周数"改为"**该对**计入周数"（对齐 docstring 承诺；
  对缺失模式不同的因子对是修正，旧行为在高缺失面板会把均值稀释）。
- layered `_group_assign` 仍用 ordinal rank（I8 仅针对 kernel decile）；layered docstring 已声明
  连续因子 tie 罕见可接受，本轮未改（未在 findings 内）。
- probe_turnover_nan 用例 (4) 的 ValueError 是 probe 自身列名（`fwd` vs
  `forward_return_5d`）问题；已用正式测试 `test_weekly_ic_infinite_signal_excluded` 覆盖 inf 语义。
- 20M 护栏：仅在 join 后超限时触发；等距 stride 保覆盖头尾，但同周保留行数仍≈限流值
  （n 不整除时 ceil）。
