"""P-5 批算编排真实现：flock 单实例 + 进程池 + 停滞看门狗 + 断点 + `_SUCCESS`。

`ports/batch.py` 从 WS6 起声明"实现者：adapters/batch_flock.py"，但该模块长期不存在
（No Orphan 欠账，见 `governance/workspace/pending-items.md#14`）；本模块**只兑现声明**，不改任何工具：
研究侧三份编排样板（converters / extract_sz_cancels / run_lob_batch）的切换仍待专项轮次
（它们的热路径需要字节级重跑对照，且各自的 manifest/月门语义不同）。

语义（与 `tests/_doubles.InlineOrchestrator` 对齐，另加只有真实现才有的四条）：
- **派单按任务序（FIFO）**：与 `InlineOrchestrator` 桩一致（历史工具各自 `pop()` 的 LIFO
  顺序不影响内容，但会让"任务序"这一直觉契约失效）；
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
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, Sequence

from factorlab.ports.batch import BatchReport, Result, Task

__all__ = ["BatchFlock", "BatchLocked"]


class BatchLocked(RuntimeError):
    """单写者锁被占用（同一目标目录已有批算在跑）。"""


def _write_state(path: Path, done: list[str]) -> None:
    """原子写断点（R13：协议单点在 adapters/atomicio）。"""
    from factorlab.adapters.atomicio import atomic_write_text
    atomic_write_text(path, json.dumps({"done": done}, ensure_ascii=False,
                                       sort_keys=True, indent=1))


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
    from factorlab.adapters.atomicio import atomic_write_bytes
    atomic_write_bytes(path, b"")


class BatchFlock:
    """`ports.batch.BatchOrchestrator` 的真实现（进程池版）。"""

    @staticmethod
    def _pool(workers: int, initializer, initargs, mp_context: str | None = None,
              pool_hook: Callable[[Any], None] | None = None):
        """建进程池。`mp_context="spawn"` 是产量循环的硬要求：**fork 会连父进程已缓冲的
        大表一起复制**（16GB 无页面文件的目标机直接爆），研究侧三份样板都用 spawn。"""
        kw: dict = {}
        if mp_context is not None:
            import multiprocessing
            kw["mp_context"] = multiprocessing.get_context(mp_context)
        if initializer is not None:
            kw["initializer"] = initializer
            kw["initargs"] = tuple(initargs)
        pool = ProcessPoolExecutor(max_workers=workers, **kw)
        if pool_hook is not None:
            pool_hook(pool)     # 池（含停滞重建后的新池）一创建就交给调用方观测
        return pool

    @staticmethod
    def _hook(on_result, task: Task, payload) -> str | None:
        """调 `on_result`；返回**回调自身**的错误文本（None = 回调成功）。

        回调成功与否**不改变**该单元本来的成败（worker 失败就是失败，回调只是观察）；
        回调自己抛错则把该单元降级为失败——不许静默吞掉父进程侧写入失败。
        """
        try:
            on_result(task, payload)
            return None
        except Exception as exc:  # noqa: BLE001 —— 回调失败即单元失败
            return f"on_result 抛错: {type(exc).__name__}: {exc}"

    def run(self, tasks: Sequence[Task], worker: Callable[[Task], Any], *,
            workers: int = 1, stall_s: int | None = None,
            lock_path: Path | None = None, state_path: Path | None = None,
            success_marker: Path | None = None,
            max_inflight: int | None = None, mp_context: str | None = None,
            initializer: Callable[..., None] | None = None, initargs: tuple = (),
            stall_policy: str = "fail", stall_strikes: int = 3,
            on_result: Callable[[Task, Any], None] | None = None,
            throttle: Callable[[], bool] | None = None,
            on_tick: Callable[[], None] | None = None,
            on_tick_s: float | None = None,
            pool_hook: Callable[[Any], None] | None = None) -> BatchReport:
        tasks = list(tasks)
        if workers < 1:
            raise ValueError(f"workers 必须 >= 1（收到 {workers}）")
        if stall_policy not in ("fail", "requeue"):
            raise ValueError(f"stall_policy 只能是 fail|requeue（收到 {stall_policy!r}）")
        inflight = max_inflight if max_inflight is not None else workers
        if inflight < 1:
            raise ValueError(f"max_inflight 必须 >= 1（收到 {inflight}）")

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
                queue = list(pending)
                head = 0                 # FIFO：按任务序派单（与桩一致）
                strikes = 0
                ex = self._pool(workers, initializer, initargs, mp_context, pool_hook)
                stalled = False
                try:
                    futs: dict = {}
                    last_tick = time.monotonic()
                    last_act = time.monotonic()
                    while futs or queue:
                        while head < len(queue) and len(futs) < inflight:
                            if throttle is not None and not throttle():
                                break       # 资源低水位：本轮不派新单，等下一 tick
                            i = queue[head]
                            head += 1
                            futs[ex.submit(worker, tasks[i])] = i
                        if on_tick is not None and on_tick_s is not None:
                            if time.monotonic() - last_tick >= on_tick_s:
                                on_tick()
                                last_tick = time.monotonic()
                        if not futs:
                            if head >= len(queue):
                                break
                            # 无处可等（闸门挡住派单）：同样按距上次完成的时间记账
                            # 停滞——否则 throttle 持续 False 时永远到不了下面的
                            # stall 判定，看门狗形同虚设（R02-I3 实测挂死；生产
                            # `run_lob_batch._mem_gate` 即此路径）。
                            if stall_s is not None \
                                    and time.monotonic() - last_act >= stall_s:
                                strikes += 1
                                if stall_policy == "requeue" \
                                        and strikes < stall_strikes:
                                    print(f"STALL: {stall_s}s 无完成（闸门挡住派单）"
                                          f" → 第 {strikes}/{stall_strikes} 次重试",
                                          flush=True)
                                    continue
                                stalled = True
                                n_left = len(queue) - head
                                for i in queue[head:]:
                                    results[i] = Result(
                                        key=tasks[i].key, status="failed",
                                        error=f"stall: {stall_s}s 内无任何单元完成"
                                              f"（strikes={strikes}，闸门挡住派单，"
                                              f"放弃 {n_left} 个单元）")
                                    report.failed += 1
                                queue.clear()
                                break
                            # 睡一个 tick 再试（等闸门放行或周期回调）
                            time.sleep(min(on_tick_s or 1.0, 5.0))
                            continue
                        timeout = stall_s
                        if on_tick is not None and on_tick_s is not None:
                            left = on_tick_s - (time.monotonic() - last_tick)
                            timeout = min(timeout, max(left, 0.05)) if timeout \
                                else left
                        done_futs, _ = wait(futs, timeout=timeout,
                                            return_when=FIRST_COMPLETED)
                        # 停滞以**距上次完成的时间**判定：周期回调会把单次 wait 超时切短，
                        # 按"这一次没等到"判停滞会误报（R14 实测：0.2s tick 立刻触发 strikes）。
                        if done_futs:
                            last_act = time.monotonic()
                        if not done_futs and (stall_s is None
                                              or time.monotonic() - last_act >= stall_s):
                            strikes += 1
                            if stall_policy == "requeue" and strikes < stall_strikes:
                                # 停滞：退回队列重试（三份产量循环的语义）
                                print(f"STALL: {stall_s}s 无完成 → 清理 worker 重试 "
                                      f"（第 {strikes}/{stall_strikes} 次，"
                                      f"in-flight={len(futs)} queue={len(queue) - head}）", flush=True)
                                queue.extend(futs.values())   # 在飞单元退回队尾（FIFO）
                                futs = {}
                                ex.shutdown(wait=False, cancel_futures=True)
                                _kill_workers(ex)
                                ex = self._pool(workers, initializer, initargs, mp_context, pool_hook)
                                continue
                            # 放弃：在飞单元全部记账为 stalled 并**立即**收敛返回（不挂死）
                            stalled = True
                            n_left = len(futs) + (len(queue) - head)
                            for fu, i in list(futs.items()):
                                results[i] = Result(
                                    key=tasks[i].key, status="failed",
                                    error=f"stall: {stall_s}s 内无任何单元完成"
                                          f"（strikes={strikes}，放弃 {n_left} 个单元）")
                                report.failed += 1
                                fu.cancel()
                            for i in queue[head:]:
                                results[i] = Result(
                                    key=tasks[i].key, status="failed",
                                    error=f"stall: 前序单元停滞放弃（strikes={strikes}）")
                                report.failed += 1
                            futs.clear()
                            queue.clear()
                            break
                        strikes = 0
                        for fu in done_futs:
                            i = futs.pop(fu)
                            key = tasks[i].key
                            try:
                                out = fu.result()
                            except Exception as exc:  # noqa: BLE001 —— 记账语义就是要吞下并继续
                                hook_err = (self._hook(on_result, tasks[i], exc)
                                            if on_result is not None else None)
                                results[i] = Result(
                                    key=key, status="failed",
                                    error=hook_err or f"{type(exc).__name__}: {exc}")
                                report.failed += 1
                                continue
                            hook_err = (self._hook(on_result, tasks[i], out)
                                        if on_result is not None else None)
                            if hook_err is not None:
                                results[i] = Result(key=key, status="failed",
                                                    error=hook_err)
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
                    # 放弃路径若 wait=True 会挂在卡死单元上（实测 1.5s+），故 wait=False + 强杀。
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
