# R37 独立复查报告（reviewer）

- 复查对象：R37「DQ 范围收窄（1996+ 非 BJ）」验收声明（实施者：T5 执行者；本轮复查独立复跑，不复用实施者日志作为结论）
- 复查日期：2026-09-20 ｜ 仓库：`/data/students/gaolei/stock` ｜ 数据：只读（CH 经 `governance/ops/heavy.sh`，health/账本/CH 未改写）
- 复查者脚本与原始输出：本目录（`governance/evidence/verification/R37/verify/`）；所有数字均为本次亲手复跑所得
- 参考规格：`knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md`、`platform/src/factorlab/core/scope.py`

## 0. 结论总表（声称 vs 实测 vs 判定）

| # | 声称 | 实测 | 判定 |
|---|---|---|---|
| 1 | health 在范围分区 7,449（daily-v3），PASS 7,414 / DEGRADED 35 / FAIL 0 | 7,449 个且全部 `dq_policy_version=daily-v3`；PASS **7,414**、DEGRADED **35**、FAIL **0**；另存 1,342 个 pre-1996 `daily-v1/UNKNOWN`（范围外保留，见 §6.1） | **与声称一致** |
| 2 | 范围 = `trade_date>=1996-01-01 且非 .BJ`；scope 内 17,787,921 raw / 17,787,885 clean / quarantine 36 | raw parquet 复算 total 18,230,232 / scope **17,787,921** / BJ 270,183 / pre1996 172,128；clean staging **17,787,885**；raw−clean=**36**；CH `daily`=**17,787,885**，`.BJ`=0、pre-1996=0、日期 1996-01-02..2026-09-17、5,540 码 | **与声称一致** |
| 3 | 严格模式（无 opt-in）`flab strategy run .../low_lottery_top30_weekly.yaml` 成功（ok:true） | 独立 out-dir 复跑：`EXIT=0`、`ok:true`、decisions 5 / execution_events 5 / fills 176、NAV 9992407.370042782→10214807.424781848、total_return 0.02225690431775429；manifest 五字段齐全 | **与声称一致**（数值与实施者日志逐位相同） |
| 4 | 19 行现代单位 bug 已修复进 canonical（volume ×100），残留 36 行保持隔离 | CH 中 19/19 行 `vol/csv_vol = 100.000000`（逐行），vwap=amount/vol 全部落 [low,high]；staging parquet 19/19 同值，且与 CH 逐列完全相同；36 行残余在 CH=0、staging=0；隔离源 3,828=1,115 BJ+2,658 pre1996+55 scope 精确闭环 | **与声称一致** |
| 5 | 读取门 <1996 报 OUT_OF_SCOPE；DEGRADED 严格模式拒绝、opt-in 可用 | 1995-12-29→`DatasetQualityError status=OUT_OF_SCOPE`（文案含 `1996-01-01`、明示无 opt-in 通道）；2025-03-10→通过（`strict=True` 亦通过，status=PASS）；1996-01-02→默认拒绝（status=DEGRADED）；`accept_quality=("DEGRADED",)+override_reason`→通过并写 manifest；`strict=True`+opt-in→ValueError | **与声称一致** |
| F | 反向检查：备份在、范围外未进 canonical、无 DEGRADED 被写成 PASS | 备份 `data/health/ashare_daily.bak-20260920/` 在（8,792 文件，mtime 2026-09-19 17:06:52）；canonical daily 层 0 范围外行；DEGRADED 集合(35)==残余行日期集合(35)，逐分区 quarantine 计数一致，无 PASS 带 quarantine、无 FAIL、无 scope 内 UNKNOWN | **与声称一致** |

**总结论：5 条声称 + 3 项反向检查全部属实，未发现不一致。** 唯一需披露的副作用：复查 C 的严格回测按平台设计写入了 `data/manifest/ashare_daily/2025-03-31.backtest.json`（gate usage sidecar，五字段；见 §6.2）。

---

## A. health 目录普查（`count_health.py` → `A-health-census.{log,json}`）

命令：`python3 governance/evidence/verification/R37/verify/count_health.py`

原始计数（`data/health/ashare_daily/`，8,792 个 json = 8,791 分区 + summary.json）：

```
policy=daily-v1  status=UNKNOWN   n=1342   (全部 partition<1996-01-01)
policy=daily-v3  status=DEGRADED  n=35
policy=daily-v3  status=PASS      n=7414
in-scope (date>=1996-01-01) partitions total: 7449
parse_errors=0
```

- 随机 3 个 PASS（seed=37）：1999-03-03、2016-09-06、2019-03-12。
- 35 个 DEGRADED 全部为早期残余（1996-01-02 … 2009-04-24），逐分区 `quarantine_count=1`（1996-02-09 为 2），合计 **36**；`expected/actual` 均差 1/2，coverage 0.9934–0.9994，`completeness=COMPLETE`。
- 集合级核验（`A-set-equality.log`）：health 在范围分区集合 == raw scope 交易日集合 == clean 交易日集合，**皆 7,449 且 set 完全相等**（非仅计数相等）。
- 交叉：DEGRADED 集合 == 残余 36 行日期集合，逐分区 quarantine 计数完全一致（`F-reverse-checks`）。

