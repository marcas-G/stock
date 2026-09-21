# R40：锁箱纪律验收证据（T12a；方案 A：命令+关键输出+结论+指路）

- 规格：`knowledge/design/platform/specs/2026-09-21-lockbox-discipline-design.md`（§10 验收标准）
- 契约：`knowledge/contracts/interface.md` §10；计划：`knowledge/design/platform/plans/2026-09-21-lockbox-discipline.md`
- 实现：T1–T11 提交（`f75731a`…`f7f8a1b`）；T12a 修正 `flab lockbox` → `factorlab lockbox`
  文案 + 契约/手册/技能同步，并落本证据
- 沙箱隔离：`SB=/tmp/opencode/r40-lockbox`（`rm -rf` 重建，全 tmp）；台账
  `DB=$SB/ledger.sqlite`（tmp 内新建，等价 `FACTORLAB_LOCKBOX_DB=$(mktemp ...)`）；
  研究根用 `QUANTRESEARCH_ROOT=$SB` 隔离（`factorlab research factor run` **无 `--root`
  参数**，`--help` 实测；输出另用 `--output-dir` 显式落 tmp）。真实台账
  `<QR>/data/ledger.sqlite` 全程**只读**（`status --json`），未执行任何 `roll`/写操作。
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

## 2. 真宿主（禁 Docker；不初始化、不 roll）

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
本段未执行 `roll` 或任何写操作——真实初始化留待 T12b。）

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
- 聚焦回归（T12a 文案修正先红后绿）：

```bash
cd platform && .venv/bin/python -m pytest -q \
  tests/test_lockbox_*.py tests/test_research_health_lockbox.py
# → 114 passed
```

## 4. 指路（产物不进 git）

- 沙箱根/日志/台账：`/tmp/opencode/r40-lockbox/`（`b_is.log`、`c_noflag.log`、
  `d_expl.log`、`e_final_1|2.log`、`f_*.log`、`g_off.log`；tmp 清理后按本文命令可复现）
- 门日志：`/tmp/opencode/r40-gates.log`（`make gates` 全量输出）
- 真实台账：`/data/students/gaolei/quantresearch/data/ledger.sqlite`（只读核验，未写）
- 契约/技能/手册：`knowledge/contracts/interface.md` §10、
  `.claude/skills/factor-mine/SKILL.md` §7/§8、
  `knowledge/handbooks/factor-mining-playbook.md` §4.1
