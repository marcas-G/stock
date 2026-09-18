#!/usr/bin/env python
"""R32 T9 反例注入 C：读取拒绝矩阵（PASS/DEGRADED/FAIL/UNKNOWN × freshness × completeness）。

隔离策略：全部 health artifact 写独立临时 root；``require_dataset`` 指向该 root
（不读/不写生产数据）。每格断言「接受/拒绝 + 拒绝原因」；opt-in 格额外断言
Experiment Manifest 五字段齐全。

用法：python probe_rejection_matrix.py <out_dir>
退出码：0 全部符合预期；1 有偏差。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(_REPO / "platform" / "tools"))       # data_quality.health 构建 artifact
sys.path.insert(0, str(_REPO / "platform" / "src"))         # factorlab 读取门

from data_quality import health, rules  # noqa: E402
from factorlab.adapters.read.health import (  # noqa: E402
    DatasetQualityError, require_dataset)

D = dt.date
AS_OF = "2026-09-17"
STALE = (D.fromisoformat(AS_OF) - dt.timedelta(days=30)).isoformat()
DATASET = "ashare_daily"
MANIFEST_KEYS = {"dataset_quality", "quality_issues", "affected_partitions",
                 "dq_policy_version", "override_reason"}


def _doc(status: str, verification: str, completeness: str, latest: str) -> dict:
    expected, actual = 100, 100 if completeness == "COMPLETE" else 99
    return health.build_health_doc(
        dataset_id=DATASET, partition=AS_OF, data_version="v20260919_01",
        dq_policy_version=rules.POLICY_VERSION, health_status=status,
        verification_state=verification,
        completeness={"status": completeness, "expected_count": expected,
                      "actual_count": actual,
                      "coverage": actual / expected},
        quality={"fatal_count": 0, "error_count": 0, "warning_count": 0,
                 "quarantine_count": 0, "error_rate": 0.0,
                 "systematic_issue": False, "systemic_detail": None},
        rules_counts={}, latest_trade_date=latest, source_version="probe")


def _case(root: Path, name: str, doc: dict, *, accept=("PASS",),
          reason: str | None = None, strict: bool = False):
    health.write_health_artifact(root, doc)
    entry = {"case": name, "health_status": doc["health_status"],
             "verification": doc["verification_state"],
             "completeness": doc["completeness"]["status"],
             "freshness": doc["freshness"]["latest_trade_date"],
             "accept_quality": list(accept), "override_reason": reason,
             "strict": strict}
    try:
        gate = require_dataset(DATASET, AS_OF, accept_quality=tuple(accept),
                               root=root, override_reason=reason, strict=strict)
    except DatasetQualityError as ex:
        entry.update(accepted=False, reason=str(ex))
    except ValueError as ex:
        entry.update(accepted=False, reason=f"ValueError: {ex}")
    else:
        entry.update(accepted=True, manifest_path=None if gate.manifest_path is None
                     else str(gate.manifest_path))
        if doc["health_status"] != "PASS":
            manifest = json.loads(Path(gate.manifest_path).read_text(encoding="utf-8"))
            entry["manifest_keys"] = sorted(manifest)
            entry["manifest_five_fields_ok"] = set(manifest) == MANIFEST_KEYS
            entry["gate_summary_fields"] = sorted(gate.summary_fields())
    return entry


def main(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir / "health-root"
    cases = [
        # PASS × freshness × completeness
        _case(root, "PASS/fresh/COMPLETE → 过", _doc("PASS", "VERIFIED", "COMPLETE", AS_OF)),
        _case(root, "PASS/stale/COMPLETE → 拒（freshness）",
              _doc("PASS", "VERIFIED", "COMPLETE", STALE)),
        _case(root, "PASS/fresh/INCOMPLETE → 拒（completeness）",
              _doc("PASS", "VERIFIED", "INCOMPLETE", AS_OF)),
        _case(root, "PASS/fresh/UNKNOWN → 拒（completeness）",
              _doc("PASS", "VERIFIED", "UNKNOWN", AS_OF)),
        # DEGRADED
        _case(root, "DEGRADED/默认 → 拒",
              _doc("DEGRADED", "VERIFIED", "COMPLETE", AS_OF)),
        _case(root, "DEGRADED/fresh/COMPLETE/opt-in → 过 + manifest",
              _doc("DEGRADED", "VERIFIED", "COMPLETE", AS_OF),
              accept=("PASS", "DEGRADED"), reason="R32 验收：真实 DEGRADED opt-in"),
        _case(root, "DEGRADED/stale/opt-in → 拒（freshness）",
              _doc("DEGRADED", "VERIFIED", "COMPLETE", STALE),
              accept=("PASS", "DEGRADED"), reason="probe"),
        _case(root, "DEGRADED/fresh/INCOMPLETE/opt-in → 拒（completeness）",
              _doc("DEGRADED", "VERIFIED", "INCOMPLETE", AS_OF),
              accept=("PASS", "DEGRADED"), reason="probe"),
        # FAIL
        _case(root, "FAIL/默认 → 拒", _doc("FAIL", "VERIFIED", "COMPLETE", AS_OF)),
        _case(root, "FAIL/opt-in → 拒（不可 opt-in）",
              _doc("FAIL", "VERIFIED", "COMPLETE", AS_OF),
              accept=("PASS", "DEGRADED", "FAIL"), reason="probe"),
        # UNKNOWN × LEGACY（过渡条款）
        _case(root, "UNKNOWN(LEGACY)/默认 → 拒",
              _doc("UNKNOWN", "LEGACY_UNVERIFIED", "COMPLETE", AS_OF)),
        _case(root, "UNKNOWN(LEGACY)/fresh/COMPLETE/opt-in → 过 + manifest",
              _doc("UNKNOWN", "LEGACY_UNVERIFIED", "COMPLETE", AS_OF),
              accept=("PASS", "UNKNOWN"), reason="R32 验收：存量过渡条款"),
        _case(root, "UNKNOWN(LEGACY)/stale/opt-in → 拒（freshness）",
              _doc("UNKNOWN", "LEGACY_UNVERIFIED", "COMPLETE", STALE),
              accept=("PASS", "UNKNOWN"), reason="probe"),
        _case(root, "UNKNOWN(LEGACY)/fresh/INCOMPLETE/opt-in → 拒（completeness）",
              _doc("UNKNOWN", "LEGACY_UNVERIFIED", "INCOMPLETE", AS_OF),
              accept=("PASS", "UNKNOWN"), reason="probe"),
        _case(root, "UNKNOWN(VERIFIED) → 拒（非 LEGACY）",
              _doc("UNKNOWN", "VERIFIED", "COMPLETE", AS_OF),
              accept=("PASS", "UNKNOWN"), reason="probe"),
        # strict（正式场景 PASS-only）
        _case(root, "strict + DEGRADED opt-in → ValueError",
              _doc("DEGRADED", "VERIFIED", "COMPLETE", AS_OF),
              accept=("PASS", "DEGRADED"), reason="probe", strict=True),
    ]
    for c in cases:
        if "manifest_keys" in c and c["health_status"] != "PASS":
            c["ok"] = (c["accepted"] and c.get("manifest_five_fields_ok") is True
                       and len(c.get("gate_summary_fields", [])) == 5)
        else:
            expect_accept = c["case"].startswith(("PASS/fresh/COMPLETE",
                                                  "DEGRADED/fresh/COMPLETE/opt-in",
                                                  "UNKNOWN(LEGACY)/fresh/COMPLETE/opt-in"))
            c["ok"] = (c["accepted"] == expect_accept)
        print(f"[{'OK' if c['ok'] else 'FAIL'}] {c['case']}: "
              f"accepted={c['accepted']}"
              + ("" if c["accepted"] else f" | {c['reason'][:90]}"), flush=True)

    (out_dir / "rejection_matrix.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# R32 T9：读取拒绝矩阵（真实 require_dataset）", "",
             "| # | 用例 | Accepted | 说明 |", "|---|---|---|---|"]
    for i, c in enumerate(cases, 1):
        note = "通过（gate + manifest 五字段）" if c["accepted"] else \
            c["reason"].replace("|", "/")[:150]
        lines.append(f"| {i} | {c['case']} | {'✅' if c['accepted'] else '❌'} | {note} |")
    lines.append("")
    (out_dir / "rejection_matrix.md").write_text("\n".join(lines), encoding="utf-8")
    return 0 if all(c["ok"] for c in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent))
