"""P-5 批算编排端口：flock 单实例 + 断点续跑 + 停滞看门狗 + `_SUCCESS` 事务。

收敛既有三份编排样板（converters.main、extract_sz_cancels.main、run_lob_batch.main）；
各工具只提供 `worker(task)` 与任务列表。失败语义：单元失败记账并继续，收尾报告
列出全部失败单元（调用方据此定退出码）；已完成单元（state）跳过。
实现者：adapters/batch_flock.py（WS6）、tests/_doubles.InlineOrchestrator。

**R10 扩展缝**（三份产量循环实测共同需要的最小集；全部可选、缺省不变）：
`max_inflight`（在飞上限，worker 慢时流水线化）、`mp_context`（如 `"spawn"`——fork 会
连父进程的大缓冲一起复制，产量循环必须用 spawn）、`initializer`/`initargs`（worker 预热）、
`stall_policy`/`stall_strikes`（`fail` 缺省 | `requeue` 退回队列重试）、
`on_result(task, result_or_exc)`（父进程逐结果回调——"worker 算、父进程写"的数据流）、
`throttle()`（派单闸门：False 则本轮不派新单）、`on_tick`/`on_tick_s`（周期回调）、
`pool_hook(pool)`（池创建即回调——审计要读 worker 进程 RSS 时用）。
**未纳入**：内存低水位派单闸门与周期性审计回调（run_lob_batch 专有，绑 16GB 内存纪律与
runbook 取证，见 `docs/pending-items.md#14`）。
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


# 注：vulture 会把下面协议方法的形参报成"未使用变量"——协议**只有签名没有实现**，
# 形参即契约词汇（本模块是 ports/，见 platform/CLAUDE.md 分层），保留。
@runtime_checkable
class BatchOrchestrator(Protocol):
    def run(self, tasks: Sequence[Task], worker: Callable[[Task], Any], *,
            workers: int = 1, stall_s: int | None = None,
            lock_path: Path | None = None, state_path: Path | None = None,
            success_marker: Path | None = None,
            max_inflight: int | None = None, mp_context: str | None = None,
            initializer: Callable[..., None] | None = None, initargs: tuple = (),
            stall_policy: str = "fail", stall_strikes: int = 3,
            on_result: Callable[[Task, Any], None] | None = None,
            throttle: Callable[[], bool] | None = None,
            on_tick: Callable[[], None] | None = None, on_tick_s: float | None = None,
            pool_hook: Callable[[Any], None] | None = None) -> BatchReport: ...
