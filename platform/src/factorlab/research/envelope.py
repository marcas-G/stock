"""统一 JSON 信封与错误码→退出码映射（R31 契约层，spec §4）。

stdout 只输出一个 JSON；日志/进度走 stderr + artifacts.log。
`--pretty` 只影响缩进，不改变契约（仍是同一个 JSON 文档）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1

# 稳定枚举（R31 spec §4）：code → exit code。未知 code 归 INTERNAL(10)。
EXIT_CODES: dict[str, int] = {
    "USAGE": 2,
    "LINT": 3,
    "MEMORY_GUARD": 4,
    "DEAD_SIGNAL": 5,
    "RUN_FAILED": 6,
    "STRATEGY_FAILED": 7,
    "DATA": 8,
    "NOT_FOUND": 9,
    "INTERNAL": 10,
    "BUSY": 11,
}


@dataclass
class Envelope:
    """统一返回信封：ok/data/artifacts/warnings/error（spec §4）。"""

    ok: bool = True
    schema_version: int = SCHEMA_VERSION
    command: str = ""
    data: Any = None
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    warnings: Sequence[str] = field(default_factory=tuple)
    error: Mapping[str, Any] | None = None

    def to_doc(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "schema_version": self.schema_version,
            "command": self.command,
            "data": self.data,
            "artifacts": dict(self.artifacts),
            "warnings": list(self.warnings),
            "error": dict(self.error) if self.error is not None else None,
        }


def ok(
    command: str,
    data: Any = None,
    artifacts: Mapping[str, Any] | None = None,
    warnings: Sequence[str] = (),
) -> Envelope:
    return Envelope(ok=True, command=command, data=data,
                    artifacts=dict(artifacts or {}), warnings=tuple(warnings))


def fail(
    command: str,
    code: str,
    message: str,
    hint: str | None = None,
    log: str | None = None,
) -> Envelope:
    return Envelope(ok=False, command=command, error={
        "code": code, "message": message, "hint": hint, "log": log,
    })


def exit_code(env: Envelope) -> int:
    if env.ok:
        return 0
    code = (env.error or {}).get("code")
    return EXIT_CODES.get(code, EXIT_CODES["INTERNAL"])


def emit(env: Envelope, *, pretty: bool = False) -> int:
    """打印单个 JSON 到 stdout，返回进程退出码（唯一出口）。"""
    print(json.dumps(env.to_doc(), ensure_ascii=False,
                     indent=2 if pretty else None))
    return exit_code(env)
