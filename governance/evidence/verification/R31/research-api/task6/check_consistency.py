#!/usr/bin/env python3
"""R31 Task6：describe / CLI help / 手册 三处一致性检查（spec §6 可发现性）。

用法：platform/.venv/bin/python governance/evidence/verification/R31/research-api/task6/check_consistency.py
（须用平台 venv——脚本 import factorlab.research；flab 从 PATH 取）
退出码非 0 = 存在漂移（打印逐项判定与命令数）。

判定：
1. registry 单点 == `flab describe --json` 的命令集；
2. describe 内置 exit_codes == envelope.EXIT_CODES（错误码不漂移）；
3. CLI help（`flab --help`）覆盖 registry 全部顶层组；
4. 手册中每个 `flab ...` 命令 ∈ registry；且 ≥10 条示例、覆盖 7 个关键命令。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[5]  # .../stock
sys.path.insert(0, str(REPO / "platform" / "src"))

from factorlab.research import COMMANDS  # noqa: E402
from factorlab.research.envelope import EXIT_CODES  # noqa: E402

_FLAB = re.compile(r"\bflab((?:\s+[a-z][a-z0-9_-]*){1,3})")


def _run(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def _manual_commands(text: str) -> tuple[set[str], list[str]]:
    resolved: set[str] = set()
    unresolved: list[str] = []
    for match in _FLAB.finditer(text):
        words = match.group(1).split()
        for n in range(min(3, len(words)), 0, -1):
            candidate = ".".join(words[:n])
            if candidate in COMMANDS:
                resolved.add(candidate)
                break
        else:
            unresolved.append(f"flab {' '.join(words)}")
    return resolved, unresolved


def main() -> int:
    describe = json.loads(_run("flab", "describe", "--json"))
    describe_cmds = set(describe["data"]["commands"])
    help_text = _run("flab", "--help")
    manual_text = (REPO / "knowledge" / "handbooks" /
                   "research-agent-manual.md").read_text(encoding="utf-8")
    manual_cmds, unresolved = _manual_commands(manual_text)

    registry = set(COMMANDS)
    groups = {name.split(".")[0] for name in registry}
    missing_help = sorted(g for g in groups if g not in help_text)
    missing_manual = sorted(c for c in registry if c in
                            {"health", "data.daily", "factor.run", "factor.admit",
                             "strategy.run", "report.url", "study.run"}
                            and c not in manual_cmds)

    checks = [
        ("registry == describe 命令集", registry == describe_cmds),
        ("describe exit_codes == EXIT_CODES", describe["data"]["exit_codes"] == EXIT_CODES),
        ("CLI help 覆盖 registry 顶层组", not missing_help),
        ("手册命令 ⊂ registry（无漂移）", not unresolved),
        ("手册 ≥10 条常用命令", len(manual_cmds) >= 10),
        ("手册覆盖 7 个关键命令", not missing_manual),
    ]
    for label, passed in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {label}")
    print(f"  registry={len(registry)} 条 | describe={len(describe_cmds)} 条 | "
          f"help 顶层组={len(groups)} | 手册命令={len(manual_cmds)} 条")
    if missing_help:
        print(f"  help 缺组: {missing_help}")
    if unresolved:
        print(f"  手册漂移: {unresolved}")
    if missing_manual:
        print(f"  手册缺关键命令: {missing_manual}")
    return 0 if all(p for _l, p in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
