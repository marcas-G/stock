# R40：锁箱纪律验收证据（T12a；方案 A：命令+关键输出+结论+指路）

- 规格：`knowledge/design/platform/specs/2026-09-21-lockbox-discipline-design.md`（§10 验收标准）
- 契约：`knowledge/contracts/interface.md` §10；计划：`knowledge/design/platform/plans/2026-09-21-lockbox-discipline.md`
- 实现：T1–T11 提交（`f75731a`…`f7f8a1b`）；T12a 修正 `flab lockbox` → `factorlab lockbox`
  文案 + 契约/手册/技能同步，并落本证据
- 修复轮1（review）：契约 §10.1 窗口示例 `2025-10-09`（10-01~08 休市）、§10.2 覆盖命令
  名更正；本 README 补 §1.8 `role=lockbox` 样本、§10-4/§10-5 单测证据与只读措辞
- 沙箱隔离：`SB=/tmp/opencode/r40-lockbox`（`rm -rf` 重建，全 tmp）；台账
  `DB=$SB/ledger.sqlite`（tmp 内新建，等价 `FACTORLAB_LOCKBOX_DB=$(mktemp ...)`）；
  研究根用 `QUANTRESEARCH_ROOT=$SB` 隔离（`factorlab research factor run` **无 `--root`
  参数**，`--help` 实测；输出另用 `--output-dir` 显式落 tmp）。真实台账
  `<QR>/data/ledger.sqlite` 全程**只读**（`status --json`/`health` 探活）：**无 roll、无数据行
  写入**（`connect` 探活只读；库/表已存在时 DDL no-op）。
- 运行环境：真 CLI `FLAB_BIN=/data/students/gaolei/stock/platform/.venv/bin/factorlab`；
  `FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow`（CH 无 `stock_st`，显式降级；
  与锁箱门无关）。
- 沙箱 spec：`$SB/factor/probe/{is,mixed,locked}.yaml`，同一公式不同窗口——
  `is`: 2025-01-02~**2025-06-30**（= `is_end`，纯 IS）；`mixed`: 2025-01-02~2025-09-30
  （跨 `window_start=2025-07-01`）；`locked`: 2025-07-01~2026-09-17（整段锁箱内）。

## 1. 沙箱验收矩阵（R40 §10）

### 1.1 前置：roll + status（窗口数学/字段）

```bash
env FACTORLAB_LOCKBOX_DB="$DB" "$FLAB_BIN" lockbox roll --as-of 2026-09-21 --quota-final 20
env FACTORLAB_LOCKBOX_DB="$DB" "$FLAB_BIN" lockbox status --json
```

输出（原样）：

```
[lockbox] window=2026Q2 start=2025-07-01 end=2026-09-17 已更新
{"initialized": true, "window_id": "2026Q2", "window_start": "2025-07-01", "window_end": "2026-09-17", "quota_final": 20, "final_used": 0, "final_remaining": 20, "rolled_at": "2026-09-21T15:48:56+00:00", "is_end": "2025-06-30"}
```

**结论**：滚动 12 个月/季末窗口正确（2026-09-21 → 2026Q2、起点 2025-07-01）；
`is_end=2025-06-30`（`window_start` 前一交易日）可直接作为挖矿 spec 的 `date.end`；
台账初始为 state 单行 + access 0 行。

### 1.2 IS 直跑（无 flag、零登记）——§10-1

```bash
env FACTORLAB_LOCKBOX_DB="$DB" QUANTRESEARCH_ROOT="$SB" \
  FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow \
  "$FLAB_BIN" research factor run "$SB/factor/probe/is.yaml" --no-backtest \
  --output-dir "$SB/results/platform/r40_is"
```

关键输出：`rc=0`；`summary.sample = {"role": "is"}`；`lockbox_access rows = 0`。

**结论**：`date.end ≤ window_start` 的 spec 照常直跑（无重链、无 flag），不登记——
"IS 内挖矿体验不变"。

### 1.3 碰箱无 flag → 拒（零产物/零登记）——§10-2

```bash
env FACTORLAB_LOCKBOX_DB="$DB" QUANTRESEARCH_ROOT="$SB" \
  FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow \
  "$FLAB_BIN" research factor run "$SB/factor/probe/mixed.yaml" --no-backtest \
  --output-dir "$SB/results/platform/r40_mixed"
```

输出：

