#!/usr/bin/env python3
"""R30 Task5：v1 口径档案注记待办清单（只读，不写档案——挖矿在途，由协调者统一处理）。

判据：`runs/platform/*/summary.json` 的 evaluation 无 `version` 键（≡ v1，interface 迁移节）；
档案关联 = 内容含 `runs/platform/<name>/summary.json` 或档案文件名与运行同名（启发式）。
输出 markdown 到 stdout；`M`/`??` 标记的档案如在途（挖矿未提交）禁止并行改动。

用法（仓库根）：platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-task0-5/pending_annotations.py
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
RUNS = ROOT / "runs" / "platform"
DOSSIERS = ROOT / "knowledge" / "dossiers" / "factors"


def git_status(rel: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain", "--", rel],
        capture_output=True, text=True)
    return proc.stdout.strip() or "clean"


def main() -> None:
    v1_runs = []
    for d in sorted(RUNS.iterdir()):
        summary_path = d / "summary.json"
        if not summary_path.is_file():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        ev = summary.get("evaluation")
        if isinstance(ev, dict) and ev.get("version") != 2:
            v1_runs.append(d.name)

    dossier_text = {f: f.read_text(encoding="utf-8") for f in sorted(DOSSIERS.glob("*/*.md"))}
    pending: dict[Path, list[str]] = {}
    unmatched: list[str] = []
    for name in v1_runs:
        needle = f"runs/platform/{name}/summary.json"
        hits = [f for f, text in dossier_text.items()
                if needle in text or f.stem == name]
        if hits:
            for f in hits:
                pending.setdefault(f, []).append(name)
        else:
            unmatched.append(name)

    print(f"# v1 口径档案注记待办清单（R30 Task5，生成于 {Path(__file__).name}）")
    print()
    print(f"- v1 运行（evaluation 无 version 键）：{len(v1_runs)} 个")
    print(f"- 关联档案（待注记「spread 为 v1 口径（负=自洽）」）：{len(pending)} 份")
    print(f"- 未匹配到档案的 v1 运行：{len(unmatched)} 个")
    print()
    print("说明：档案注记**不重算**（interface 迁移节），由协调者在挖矿在途批次收尾后统一补；")
    print("下表 `git status` 非 clean 的档案 = 挖矿在途，本轮（eval-v2 Task 0/5）禁动。")
    print()
    print("| # | 档案路径 | 关联 v1 运行 | git status |")
    print("|---|---|---|---|")
    for i, (f, names) in enumerate(sorted(pending.items()), 1):
        rel = f.relative_to(ROOT)
        print(f"| {i} | `{rel}` | {', '.join(f'`{n}`' for n in names)} | {git_status(str(rel))} |")
    if unmatched:
        print()
        print("## 未匹配档案的 v1 运行（无档案引用；核对其档案命名）")
        print()
        for n in unmatched:
            print(f"- `{n}`")


if __name__ == "__main__":
    main()