## B. 范围/账本独立核数（`scope_ledger_probe.py` → `B-scope-ledger.{log,json}`）

命令：`governance/ops/heavy.sh platform/.venv/bin/python governance/evidence/verification/R37/verify/scope_ledger_probe.py`（直连 CH `127.0.0.1:8123`，clickhouse-connect，全程 SELECT）

```
CH daily        rows=17787885 .BJ=0 pre1996=0 date=1996-01-02..2026-09-17 codes=5540
CH daily_basic  rows=17787885 .BJ=0 pre1996=0 date=1996-01-02..2026-09-17 codes=5540
CH adj_factor   rows=17787885 .BJ=0 pre1996=0 date=1996-01-02..2026-09-17 codes=5540
CH stk_limit    rows=17690269 .BJ=0 pre1996=0 date=1996-12-16..2026-09-17 codes=5540
CH daily .BJ share = 0.0
CH daily scoped(>=1996 & non-BJ) rows = 17787885

raw daily_fact.parquet (lazy scan): total=18230232 scope=17787921 (.BJ=270183, pre1996=172128) date=1990-12-19..2026-09-17
clean staging parquet: total=17787885 .BJ=0 pre1996=0 trade_dates=7449 codes=5540 date=1996-01-02..2026-09-17
raw_scope - clean = 36 ；raw scope trade_dates=7449 vs clean=7449
```

说明（documented design，非违规）：
- `adj_detail`(18,230,232) / `adj_event`(57,426) / `trade_cal`(8,791，pre1996=1,342) 仍为 **raw 全量口径**——`reconcile.py` docstring 明确派生表/日历保持 raw 口径；canonical daily 层（daily/daily_basic/adj_factor/stk_limit/stock_basic）已 0 范围外行。
- 范围外行数闭环：270,183(BJ) + 172,128(pre1996) = 442,311 = `clean.log` 的 [scope] 过滤行数。

## C. 严格模式回测独立复跑（`C-strict-run.{stdout,stderr}.log`）

命令（无 `--accept-quality`；新 out-dir）：

```
FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/factorlab research strategy run \
  /data/students/gaolei/quantresearch/strategy/low_lottery_top30_weekly.yaml \
  --out-dir /tmp/opencode/R37-verify/out
```

实测：`EXIT=0`；stderr `[guard] slot=1/2 argv=strategy run ...`；stdout JSON：

```
{"ok": true, "command": "strategy.run", "data": {"name": "low_lottery_top30_weekly",
 "signal": "max_effect_20d_high", "decisions": 5, "execution_events": 5, "fills": 176,
 "nav": {"events": 5, "first": 9992407.370042782, "last": 10214807.424781848,
 "total_return": 0.02225690431775429, "date_start": "2025-03-10", "date_end": "2025-04-01"}}}
```

`/tmp/opencode/R37-verify/out/manifest.json` 五字段（`data_quality`）：

```
dataset_version=vscope20260920_01, quality_status=PASS, quarantined_rows=0,
coverage=1.0, cleaning_policy_version=daily-v3
```

与实施者 `acceptance/strategy-strict.log` 的 ok/事件数/收益/NAV 完全一致（独立路径复现）。
门读的是窗口末分区 2025-03-31（PASS / COMPLETE / quarantine 0）。

## D. 读取门 probe（`read_gate_probe.py` → `D-read-gate.{log,json}`）

直接调用公开入口 `factorlab.adapters.read.health.require_dataset`（manifest 重定向 /tmp，不写仓库 data/）。18/18 断言全 OK：

```
1995-12-29       → REJECTED status=OUT_OF_SCOPE；文案含 "早于数据集范围 1996-01-01"；
                   guidance 明示"不属 UNKNOWN/LEGACY…无 opt-in 通道"
2025-03-10       → PASS_THROUGH（PASS/VERIFIED/COMPLETE, q=0, coverage=1.0）
2025-03-10 strict→ PASS_THROUGH
1996-01-02       → REJECTED status=DEGRADED（默认 accept_quality=('PASS',)，"默认 fail-closed"）
1996-01-02 opt-in→ PASS_THROUGH（DEGRADED, q=1, coverage=0.99664）+ manifest 已写 /tmp
FAIL opt-in      → ValueError（FAIL 不可 opt-in）
1996-01-02 strict→ REJECTED（同默认拒绝）
1996-01-02 strict+opt-in → ValueError（strict 只接受 PASS）
```

manifest 内容符合契约（`dataset_quality/quality_issues/affected_partitions/dq_policy_version/override_reason`）。
关键点：1995-12-29 的 health 文件**存在**（daily-v1/UNKNOWN），读取门仍先判 OUT_OF_SCOPE——范围门优先于 LEGACY 过渡条款，与规格 §3 一致。

