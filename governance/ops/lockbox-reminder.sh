#!/usr/bin/env bash
# lockbox-reminder.sh —— 季度锁箱 roll 提醒（R40 §12；R42 §5 保留）。
#
# 季度首月 1 日 09:00 由 user timer `factorlab-lockbox-reminder.timer` 触发
# （安装：`bash governance/ops/install_lockbox_timer.sh install`）。
# 本脚本**只提醒不自动 roll**——roll 会解封旧窗并入 IS，必须人工确认。
#
# 判定：调 `factorlab lockbox status --json`（与人工入口同源；PATH 优先，
# 缺省平台 venv 绝对路径）：
#   - 非 0 / initialized=false / state window_id != 当前季度窗口 → 提醒 + exit 1；
#   - 正常 → 窗口摘要 + exit 0。
# 退出码：0 正常；1 需人工处理（提醒已打印）；2 环境错误（找不到 factorlab/解释器）。
set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$OPS_DIR/../.." && pwd)"
FLAB="${FACTORLAB_BIN:-$(command -v factorlab || true)}"
[ -n "$FLAB" ] || FLAB="$ROOT/platform/.venv/bin/factorlab"
if [ ! -x "$FLAB" ]; then
    echo "lockbox-reminder: 找不到 factorlab（$FLAB）；先装平台 venv 或设 FACTORLAB_BIN" >&2
    exit 2
fi
PY="${FACTORLAB_PYTHON:-$ROOT/platform/.venv/bin/python}"
if [ ! -x "$PY" ]; then
    PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ]; then
    echo "lockbox-reminder: 找不到 python3（也无法平台 venv）" >&2
    exit 2
fi

if OUT="$("$FLAB" lockbox status --json 2>&1)"; then
    RC=0
else
    RC=$?
fi

LOCKBOX_REMINDER_RC="$RC" LOCKBOX_REMINDER_OUT="$OUT" "$PY" - <<'PY'
import datetime as dt
import json
import os
import sys

try:
    from factorlab.core.lockbox import quarter_end_before, window_id_of
except Exception as exc:  # noqa: BLE001
    print(f"[lockbox-reminder] 无法加载 factorlab.core.lockbox"
          f"（{type(exc).__name__}: {exc}）；请检查平台 venv", file=sys.stderr)
    sys.exit(2)

rc = int(os.environ.get("LOCKBOX_REMINDER_RC", "0"))
raw = os.environ.get("LOCKBOX_REMINDER_OUT", "")
expected = window_id_of(quarter_end_before(dt.date.today()))

doc = None
try:
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        doc = parsed
except ValueError:
    pass

problems: list[str] = []
if rc != 0:
    problems.append(f"`factorlab lockbox status` 退出码 {rc}")
if doc is None:
    problems.append(f"status 输出无法解析为 JSON：{raw.strip()[:200]!r}")
elif not doc.get("initialized"):
    problems.append("锁箱未初始化（ledger 无 state）——与锁箱相交的评估会被拒"
                    "（LOCKBOX_NO_STATE）")
elif doc.get("window_id") != expected:
    problems.append(f"锁箱窗口陈旧：state={doc.get('window_id')}，"
                    f"当前季度应为 {expected}")

if problems:
    print("[lockbox-reminder] 锁箱需要人工处理：", file=sys.stderr)
    for line in problems:
        print(f"  - {line}", file=sys.stderr)
    print("  操作：`factorlab lockbox roll`（季初人工确认后执行；roll 会把旧窗"
          "解封并入 IS，本提醒不自动执行）", file=sys.stderr)
    sys.exit(1)

print(f"[lockbox-reminder] 锁箱窗口正常 window={doc['window_id']} "
      f"start={doc['window_start']} end={doc['window_end']} "
      f"is_end={doc.get('is_end')} finals_total={doc.get('finals_total')}")
PY
