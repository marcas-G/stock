#!/usr/bin/env python3
"""R06-MIG-I3 盘点：根 results/ vs runs/platform/ 逐目录对照（只读，不删不移）。

对每个同名目录：比较文件集合 + 逐文件 sha256 + 大小 + mtime，分类：
  IDENTICAL   两副本逐文件 sha256 全等（文件集合相同）→ 根为迁移前 legacy 同内容副本
  ROOT_SUBSET 根全部文件在 runs 中逐值相等，但 runs 更全（额外文件）→ 根为旧态
  RUNS_SUBSET 反向（根更全）
  DIFFER      共有文件内容不同或各有独有文件 → 需人工裁决
  ROOT_ONLY   只存在于根 → 候选移入 runs/platform（单点）
输出：人读表 + JSON（含逐文件 sha256，供删除/移动证据）。
用法：python 08-mig-i3-inventory.py <json-out>
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path("results")
RUNS = Path("runs/platform")


def sha256(p: Path, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def tree_info(d: Path) -> dict:
    info = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            st = p.stat()
            info[str(p.relative_to(d))] = {
                "size": st.st_size,
                "mtime": st.st_mtime,
                "sha256": sha256(p),
            }
    return info


def summarize(info: dict) -> dict:
    return {
        "files": len(info),
        "bytes": sum(v["size"] for v in info.values()),
        "max_mtime": max((v["mtime"] for v in info.values()), default=0),
    }


def mfmt(ts: float) -> str:
    if not ts:
        return "-"
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def main() -> None:
    out_json = sys.argv[1]
    report = {}
    for entry in sorted(ROOT.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_file():  # 期外散落文件（本轮应无：_mine_round_* 已迁）
            st = entry.stat()
            report[entry.name] = {
                "cls": "STRAY_FILE", "root": {"files": 1, "bytes": st.st_size,
                                              "max_mtime": st.st_mtime,
                                              "sha256": sha256(entry)},
            }
            continue
        other = RUNS / entry.name
        ri = tree_info(entry)
        if other.is_dir():
            oi = tree_info(other)
            common = set(ri) & set(oi)
            only_root = sorted(set(ri) - set(oi))
            only_runs = sorted(set(oi) - set(ri))
            diff = sorted(f for f in common if ri[f]["sha256"] != oi[f]["sha256"])
            if not only_root and not only_runs and not diff:
                cls = "IDENTICAL"
            elif not only_root and not diff:
                cls = "ROOT_SUBSET"
            elif not only_runs and not diff:
                cls = "RUNS_SUBSET"
            else:
                cls = "DIFFER"
            report[entry.name] = {
                "cls": cls,
                "root": summarize(ri),
                "runs": summarize(oi),
                "only_root": only_root,
                "only_runs": only_runs,
                "diff_files": diff,
                "files_root": {k: v["sha256"] for k, v in ri.items()},
                "files_runs": {k: v["sha256"] for k, v in oi.items()},
            }
        else:
            report[entry.name] = {"cls": "ROOT_ONLY", "root": summarize(ri),
                                  "files_root": {k: v["sha256"] for k, v in ri.items()}}

    json.dump(report, open(out_json, "w"), indent=1, sort_keys=True)

    print(f"{'dir':28s} {'class':11s} {'root':>18s} {'runs':>18s}  files(root/runs) mtime(root/runs)")
    for name, r in report.items():
        rs = r["root"]
        os_ = r.get("runs")
        rtxt = f"{rs['files']}f {rs['bytes']/1e6:.0f}MB"
        otxt = f"{os_['files']}f {os_['bytes']/1e6:.0f}MB" if os_ else "-"
        print(f"{name:28s} {r['cls']:11s} {rtxt:>18s} {otxt:>18s}  "
              f"{rs['files']}/{os_['files'] if os_ else 0}          "
              f"{mfmt(rs['max_mtime'])}/{mfmt(os_['max_mtime']) if os_ else '-'}")
        if os_:
            if r["only_root"]:
                print(f"{'':30s}only_root={r['only_root']}")
            if r["only_runs"]:
                print(f"{'':30s}only_runs={r['only_runs']}")
            if r["diff_files"]:
                print(f"{'':30s}diff={r['diff_files']}")


if __name__ == "__main__":
    main()
