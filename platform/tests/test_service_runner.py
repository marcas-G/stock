"""T3 worker/runner 测试（规格 §4/§7 + 计划 T3）。

断言来源：规格 §4 命令映射表、§7（进程组隔离 / 超时 SIGTERM→10s→SIGKILL→failed(timeout) /
cancel 语义 / 日志合流 / 固定 env）与计划 T3。
不真跑 factorlab CLI：命令映射用 build_command 直测，行为用注入 fake runner 与
真实 `python -c sleep` 子进程（验证进程组信号，而非真 CLI）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from factorlab.surfaces.service.models import Job
from factorlab.surfaces.service.runner import (DEFAULT_TIMEOUTS, Worker,
                                               build_command)
from factorlab.surfaces.service.store import JobStore

PY = Path(sys.executable)


def make_job(type_: str, params: dict) -> Job:
    return Job(id="01JOBJOBJOBJOBJOBJOBJOBJOB", type=type_, status="queued",
               params=params, created_at="2026-09-21T00:00:00.000+00:00")


@pytest.fixture()
def store(tmp_path: Path) -> JobStore:
    s = JobStore(tmp_path / "jobs.sqlite3")
    yield s
    s.close()


def _worker(store: JobStore, tmp_path: Path, **kw) -> Worker:
    opts = dict(concurrency=1, log_dir=tmp_path / "state" / "logs",
                result_dir=tmp_path / "state" / "results", python=PY,
                cli_results_dir=tmp_path / "results",
                cache_dir=tmp_path / "state" / "cache")
    opts.update(kw)
    return Worker(store, **opts)


# ---- 命令映射（逐字）---------------------------------------------------

def test_build_command_factor_run_full():
    job = make_job("factor_run", {
        "spec": "/r/factor/a.yaml",
        "set": ["n=20", "m=3"],
        "universe": "all",
        "output_dir": "/r/results/foo",
        "profile": True,
    })
    cmd = build_command(job, python=PY)
    assert cmd == [
        str(PY.parent / "factorlab"), "research", "factor", "run", "/r/factor/a.yaml",
        "--set", "n=20", "m=3",
        "--universe", "all",
        "--output-dir", "/r/results/foo",
        "--profile",
    ]


def test_build_command_factor_run_minimal():
    job = make_job("factor_run", {"spec": "/r/factor/a.yaml"})
    assert build_command(job, python=PY) == [
        str(PY.parent / "factorlab"), "research", "factor", "run", "/r/factor/a.yaml"]


def test_build_command_compose():
    job = make_job("compose", {"spec": "/r/composites/specs/c.yaml"})
    assert build_command(job, python=PY) == [
        str(PY.parent / "factorlab"), "compose", "/r/composites/specs/c.yaml"]


def test_build_command_strategy_run_with_read_gate_flags():
    job = make_job("strategy_run", {
        "doc": "/r/strategy/s.yaml",
        "signal": "sig_a",
        "accept_quality": "DEGRADED",
        "override_reason": "维护窗口",
    })
    assert build_command(job, python=PY) == [
        str(PY.parent / "factorlab"), "research", "strategy", "run", "/r/strategy/s.yaml",
        "--signal", "sig_a",
        "--accept-quality", "DEGRADED",
        "--override-reason", "维护窗口",
    ]


def test_build_command_factor_admit():
    job = make_job("factor_admit", {"spec": "/r/factor/a.yaml",
                                    "scales": "daily", "wait": True})
    assert build_command(job, python=PY) == [
        str(PY.parent / "factorlab"), "research", "factor", "admit", "/r/factor/a.yaml",
        "--scales", "daily",
        "--wait",
    ]


def test_build_command_uses_sibling_of_python():
    job = make_job("compose", {"spec": "/r/c.yaml"})
    cmd = build_command(job, python=Path("/opt/venv/bin/python"))
    assert cmd[0] == "/opt/venv/bin/factorlab"


def test_default_timeouts_match_spec():
    assert DEFAULT_TIMEOUTS == {"factor_run": 7200.0, "compose": 3600.0,
                                "strategy_run": 3600.0, "factor_admit": 1800.0}


def test_worker_timeout_override(store: JobStore, tmp_path: Path):
    w = _worker(store, tmp_path, timeouts={"factor_run": 5})
    assert w.timeout_for("factor_run") == 5
    assert w.timeout_for("compose") == 3600.0


# ---- 运行语义（注入 fake runner）---------------------------------------

def test_worker_success_copies_cli_json_and_writes_log(store: JobStore, tmp_path: Path):
    calls: list[tuple[list[str], float]] = []

    def fake(cmd, log_path, timeout, register):
        calls.append((list(cmd), timeout))
        log_path.write_text(
            "warning: demo\n"
            + json.dumps({"ok": True, "command": "factor.run", "data": {"name": "a"}})
            + "\n", encoding="utf-8")
        return 0, False

    w = _worker(store, tmp_path, runner=fake,
                command_builder=lambda job, python: ["/fake/factorlab", "research"])
    job = store.create("factor_run", {"spec": "/r/factor/a.yaml"})
    assert w.run_once() is True

    record = store.get(job.id)
    assert record is not None
    assert record.status == "succeeded"
    assert record.exit_code == 0
    assert record.log_path is not None and Path(record.log_path).is_file()
    assert "warning: demo" in Path(record.log_path).read_text(encoding="utf-8")
    assert record.result_path is not None
    result = json.loads(Path(record.result_path).read_text(encoding="utf-8"))
    assert result["command"] == "factor.run"
    assert result["data"] == {"name": "a"}
    assert calls == [(["/fake/factorlab", "research"], 7200.0)]
    assert w.busy is False


def test_worker_nonzero_exit_maps_failed(store: JobStore, tmp_path: Path):
    def fake(cmd, log_path, timeout, register):
        log_path.write_text("boom\n", encoding="utf-8")
        return 6, False

    w = _worker(store, tmp_path, runner=fake)
    job = store.create("compose", {"spec": "/r/c.yaml"})
    assert w.run_once() is True

    record = store.get(job.id)
    assert record is not None
    assert record.status == "failed"
    assert record.exit_code == 6
    assert "6" in (record.error or "")
    assert not Path(record.result_path).exists()


def test_worker_timeout_kills_process_group_and_records_failed(store: JobStore, tmp_path: Path):
    def sleeper(job, python):
        return [sys.executable, "-c", "import time; time.sleep(30)"]

    w = _worker(store, tmp_path, command_builder=sleeper,
                timeouts={"factor_run": 0.4}, term_grace=0.3)
    job = store.create("factor_run", {"spec": "/r/a.yaml"})
    started = time.monotonic()
    assert w.run_once() is True
    elapsed = time.monotonic() - started

    record = store.get(job.id)
    assert record is not None
    assert record.status == "failed"
    assert "timeout" in (record.error or "").lower()
    assert elapsed < 10, f"超时链必须快速收敛（含 SIGTERM→KILL），实际 {elapsed:.1f}s"
    assert record.pid is not None
    assert not Path(f"/proc/{record.pid}").exists(), "超时后子进程必须已被终止"
    assert w.busy is False


def test_worker_cancel_running_sends_sigterm(store: JobStore, tmp_path: Path):
    def sleeper(job, python):
        return [sys.executable, "-c", "import time; time.sleep(30)"]

    w = _worker(store, tmp_path, command_builder=sleeper, term_grace=0.2)
    job = store.create("factor_run", {"spec": "/r/a.yaml"})
    t = threading.Thread(target=w.run_once)
    t.start()

    deadline = time.monotonic() + 10
    record = None
    while time.monotonic() < deadline:
        record = store.get(job.id)
        if record is not None and record.status == "running" and record.pid:
            break
        time.sleep(0.02)
    assert record is not None and record.pid, "runner 未登记 pid"
    pid = record.pid

    assert w.cancel(job.id) is True
    t.join(timeout=15)
    assert not t.is_alive()

    done = store.get(job.id)
    assert done is not None
    assert done.status == "cancelled"
    assert done.exit_code == -15, f"应记录 SIGTERM 退出码 -15: {done.exit_code}"
    assert not Path(f"/proc/{pid}").exists(), "取消后子进程必须已被终止"


def test_worker_cancel_queued_immediate(store: JobStore, tmp_path: Path):
    called = []

    def fake(cmd, log_path, timeout, register):
        called.append(cmd)
        return 0, False

    w = _worker(store, tmp_path, runner=fake)
    job = store.create("factor_run", {"spec": "/r/a.yaml"})
    assert w.cancel(job.id) is True
    assert store.get(job.id).status == "cancelled"  # type: ignore[union-attr]
    assert called == []


def test_worker_cancel_unknown_or_terminal_returns_false(store: JobStore, tmp_path: Path):
    def fake(cmd, log_path, timeout, register):
        return 0, False

    w = _worker(store, tmp_path, runner=fake)
    assert w.cancel("01ZZZZZZZZZZZZZZZZZZZZZZZZ") is False
    job = store.create("compose", {"spec": "/r/c.yaml"})
    assert w.run_once() is True
    assert store.get(job.id).status == "succeeded"  # type: ignore[union-attr]
    assert w.cancel(job.id) is False


def test_worker_paused_does_not_claim_until_resumed(store: JobStore, tmp_path: Path):
    calls = []

    def fake(cmd, log_path, timeout, register):
        calls.append(cmd)
        return 0, False

    w = _worker(store, tmp_path, runner=fake)
    job = store.create("factor_run", {"spec": "/r/a.yaml"})
    w.paused = True
    assert w.run_once() is False
    assert store.get(job.id).status == "queued"  # type: ignore[union-attr]
    assert calls == []

    w.paused = False
    assert w.run_once() is True
    assert store.get(job.id).status == "succeeded"  # type: ignore[union-attr]
    assert len(calls) == 1


def test_run_forever_processes_queue_then_stops(store: JobStore, tmp_path: Path):
    calls = []

    def fake(cmd, log_path, timeout, register):
        calls.append(cmd)
        return 0, False

    w = _worker(store, tmp_path, runner=fake)
    a = store.create("factor_run", {"spec": "/r/a.yaml"})
    b = store.create("compose", {"spec": "/r/c.yaml"})
    stop = threading.Event()
    t = threading.Thread(target=w.run_forever, args=(stop,))
    t.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if store.get(a.id).status == "succeeded" and store.get(b.id).status == "succeeded":  # type: ignore[union-attr]
                break
            time.sleep(0.02)
        assert store.get(a.id).status == "succeeded"  # type: ignore[union-attr]
        assert store.get(b.id).status == "succeeded"  # type: ignore[union-attr]
        assert len(calls) == 2
    finally:
        stop.set()
        t.join(timeout=10)


def test_build_env_fixed_values(store: JobStore, tmp_path: Path):
    w = _worker(store, tmp_path)
    env = w.build_env()
    assert env["FACTORLAB_DATA_BACKEND"] == "ch"
    assert env["FACTORLAB_RESULTS_DIR"] == str(tmp_path / "results")
    assert env["FACTORLAB_MAX_MEMORY"] == "8GB"
    assert env["FACTORLAB_CH_MAX_THREADS"] == "8"
    assert env["FACTORLAB_READ_CACHE_DIR"] == str(tmp_path / "state" / "cache" / "bars_1m")
    assert env["PATH"] == os.environ["PATH"], "父进程 env 必须继承"