```
rc=10
{"ok": false, ..., "error": {"code": "LOCKBOX_INTENT_REQUIRED",
 "message": "评估窗口 [2025-01-02~2025-09-30] 与锁箱（2026Q2，起点 2025-07-01）相交：加 `--lockbox exploration|final` 与 `--lockbox-reason <理由>`",
 "hint": "`factorlab lockbox status` 看窗口与配额"}}
artifact dir exists: NO ; lockbox_access rows = 0
```

**结论**：非零退出 + 稳定错误码 + 无产物 + 无登记；拒跑发生在开库/重链之前；
hint 已是顶层入口 `factorlab lockbox status`（T12a 修正后）。

### 1.4 exploration → 1 行登记 + `summary.sample` 一致——§10-3

```bash
env FACTORLAB_LOCKBOX_DB="$DB" QUANTRESEARCH_ROOT="$SB" \
  FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow \
  "$FLAB_BIN" research factor run "$SB/factor/probe/mixed.yaml" --no-backtest \
  --lockbox exploration --lockbox-reason "R40 沙箱验收：登记与 sample 可见性" \
  --output-dir "$SB/results/platform/r40_expl"
```

关键输出（`rc=0`）：

```
summary.sample = {"role": "mixed", "window_id": "2026Q2", "window_start": "2025-07-01",
                  "window_end": "2026-09-17", "access_id": "01M32AH2M8CFGR7MJ0HRRBK1R0"}
access: {"access_id": "01M32AH2M8CFGR7MJ0HRRBK1R0", "kind": "exploration",
         "window_id": "2026Q2", "window_end": "2026-09-17",
         "reason": "R40 沙箱验收：登记与 sample 可见性",
         "result_ref": "$SB/results/platform/r40_expl"}
```

**结论**：exploration 自动登记 1 行；`role=mixed`（跨边界）；`access_id` 与登记一致；
评估结束回填 `result_ref`。

### 1.5 同候选 final 二次 → guard 复用、登记仅 1 行

```bash
for i in 1 2; do
  env FACTORLAB_LOCKBOX_DB="$DB" QUANTRESEARCH_ROOT="$SB" \
    FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow \
    "$FLAB_BIN" research factor run "$SB/factor/probe/mixed.yaml" --no-backtest \
    --lockbox final --lockbox-reason "R40 终评复用验收 #$i" \
    --output-dir "$SB/results/platform/r40_final_$i"
done
```

关键输出：

```
run1 rc=0 ; run2 rc=0
run1.sample.access_id = 01M32AHMJEF7T46K6G919MS7FE
run2.sample.access_id = 01M32AHMJEF7T46K6G919MS7FE   # 同一登记复用
final rows = 1 [("01M32AHMJEF7T46K6G919MS7FE", "final", "a6f2b5ad...")]
```

**结论**：现行语义 = guard 层同 `(window_id, fingerprint)` final 幂等复用（不报错、
不重复登记）；台账严格唯一性/配额（`LOCKBOX_FINAL_DUPLICATE` / `LOCKBOX_QUOTA_EXCEEDED`）
由登记层 API 与单测覆盖（§3）。

### 1.6 跨季未 roll → `LOCKBOX_WINDOW_STALE`——§10-6

```bash
DB_STALE=$(mktemp /tmp/opencode/r40-lockbox/stale-XXXX.sqlite)
env FACTORLAB_LOCKBOX_DB="$DB_STALE" "$FLAB_BIN" lockbox roll --as-of 2026-10-01
env FACTORLAB_LOCKBOX_DB="$DB_STALE" "$FLAB_BIN" lockbox status --json
env FACTORLAB_LOCKBOX_DB="$DB_STALE" QUANTRESEARCH_ROOT="$SB" \
  FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow \
  "$FLAB_BIN" research factor run "$SB/factor/probe/mixed.yaml" --no-backtest \
  --lockbox exploration --lockbox-reason "stale 探针"
```

输出（原样）：

```
[lockbox] window=2026Q3 start=2025-10-09 end=2026-09-17 已更新
{"initialized": true, "window_id": "2026Q3", ..., "is_end": "2025-09-30"}
rc=10
{"ok": false, ..., "error": {"code": "LOCKBOX_WINDOW_STALE",
 "message": "状态窗口 2026Q3 与当前季度窗口 2026Q2 不一致：先 `factorlab lockbox roll` 对齐（解封旧窗并入 IS）"}}
```

**结论**：state 窗口与当前季度不符（真墙钟 2026-09-21 = 2026Q2）时所有评估拒跑，
防止静默跨季解封；错误信息给出 roll 指引。

### 1.7 `FACTORLAB_LOCKBOX=off` 跳过硬门

