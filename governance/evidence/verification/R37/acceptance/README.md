# R37 验收证据索引（方案 A：命令 + 结论 + 指路；**产物拷贝不进 git**）

> 产物本体在 `runs/platform/**`（平台运行产物）与 `~/quantresearch/**`（研究产物区）。
> 本目录只保留结论/关键数字与命令；原先进 git 的运行输出 JSON 已按方案 A 移除。

## 命令与结论（scope 收窄执行，2026-09-20）

| 步骤 | 命令（摘要） | 关键结果 |
|---|---|---|
| clean | `governance/ops/heavy.sh platform/.venv/bin/python platform/tools/data_quality/pipeline.py clean --partition latest --run-tag scope20260920` | scope 过滤 442,311 行；clean **17,787,885** / quarantine **36**；decision PASS；UNIT_SCALE_REPAIRED=19 |
| ingest | `... platform/tools/ch_ingest/ingest_daily.py --source data/staging/ashare_daily/scope20260920/daily_fact.parquet --calendar-source data/fact/daily_fact/daily_fact.parquet` | daily/adj_factor/daily_basic **17,787,885**；trade_cal 8,791；stock_basic 5,540 |
| 派生 | `platform/tools/ch_ingest/derive_stk_limit.py` | stk_limit **17,690,269**（悬空 268,729→0） |
| 对账 | `.../reconcile.py --source <staging>` | 全库一致 **rc=0** |
| 发布 | `FACTORLAB_DATA_BACKEND=ch ... health.py publish-history --from 1996-01-01 --run-tag scope20260920` | **7,449 分区：PASS 7,414 / DEGRADED 35 / FAIL 0** |
| 严格回测 | `flab strategy run .../low_lottery_top30_weekly.yaml`（无 opt-in） | `ok:true`；5 事件；+2.23%；manifest 五字段 PASS/daily-v3 |

## 产物指路

- 平台产物：`runs/platform/strategies/low_lottery_scope_20260920/`、`runs/platform/composites/*`、`runs/platform/*_5y/summary.json`
- 数据面：`data/health/ashare_daily/`（备份 `data/health/ashare_daily.bak-20260920/`）
- 研究区：`~/quantresearch/experiments/r37_5y/`（脚本与批次日志）、`~/quantresearch/factor/_reference_policy.yaml`、`_reference_metrics.{json,md}`
- 独立复查：`governance/evidence/verification/R37/verify/report.md`（含命令与逐项判定）

## 参考库聚合验收（摘要）

- 秩归一等权 42 员 OOS（2024-01→2026-07，133 周）：**+52.85%**，年化 19.44%，Sharpe 0.81，MaxDD -17.74%
- 基础指标过滤 37 员：**+55.46%**，Sharpe 0.84，MaxDD -16.79%（详见 `ref10/reference-audit-20260921.md`）
