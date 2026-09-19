# Plan DQ-M1.5c 解阻断证据（门控作用域 = delta + quality_backlog）

- 日期：2026-09-19 ｜ 分支：`restructure/monorepo`
- 权威：设计 §3.3（2026-09-19 用户批准选 A）；计划 §Task 5 DoD
- 目标：解除日更阻断（`make data-update` daily 链）且**不弱化检测**（阈值/检测器不动）。

## 1. 重跑与门结果

| 步骤 | 命令 | rc | 结果 |
|---|---|---|---|
| 全链（含网盘 sync） | `make data-update`（见 `data-update.log`） | **2** | sync 7 项 HTTP 412/403（Quark guest 降级，外部）；daily 阶段因当日已标记而跳过；verify 与旧 staging 比对不一致 |
| 阶段重置（操作者） | 移除 `daily.build` 标记（备份 `pan_state.before.json`，重置记录 `stage-reset.json`） | — | 仅 daily，可追溯 |
| daily 链 | `pan_update cli build --categories daily`（heavy 闸） | **0** | 6 步全过（`daily-chain.log` + `pan_update-20260919.log`） |
| verify | `pan_update cli verify --categories daily` | **0** | 「全库一致」（`verify.log`） |
| 读取门 | `require_dataset("ashare_daily", as_of="2026-09-17")`（PASS-only 默认） | OK | 默认读取恢复（`read-gate.log`） |

daily 链关键输出（`pan_update-20260919.log`）：
```
[ashare_daily] run_tag=20260919 scope=full_table gate_scope=delta watermark=2026-09-17
  delta_rows=0 partition=2026-09-17: decision=PASS rows=18230232 clean=18226404
  quarantined=3828 (full_table error=3828)
[health] 2026-09-17 health_status=PASS note=no_new_data backlog_error=3828
```

## 2. 最新 health（`data/health/ashare_daily/2026-09-17.json`）

- `health_status = PASS`，`note = "no_new_data"`，`verification_state = VERIFIED`
- `quality`（delta 口径）：error/warning/quarantine = 0；`completeness` 0/0 COMPLETE
- `quality_backlog`（full_table，**不参与判定**）：
  - `expected_count=18,230,232` / `actual_count=18,226,404`（clean） / `error_count=3,828`
    / `quarantine_count=3,828` / `error_rate=2.0998e-4`
  - `systemic_detail = "字段集中：单一字段 amount 占全部 error 100.00%（>80%）且分区 error_count 3828 > 50"`
  - `top_classes`：MISSING_VALUE 1,251,010 / ADJ_FACTOR_CA_MISMATCH 43,827 / ADJ_NULLED 8,235
    / HISTORIC_UNIT_EXCEPTION 4,000 / VWAP_OUT_OF_RANGE 3,828
- 读取门三腿：health PASS + completeness COMPLETE + freshness 2026-09-17 → 默认放行。

## 3. 不弱化证明（注入对照，真实 CLI + sandbox 根）

`probe_gate_scope.py` / `injection-probe.log`（三场景，PROBE PASS）：

| 场景 | 构造 | rc | decision | 说明 |
|---|---|---|---|---|
| A delta 干净 | 水位 2026-09-17；旧日 150 残错 + 新 2 日 600 行干净 | **0** | **PASS** | 旧残余只进 backlog 披露；delta_rows=600 |
| B delta 系统性 | 水位同上；新日注入 60 行 VWAP 越带（field=amount 集中） | **1** | **FAIL** | `SYSTEMATIC_FIELD_FAILURE`，delta 内照旧阻断 |
| C 无发布历史 | 同 A 数据、无 health 档案 | **1** | **FAIL** | `SYSTEMATIC_TEMPORAL_FAILURE`（delta=全量，首跑等价） |

## 4. 回归

- `POLARS_MAX_THREADS=1 platform/.venv/bin/python -m pytest platform/tools/data_quality/tests
  platform/tools/ch_ingest/tests platform/tools/pan_update/tests -q` → **534 passed**
  （M1.5c 新增 12 例：watermark 选择 / delta 门 / 注入反例 / 空 delta / backlog 披露与
  「backlog 不影响 status」/ FINAL delta 完整性腿 / 键集断言）。
- 本次顺带修复 T2 的内存回归：`repair` 字段级置 NULL 曾对 18M clean 帧建 Python key set，
  在阶段链 RLIMIT_AS 下 `MemoryError`（首跑 `daily-chain.log` 失败根因）；改为只物化被
  flag 的键（万级）并在 polars 侧 `is_in`——行为不变（既有单测全绿），链路恢复 rc=0。

## 5. 遗留

1. **`make data-update` 全链 rc=0 仍受网盘 sync 阻断**（HTTP 412/403，Quark guest
   cookie 降级；7 项外部失败）——需刷新 `quark_cookies.txt`；daily 数据链本身
   （build+publish+verify）已 rc=0。
2. 首跑/无发布历史仍按全量判（fail-closed）；本数据集水位来自已有的链发布档案
   （2026-09-17，含 FAIL 档案也推进水位，防失败重跑死锁）。
3. `quality_backlog` 为 clean 阶段全表计数（quarantine+WARN），未含 post-ingest 审计；
   `backlog.error_rate` 分母 = raw 全表。
4. 读取恢复后，消费方应同时读取 `quality_backlog` 了解历史残余（3,828 → M3 修复），
   不要把 PASS 理解为"全史干净"。
5. 本次为强制重跑移除了 `data/raw/pan_state.json` 的 daily.build 标记（备份/重置记录在
   本目录）；如需审计原始状态见 `pan_state.before.json`。
