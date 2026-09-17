# R22 开放算子底座 — 改动前基线（00-baseline）

**时间**：2026-09-15 22:19–22:29（Task 1 任何代码改动之前）
**环境**：`FACTORLAB_DATA_BACKEND=ch`（ClickHouse `factorlab` 库，只读）；cwd=`platform/`；
命令模板：`platform/.venv/bin/factorlab run <spec>`（默认 backtest 开启，与 plan Task 9 测试一致）。
**复跑入口**：`bash prepare_baseline_specs.sh && bash run_baseline.sh 00-baseline`。

## 1. 数据面限制（实测，决定了"等价代表 spec"的选择）

| 限制 | 证据 | 影响 |
|---|---|---|
| 无 `stock_st` 表；`--universe` override 不传播到 `resolve_universe_frame`（run.py:450/452） | `exclude_st` 直接 ValueError（`excluded/probe_exclude_st_failure.txt`） | 计划中 6 个 spec 均含 `exclude_st: true` → 全部不可直接跑 |
| `daily_basic` 仅 `total_mv`/`turnover_rate` 非 null（1687 万行）；`pb`/`pe_ttm`/`circ_mv`/`dv_ratio`/`volume_ratio` 全 null | `SELECT count(pb) FROM daily_basic` = 0 | value 族全部、`crash_bottom_leader` 主 spec 信号全 null |
| `index_daily` 空表（0 行） | `SELECT count() FROM index_daily` = 0 | `idx_ret` 全 null → `crash_bottom_leader/adv20` 信号全 null |

**应对**：`prepare_baseline_specs.sh` 将 6 个代表 spec 的 `universe.rules` 中
`exclude_st: true, ` 文本移除（formula/date/params 逐字不变），得到"同 spec 等价代表"；
对数据面导致信号全 null 的 2 个 spec，用"改动前后同 spec 对比"语义下的等价代表替换
（见下表），替换决定在改动前做出，前后一致。

## 2. 代表 spec 与结果（改动前基线）

| 原计划 spec | 实际运行 spec | spec.name | n_weeks | ic.mean | ic.t_stat | ic.ir | 说明 |
|---|---|---|---|---|---|---|---|
| reversal_20d | reversal_20d/reversal_20d.yaml | reversal_20d | 178 | -0.0414988679293545 | -3.4310520769863486 | -0.2571682258296336 | 原样 |
| momentum_20d | momentum_20d/momentum_20d.yaml | momentum_20d | 178 | -0.0414988679293545 | -3.4310520769863486 | -0.2571682258296336 | 原样 |
| vol_run_energy/symrun | vol_run_energy/symrun.yaml | vol_run_energy_symrun | 142 | -0.025586657865997547 | -6.885716292772831 | -0.5778364751414747 | 原样 |
| crash_bottom_leader | **liquidity/accel.yaml** | turnover_accel | 178 | -0.0367343842352204 | -5.29544915208491 | -0.3969106946953905 | 替换：主 spec 依赖 circ_mv（全 null）、adv20 依赖 idx_ret（空表）→ 同覆盖面代表（turnover + 双 ts_mean 窗口） |
| value/bp | **reversal_20d/wcorr.yaml** | reversal_20d_wcorr | 178 | -0.06746037692967563 | -7.297879342063747 | -0.5469991829345046 | 替换：value 族全部依赖 pb/pe_ttm/dv_ratio（全 null）→ 多算子组合代表（ts_corr/ts_delta/ts_sum/ts_rank/cs_rank + open/volume/turnover） |
| volatility/low_vol_20d | volatility/low_vol_20d.yaml | low_vol_20d | 178 | 0.07007485259689888 | 4.095688789051659 | 0.30698485356547047 | 原样 |

逐 spec 原始输出：`run_<name>.log`（文件头为命令）；汇总 summary：
`<name>.json`（直接拷自 `platform/results/<name>/summary.json`）。

## 3. 排除的尝试（保留证据）

- `excluded/probe_exclude_st_failure.txt`：不改 universe 直接跑 → `exclude_st 需要 stock_st 表`。
- `excluded/run_value_bp_allnull.log` + `excluded/value_bp.json`：value/bp 跑通但信号全 null
  （`signal = 1/pb`，pb 全 null）→ n_weeks=0。
- `run_crash_bottom_leader_adv20.log` + `excluded/crash_bottom_leader_adv20.json`：adv20 跑通但
  `idx_ret` 全 null → 信号全 null → n_weeks=0。

## 4. 改动后对比方式（Task 9）

`bash run_baseline.sh 10-regression` 用同一 spec 副本与同一命令重跑，逐 spec 对比
`evaluation.ic.{mean,t_stat,ir}` 与 `00-baseline/<name>.json`，容差 `|Δ| ≤ 1e-9`。

## 3. 基线刷新（2026-09-17，R30 批 4 Task 11 / R08-DATA-I2 后置）

R08-DATA-I2 数据修复（退市股 adj_factor 补灌，`fix(data)` 2b39807）恢复了评估窗口内
约 130 只退市股的历史样本 → 本基线（改动前口径）与现行数据不再逐值可比：
`platform/tests/test_regression_152.py` 实测红色（reversal_20d `ic.mean` Δ=+1.37e-3）。
按 D7「不留快照兼容层」刷新基线（weekly 重跑，spec 副本逐字不变）：

- 刷新脚本/留痕：`governance/evidence/verification/R30/eval-v2-task14-12-11/refresh_r22_baseline.py`
  + `32-r22-baseline-refresh.txt`（旧→新逐值）；
- 旧基线值见 git 历史与本目录 §2 表；本次刷新只更新 `evaluation` 数值字段；
- 说明：R22 基线是**数据相关**的值级回归锚；数据修复/更新后需同批刷新，
  本测试已改为写 tmp 输出目录（不再覆盖 `runs/platform/` 主产物）。

### 3.1 二次刷新（2026-09-17 21:13 后，R30 eval-v2-fix / pan 数据更新后置）

R30 pan-large-transfer `make data-update`（20:37）重建 `daily_fact.parquet`（21:12）
并重灌 CH `daily/adj_factor/daily_basic`（21:13；18,191,285→18,230,232 行）→ 6 个
5d 基线全部再次前移（1.5e-7..9.5e-6）。按 D7 同批二次刷新（旧→新逐值、D3 零适用
与代码零提交归因：`../R30/eval-v2-fix/30..32-*`；刷新脚本
`../R30/eval-v2-fix/refresh_r22_baseline_2.py`）。6 个目标均为 `forward_return_5d`，
D3 不重叠采样（h>5）对本回归零适用；`evaluation` 新含 E2 `ic_decay` 字段。
