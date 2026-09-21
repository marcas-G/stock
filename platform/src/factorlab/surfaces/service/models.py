"""作业模型（规格 §6）：类型/状态枚举 + Job 记录 + ULID 生成。

id 形状：26 字符 Crockford Base32 ULID（48-bit 毫秒时间戳 + 80-bit 随机）；
纯 stdlib 实现，无外部依赖。状态机见规格 §6：
`queued → running → {succeeded|failed|cancelled}`，服务重启时 `running → interrupted`
（不自动重跑）。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

JOB_TYPES: tuple[str, ...] = ("factor_run", "compose", "strategy_run", "factor_admit")
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"succeeded", "failed", "cancelled", "interrupted"})


class JobType(str, Enum):
    factor_run = "factor_run"
    compose = "compose"
    strategy_run = "strategy_run"
    factor_admit = "factor_admit"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    interrupted = "interrupted"


def new_job_id() -> str:
    """生成 ULID（时间戳+随机；单调可排序前缀，纯 stdlib）。"""
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    value = (ts << 80) | int.from_bytes(os.urandom(10), "big")
    chars = [_ULID_ALPHABET[value >> (5 * i) & 0x1F] for i in range(25, -1, -1)]
    return "".join(chars)


def now_iso() -> str:
    """UTC ISO-8601（毫秒精度）；字符串可直接排序。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Job:
    """作业记录（字段与规格 §6 一一对应）。"""

    id: str
    type: str
    status: str
    params: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    error: str | None = None
    log_path: str | None = None
    result_path: str | None = None
    image_ref: str | None = None
    dataset_version: str | None = None
    pid: int | None = None

    def to_doc(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "params": dict(self.params),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "error": self.error,
            "log_path": self.log_path,
            "result_path": self.result_path,
            "image_ref": self.image_ref,
            "dataset_version": self.dataset_version,
            "pid": self.pid,
        }