```bash
env FACTORLAB_LOCKBOX=off FACTORLAB_LOCKBOX_DB="$DB" QUANTRESEARCH_ROOT="$SB" \
  FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow \
  "$FLAB_BIN" research factor run "$SB/factor/probe/mixed.yaml" --no-backtest \
  --output-dir "$SB/results/platform/r40_off"
```

关键输出：`rows before=2`；`rc=0`；`summary.sample={"role":"is"}`；`rows after=2`。

**结论**：显式 off 时直接 IS 放行、不读 state、不登记（CI/离线基线行为；生产不设）。

### 1.8 `role=lockbox`（纯锁箱窗）补充样本

```bash
env FACTORLAB_LOCKBOX_DB="$DB" QUANTRESEARCH_ROOT="$SB" \
  FACTORLAB_DATA_BACKEND=ch FACTORLAB_ST_DEGRADE=allow \
  "$FLAB_BIN" research factor run "$SB/factor/probe/locked.yaml" --no-backtest \
  --lockbox exploration --lockbox-reason "R40 沙箱验收：纯锁箱窗 role=lockbox" \
  --output-dir "$SB/results/platform/r40_locked"
```

关键输出（`rows before=2`）：

```
run rc=0
summary.sample = {"role": "lockbox", "window_id": "2026Q2", "window_start": "2025-07-01",
                  "window_end": "2026-09-17", "access_id": "01M32B60NBH286P2N4GSAG8VV8"}
rows after = 3 ; kinds = [('exploration', 2), ('final', 1)]
```

**结论**：整段 ≥ `window_start` 的面板判 `role=lockbox`（与 `mixed` 并列覆盖碰箱两分支）；
新增 1 行 exploration、final 计数不变。

## 2. 真宿主（禁 Docker；无 roll、无数据行写入）

### 2.1 真台账 status（只读）

```bash
$FLAB_BIN lockbox status --json
```

输出（原样）：

```
{"initialized": false}
rc=1
```

（真实 `<QR>/data/ledger.sqlite` 已存在：`lockbox_state` 0 行、`lockbox_access` 0 行；
本段**无 roll、无数据行写入**——`status` 的 `connect` 为只读探活、表已存在时 DDL no-op；
真实台账 roll 由 controller 在 T12b 后执行。）

### 2.2 `flab health --json` 锁箱段

```bash
flab health --json     # /home/gaolei/.local/bin/flab = factorlab research（CH 后端）
```

关键输出：`rc=0`；

```
data.lockbox = {"initialized": false}
warnings = ["锁箱未初始化：与锁箱相交的评估会被拒（LOCKBOX_NO_STATE）——先 `factorlab lockbox roll`"]
```

**结论**：health 探活正常返回锁箱段（未初始化不拖垮探活）；warning 指向正确入口。

### 2.3 季度提醒脚本（未初始化 → rc=1 指引）

```bash
bash governance/ops/lockbox-reminder.sh
```

输出（原样）：

```
rc=1
[lockbox-reminder] 锁箱需要人工处理：
  - `factorlab lockbox status` 退出码 1
  - 锁箱未初始化（ledger 无 state）——与锁箱相交的评估会被拒（LOCKBOX_NO_STATE）
  操作：`factorlab lockbox roll`（季初人工确认后执行；roll 会把旧窗解封并入 IS，本提醒不自动执行）
```

**结论**：提醒脚本不自动 roll、退出码 1（需人工处理），指引正确。

## 3. 门与聚焦回归

- `make gates`：**G-ANNOTATE ✓**（231 份档案 snapshot 齐备、`sample_role` 齐备）；
  **G-LOCKBOX ✓**（`root=<QR> errors=0 warnings=0`，含台账交叉核对；负向自检通过——
  缺字段/空 id/幽灵 id/探索冒充终评/档案缺声明均被抓、干净样本不误伤）。
  门整体 rc≠0 的唯一失败 = `[G-TOPO] research/tools/porteval/run.py: import 'engine' →
  lob_fact/core`（跨工具 import，**与本任务无关**；按裁定标注 porteval #33，未改动）。
- 聚焦回归（T12a 文案修正先红后绿；修复轮1 复跑同集）：

```bash
cd platform && .venv/bin/python -m pytest -q \
  tests/test_lockbox_*.py tests/test_research_health_lockbox.py tests/test_doc_paths_exist.py
# → 125 passed
```

