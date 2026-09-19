"""测试文本工具：CLI 输出在 CI（GITHUB_ACTIONS）下会被 rich 强制 ANSI 化，
裸子串断言（如 `"--universe" in result.stdout`）会因转义序列插入而误红——
帮助文本/长文本断言统一先过 `strip_ansi`（R31-ci-fix）。
"""
from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)
