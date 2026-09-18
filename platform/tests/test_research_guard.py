"""R31 Task 2 测试：重任务闸 `_guard` + flab 安装脚本。

断言来源：
- spec `knowledge/design/platform/specs/2026-09-18-research-api-design.md` §5：
  重命令自动过 heavy 闸（flock 2 槽，非阻塞→BUSY；--wait 阻塞）、内存预检
  （avail<8GB→MEMORY_GUARD）、env 注入（FACTORLAB_MAX_MEMORY=8GB/
  FACTORLAB_MIN_AVAILABLE_MEMORY=6GB/OMP|POLARS_MAX_THREADS=8/nice）。
- plan Task 2：槽文件 `~/.cache/factorlab/heavy.{1,2}.lock`；install_flab.sh 写
  `~/.local/bin/flab` 注入 `FACTORLAB_DATA_BACKEND=ch` 并 exec
  `factorlab research "$@"`，支持 --dry-run。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from factorlab.research import _guard
from factorlab.research._guard import GuardError, guard_heavy, release_slots

REPO = Path(__file__).resolve().parents[2]
INSTALL_FLAB = REPO / "governance" / "ops" / "install_flab.sh"

EXPECTED_ENV = {
    "FACTORLAB_MAX_MEMORY": "8GB",
    "FACTORLAB_MIN_AVAILABLE_MEMORY": "6GB",
    "OMP_NUM_THREADS": "8",
    "POLARS_MAX_THREADS": "8",
}


@pytest.fixture(autouse=True)
def _isolated_locks(tmp_path, monkeypatch):
    """槽目录隔离到 tmp；默认关 nice（测试进程不被降优先级）。"""
    monkeypatch.setenv("FACTORLAB_HEAVY_LOCK_DIR", str(tmp_path))
    monkeypatch.setenv("FACTORLAB_GUARD_NICE", "0")
    for key in EXPECTED_ENV:
        monkeypatch.delenv(key, raising=False)
    release_slots()
    yield
    release_slots()


# ================================================================
# 闸：2 槽 / BUSY / --wait / 内存预检 / env / nice
# ================================================================

def test_two_slots_then_busy_and_slot_files(tmp_path, capsys):
    env1, slot1 = guard_heavy(["factor", "run"])
    env2, slot2 = guard_heavy(["strategy", "run"])
    assert slot1.name == "heavy.1.lock" and slot1.parent == tmp_path
    assert slot2.name == "heavy.2.lock"
    assert env1 == EXPECTED_ENV and env2 == EXPECTED_ENV
    err1 = capsys.readouterr().err
    assert "slot=1/2" in err1 and "factor run" in err1  # 诊断走 stderr

    with pytest.raises(GuardError) as ei:
        guard_heavy(["study", "run"])
    e = ei.value
    assert e.code == "BUSY"
    assert e.message and e.hint and str(e) == e.message
    assert "2/2" in e.message
    out = capsys.readouterr().out
    assert out == ""  # guard 不得污染 stdout（单 JSON 契约）


def test_release_allows_reacquire(tmp_path):
    guard_heavy(["a"])
    guard_heavy(["b"])
    with pytest.raises(GuardError):
        guard_heavy(["c"])
    release_slots()
    _env, slot = guard_heavy(["d"])
    assert slot.name == "heavy.1.lock"  # 释放后重新可用


def test_wait_blocks_until_slot_released():
    guard_heavy(["a"])
    guard_heavy(["b"])
    result: dict[str, object] = {}

    def worker() -> None:
        try:
            result["env"], result["slot"] = guard_heavy(["factor", "run"], wait=True)
        except BaseException as exc:  # noqa: BLE001 —— 测试线程序列化异常
            result["exc"] = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=0.5)
    assert thread.is_alive(), "wait=True 应在槽满时阻塞"
    release_slots()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "exc" not in result, result.get("exc")
    assert result["env"] == EXPECTED_ENV
    assert Path(result["slot"]).exists()


def test_memory_guard_rejects_and_releases_slot(monkeypatch):
    mem = SimpleNamespace(available=4 * 1024**3)
    monkeypatch.setattr(_guard.psutil, "virtual_memory", lambda: mem)
    with pytest.raises(GuardError) as ei:
        guard_heavy(["factor", "run"])
    assert ei.value.code == "MEMORY_GUARD"
    assert "4.0GB" in ei.value.message
    assert ei.value.hint
    # 预检失败不得占槽：恢复内存后立即可取下一条
    mem.available = 64 * 1024**3
    _env, slot = guard_heavy(["factor", "run"])
    assert slot.name == "heavy.1.lock"


def test_env_injection_defaults():
    env, _slot = guard_heavy(["a"])
    assert env == EXPECTED_ENV


def test_env_injection_explicit_values_win(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    monkeypatch.setenv("FACTORLAB_MAX_MEMORY", "12GB")
    env, _slot = guard_heavy(["a"])
    assert env["OMP_NUM_THREADS"] == "3"
    assert env["FACTORLAB_MAX_MEMORY"] == "12GB"
    assert env["FACTORLAB_MIN_AVAILABLE_MEMORY"] == "6GB"


def test_nice_applied_in_process(monkeypatch):
    monkeypatch.setenv("FACTORLAB_GUARD_NICE", "10")
    calls: list[int] = []

    def fake_nice(delta: int) -> int:
        calls.append(delta)
        return 0 if delta == 0 else delta

    monkeypatch.setattr(_guard.os, "nice", fake_nice)
    guard_heavy(["a"])
    assert calls == [0, 10]  # 读当前值 → 差量调至 10


def test_nice_disabled_by_env(monkeypatch):
    monkeypatch.setenv("FACTORLAB_GUARD_NICE", "off")
    called: list[int] = []
    monkeypatch.setattr(_guard.os, "nice", lambda d: called.append(d) or 0)
    guard_heavy(["a"])
    assert called == []


def test_cross_process_flock_is_real(tmp_path):
    """真 OS flock：子进程持两槽 → 本进程 BUSY（杀"进程内计数器"存根）。"""
    script = (
        "import fcntl, sys\n"
        "fds = []\n"
        f"for i in (1, 2):\n"
        f"    fd = open(r'{tmp_path}/heavy.%d.lock' % i, 'a+')\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    fds.append(fd)\n"
        "print('HOLDING', flush=True)\n"
        "sys.stdin.readline()\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True)
    try:
        assert proc.stdout.readline().strip() == "HOLDING"
        with pytest.raises(GuardError) as ei:
            guard_heavy(["factor", "run"])
        assert ei.value.code == "BUSY"
    finally:
        proc.stdin.write("\n")
        proc.stdin.flush()
        proc.wait(timeout=10)


# ================================================================
# install_flab.sh（plan Task 2 Step 3/4）
# ================================================================

def test_install_flab_dry_run_prints_script(tmp_path):
    result = subprocess.run(
        ["bash", str(INSTALL_FLAB), "--dry-run"],
        capture_output=True, text=True,
        env={**os.environ, "FLAB_INSTALL_DIR": str(tmp_path)}, timeout=60)
    assert result.returncode == 0
    assert str(tmp_path / "flab") in result.stdout
    assert "FACTORLAB_DATA_BACKEND=ch" in result.stdout
    assert 'research "$@"' in result.stdout
    assert not (tmp_path / "flab").exists()  # dry-run 不落盘


def test_install_flab_installs_and_smokes(tmp_path):
    result = subprocess.run(
        ["bash", str(INSTALL_FLAB)],
        capture_output=True, text=True,
        env={**os.environ, "FLAB_INSTALL_DIR": str(tmp_path)}, timeout=60)
    assert result.returncode == 0
    target = tmp_path / "flab"
    assert target.is_file()
    assert os.access(target, os.X_OK)
    content = target.read_text(encoding="utf-8")
    assert "FACTORLAB_DATA_BACKEND=ch" in content
    assert "research" in content

    smoke = subprocess.run([str(target), "describe", "--json"],
                           capture_output=True, text=True, timeout=180)
    assert smoke.returncode == 0, smoke.stderr
    assert len(smoke.stdout.strip().splitlines()) == 1
    doc = json.loads(smoke.stdout)
    assert doc["ok"] is True
    assert "describe" in doc["data"]["commands"]
