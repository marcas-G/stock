#!/usr/bin/env python3
"""R30 fix 波：v1/v2 档案注记待办清单 v2（只读，不写档案——挖矿在途由协调者处理）。

背景：D7 重跑/删除后旧清单（eval-v2-task0-5/pending_annotations.py）的「74 runs /
46 档案」口径已过期。本脚本重生成实际清单并区分两类：

  a) run 仍 v1 未重跑（evaluation 无 `version` 键）：档案需注
     「spread 为 v1 口径（负=自洽）；数值为 v1 宽口径」；
  b) run 已重跑为 v2（有 `version=2`）但档案值写于 v1 时代（如 low_vol_20d）：
     档案数值/符号**待按 v2 产物刷新**（不能沿用 spread 旧符号读法）。

关联启发式与旧脚本一致：档案正文含 `runs/platform/<name>/summary.json` 或档案
文件名与运行同名。`git status` 非 clean 的档案 = 挖矿在途，本轮禁动。

用法（仓库根）：
  platform/.venv/bin/python governance/evidence/verification/R30/eval-v2-fix/pending_annotations_v2.py
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


def classify() -> tuple[list[str], list[tuple[str, str]]]:
    """返回 (v1_runs, v2_runs[(name, frequency)])——按 summary.evaluation 判定。"""
    v1, v2 = [], []
    for d in sorted(RUNS.iterdir()):
        summary_path = d / "summary.json"
        if not summary_path.is_file():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        ev = summary.get("evaluation")
        if isinstance(ev, dict) and ev.get("version") == 2:
            v2.append((d.name, str(ev.get("frequency", "?"))))
        else:
            v1.append(d.name)
    return v1, v2


def match(name: str, dossier_text: dict[Path, str]) -> list[Path]:
    needle = f"runs/platform/{name}/summary.json"
    return [f for f, text in dossier_text.items()
            if needle in text or f.stem == name]


def main() -> None:
    v1_runs, v2_runs = classify()
    dossier_text = {f: f.read_text(encoding="utf-8")
                    for f in sorted(DOSSIERS.glob("*/*.md"))}

    a_pending: dict[Path, list[str]] = {}
    a_unmatched: list[str] = []
    for name in v1_runs:
        hits = match(name, dossier_text)
        if hits:
            for f in hits:
                a_pending.setdefault(f, []).append(name)
        else:
            a_unmatched.append(name)

    b_pending: dict[Path, list[str]] = {}
    b_unmatched: list[str] = []
    for name, freq in v2_runs:
        hits = match(name, dossier_text)
        if hits:
            for f in hits:
                b_pending.setdefault(f, []).append(f"{name}({freq})")
        else:
            b_unmatched.append(f"{name}({freq})")

    print("# v1/v2 口径档案注记待办清单（R30 fix 波重生成，只读脚本）")
    print()
    print(f"- 运行目录总数：{len(v1_runs) + len(v2_runs)}")
    print(f"- a) run 仍 v1 未重跑：{len(v1_runs)} 个（关联档案 {len(a_pending)} 份；"
          f"未匹配 {len(a_unmatched)} 个）")
    print(f"- b) run 已 v2（档案值写于 v1 需刷新）：{len(v2_runs)} 个"
          f"（关联档案 {len(b_pending)} 份；未匹配 {len(b_unmatched)} 个）")
    print()
    print("口径：a) 档案注「spread 为 v1 口径（负=自洽）」；b) 档案数值/符号按 v2 "
          "产物刷新（spread 正=好、版本字段 v2）。**不重算历史 summary**（interface 迁移节）。")
    print("下表 `git status` 非 clean = 挖矿在途，本轮禁动。")
    print()

    print("## a) 仍为 v1 的 run（未重跑）")
    print()
    print("| # | 档案路径 | 关联 v1 运行 | git status |")
    print("|---|---|---|---|")
    for i, (f, names) in enumerate(sorted(a_pending.items()), 1):
        rel = f.relative_to(ROOT)
        print(f"| {i} | `{rel}` | {', '.join(f'`{n}`' for n in names)} | "
              f"{git_status(str(rel))} |")
    if a_unmatched:
        print()
        print("未匹配档案的 v1 运行（无档案引用；核对其档案命名）："
              + "、".join(f"`{n}`" for n in a_unmatched))

    print()
    print("## b) 已 v2 的 run（档案数值待刷新）")
    print()
    print("| # | 档案路径 | 关联 v2 运行（frequency） | git status |")
    print("|---|---|---|---|")
    for i, (f, names) in enumerate(sorted(b_pending.items()), 1):
        rel = f.relative_to(ROOT)
        print(f"| {i} | `{rel}` | {', '.join(f'`{n}`' for n in names)} | "
              f"{git_status(str(rel))} |")
    if b_unmatched:
        print()
        print("未匹配档案的 v2 运行（无档案引用；多为变体/探针）："
              + "、".join(f"`{n}`" for n in b_unmatched))


if __name__ == "__main__":
    main()
