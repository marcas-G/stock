#!/usr/bin/env python
"""R37 quantresearch 目录公约检查器：根白名单 / scratch 命名 / results manifest / __pycache__。

只报告不修改（无 --fix）。退出码：0=无 error（warning 不失败）；1=有 error；root 不存在=SKIP(0)。
用法：platform/.venv/bin/python governance/ops/research_tidy.py [--root DIR] [--allow-missing-manifest]
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import NamedTuple

DEFAULT_ROOT = "/data/students/gaolei/quantresearch"
ROOT_FILES = ("README.md", "CONVENTIONS.md")
ROOT_REPORT_GLOB = "REPORT*.md"
RESULTS = "results"
SCRATCH = "scratch"
MANIFEST = "manifest.json"
SCRATCH_RE = re.compile(r"^\d{8}_[A-Za-z0-9][A-Za-z0-9_.-]*\.py$")


class Finding(NamedTuple):
    level: str  # "error" | "warning"
    path: str
    message: str


def resolve_root(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value)
    env = os.environ.get("QUANTRESEARCH_ROOT")
    if env:
        return Path(env)
    return Path(DEFAULT_ROOT)


def _root_allowed(name: str) -> bool:
    return name in ROOT_FILES or Path(name).match(ROOT_REPORT_GLOB)


def check_root(root: Path) -> list[Finding]:
    out = []
    for p in sorted(root.iterdir()):
        if p.is_dir():
            continue
        if not _root_allowed(p.name):
            out.append(Finding(
                "error", str(p),
                f"根白名单外裸文件：{p.name}（仅允许 {'、'.join(ROOT_FILES)}、{ROOT_REPORT_GLOB}）"))
    return out


def check_scratch(root: Path) -> list[Finding]:
    out = []
    s = root / SCRATCH
    if not s.is_dir():
        return out
    for p in sorted(s.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        if not SCRATCH_RE.match(p.name):
            out.append(Finding(
                "error", str(p), f"scratch 命名不合规：{p.name}（应为 YYYYMMDD_<topic>.py）"))
    return out


def check_results(root: Path, allow_missing_manifest: bool = False) -> list[Finding]:
    out = []
    r = root / RESULTS
    if not r.is_dir():
        return out
    for p in sorted(r.iterdir()):
        if p.is_file():
            out.append(Finding(
                "error", str(p), f"{RESULTS}/ 根下裸文件：{p.name}（应归入 campaign 目录）"))
        elif p.is_dir() and not (p / MANIFEST).is_file():
            level = "warning" if allow_missing_manifest else "error"
            hint = "（--allow-missing-manifest：暂缓）" if allow_missing_manifest else ""
            out.append(Finding(level, str(p / MANIFEST), f"{RESULTS}/{p.name}/ 缺 {MANIFEST}{hint}"))
    return out


def check_pycache(root: Path) -> list[Finding]:
    out = []
    for p in sorted(root.rglob("__pycache__")):
        if p.is_dir():
            out.append(Finding("warning", str(p), "__pycache__ 存在（可再生缓存，建议删除）"))
    return out


def findings(root: Path, *, allow_missing_manifest: bool = False) -> list[Finding]:
    fs = (check_root(root) + check_scratch(root)
          + check_results(root, allow_missing_manifest) + check_pycache(root))
    return sorted(fs, key=lambda f: (f.level != "error", f.path))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="quantresearch 目录公约检查（只报告，不修改）")
    ap.add_argument("--root", default=None)
    ap.add_argument("--allow-missing-manifest", action="store_true")
    args = ap.parse_args(argv)
    root = resolve_root(args.root)
    if not root.is_dir():
        print(f"SKIP: root 不存在：{root}")
        return 0
    fs = findings(root, allow_missing_manifest=args.allow_missing_manifest)
    n_err = sum(1 for f in fs if f.level == "error")
    for f in fs:
        print(f"[{f.level.upper()}] {f.path}: {f.message}")
    print(f"root={root} errors={n_err} warnings={len(fs) - n_err}")
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