## E. 19 行抽核（`row19_check.py` / `ch_vs_staging_19.py` → `E-*.log`）

- 19 行（`scoped-55-rows.csv` class=unit_like）在 CH **19/19 命中**，`CH vol / csv vol = 100.000000`（逐行），`vwap=amount/vol` 全部落 [low,high]（1% 宽容带同真）。
- 重点 3 行（按要求含 2015 类 + 分钟确证类）：

| code×date | CH vol（=csv×100） | amount | vwap | [low,high] |
|---|---|---|---|---|
| 2015-05-28 000725.SZ | 5,135,466,500 | 27,293,497,344 | 5.314707 | [4.89,5.43] |
| 2015-06-09 601988.SH | 4,795,353,100 | 25,401,788,416 | 5.297167 | [5.12,5.46] |
| 2026-03-12 601868.SH | 4,784,655,900 | 17,458,274,304 | 3.648805 | [3.50,3.80] |

- `ch_vs_staging_19.py`：19 行 CH vs staging clean parquet `open/high/low/close/vol/amount` **逐列完全相同（0 mismatch）**。
- 残余 36 行：CH 命中 0、staging 命中 0（保持隔离）。
- 范围划分独立闭环（`E3-quarantine-scope-split.log`）：隔离源 `rows.parquet` 3,828 = BJ 1,115 + pre1996 2,658 + scope 55（与 addendum §5.3 数字一致；55 行分布在 54 个交易日）。
- 附：raw `daily_fact.parquet` 对 19 行仍是源值（如 2015-05-28 volume=51,354,665）——修复发生在 clean/canonical 层，raw 归档未动，符合口径。

## F. 反向检查（`reverse_checks.py` → `F-reverse-checks.{log,json}`）

- **F1 备份**：`data/health/ashare_daily.bak-20260920/` 存在，8,792 文件，mtime 2026-09-19 17:06:52；抽样对比（bak → 当前）：2025-03-10 `UNKNOWN/daily-v1 → PASS/daily-v3`；1996-01-02 `UNKNOWN/daily-v1 → DEGRADED/daily-v3`；1995-12-29 均为 `UNKNOWN/daily-v1`（范围外未重发）。
- **F3 无 DEGRADED 冒充 PASS**：DEGRADED 集合(35) == 残余行日期集合(35)；每分区 `quality.quarantine_count` == 该日残余行数；`PASS 带 quarantine>0` = none；`FAIL` = none；scope 内 `UNKNOWN` = none。
- **F4 summary 一致性**：`data/health/ashare_daily/summary.json`（mtime 2026-09-20 16:35:20）已重建：PASS 7,414 / DEGRADED 35 / UNKNOWN 1,342，共 8,791 分区；条目与单分区文件一致（2025-03-10=PASS、1996-01-02=DEGRADED）。
- **F2 范围外未进 canonical**：见 B（CH daily/daily_basic/adj_factor/stk_limit `.BJ`=0、pre1996=0）。

## 6. 披露（非差异，供控制者知悉）

1. **health 目录仍有 1,342 个 pre-1996 `daily-v1/UNKNOWN` 文件**：范围收窄裁定为"保留在盘、不删除不改写"，读取门对这些分区一律 OUT_OF_SCOPE（D1 证实），不构成"漏发布"。
2. **复查副作用**：C 严格回测按平台设计调用 `record_gate_usage`，新增 `data/manifest/ashare_daily/2025-03-31.backtest.json`（五字段 usage sidecar，内容：PASS/vscope20260920_01/q=0/cov=1.0/daily-v3）。D 的 opt-in manifest 已重定向 /tmp，仓库 `data/manifest` 未被 D 写入。
3. `adj_detail/adj_event/trade_cal` 保留范围外行属**文档化设计**（reconcile docstring：派生表 raw 口径），与"CLEAN/范围账本"不冲突。
4. 实施者首轮 `reconcile.log` 曾报 `stk_limit` 不一致（悬空 268,729）——发生在第二次 derive 之前；`reconcile-after.log` 已一致，且本次独立查 CH `stk_limit=17,690,269`、0 范围外行、0 悬空（B 步）。此为过程记录，不属最终状态差异。

## 7. 本次证据文件清单（将提交）

```
governance/evidence/verification/R37/verify/
  report.md
  count_health.py            A-health-census.json  A-health-census.log  A-set-equality.log
  scope_ledger_probe.py      B-scope-ledger.json   B-scope-ledger.log
  C-strict-run.stdout.log    C-strict-run.stderr.log
  read_gate_probe.py         D-read-gate.json      D-read-gate.log
  row19_check.py             E-19row-check.json    E-19row-check.log
  ch_vs_staging_19.py        E2-ch-vs-staging.log  E3-quarantine-scope-split.log
  reverse_checks.py          F-reverse-checks.json F-reverse-checks.log
  artifact-sha256.log
```

`artifact-sha256.log`：raw fact `903ab160…`、clean staging `b75049a3…`、staging summary `e1ece53d…`、health summary `d5b748ae…`、策略 doc `2b26c094…`。
