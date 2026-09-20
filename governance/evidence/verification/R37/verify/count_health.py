#!/usr/bin/env python3
"""R37 独立复查 A：health 目录普查（只读）。

按 dq_policy_version × health_status 计数；按 scope 分界（1996-01-01）计数；
列出全部 DEGRADED 分区（partition/quarantine/expected）与随机 3 个 PASS 分区。
"""
from __future__ import annotations

import datetime as dt
import json
import random
import sys
from collections import Counter
from pathlib import Path

HEALTH = Path("/data/students/gaolei/stock/data/health/ashare_daily")
MIN_DATE = dt.date(1996, 1, 1)
OUT_JSON = Path(__file__).with_name("A-health-census.json")
OUT_LOG = Path(__file__).with_name("A-health-census.log")

log_lines: list[str] = []


def log(msg: str) -> None:
    print(msg)
    log_lines.append(msg)


def main() -> int:
    files = sorted(HEALTH.glob("*.json"))
    log(f"health dir: {HEALTH}")
    log(f"json files total: {len(files)}")

    by_policy_status: Counter = Counter()
    by_policy_scope: Counter = Counter()
    degraded: list[dict] = []
    passed: list[str] = []
    parse_errors: list[str] = []
    in_scope_total = 0

    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"{f.name}: {exc}")
            continue
        part = doc.get("partition", f.stem)
        status = doc.get("health_status", "<missing>")
        policy = doc.get("dq_policy_version", "<missing>")
        try:
            pdate = dt.date.fromisoformat(str(part))
            in_scope = pdate >= MIN_DATE
        except ValueError:
            in_scope = None
        by_policy_status[(policy, status)] += 1
        by_policy_scope[(policy, str(in_scope))] += 1
        if in_scope:
            in_scope_total += 1
        if status == "DEGRADED":
            completeness = doc.get("completeness") or {}
            quality = doc.get("quality") or {}
            backlog = doc.get("quality_backlog") or {}
            degraded.append({
                "partition": part,
                "policy": policy,
                "quarantine_count": quality.get("quarantine_count"),
                "error_count": quality.get("error_count"),
                "expected_count": completeness.get("expected_count"),
                "actual_count": completeness.get("actual_count"),
                "coverage": completeness.get("coverage"),
                "completeness_status": completeness.get("status"),
                "backlog_quarantine": backlog.get("quarantine_count"),
                "backlog_scope": backlog.get("scope"),
                "in_scope_date": in_scope,
            })
        if status == "PASS":
            passed.append(part)

    log("")
    log("== by (dq_policy_version, health_status) ==")
    for (policy, status), n in sorted(by_policy_status.items()):
        log(f"  policy={policy:<12} status={status:<10} n={n}")

    log("")
    log("== by (dq_policy_version, partition>=1996-01-01) ==")
    for (policy, in_scope), n in sorted(by_policy_scope.items()):
        log(f"  policy={policy:<12} in_scope={in_scope:<5} n={n}")
    log(f"in-scope (date>=1996-01-01) partitions total: {in_scope_total}")

    log("")
    log(f"== DEGRADED partitions ({len(degraded)}) ==")
    for d in sorted(degraded, key=lambda x: x["partition"]):
        log(f"  {d['partition']}  quarantine={d['quarantine_count']}  "
            f"error={d['error_count']}  expected={d['expected_count']}  "
            f"actual={d['actual_count']}  coverage={d['coverage']}  "
            f"completeness={d['completeness_status']}  policy={d['policy']}  "
            f"backlog_q={d['backlog_quarantine']}")

    random.seed(37)
    sample = sorted(random.sample(passed, 3)) if len(passed) >= 3 else sorted(passed)
    log("")
    log(f"== random 3 PASS (seed=37) ==")
    for p in sample:
        log(f"  {p}")
    log("")
    log(f"parse_errors={len(parse_errors)}")
    for e in parse_errors[:20]:
        log(f"  {e}")

    OUT_JSON.write_text(json.dumps({
        "files_total": len(files),
        "in_scope_partitions_total": in_scope_total,
        "by_policy_status": {f"{k[0]}|{k[1]}": v for k, v in sorted(by_policy_status.items())},
        "by_policy_scope": {f"{k[0]}|{k[1]}": v for k, v in sorted(by_policy_scope.items())},
        "degraded": degraded,
        "pass_sample_3": sample,
        "parse_errors": parse_errors,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_LOG.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
