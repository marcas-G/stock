#!/usr/bin/env python
"""R32 终审修复 N1 证据探针：阶段链子进程缺省拿到 FACTORLAB_DATA_BACKEND=ch。

不真跑 CH：用真实 `cli._stage_env()` + 真实 `stages.run_cmd` 起子进程打印它看到的
后端；再验证调用方显式覆盖不被改写。证明 health/ingest 子进程不再因缺 backend 必败。

退出码：0 全过；1 有偏差。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(_REPO / "platform" / "tools"))

from pan_update import cli, stages  # noqa: E402


def main() -> int:
    os.environ.pop("FACTORLAB_DATA_BACKEND", None)
    checks: dict[str, bool] = {}

    env = cli._stage_env()
    print("cli._stage_env() backend =", repr(env.get("FACTORLAB_DATA_BACKEND")),
          "| arena =", repr(env.get("MALLOC_ARENA_MAX")), flush=True)
    checks["default_ch"] = env.get("FACTORLAB_DATA_BACKEND") == "ch"

    lines: list[str] = []

    def log(line: str) -> None:
        lines.append(line)
        print("child>", line, flush=True)

    stages.run_cmd(
        [sys.executable, "-c",
         "import os; print('FACTORLAB_DATA_BACKEND=' + "
         "repr(os.environ.get('FACTORLAB_DATA_BACKEND')))"],
        log=log, env=env)
    checks["child_sees_ch"] = any("FACTORLAB_DATA_BACKEND='ch'" in ln for ln in lines)

    os.environ["FACTORLAB_DATA_BACKEND"] = "duckdb"
    env2 = cli._stage_env()
    print("显式覆盖 ->", repr(env2.get("FACTORLAB_DATA_BACKEND")), flush=True)
    checks["explicit_not_overwritten"] = (
        env2.get("FACTORLAB_DATA_BACKEND") == "duckdb")
    os.environ.pop("FACTORLAB_DATA_BACKEND", None)

    print("checks =", checks)
    print("N1 PROBE OK" if all(checks.values()) else "N1 PROBE FAIL")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
