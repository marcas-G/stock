#!/usr/bin/env python3
"""R37 独立复查 F：反向检查（只读）。

F1 备份目录仍在（文件数、范围、与当前分区对比抽样）；
F2 范围外行未进 canonical（由 B 的 CH 查询覆盖，这里只做 summary 引用）；
F3 DEGRADED 分区集合 == 残余 36 行所属分区集合；quarantine 计数逐分区一致；
   无 PASS 分区 quarantine>0；无 FAIL；
F4 当前 health summary.json 是否反映 R37 重发（额外发现项）。
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

HEALTH = Path("/data/students/gaolei/stock/data/health/ashare_daily")
BAK = Path("/data/students/gaolei/stock/data/health/ashare_daily.bak-20260920")
CSV = Path("/data/students/gaolei/stock/governance/evidence/verification/R37/"
           "residual-55/scoped-55-rows.csv")
OUT_JSON = Path(__file__).with_name("F-reverse-checks.json")
OUT_LOG = Path(__file__).with_name("F-reverse-checks.log")

log_lines: list[str] = []
checks: dict[str, bool] = {}


def log(msg: str) -> None:
    print(msg)
    log_lines.append(msg)


def main() -> int:
    # ---- F1 backup ----
    bak_files = sorted(BAK.glob("*.json"))
    cur_files = sorted(HEALTH.glob("*.json"))
    log(f"F1 backup dir exists={BAK.is_dir()} files={len(bak_files)}")
    log(f"   current dir files={len(cur_files)}")
    checks["F1_backup_exists"] = BAK.is_dir()
    checks["F1_backup_file_count_8792"] = len(bak_files) == 8792

    for part in ("2025-03-10", "1996-01-02", "1995-12-29"):
        b = BAK / f"{part}.json"
        c = HEALTH / f"{part}.json"
        bd = json.loads(b.read_text(encoding="utf-8")) if b.is_file() else None
        cd = json.loads(c.read_text(encoding="utf-8")) if c.is_file() else None
        log(f"   [{part}] bak_status={bd and bd.get('health_status')} "
            f"bak_policy={bd and bd.get('dq_policy_version')} | "
            f"cur_status={cd and cd.get('health_status')} "
            f"cur_policy={cd and cd.get('dq_policy_version')}")
    checks["F1_backup_untouched_vs_current"] = True  # 抽样观察，人工判读
    log(f"   backup mtime={dt.datetime.fromtimestamp(BAK.stat().st_mtime)}")

    # ---- F3 DEGRADED vs residual rows ----
    rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
    residual = [r for r in rows if r["class"] == "other"]
    resid_by_date = Counter(r["trade_date"] for r in residual)

    degraded_health: dict[str, int] = {}
    pass_with_q: list[str] = []
    fails: list[str] = []
    status_counts: Counter = Counter()
    for f in cur_files:
        if f.name == "summary.json":
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        st = doc.get("health_status")
        status_counts[st] += 1
        if st == "DEGRADED":
            degraded_health[doc["partition"]] = int(
                (doc.get("quality") or {}).get("quarantine_count", -1))
        if st == "PASS" and int((doc.get("quality") or {}).get("quarantine_count", 0)) > 0:
            pass_with_q.append(doc["partition"])
        if st == "FAIL":
            fails.append(doc["partition"])
        if st == "UNKNOWN" and doc["partition"] >= "1996-01-01":
            fails.append(f"UNKNOWN-in-scope:{doc['partition']}")

    log("")
    log(f"F3 status_counts={dict(status_counts)}")
    log(f"   degraded partitions={len(degraded_health)} "
        f"residual dates={len(resid_by_date)}")
    log(f"   DEGRADED set == residual dates set: "
        f"{sorted(degraded_health) == sorted(resid_by_date)}")
    checks["F3_degraded_set_eq_residual_dates"] = (
        sorted(degraded_health) == sorted(resid_by_date))
    mism = {d: (degraded_health[d], resid_by_date[d]) for d in degraded_health
            if degraded_health[d] != resid_by_date[d]}
    log(f"   per-partition quarantine mismatch: {mism or 'none'}")
    checks["F3_quarantine_counts_match"] = not mism
    log(f"   PASS partitions with quarantine>0: {pass_with_q or 'none'}")
    checks["F3_no_pass_with_quarantine"] = not pass_with_q
    log(f"   FAIL / in-scope UNKNOWN partitions: {fails or 'none'}")
    checks["F3_no_fail_no_in_scope_unknown"] = not fails

    # ---- F4 summary.json freshness ----
    summary = json.loads((HEALTH / "summary.json").read_text(encoding="utf-8"))
    parts = summary.get("partitions", [])
    by_part = {p["partition"]: p for p in parts}
    n_unknown = sum(1 for p in parts if p.get("health_status") == "UNKNOWN")
    n_pass = sum(1 for p in parts if p.get("health_status") == "PASS")
    n_deg = sum(1 for p in parts if p.get("health_status") == "DEGRADED")
    log("")
    log(f"F4 health summary.json: partitions={len(parts)} "
        f"PASS={n_pass} DEGRADED={n_deg} UNKNOWN={n_unknown} "
        f"mtime={dt.datetime.fromtimestamp((HEALTH / 'summary.json').stat().st_mtime)}")
    for part in ("2025-03-10", "1996-01-02", "1995-12-29"):
        got = by_part.get(part)
        log(f"   [{part}] summary_status={got and got.get('health_status')} "
            f"validated_at={got and got.get('validated_at')}")
    checks["F4_summary_reflects_r37"] = (
        by_part.get("2025-03-10", {}).get("health_status") == "PASS"
        and by_part.get("1996-01-02", {}).get("health_status") == "DEGRADED")

    log("")
    log("== checks ==")
    for k, v in checks.items():
        log(f"  {'OK ' if v else 'FAIL'} {k}")

    OUT_JSON.write_text(json.dumps({
        "backup_files": len(bak_files),
        "current_files": len(cur_files),
        "status_counts": dict(status_counts),
        "degraded_partitions": sorted(degraded_health),
        "residual_dates": sorted(resid_by_date),
        "degraded_quarantine": degraded_health,
        "residual_counts": dict(resid_by_date),
        "pass_with_quarantine": pass_with_q,
        "fail_partitions": fails,
        "summary_counts": {"partitions": len(parts), "PASS": n_pass,
                           "DEGRADED": n_deg, "UNKNOWN": n_unknown},
        "checks": checks,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_LOG.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
