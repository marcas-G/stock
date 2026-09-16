#!/usr/bin/env python3
"""R07-MIG-I2 (b): 档案旧坐标机械替换（仅 tracked 且工作区/索引均未修改的文件）。

替换规则（按序）：
  R1 `results/_mine_round_` → `runs/platform/_mine_rounds/_mine_round_`（反引号指针；先于 R2）
  R2 `results/`（反引号后） → `runs/platform/`
  R3 `platform/results/` → `runs/platform/`
  R4 `--panel results/`（代码块命令行形态，非反引号） → `--panel runs/platform/`

排除：`knowledge/dossiers/factors/README.md`（映射/历史事实文档，非档案；行级豁免见 gates.sh）。
安全性：只改 tracked 且 `git diff` / `git diff --cached` 均干净的文件；
        挖矿在途（M/??/staged）文件跳过并列名。

用法：bulk_replace_oldcoord.py [--apply]   # 默认 dry-run
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[5]  # stock/
EXCLUDE = {"knowledge/dossiers/factors/README.md"}

RULES = [
    ("R1 mine_round", re.compile(r"`results/_mine_round_"), "`runs/platform/_mine_rounds/_mine_round_"),
    ("R2 backticked", re.compile(r"`results/"), "`runs/platform/"),
    ("R3 platform", re.compile(r"platform/results/"), "runs/platform/"),
    ("R4 panel arg", re.compile(r"--panel results/"), "--panel runs/platform/"),
]


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "knowledge/dossiers"],
                         cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def clean(path: str) -> bool:
    for args in (["git", "diff", "--quiet", "--", path],
                 ["git", "diff", "--cached", "--quiet", "--", path]):
        if subprocess.run(args, cwd=ROOT).returncode != 0:
            return False
    return True


def main() -> int:
    apply = "--apply" in sys.argv
    changed = skipped = 0
    total_hits = 0
    for rel in tracked():
        if rel in EXCLUDE:
            continue
        p = ROOT / rel
        text = p.read_text(encoding="utf-8")
        new = text
        hits = 0
        for name, pat, repl in RULES:
            new, n = pat.subn(repl, new)
            hits += n
        if new == text:
            continue
        if not clean(rel):
            print(f"  SKIP(在途 M/??/staged): {rel}")
            skipped += 1
            continue
        print(f"  {'WRITE' if apply else 'WOULD'} {rel}  ({hits} 处)")
        if apply:
            p.write_text(new, encoding="utf-8")
        changed += 1
        total_hits += hits
    print(f"== {'写入' if apply else 'dry-run'}：文件 {changed}、替换 {total_hits} 处、跳过在途 {skipped} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
