#!/usr/bin/env python3
"""R31.2 突变检验：4 处改实现（透传丢参/health 硬编码/事件不落盘/门遇漂移），
逐个应使目标测试失败（KILLED）后复原。

用法（平台 venv；从 stock 根跑）：
  platform/.venv/bin/python governance/evidence/verification/R31/research-api/r31.2-perf-knobs/run_mutations.py
产物：04-mutation-*.txt / 04-mutation-summary.txt；退出码非 0 = 有突变存活。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[5]                       # .../stock
PLATFORM = REPO / "platform"
FACTOR = PLATFORM / "src" / "factorlab" / "research" / "factor.py"
HEALTH = PLATFORM / "src" / "factorlab" / "research" / "health.py"
CACHE = PLATFORM / "src" / "factorlab" / "adapters" / "read" / "chunk_cache.py"
HANDBOOK = REPO / "knowledge" / "handbooks" / "research-agent-manual.md"

MUTATIONS = [
    {
        "name": "m1-drop-pass-through",
        "file": FACTOR,
        "old": (
            '                profile=getattr(args, "profile", None),'
            '          # R31.2 透传\n'
            '                chunk_workers=getattr(args, "chunk_workers", None) or 1,\n'
            '                # R31.2：--no-read-cache → False 强制关；缺省/None → env 默认开\n'
            '                read_cache=False if getattr(args, "no_read_cache", None) else None,\n'
        ),
        "new": (
            '                chunk_workers=getattr(args, "chunk_workers", None) or 1,\n'
            '                read_cache=None,  # mutation: 丢透传\n'
        ),
        "tests": ("tests/test_research_perf_knobs.py",),
    },
    {
        "name": "m2-health-hardcode-zero",
        "file": HEALTH,
        "old": (
            '    stats = manifest_stats()\n'
            '    return ({key: stats[key] for key in _RC_KEYS}, stats.get("degraded"))\n'
        ),
        "new": (
            '    stats = manifest_stats()\n'
            '    return ({"dir": stats["dir"], "entries": 0, "size_bytes": 0,\n'
            '             "hits": 0, "misses": 0, "fallbacks": 0}, None)  # mutation\n'
        ),
        "tests": ("tests/test_research_perf_knobs.py",),
    },
    {
        "name": "m3-events-not-persisted",
        "file": CACHE,
        "old": '                _events(manifest)["hits"] += 1\n',
        "new": "",
        "tests": ("tests/test_read_cache.py", "tests/test_research_perf_knobs.py"),
    },
    {
        "name": "m4-gate-catches-handbook-drift",
        "file": HANDBOOK,
        "old": "## 错误处理\n",
        "new": "`flab factor lint research/factor/x.yaml --profile`（mutation 漂移行）\n\n## 错误处理\n",
        "tests": ("tests/test_doc_paths_exist.py",),
    },
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    failures: list[str] = []
    for mut in MUTATIONS:
        path: Path = mut["file"]
        original = path.read_text(encoding="utf-8")
        before = _sha(path)
        assert mut["old"] in original, f"{mut['name']}: 锚点未命中 {path}"
        path.write_text(original.replace(mut["old"], mut["new"], 1),
                        encoding="utf-8")
        try:
            proc = subprocess.run(
                [str(PLATFORM / ".venv" / "bin" / "python"), "-m", "pytest",
                 "-q", *mut["tests"]],
                cwd=str(PLATFORM), capture_output=True, text=True)
        finally:
            path.write_text(original, encoding="utf-8")
        assert _sha(path) == before, f"{mut['name']}: 复原校验失败"
        killed = proc.returncode != 0
        out = (f"# mutation: {mut['name']}\n"
               f"# file: {path.relative_to(REPO)}\n"
               f"# tests: {' '.join(mut['tests'])}\n"
               f"# exit={proc.returncode} → {'KILLED' if killed else 'SURVIVED'}\n\n"
               + proc.stdout + proc.stderr)
        (HERE / f"04-mutation-{mut['name']}.txt").write_text(out, encoding="utf-8")
        print(f"{mut['name']}: {'KILLED' if killed else 'SURVIVED'}")
        if not killed:
            failures.append(mut["name"])
    (HERE / "04-mutation-summary.txt").write_text(
        "\n".join(f"{m['name']}: "
                  f"{'KILLED' if m['name'] not in failures else 'SURVIVED'}"
                  for m in MUTATIONS) + "\n", encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
