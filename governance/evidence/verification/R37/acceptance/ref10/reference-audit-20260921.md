# 参考库 42 员 · 基础指标审计与过滤对照（R37，2026-09-21）

口径：5 年产物（`*_5y`，2021-08→2026-07）；raw 日频 rank IC vs forward_return_1d/20d；
t = mean/(std/√n)；方向一致率 = sign(当日IC)==sign(使用方向) 占比。

## 审计结果（裸指标过滤规则：声明窗口上 |t|≥2、|IR|≥0.1、一致率≥55%；h20 家族按 20d+符号校验）

- **KEEP 37 / DROP 5**：`intraday_abs_auction_premium`（IR 0.09+一致率 53%）、
  `intraday_turnover_cv`（raw t=1.4，纯条件型）、`intraday_parkinson_ratio`（一致率 54%）、
  `intraday_volume_entropy`（raw 与条件反号）、`intraday_time_of_max_r`（1d 无信息）。
- **方向标错 3 员（已修 spec）**：`intraday_volume_autocorr` +1→-1（一致率 26%→74%）、
  `intraday_amihud_autocorr` +1→-1（33%→67%）、`reversal_20d_overnight` -1→+1（37%→63%，
  档案本就记录"隔夜是动量"）。修正记录写入对应 spec 注释与 overnight 档案。
- 过滤规则与逐员判定：`reference-filter-20260921.json`。

## 过滤对照（OOS 2024-01→2026-07，133 周，top30 周频，秩归一等权，含费用不含滑点）

| 版本 | 成员数 | 总收益 | 年化 | Sharpe | 最大回撤 |
|---|---|---|---|---|---|
| 基础指标过滤后 | 37 | **+55.46%** | 20.04% | **0.84** | **-16.79%** |
| 未过滤 | 42 | +52.85% | 19.44% | 0.81 | -17.74% |

产物：`runs/platform/composites/ref_filtered37_5y_rk`、`runs/platform/strategies/filtered37_5y_rk_ew`。

## 请求（代码流程层限制，四层）

1. 入库审计：`research/tools/factor_lib/reference_audit.py`（重算裸指标→对照 `_reference_policy.yaml`→
   写 sidecar `_reference_metrics.json/md`，`--check` 供门用；指标绑 artifact 指纹）；
2. 库文件 schema：`_reference.yaml` 增 `horizon`/`usage`（linear_aggregate | conditional_only）/
   指纹字段，校验器拒载不一致；
3. 聚合层 `member_policy`（平台增补，Plan CX 后续）：composite spec 声明门槛与
   `on_violation: reject|drop|warn`，运行期校验（reject=fail fast）；
4. 门：`gates.sh` 增 `G-REF`（结构校验必须绿；门槛违规先报告模式，库清理后翻 enforce）。
