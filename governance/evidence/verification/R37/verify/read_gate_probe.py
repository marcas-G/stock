#!/usr/bin/env python3
"""R37 独立复查 D：读取门行为 probe（公开入口 require_dataset）。

- 1995-12-29 → 期望 DatasetQualityError，status=OUT_OF_SCOPE，文案含 1996-01-01；
- 2025-03-10（PASS）→ 通过（strict=True 亦通过）；
- 1996-01-02（DEGRADED，A 步实测）→ 默认拒绝；accept_quality=("DEGRADED",) +
  override_reason → 通过。manifest 重定向到 /tmp，不写仓库 data/。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path("/data/students/gaolei/stock")
sys.path.insert(0, str(REPO / "platform" / "src"))

from factorlab.adapters.read.health import (  # noqa: E402
    DatasetQualityError,
    require_dataset,
)

OUT_JSON = Path(__file__).with_name("D-read-gate.json")
OUT_LOG = Path(__file__).with_name("D-read-gate.log")
TMP = Path("/tmp/opencode/R37-verify")
TMP.mkdir(parents=True, exist_ok=True)

log_lines: list[str] = []
results: dict = {}


def log(msg: str) -> None:
    print(msg)
    log_lines.append(msg)


def attempt(name: str, fn) -> None:
    try:
        gate = fn()
        rec = {"outcome": "PASS_THROUGH", "gate": {
            "dataset": gate.dataset, "partition": gate.partition,
            "health_status": gate.health_status,
            "verification_state": gate.verification_state,
            "completeness_status": gate.completeness_status,
            "quarantined_rows": gate.quarantined_rows,
            "coverage": gate.coverage,
            "dq_policy_version": gate.dq_policy_version,
            "override_reason": gate.override_reason,
        }}
        log(f"[{name}] PASS_THROUGH -> {json.dumps(rec['gate'], ensure_ascii=False)}")
    except DatasetQualityError as exc:
        rec = {"outcome": "REJECTED", "status": exc.status,
               "message": str(exc), "guidance": exc.guidance}
        log(f"[{name}] REJECTED status={exc.status}")
        log(f"    message : {exc}")
        log(f"    guidance: {exc.guidance}")
    except ValueError as exc:
        rec = {"outcome": "VALUE_ERROR", "message": str(exc)}
        log(f"[{name}] VALUE_ERROR: {exc}")
    results[name] = rec


log("== D. read gate require_dataset behavior ==")
log("")

attempt("D1_1995-12-29_default", lambda: require_dataset(
    "ashare_daily", "1995-12-29"))

attempt("D2_2025-03-10_default", lambda: require_dataset(
    "ashare_daily", "2025-03-10"))

attempt("D3_2025-03-10_strict", lambda: require_dataset(
    "ashare_daily", "2025-03-10", strict=True))

attempt("D4_1996-01-02_DEGRADED_default", lambda: require_dataset(
    "ashare_daily", "1996-01-02"))

attempt("D5_1996-01-02_DEGRADED_optin", lambda: require_dataset(
    "ashare_daily", "1996-01-02",
    accept_quality=("DEGRADED",),
    override_reason="R37 verify: opt-in path smoke test",
    manifest_path=TMP / "manifest-1996-01-02.json"))

# 负向：FAIL 不可 opt-in（用非法状态触发解析层拒绝，证明通道存在）
attempt("D6_FAIL_optin_rejected", lambda: require_dataset(
    "ashare_daily", "2025-03-10",
    accept_quality=("FAIL",), override_reason="x"))

# 严格模式（strict=True）对 DEGRADED：默认拒绝；即使 opt-in 也 ValueError
attempt("D7_1996-01-02_strict_default", lambda: require_dataset(
    "ashare_daily", "1996-01-02", strict=True))

attempt("D8_1996-01-02_strict_optin", lambda: require_dataset(
    "ashare_daily", "1996-01-02", strict=True,
    accept_quality=("DEGRADED",),
    override_reason="R37 verify: strict must reject even with opt-in",
    manifest_path=TMP / "manifest-strict-should-not-exist.json"))

# 反向断言
checks = {
    "D1_status_is_OUT_OF_SCOPE": results["D1_1995-12-29_default"].get("status") == "OUT_OF_SCOPE",
    "D1_message_has_1996-01-01": "1996-01-01" in results["D1_1995-12-29_default"].get("message", ""),
    "D1_no_optin_channel": "无 opt-in" in results["D1_1995-12-29_default"].get("guidance", ""),
    "D2_pass": results["D2_2025-03-10_default"]["outcome"] == "PASS_THROUGH",
    "D3_pass": results["D3_2025-03-10_strict"]["outcome"] == "PASS_THROUGH",
    "D3_status_PASS": results["D3_2025-03-10_strict"].get("gate", {}).get("health_status") == "PASS",
    "D4_rejected": results["D4_1996-01-02_DEGRADED_default"]["outcome"] == "REJECTED",
    "D4_status_DEGRADED": results["D4_1996-01-02_DEGRADED_default"].get("status") == "DEGRADED",
    "D4_message_mentions_default_fail_closed": "默认 fail-closed" in results["D4_1996-01-02_DEGRADED_default"].get("message", ""),
    "D5_pass": results["D5_1996-01-02_DEGRADED_optin"]["outcome"] == "PASS_THROUGH",
    "D5_status_DEGRADED": results["D5_1996-01-02_DEGRADED_optin"].get("gate", {}).get("health_status") == "DEGRADED",
    "D5_manifest_written": (TMP / "manifest-1996-01-02.json").is_file(),
    "D5_manifest_not_in_repo": not (REPO / "data" / "manifest" / "ashare_daily" / "1996-01-02.json").exists(),
    "D6_ValueError": results["D6_FAIL_optin_rejected"]["outcome"] == "VALUE_ERROR",
    "D7_strict_default_rejected": results["D7_1996-01-02_strict_default"]["outcome"] == "REJECTED",
    "D7_status_DEGRADED": results["D7_1996-01-02_strict_default"].get("status") == "DEGRADED",
    "D8_strict_optin_ValueError": results["D8_1996-01-02_strict_optin"]["outcome"] == "VALUE_ERROR",
    "D8_manifest_not_written": not (TMP / "manifest-strict-should-not-exist.json").exists(),
}
log("")
log("== checks ==")
for k, v in checks.items():
    log(f"  {'OK ' if v else 'FAIL'} {k}")

OUT_JSON.write_text(json.dumps({"results": results, "checks": checks},
                               ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
OUT_LOG.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
sys.exit(0 if all(checks.values()) else 1)
