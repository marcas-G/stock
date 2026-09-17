# R30 批 4 证据：Task 14 / Task 12 / Task 11（eval-v2 D10 / R08-DATA-I2 / R08-MET-I1）

执行：stock 开发（2026-09-17）。计划：`knowledge/design/platform/plans/2026-09-16-factorlab-eval-metrics-v2.md`
（Task 14 → Task 12 → Task 11）；spec：`.../specs/2026-09-16-factorlab-eval-metrics-v2-design.md`（§3b D10）。

## Task 14：参考库与增量信息评估（D10）——`feat(eval): 参考库与增量信息评估（D10）`

- 库文件：`research/factor/_reference.yaml`（初始 10 只、每风格一只、种子
  `momentum_20d_turnrank_top2`、`scales: daily` 分组、minute 空待另立）。
- 实现：`app/analysis/reference.py`（loader）、`correlation.resolve_against`/`--against`、
  `cross_section.incremental_diagnostics`（rank 残差回归 → r2_lib/resIC/retention/verdict）、
  CLI `corr|resic --against`、`svd` 默认参考库/`--all`、`ref list`。
- 测试：`platform/tests/test_reference_library.py`（13 条；含禁止行为断言：`--against reference`
  不扫全库（list_factors 调用即失败）、minute 不混入 daily、corr 与 target 无关）。

### 真实对照（命令 + 产物同目录）

| 文件 | 内容 |
|---|---|
| `01-reference-selfcorr.{txt,csv,json}` | 库内 10 只 pairwise（CLI 输出 + CSV + max|ρ|）；**45 对最大 |ρ|=0.4903**（rsi × netflow）→ 独立性全过 |
| `03-entry-corr-max.json` | 回填 `_reference.yaml` 的 entry_corr_max（逐只对库内其余成员 max|ρ|） |
| `04-entry-incremental.json` + `04-entry-check.log` | 逐只对库内其余成员的增量：可加入 2（turnover_accel / reversal_20d_overnight）、观察 6、冗余 2（rsi_reversal_14 / amihud_illiq_turn_20d，retention<20%）；冗余两只的 8 个候选替换实测同样冗余（见日志），按初始 10 只保留待新风格候选 |
| `02-candidate-netflow-vol.{txt,cli.txt,json}` | **Step 4 库外候选** `reversal_20d_netflow_vol` 对参考库：corr_max=0.9196（vs netflow）、r2_lib=0.854、resIC t=0.25、retention=-0.06 → **verdict=冗余**（近亲被正确识别） |
| `05-tests-reference.txt` | 相关测试 78 passed（含新 13 条） |
| `06-gates.txt` | `make gates`：唯一红 = G-INDEX（**预存**：挖矿在途 `research/factor/intraday/*` 未入索引；diff 仅 intraday_*，与批 4 无关，已复原未重生成） |
| `07-platform-fullsuite.txt` | 平台全量 **3150 passed / 11 skipped / 0 failed**（基线 3137+11；+13 = 新增测试） |

复现：`platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-task14-12-11/reference_audit.py [--entry-check]`。

口径说明（真实数据发现，已写 yaml 头注）：`retention≥50%` 建议门槛对多成员库偏严（成员互为边际），
初始批次以「风格覆盖 + 单因子显著 + 库内 max|ρ|<0.7」选入，逐只增量结论见上表；新因子入库仍按建议门槛。

## Task 12：退市股 adj_factor 补灌（R08-DATA-I2）——`fix(data): 退市股 adj_factor 补灌（R08-DATA-I2）`

（待填）

## Task 11：D7 脏产物重跑/清理（R08-MET-I1）——`chore(runs): D7 脏产物重跑/清理（R08-MET-I1）`

（待填）
