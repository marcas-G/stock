#!/usr/bin/env python3
"""R06 独立复核：R24 migration-r04.md §3 旧→新映射总表（14 行）逐项实测。只读。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("/data/students/gaolei/stock")

# (旧路径 glob, 新路径, commit, 备注)
ROWS = [
    ("platform/docs/{interface,catalog,data-ops-playbook,teajoin-guide}.md",
     "knowledge/contracts/", "e3d2887", "行 1"),
    ("platform/docs/superpowers/{plans,specs}", "knowledge/design/platform/", "305b757", "行 2"),
    ("research/docs/superpowers/{plans,specs}", "knowledge/design/research/", "305b757", "行 3"),
    ("research/docs/{factors,strategies,factor-mining-playbook.md}", "knowledge/dossiers/", "6aaefad", "行 4"),
    ("docs/reviews/2026-09-15-{open-operators,minute-execution}/", "knowledge/design/workspace/", "305b757", "行 5"),
    ("docs/{data-map,directory-conventions,pending-items,archive-policy,traceability-matrix,remote-cleanup-checklist,workspace-p0p8}.md",
     "governance/workspace/", "f238ea5", "行 6"),
    ("docs/递归式需求驱动系统工程开发手册：NASA Systems Engineering × V-Model.md",
     "knowledge/handbooks/", "6aaefad", "行 7"),
    ("docs/index/{factors,strategies}.md", "knowledge/index/", "6aaefad", "行 8"),
    ("docs/verification/", "governance/evidence/verification/", "583217f", "行 9"),
    ("docs/reviews/（余下）", "governance/evidence/reviews/", "583217f", "行 10"),
    ("scripts/*", "governance/ops/", "227acd9", "行 11"),
    ("platform/results/", "runs/platform/", "024e45f", "行 12"),
    ("projects/ashare_alpha3", "_archive/2026-09-16-ashare-alpha3/", "ca8c6a5", "行 14"),
]

NEW_SPOT = {
    "knowledge/contracts/": ["interface.md", "catalog.md", "data-ops-playbook.md", "teajoin-guide.md"],
    "knowledge/design/platform/": ["specs", "plans"],
    "knowledge/design/research/": [],
    "knowledge/dossiers/": ["factors", "strategies"],
    "knowledge/design/workspace/": ["2026-09-15-open-operators", "2026-09-15-minute-execution",
                                    "2026-09-16-strategy-decomposition"],
    "governance/workspace/": ["data-map.md", "directory-conventions.md", "pending-items.md",
                              "archive-policy.md", "traceability-matrix.md",
                              "remote-cleanup-checklist.md", "workspace-p0p8.md"],
    "knowledge/handbooks/": [],
    "knowledge/index/": ["factors.md", "strategies.md"],
    "governance/evidence/verification/": ["R21", "R24", "R27", "R28"],
    "governance/evidence/reviews/": ["findings.md", "r01-2026-09-15-strict-review",
                                     "r04-efficiency-2026-09-16"],
    "governance/ops/": ["gates.sh", "check_reviews.py"],
    "runs/platform/": ["r12_smoke"],
    "_archive/2026-09-16-ashare-alpha3/": [],
}
OLD_ABSENT = [
    "platform/docs/interface.md", "platform/docs/catalog.md",
    "platform/docs/data-ops-playbook.md", "platform/docs/teajoin-guide.md",
    "platform/docs/superpowers", "research/docs/superpowers",
    "research/docs/factors", "research/docs/strategies",
    "research/docs/factor-mining-playbook.md",
    "docs/reviews", "docs/verification", "docs/index", "docs/data-map.md",
    "docs/pending-items.md", "docs/directory-conventions.md",
    "scripts", "platform/results", "projects/ashare_alpha3",
]


def git_has(sha: str) -> bool:
    r = subprocess.run(["git", "-C", str(REPO), "cat-file", "-t", sha],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "commit"


def main() -> int:
    print("# R24 映射总表独立复核（migration-r04.md §3）")
    print()
    print("## 新路径存在性（spot-check）")
    bad = 0
    for new, spots in NEW_SPOT.items():
        p = REPO / new
        ok = p.exists()
        print(f"  {'OK ' if ok else 'MISS'} {new} (#{len(spots)} spot)")
        if not ok:
            bad += 1
        for s in spots:
            sp = p / s
            if not sp.exists():
                print(f"      MISS spot: {new}{s}")
                bad += 1
    print()
    print("## 旧路径应已消失（留壳/冻结除外）")
    for old in OLD_ABSENT:
        p = REPO / old
        exists = p.exists()
        marker = "REMAIN" if exists else "GONE  "
        if exists:
            isdir = "dir" if p.is_dir() else "file"
            print(f"  {marker} {old} ({isdir})")
        else:
            print(f"  {marker} {old}")
    print()
    print("## 提交 SHA 存在性")
    for _old, _new, sha, note in ROWS:
        ok = git_has(sha)
        print(f"  {'OK ' if ok else 'MISS'} {sha} ({note})")
        if not ok:
            bad += 1
    print()
    print(f"硬失败数：{bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
