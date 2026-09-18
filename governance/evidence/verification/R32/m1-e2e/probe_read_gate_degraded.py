#!/usr/bin/env python
"""R32 T9 反例注入 B：真实 DEGRADED health → 默认读取被拒 / opt-in 通过 + manifest 完整。

数据源 = T9 真跑发布的 ``data/health/ashare_daily/2026-09-17.json``
（``DEGRADED/VERIFIED``，full_table 口径）——不伪造 artifact。
manifest 落设计落点 ``data/manifest/ashare_daily/<partition>.json``（本地数据区）。

退出码：0 断言全过；1 有偏差。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(_REPO / "platform" / "src"))

from factorlab.adapters.read.health import (  # noqa: E402
    DatasetQualityError, require_dataset)

DATASET, AS_OF = "ashare_daily", "2026-09-17"
MANIFEST_KEYS = {"dataset_quality", "quality_issues", "affected_partitions",
                 "dq_policy_version", "override_reason"}


def main() -> int:
    artifact = _REPO / "data" / "health" / DATASET / f"{AS_OF}.json"
    doc = json.loads(artifact.read_text(encoding="utf-8"))
    print(f"artifact: {artifact}")
    print(f"health_status={doc['health_status']} "
          f"verification_state={doc['verification_state']} "
          f"completeness={doc['completeness']['status']} "
          f"coverage={doc['completeness']['coverage']}", flush=True)

    checks: dict[str, bool] = {}
    try:
        require_dataset(DATASET, AS_OF)
    except DatasetQualityError as ex:
        checks["default_rejected"] = True
        print(f"[OK] 默认读取被拒：{str(ex)[:160]}", flush=True)
    else:
        checks["default_rejected"] = False
        print("[FAIL] 默认读取未被拒", flush=True)

    try:
        require_dataset(DATASET, AS_OF, accept_quality=("PASS", "DEGRADED"),
                        override_reason="probe", strict=True)
    except ValueError as ex:
        checks["strict_rejected"] = "strict" in str(ex)
        print(f"[OK] strict PASS-only 拒绝 opt-in：{str(ex)[:120]}", flush=True)
    else:
        checks["strict_rejected"] = False
        print("[FAIL] strict 未拒绝 opt-in", flush=True)

    gate = require_dataset(
        DATASET, AS_OF, accept_quality=("PASS", "DEGRADED"),
        override_reason="R32 T9 验收：真实 DEGRADED health opt-in")
    manifest = json.loads(Path(gate.manifest_path).read_text(encoding="utf-8"))
    checks["opt_in_accepted"] = gate.health_status == "DEGRADED"
    checks["manifest_five_fields"] = set(manifest) == MANIFEST_KEYS
    checks["summary_five_fields"] = len(gate.summary_fields()) == 5
    print(f"[{'OK' if checks['opt_in_accepted'] else 'FAIL'}] opt-in 通过："
          f"gate={gate.health_status}/{gate.verification_state} "
          f"manifest={gate.manifest_path}", flush=True)
    print(f"[{'OK' if checks['manifest_five_fields'] else 'FAIL'}] manifest 键："
          f"{sorted(manifest)}", flush=True)
    print(f"[{'OK' if checks['summary_five_fields'] else 'FAIL'}] 产物五字段："
          f"{json.dumps(gate.summary_fields(), ensure_ascii=False)}", flush=True)

    out = {"artifact": str(artifact),
           "artifact_status": {k: doc[k] for k in
                               ("health_status", "verification_state", "data_version")},
           "manifest": manifest, "summary_fields": gate.summary_fields(),
           "checks": checks, "ok": all(checks.values())}
    print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
