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

### 根因（源缺，非派生跳过）

- `data/raw/daily/退市股/*.xlsx` 全为 8 列精简格式（date/OHLCV；368/371 文件 8 列、3 空 stub）——
  无复权列；全量 zip 5546 码与退市 366 码交集 **0**（`import_daily._parse_one(delisted=True)`
  按设计置 `adj_factor=NaN`）→ CH `adj_factor` 对退市股全 NULL → `load_daily` inner join
  丢全历史 → R08 实测 131 只面板全历史 signal null。

### 修复（补口工具 + sidecar + ingest coalesce + reconcile 不变量）

- `platform/tools/ch_ingest/delisted_adj_backfill.py`：腾讯复权 K 线（4 端点轮换，
  `web.ifzq` 旧路径限流时走 `proxy.finance.qq.com/ifzqgtimg`）→ `adj=后复权价/收盘`；
  **raw 收盘逐日对拍 `daily_fact`（不一致拒绝）**；部分有 vendor 的按 vendor 段末端
  锚定常数（漂移>0.5% 拒绝）；hfq<=0 源行剔除记账（8 码 87 行）；写 sidecar
  `data/fact/daily_fact/delisted_adj_factor.parquet`（+meta；合并写=断点续跑）。
- `ingest_daily.py`：灌 `adj_factor` 表时 `coalesce(vendor, sidecar)`（只填 NULL）；
  `reconcile.py`：新增「退市股 adj 补灌」不变量（sidecar 每条键 CH 非空/无重复）。
- 测试：`tests/test_delisted_adj_backfill.py`（19 条：raw 对拍/校准/漂移拒绝/端点轮换/
  sidecar 合并/只填 NULL/reconcile 红绿）+ 更新 1 条 R21 旧断言（600811 全 NULL → 2022+ 覆盖）。

### 执行结果

| 文件 | 内容 |
|---|---|
| `10-backfill-dryrun.log` / `11-backfill-real.log` / `12-backfill-retry.log` / `13-backfill-retry2.log` / `25-backfill-regenerate.log` | 取数过程（含腾讯限流 501 → 端点轮换→ 全量 clean regenerate）。最终：**185 目标 → 180 码 / 83,967 行**；5 码拒绝（vendor 与 hfq/raw 漂移过大：000004/002808/600608/600636/688287——保留其 vendor 段，不伪造尾段） |
| `23-sidecar-sha256.txt` | sidecar + meta sha256（clean 版） |
| `14-ch-adj-before.txt` → `17-ch-adj-after.txt` | CH 前后：NULL 行 1,259,244→1,182,578；all-NULL 退市类 315→142（142=2022 前退市，超范围）；**面板窗（≥2023）NULL 行 41,984→157，覆盖码 146→10**；样本 5 码（300379/600811/600355/002231/300391）≥2023 NULL=0 |
| `15/26-ingest-adj-factor*.log` | `ingest_daily --only adj_factor`（18,191,285 行不变；sidecar 83,967 行） |
| `16/27-reconcile-all*.log` | `reconcile.py all` **exit 0 全库一致**（含新「退市股 adj 补灌」：缺行/空值=0、重复键=0） |
| `18/18b/20/22-signal-*-*.txt`、`19/21/28-rerun-*.log` | 代表因子重跑（CH+8GB 护栏；`FACTORLAB_ST_DEGRADE=allow`=无 ST 口径）：**low_vol_20d signal null 148,181（3.35%）→106,883（2.42%）、sidecar 码 all-null 131→2**（残余 2=600260/000587，2023 窗口内仅 18 行被 20 日 warmup 吃满，非 adj 洞）；产物 `version=2 frequency=daily target=forward_return_1d`。seed（turnrank_top2）仍 131 全 null：其公式用 `turnover`，退市源无股本列 → turnover NULL（**附加缺口，超出 adj 范围**，见「未竟」） |
| `24-test-research.log` | `make test-research`：platform/tools **549 passed / 0 failed**（530 基线+19 新）；research/tools 2 failed = **预存挖矿在途**（intraday 索引/档案，与本批无关） |

### 口径与边界

- 恢复范围 2022-01-01 起（评估窗口 2023+ 与 warmup）；2022 前不恢复（腾讯/通达信
  1990s 存在个别交易日/分位差异，实测 000004 119 日、000005 1 日）。
- 已知精度：hfq 小数位 + 低价股 → 派生 TR 日差 p99≈0.16%（000638 对拍）；
  恢复样本 > 全空。
- 5 码尾段 + 87 日 hfq<=0 保持 NULL（loud 记录，不伪造）。
- **未竟**：退市股 `float_shares/total_shares/amount` 源缺 → turnover 类因子对退市股
  仍 null（signal 恢复需股本补口，另行立项）。

## Task 11：D7 脏产物重跑/清理（R08-MET-I1）——`chore(runs): D7 脏产物重跑/清理（R08-MET-I1）`

（待填）
