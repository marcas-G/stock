"""P-5 批算编排端口：flock 单实例 + 断点续跑 + 停滞看门狗 + `_SUCCESS` 事务。

收敛既有三份编排样板（converters.main、extract_sz_cancels.main、run_lob_batch.main）；
各工具只提供 `worker(task)` 与任务列表。失败语义：单元失败记账并继续，收尾报告
列出全部失败单元（调用方据此定退出码）；已完成单元（state）跳过。
实现者：adapters/batch_flock.py（WS6）、tests/_doubles.InlineOrchestrator。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class Task:
    key: str
    payload: Any = None


@dataclass(frozen=True)
class Result:
    key: str
    status: str            # "ok" | "failed" | "skipped"
    metrics: dict | None = None
    error: str | None = None


@dataclass
class BatchReport:
    done: int = 0
    failed: int = 0
    skipped: int = 0
    results: list[Result] = field(default_factory=list)

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if r.status == "failed"]


@runtime_checkable
class BatchOrchestrator(Protocol):
    def run(self, tasks: Sequence[Task], worker: Callable[[Task], Any], *,
            workers: int = 1, stall_s: int | None = None,
            lock_path: Path | None = None, state_path: Path | None = None,
            success_marker: Path | None = None) -> BatchReport: ...
