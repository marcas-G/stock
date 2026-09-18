#!/usr/bin/env bash
# R31 Task 6 E2E 一键复跑（真 CH；plan Step 4 / spec §7）。
#
# 链：flab health → data daily → factor run（小 universe 探针 spec）→ admit
#     → strategy run（现有策略）→ report url。
# 约定：每条命令 exit 0 且 stdout 恰好一个 JSON 信封；原始输出落本目录。
# 用法：bash governance/evidence/verification/R31/research-api/e2e/run_e2e.sh
set -uo pipefail

OUT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$OUT/../../../../../.." && pwd)"
PROBE="$OUT/e2e_factor.yaml"
STRATEGY="$REPO/research/strategy/low_lottery_top30_weekly.yaml"
FAIL=0

check_json() {
    python3 - "$1" <<'PY'
import json, sys
raw = open(sys.argv[1], encoding="utf-8").read()
lines = [ln for ln in raw.strip().splitlines() if ln.strip()]
assert len(lines) == 1, f"stdout 非单个 JSON（{len(lines)} 行）"
doc = json.loads(raw)
assert doc["schema_version"] == 1, doc.get("schema_version")
assert doc["ok"] is True, doc.get("error")
print(f"  command={doc['command']} ok={doc['ok']}", file=sys.stderr)
PY
}

run() {
    local label="$1"; shift
    if "$@" > "$OUT/$label.json" 2> "$OUT/$label.err"; then
        if check_json "$OUT/$label.json"; then
            echo "PASS $label exit=0 单 JSON"
            return 0
        fi
        echo "FAIL $label 输出不是单个合法 JSON 信封"
    else
        echo "FAIL $label 非零退出（见 $label.err）"
    fi
    FAIL=1
}

cd "$REPO"
run 01-health       flab health
run 02-data-daily   flab data daily --codes 600176.SH --start 2026-09-01 --end 2026-09-17 --json
run 03-factor-run   flab factor run "$PROBE"
run 04-factor-admit flab factor admit "$PROBE"
run 05-strategy-run flab strategy run "$STRATEGY"
run 06-report-url   flab report url e2e_research_api_probe

if [ "$FAIL" -eq 0 ]; then
    echo "E2E: 6/6 PASS"
else
    echo "E2E: 有失败（见上）"
fi
exit "$FAIL"