- §10-4/§10-5 唯一性/配额/终评要求的单测证据（登记层严格语义；行号为本提交时）：
  - `platform/tests/test_lockbox_registry.py`：`test_final_unique_per_fingerprint`（L23 → L30
    `LOCKBOX_FINAL_DUPLICATE`）、`test_quota_exhausted`（L33 → L42 `LOCKBOX_QUOTA_EXCEEDED`）、
    `test_exploration_not_counted_toward_quota`（L48）、`test_require_final_missing`（L74 →
    L78 `LOCKBOX_FINAL_REQUIRED`）、`test_final_concurrent_same_fingerprint`（L85 → L112
    `LOCKBOX_FINAL_DUPLICATE`）。
  - `platform/tests/test_lockbox_admit.py`：`test_admit_locked_without_final_and_reason_is_refused`
    （L110 → L115 `LOCKBOX_FINAL_REQUIRED`）、`test_ref_add_locked_without_final_and_reason_is_refused`
    （L199 → L206）、`test_admit_locked_without_state_is_no_state`（L185，NO_STATE 不误报 FINAL_REQUIRED）。

## 4. 指路（产物不进 git）

- 沙箱根/日志/台账：`/tmp/opencode/r40-lockbox/`（`b_is.log`、`c_noflag.log`、
  `d_expl.log`、`e_final_1|2.log`、`f_*.log`、`g_off.log`、`h_locked.log`；tmp 清理后按本文
  命令可复现）
- 门日志：`/tmp/opencode/r40-gates.log`（`make gates` 全量输出）
- 真实台账：`/data/students/gaolei/quantresearch/data/ledger.sqlite`（只读核验，无 roll/无数据行写入）
- 契约/技能/手册：`knowledge/contracts/interface.md` §10、
  `.claude/skills/factor-mine/SKILL.md` §7/§8、
  `knowledge/handbooks/factor-mining-playbook.md` §4.1

## 部署形态切换：Docker 挖矿服务退役（2026-09-21，用户裁定 A）

- 操作：`systemctl --user stop factorlab-svc && systemctl --user disable factorlab-svc`；
  `docker rm -f factorlab-svc`（镜像保留 `factorlab-svc:{33ec23e,stable,a9b9bfa}` 以便回退）。
- 验证：`is-active=inactive`、`is-enabled=disabled`、`docker ps -a` 无容器、
  `http://127.0.0.1:8787/health` 不可达；`factorlab lockbox status` 真实台账仍 `initialized:false`（未 roll）。
- 生产路径：Prefect 工作流（server/runner 常驻，127.0.0.1:4200）+ 宿主 `factorlab`；
  **锁箱硬门在 `execute_run` 层，与容器无关**（R39 的 L1 验收作为历史记录保留）。
- 回退：`make svc-image REF=<sha> STABLE=1` + `governance/ops/install_svc.sh install`。

## 真实宿主验收（2026-09-21；状态已初始化）

- `factorlab lockbox roll` → `window=2026Q2 start=2025-07-01 end=2026-09-17`（`is_end=2025-06-30`；roll 后 `final**used=0`）。
- 碰箱无 flag 拒跑：`factorlab run <spec> --no-backtest` → `LOCKBOX_INTENT_REQUIRED`（含窗口与提示，零产物）。
- **exploration 真跑**（flab/ch/ST_DEGRADE）：rc=0，IC=0.05646；`summary.sample.role=mixed`、
  `access_id=01M32CQXYC…`（与台账一致，`result_ref` 已回填产物目录）。
- **final 真跑**：rc=0；**复用**既有 final 登记 `01M32CP8SN…`（`result_ref` 回填），未新增行 → quota 19/20。
- **IS 无 flag**：临时 spec（end=2025-06-30）→ rc=0；`summary.sample.role=is`；台账行数不变（4）→ 证 IS 零登记。
- `flab health --json` lockbox 段：`initialized=true / window=2026Q2 / final_used=1 / final_remaining=19`；
  `lockbox-reminder.sh` rc=0（窗口正常）。
- 门：`G-ANNOTATE` ✓（231 份 sample_role 齐备）；`G-LOCKBOX` ✓ host 交叉核对 + `--selftest` 7 类造假必抓；
  `make gates` 唯一 `[BAD]`=既有 porteval #33（与锁箱无关）。
- 说明：早期两次失败 run（宿主默认 duckdb 后端 / 缺 `FACTORLAB_ST_DEGRADE`）在 guard 之后失败，
  台账保留其 `result_ref=NULL` 行——**登记先于重链**（如实记录访问尝试；失败不抹除）。
- 指路：`/tmp/r40-real-roll.log`、`/tmp/r40-real-status.json`、`/tmp/r40-{expl,final,is}.json`；
  产物 `quantresearch/results/platform/_svc_smoke/r40_{expl,final,is}/`。
