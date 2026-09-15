"""P-5 批算编排真实现：flock 单实例 + 进程池 + 停滞看门狗 + 断点 + `_SUCCESS`。

`ports/batch.py` 从 WS6 起声明"实现者：adapters/batch_flock.py"，但该模块长期不存在
（No Orphan 欠账，见 `docs/pending-items.md#14`）；本模块**只兑现声明**，不改任何工具：
研究侧三份编排样板（converters / extract_sz_cancels / run_lob_batch）的切换仍待专项轮次
（它们的热路径需要字节级重跑对照，且各自的 manifest/月门语义不同）。

语义（与 `tests/_doubles.InlineOrchestrator` 对齐，另加只有真实现才有的四条）：
- **失败记账不中断**：单元异常 → `Result(status="failed")`，其余照跑；
- **断点**：`state_path` 里的 key 直接 `skipped`，每完成一个就原子写回（**保留既有 key**）；
- **单写者**：`lock_path` 被占 → `BatchLocked`，且**一个任务都不跑**（不半途进批算）；
- **看门狗**：`stall_s` 秒内无任何完成 → 在飞的单元记账为 `stalled` 并立即返回
  （不静默挂死；在跑的子进程 SIGKILL 回收，做法与 run_lob_batch 生产实现同源）——
  调用方可凭 state 重跑，这也是 state 必须保留既有 key 的原因；
- **`_SUCCESS`**：全部单元无失败才落标记（空批视为无失败），有失败**不落**——否则消费侧
  会把半批当整批完成。

调用方的退出码约定由调用方决定（工具侧惯例：锁占用 3、有失败 1）。
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import tempfile
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, Sequence

from factorlab.ports.batch import BatchReport, Result, Task

__all__ = ["BatchFlock", "BatchLocked"]


class BatchLocked(RuntimeError):
    """单写者锁被占用（同一目标目录已有批算在跑）。"""


def _write_state(path: Path, done: list[str]) -> None:
    """原子写断点（tmp + fsync + os.replace + 目录 fsync），不留半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"done": done}, f, ensure_ascii=False, sort_keys=True, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_state(path: Path) -> list[str]:
    """读断点：接受 {"done": [...]} 与裸列表两种历史形态；坏文件不静默吞（点名抛出）。"""
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    data = json.loads(text)
    done = data.get("done", []) if isinstance(data, dict) else data
    if not isinstance(done, list):
        raise ValueError(f"state 文件的 done 必须是列表: {path}（收到 {type(done).__name__}）")
    return [str(k) for k in done]


def _kill_workers(ex: ProcessPoolExecutor) -> None:
    """停滞时强杀在跑的子进程。

    `shutdown(wait=False, cancel_futures=True)` 只取消**未启动**的任务，在跑的单元照跑
    （端口语义不允许我们挂死等它）。做法与 `run_lob_batch` 的生产实现同源：
    先 shutdown(wait=False) 再 SIGKILL + waitpid 回收僵尸。
    """
    for pid in list(getattr(ex, "_processes", {}) or {}):
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except (OSError, ChildProcessError):
            pass


def _write_marker(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.close(fd)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class BatchFlock:
    """`ports.batch.BatchOrchestrator` 的真实现（进程池版）。"""

    def run(self, tasks: Sequence[Task], worker: Callable[[Task], Any], *,
            workers: int = 1, stall_s: int | None = None,
            lock_path: Path | None = None, state_path: Path | None = None,
            success_marker: Path | None = None) -> BatchReport:
        tasks = list(tasks)
        if workers < 1:
            raise ValueError(f"workers 必须 >= 1（收到 {workers}）")

        # ---- 单写者锁：占用即退出，绝不半途进批算 ----
        lock_f = None
        if lock_path is not None:
            lock_path = Path(lock_path)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_f = open(lock_path, "a")
            try:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                lock_f.close()
                raise BatchLocked(f"已有批算实例持有锁: {lock_path}") from exc

        try:
            report = BatchReport()
            results: list[Result | None] = [None] * len(tasks)

            # ---- 断点：已完成的直接 skipped ----
            done: list[str] = list(_read_state(state_path)) if state_path else []
            done_set = set(done)
            pending: list[int] = []
            for i, t in enumerate(tasks):
                if t.key in done_set:
                    results[i] = Result(key=t.key, status="skipped")
                    report.skipped += 1
                else:
                    pending.append(i)

            if pending:
                ex = ProcessPoolExecutor(max_workers=workers)
                stalled = False
                try:
                    futs = {ex.submit(worker, tasks[i]): i for i in pending}
                    while futs:
                        done_futs, _ = wait(futs, timeout=stall_s,
                                            return_when=FIRST_COMPLETED)
                        if not done_futs:
                            # 停滞：在飞单元全部记账为 stalled 并**立即**收敛返回（不挂死）
                            stalled = True
                            for fu, i in list(futs.items()):
                                results[i] = Result(
                                    key=tasks[i].key, status="failed",
                                    error=f"stall: {stall_s}s 内无任何单元完成")
                                report.failed += 1
                                fu.cancel()
                            futs.clear()
                            break
                        for fu in done_futs:
                            i = futs.pop(fu)
                            key = tasks[i].key
                            try:
                                out = fu.result()
                            except Exception as exc:  # noqa: BLE001 —— 记账语义就是要吞下并继续
                                results[i] = Result(key=key, status="failed",
                                                    error=f"{type(exc).__name__}: {exc}")
                                report.failed += 1
                                continue
                            results[i] = Result(key=key, status="ok",
                                                metrics=out if isinstance(out, dict) else None)
                            report.done += 1
                            if state_path is not None and key not in done_set:
                                done.append(key)
                                done_set.add(key)
                                _write_state(Path(state_path), done)
                finally:
                    # 正常路径所有 future 已取完 → wait=True 不会等出额外时间；
                    # 停滞路径若 wait=True 会挂在卡死单元上（实测 1.5s+），故 wait=False + 强杀。
                    ex.shutdown(wait=not stalled, cancel_futures=stalled)
                    if stalled:
                        _kill_workers(ex)

            report.results = [r for r in results if r is not None]

            # ---- 完成标记：无失败才落 ----
            if success_marker is not None and report.failed == 0:
                _write_marker(Path(success_marker))
            return report
        finally:
            if lock_f is not None:
                try:
                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
                finally:
                    lock_f.close()
