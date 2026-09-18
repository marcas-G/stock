# R32：Plan DQ-M1 证据（Task 8 全史体检 + Task 9 端到端验收）

日期：2026-09-19 ｜ 分支：`restructure/monorepo`
计划：`knowledge/design/platform/plans/2026-09-18-data-quality-pipeline-m1.md`
设计：`knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md`（v2）

| 任务 | commit | 内容 | 证据 |
|---|---|---|---|
| T8 | `1053e4b` | `data_quality/historical_audit.py` + tests + 存量打标 | `historical-audit/` |
| T9 | 本次提交 | 真跑 + 反例注入 + 拒绝矩阵 | `m1-e2e/` |

## T8：daily 全史体检（只审不改）+ 存量打标

- 范围：1990-12-19..2026-09-17，8,791 个交易日，18,230,232 行；耗时 50.1s；分块 250
  交易日/块（重叠一日 + 停牌 carry 行，跨日规则与全表 shift 语义对齐）。
- 问题清单 15,219 行（日期×规则×字段）；总命中 1,310,927：
  | 规则 | 命中 | 涉及交易日 |
  |---|---|---|
  | MISSING_VALUE（WARN） | 1,251,010 | 8,658 |
  | ADJ_FACTOR_CA_MISMATCH（WARN） | 43,827 | 4,190 |
  | ADJ_NEGATIVE（ERROR） | 8,184 | 879 |
  | VWAP_OUT_OF_RANGE（ERROR） | 7,906 | 1,492 |

  数量与 T5 全表 clean 真跑（Batch B）逐项一致；修复后 carry 行使 CA 命中从 43,622
  回补到 43,827（+205，块边界停牌缺口）。
- 影响分析（`historical-audit/impact-analysis.md`）：ADJ_NEGATIVE 8,184 行/879 日；
  退市股 331 码/1,316,380 行，其中 adj 缺失 1,251,010 行、非正 12 行；float_shares
  缺失 1,251,010、零值 51（circ_mv 代用指标）；跨频推迟 M2。
- 存量打标：8,791 个分区全量写 `health_status=UNKNOWN,
  verification_state=LEGACY_UNVERIFIED`（`data/health/` 本地，不入库）；幂等重跑
  marked=0/existing=8,791；新链已发布文件不覆盖（合并重建 summary）。
- 只审不改：raw sha256 `903ab16…` 全程未变；测试以 monkeypatch `write_parquet`
  与隔离根快照断言零 canonical/parquet 写入。

## T9：M1 端到端验收

### 真跑（daily 类别）

- `sync --categories daily`：发现新分享 `2026-09-01至2026-09-18`，下载失败
  **HTTP 412**（Quark guest 链接降级/限流；已知恢复手段=重导浏览器 cookie，
  非 DQ 链问题）。见 `m1-e2e/sync.log`。
- `build --categories daily`（`FACTORLAB_DATA_BACKEND=ch`，heavy 闸 8GB）：
  6 步全链完成（1317.2s）；`pipeline clean`：raw 18,230,232 → clean 18,214,142 /
  quarantine 16,090，`decision=DEGRADED`（systemic=false）；health 发布
  `2026-09-17.json`：`DEGRADED/VERIFIED`、`completeness=COMPLETE`（coverage
  0.99912）、`error_rate=0.000883`、`quarantine_count=16,090`；阶段标记落
  `2026-09-19T03:20:56`。见 `m1-e2e/data-update.log`。
- 首次运行发现接线缺口：阶段链 `health.py publish` 未显式设
  `FACTORLAB_DATA_BACKEND`，默认 duckdb 库不存在 → FileNotFoundError（已在真跑
  命令中以 `FACTORLAB_DATA_BACKEND=ch` 显式修复；是否在 stages 默认注入待裁定）。
- `verify`：非 daily 全绿；daily 层三项行数差 = −16,090（= quarantine，I1 口径
  已内建说明），`trade_cal` 差 −18 = 18 个「全行隔离日」（1991–1993，27 行）。
  reconcile 目前按 raw 口径判红，**未消费 clean staging**，故 `rc=1`。见
  `m1-e2e/verify.log` 与本目录「待裁定」。

### 反例注入（独立临时 STOCK_ROOT，生产 CH 前后计数一致）

- FATAL/FAIL：`m1-e2e/probe_fatal_injection.py`（两场景全过）——
  A 帧级 FATAL（缺 volume）→ clean rc=1、链止于第二步、ingest tripwire 未触发、
  阶段标记未落、staging 无 `_SUCCESS`、生产 CH 计数不变；
  B 行级 FAIL（2/52）→ 同上 + quarantine 2 行保留 `_SUCCESS`。
- DEGRADED 读取门：`m1-e2e/probe_read_gate_degraded.py`——真实 artifact 默认拒、
  strict 拒、opt-in 通过且 Experiment Manifest 五字段齐；产物五字段记录就绪。
- LEGACY：`m1-e2e/legacy-default-reject.log`——真实存量分区默认拒；显式
  `accept_quality=("PASS","UNKNOWN")` + `completeness_required="UNKNOWN"` +
  `override_reason` 后通过并写 manifest。
- 拒绝矩阵：`m1-e2e/rejection_matrix.md`（16 用例，PASS/DEGRADED/FAIL/UNKNOWN ×
  freshness × completeness × strict，全过）；probe 可复跑。

## 待裁定 / 遗留（交控制者）

1. **verify 绿**：reconcile 的 daily 层期望仍按 raw 源行数；I1 之后应为 clean
   staging 口径（raw − quarantine − dedup），`trade_cal` 期望应按 canonical
   distinct 日期。`ch_ingest/reconcile.py` 不在 T9 精确 add 范围，未改。
2. **阶段链 backend**：`stages.py` 是否给 health（或整链）默认注入
   `FACTORLAB_DATA_BACKEND=ch`，避免 `make data-update` 裸跑在 health 步骤必败。
3. **18 个全行隔离日**（1991–1993）从 canonical/`trade_cal` 消失；建议 M3
   定向核（VWAP 单位/早期数据），或日历改权威来源。
4. **LEGACY 过渡**：仅放宽 `accept_quality` 仍被 completeness 独立腿拒，需同时
   `completeness_required="UNKNOWN"`（当前 API 已支持；是否一键化待裁定）。
5. 腾讯抽样在本轮生产 health 中为 `SUSPECT`（网络/限流降级，不影响 FINAL）。

## 复现

```bash
# T8 全史体检（只审不改 + 存量打标 + 报告）
FACTORLAB_MAX_MEMORY=8GB governance/ops/heavy.sh platform/.venv/bin/python \
  platform/tools/data_quality/historical_audit.py audit \
  --raw data/fact/daily_fact/daily_fact.parquet \
  --report-dir governance/evidence/verification/R32/historical-audit

# T9 链（daily）；sync 需有效 quark cookie
FACTORLAB_DATA_BACKEND=ch FACTORLAB_MAX_MEMORY=8GB governance/ops/heavy.sh \
  platform/.venv/bin/python platform/tools/pan_update/cli.py build --categories daily
FACTORLAB_DATA_BACKEND=ch platform/.venv/bin/python \
  platform/tools/pan_update/cli.py verify

# T9 反例
platform/.venv/bin/python governance/evidence/verification/R32/m1-e2e/probe_fatal_injection.py
platform/.venv/bin/python governance/evidence/verification/R32/m1-e2e/probe_read_gate_degraded.py
platform/.venv/bin/python governance/evidence/verification/R32/m1-e2e/probe_rejection_matrix.py \
  governance/evidence/verification/R32/m1-e2e
```
