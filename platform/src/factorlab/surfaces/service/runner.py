"""Worker / Runner（规格 §4 命令映射 + §7 执行与资源；计划 T3）。

- `build_command`：四类作业 → 镜像内 `factorlab` 固定命令（结构化 argv，无 shell）；
- `Worker`：claim → 子进程（`start_new_session` 进程组隔离）→ 超时/取消信号 →
  `store.finish`；stdout+stderr 合流写 `<log_dir>/<id>.log`；成功时把 CLI 的
  单 JSON 信封抄写到 `<result_dir>/<id>.json`（GET /result 再叠加 service 段）。

测试可注入 `runner`（不真跑 CLI）与 `command_builder`（真实 `python -c sleep`
子进程验证进程组信号语义）。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from factorlab.adapters.atomicio import atomic_write_text
from factorlab.surfaces.service.models import Job, JobType
from factorlab.surfaces.service.store import JobStore

DEFAULT_TIMEOUTS: dict[str, float] = {
    "factor_run": 7200.0,   # 2h
    "compose": 3600.0,      # 1h
    "strategy_run": 3600.0,  # 1h
    "factor_admit": 1800.0,  # 30m
}

RegisterProc = Callable[[subprocess.Popen], None]
RunnerFn = Callable[[list[str], Path, float, RegisterProc], tuple[int | None, bool]]

DEFAULT_HEALTH_DATASET = "ashare_daily"


def current_dataset_version(health_root: Path) -> str | None:
    """最新 health 分区里的 data_version（L2：作业记录冻结当刻数据版本）。

    从最新分区向前回退，取第一个非空 `data_version`；目录缺失/全空 → None。
    """
    dataset_dir = Path(health_root) / DEFAULT_HEALTH_DATASET
    if not dataset_dir.is_dir():
        return None
    for path in sorted(dataset_dir.glob("*.json"), reverse=True):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict):
            version = doc.get("data_version")
            if version:
                return str(version)
    return None


def build_command(job: Job, *, python: Path) -> list[str]:
    """作业 → argv（镜像内 CLI；python 的同级脚本 = factorlab 入口点）。"""
    cli = str(Path(python).parent / "factorlab")
    params = job.params
    if job.type == JobType.factor_run.value:
        cmd = [cli, "research", "factor", "run", params["spec"]]
        if params.get("set"):
            cmd += ["--set", *params["set"]]
        if params.get("universe"):
            cmd += ["--universe", params["universe"]]
        if params.get("output_dir"):
            cmd += ["--output-dir", params["output_dir"]]
        if params.get("profile"):
            cmd += ["--profile"]
        return cmd
    if job.type == JobType.compose.value:
        return [cli, "compose", params["spec"]]
    if job.type == JobType.strategy_run.value:
        cmd = [cli, "research", "strategy", "run", params["doc"]]
        if params.get("signal"):
            cmd += ["--signal", params["signal"]]
        if params.get("accept_quality"):
            cmd += ["--accept-quality", params["accept_quality"]]
        if params.get("override_reason"):
            cmd += ["--override-reason", params["override_reason"]]
        return cmd
    if job.type == JobType.factor_admit.value:
        cmd = [cli, "research", "factor", "admit", params["spec"]]
        if params.get("scales"):
            cmd += ["--scales", params["scales"]]
        if params.get("wait"):
            cmd += ["--wait"]
        return cmd
    raise ValueError(f"未知作业类型: {job.type}")


class Worker:
    """单机作业 worker：FIFO claim + 子进程执行 + 超时/取消 + 结果抄写。"""

    def __init__(self, store: JobStore, *, concurrency: int = 1, log_dir: Path,
                 result_dir: Path, python: Path, runner: RunnerFn | None = None,
                 timeouts: dict[str, float] | None = None, term_grace: float = 10.0,
                 cli_results_dir: Path | None = None, cache_dir: Path | None = None,
                 env_overrides: dict[str, str] | None = None,
                 command_builder: Callable[[Job, Path], list[str]] | None = None,
                 dataset_version_fn: Callable[[], str | None] | None = None):
        self._store = store
        self.concurrency = int(concurrency)
        self._log_dir = Path(log_dir)
        self._result_dir = Path(result_dir)
        self._python = Path(python)
        self._runner: RunnerFn = runner or self._subprocess_runner
        self._timeouts = dict(timeouts or {})
        self._term_grace = float(term_grace)
        self._cli_results_dir = (Path(cli_results_dir) if cli_results_dir
                                 else self._result_dir.parent)
        self._cache_dir = (Path(cache_dir) if cache_dir
                           else self._result_dir.parent / "cache")
        self._env_overrides = dict(env_overrides or {})
        self._command_builder = command_builder or build_command
        self._dataset_version_fn = dataset_version_fn

        self.paused = False
        self._lock = threading.RLock()
        self._active_ids: set[str] = set()
        self._procs: dict[str, subprocess.Popen] = {}
        self._cancel_requested: set[str] = set()
        self._done_events: dict[str, threading.Event] = {}
        self._threads: list[threading.Thread] = []

    def _resolve_dataset_version(self) -> str | None:
        """版本读取失败不阻塞作业（L2 记录项，非准入项）。"""
        fn = self._dataset_version_fn
        if fn is None:
            return None
        try:
            value = fn()
        except Exception:  # noqa: BLE001
            return None
        return str(value) if value else None

    # ---- 观察面（health/API） -----------------------------------------

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._active_ids)

    @property
    def active_job_id(self) -> str | None:
        with self._lock:
            return next(iter(self._active_ids), None)

    def timeout_for(self, job_type: str) -> float:
        return self._timeouts.get(job_type, DEFAULT_TIMEOUTS.get(job_type, 3600.0))

    def build_env(self) -> dict[str, str]:
        """规格 §7 固定 env（父进程 env 继承 + 服务注入覆盖）。"""
        env = dict(os.environ)
        env["FACTORLAB_DATA_BACKEND"] = "ch"
        env["FACTORLAB_RESULTS_DIR"] = str(self._cli_results_dir)
        env["FACTORLAB_MAX_MEMORY"] = "8GB"
        env["FACTORLAB_CH_MAX_THREADS"] = "8"
        env["FACTORLAB_READ_CACHE_DIR"] = str(self._cache_dir / "bars_1m")
        env.update(self._env_overrides)
        return env

    # ---- 主循环 --------------------------------------------------------

    def run_once(self) -> bool:
        """claim 一条并同步执行；无作业/暂停/满并发 → False。"""
        if self.paused:
            return False
        with self._lock:
            if len(self._active_ids) >= self.concurrency:
                return False
        job = self._store.claim_next(
            dataset_version=self._resolve_dataset_version())
        if job is None:
            return False
        self._run_job(job)
        return True

    def run_forever(self, stop_event: threading.Event) -> None:
        try:
            while not stop_event.is_set():
                if self.paused:
                    stop_event.wait(0.05)
                    continue
                with self._lock:
                    full = len(self._active_ids) >= self.concurrency
                if full:
                    stop_event.wait(0.05)
                    continue
                job = self._store.claim_next(
                    dataset_version=self._resolve_dataset_version())
                if job is None:
                    stop_event.wait(0.05)
                    continue
                thread = threading.Thread(target=self._run_job, args=(job,),
                                          daemon=True)
                with self._lock:
                    self._threads.append(thread)
                thread.start()
        finally:
            with self._lock:
                pending = list(self._threads)
            for thread in pending:
                thread.join(timeout=self._term_grace + 10.0)

    # ---- 取消 ----------------------------------------------------------

    def cancel(self, job_id: str) -> bool:
        """queued→cancelled；running→SIGTERM→10s→SIGKILL（进程组）。"""
        if self._store.cancel(job_id):
            return True
        job = self._store.get(job_id)
        if job is None or job.status != "running":
            return False
        with self._lock:
            self._cancel_requested.add(job_id)
            proc = self._procs.get(job_id)
            done = self._done_events.get(job_id)
        if proc is not None:
            self._terminate_group(proc)
        if done is not None and not done.wait(timeout=self._term_grace):
            if proc is not None:
                self._kill_group(proc)
            done.wait(timeout=5.0)
        return True

    # ---- 单作业执行 ----------------------------------------------------

    def _run_job(self, job: Job) -> None:
        log_path = self._log_dir / f"{job.id}.log"
        result_path = self._result_dir / f"{job.id}.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)
        try:
            self._store.update_running(job.id, log_path=str(log_path),
                                       result_path=str(result_path))
        except ValueError:
            return  # 已被外部迁移（如取消竞态），不覆盖
        done = threading.Event()
        with self._lock:
            self._active_ids.add(job.id)
            self._done_events[job.id] = done
        try:
            if self._cancel_pending(job.id):
                self._store.finish(job.id, status="cancelled", error="cancelled")
                return
            cmd = self._command_builder(job, python=self._python)
            timeout = self.timeout_for(job.type)
            rc, timed_out = self._runner(cmd, log_path, timeout, self._register(job.id))
            if self._cancel_pending(job.id):
                self._store.finish(job.id, status="cancelled", exit_code=rc,
                                   error="cancelled")
            elif timed_out:
                self._store.finish(job.id, status="failed", exit_code=rc,
                                   error=f"timeout after {timeout:g}s")
            elif rc == 0:
                self._copy_result(log_path, result_path)
                self._store.finish(job.id, status="succeeded", exit_code=0)
            else:
                self._store.finish(job.id, status="failed", exit_code=rc,
                                   error=f"exit code {rc}")
        except Exception as exc:  # noqa: BLE001 —— runner 异常不得留下 running 僵尸
            try:
                self._store.finish(job.id, status="failed",
                                   error=f"{type(exc).__name__}: {exc}")
            except ValueError:
                pass
        finally:
            with self._lock:
                self._active_ids.discard(job.id)
                self._procs.pop(job.id, None)
                self._cancel_requested.discard(job.id)
                self._done_events.pop(job.id, None)
            done.set()

    def _cancel_pending(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancel_requested

    def _register(self, job_id: str) -> RegisterProc:
        def register(proc: subprocess.Popen) -> None:
            with self._lock:
                self._procs[job_id] = proc
                cancel_pending = job_id in self._cancel_requested
            try:
                self._store.update_running(job_id, pid=proc.pid)
            except ValueError:
                pass
            if cancel_pending:
                self._terminate_group(proc)
        return register

    def _copy_result(self, log_path: Path, result_path: Path) -> bool:
        """把 CLI 单 JSON 信封（日志最后一行 JSON）抄写到 result_path。"""
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(doc, dict):
                atomic_write_text(Path(result_path),
                                  json.dumps(doc, ensure_ascii=False))
                return True
        return False

    # ---- 子进程默认 runner ---------------------------------------------

    def _subprocess_runner(self, cmd: list[str], log_path: Path, timeout: float,
                           register: RegisterProc) -> tuple[int | None, bool]:
        with open(log_path, "ab") as logf:
            proc = subprocess.Popen(
                cmd, stdout=logf, stderr=subprocess.STDOUT,
                env=self.build_env(), start_new_session=True)
            register(proc)
            try:
                return proc.wait(timeout=timeout), False
            except subprocess.TimeoutExpired:
                self._terminate_group(proc)
                try:
                    rc = proc.wait(timeout=self._term_grace)
                except subprocess.TimeoutExpired:
                    self._kill_group(proc)
                    rc = proc.wait(timeout=5.0)
                return rc, True

    @staticmethod
    def _signal_group(proc: subprocess.Popen, sig: int) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.send_signal(sig)
            except OSError:
                pass

    def _terminate_group(self, proc: subprocess.Popen) -> None:
        self._signal_group(proc, signal.SIGTERM)

    def _kill_group(self, proc: subprocess.Popen) -> None:
        self._signal_group(proc, signal.SIGKILL)


__all__ = ["DEFAULT_TIMEOUTS", "RunnerFn", "Worker", "build_command"]
