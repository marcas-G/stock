"""heavy.sh 重任务闸单测（R30）：flock 限 2 并发；启动前可用内存 <8GB 拒绝；
默认注入 FACTORLAB_MAX_MEMORY=8GB / FACTORLAB_MIN_AVAILABLE_MEMORY=6GB /
OMP_NUM_THREADS=8 / POLARS_MAX_THREADS=8 + nice -n 10。

R30.1 OOM 自牺牲优先级：exec 前写 /proc/self/oom_score_adj=700（内核 OOM 时
优先杀本任务而非 sshd/关键进程）；HEAVY_OOM_SCORE_ADJ 可覆盖（0..1000）；
非法值拒绝且命令不执行。

测试用真实 bash 子进程 + fake 内存文件/锁目录（不 mock 逻辑，测真实行为：
退出码/输出/marker 文件/阻塞时序）。
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

HEAVY = Path(__file__).resolve().parents[1] / "heavy.sh"
GB_KB = 1024 ** 2


def _meminfo(avail_gb: float) -> Path:
    p = Path(os.environ.get("TMPDIR", "/tmp")) / f"heavy-meminfo-{os.getpid()}-{avail_gb}.txt"
    p.write_text(
        f"MemTotal:       131072000 kB\n"
        f"MemFree:         20000000 kB\n"
        f"MemAvailable:    {int(avail_gb * GB_KB)} kB\n"
        f"SwapFree:        33554432 kB\n", encoding="utf-8")
    return p


@pytest.fixture()
def env(tmp_path):
    e = os.environ.copy()
    e["HEAVY_LOCK_DIR"] = str(tmp_path / "locks")
    e["HEAVY_MEMINFO_FILE"] = str(_meminfo(40))
    return e


def _run(args, env, timeout=15):
    return subprocess.run(["bash", str(HEAVY), *args], env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_injects_guard_env_and_nice(env):
    r = _run(["bash", "-c",
              'echo "$FACTORLAB_MAX_MEMORY|$FACTORLAB_MIN_AVAILABLE_MEMORY|'
              '$OMP_NUM_THREADS|$POLARS_MAX_THREADS|$(ps -o ni= -p $$ | tr -d " ")"'],
             env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "8GB|6GB|8|8|10"


def test_existing_env_overrides_not_clobbered(env):
    env["FACTORLAB_MAX_MEMORY"] = "4GB"
    env["OMP_NUM_THREADS"] = "3"
    r = _run(["bash", "-c", 'echo "$FACTORLAB_MAX_MEMORY|$OMP_NUM_THREADS"'], env)
    assert r.stdout.strip() == "4GB|3"


def test_refuses_when_available_below_8gb(env, tmp_path):
    env["HEAVY_MEMINFO_FILE"] = str(_meminfo(3))
    marker = tmp_path / "ran"
    r = _run(["bash", "-c", f"touch {marker}"], env)
    assert r.returncode != 0
    assert "8" in r.stderr and "GB" in r.stderr        # 提示可用内存不足
    assert not marker.exists()                          # 命令真的没跑


def test_runs_when_available_sufficient(env, tmp_path):
    marker = tmp_path / "ran"
    r = _run(["bash", "-c", f"touch {marker}"], env)
    assert r.returncode == 0, r.stderr
    assert marker.exists()


def test_default_concurrency_limit_is_two(env):
    # 两个长任务占满 2 槽；第三个应阻塞（timeout → 124），释放后恢复可跑
    p1 = subprocess.Popen(["bash", str(HEAVY), "sleep", "3"], env=env)
    p2 = subprocess.Popen(["bash", str(HEAVY), "sleep", "3"], env=env)
    time.sleep(0.7)
    r = subprocess.run(["timeout", "1", "bash", str(HEAVY), "true"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 124, f"第三个任务未被闸住: rc={r.returncode} {r.stderr}"
    p1.wait(timeout=15)
    p2.wait(timeout=15)
    r2 = subprocess.run(["timeout", "10", "bash", str(HEAVY), "true"],
                        env=env, capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr


def test_custom_concurrency_limit_via_env(env):
    env["HEAVY_MAX_CONCURRENT"] = "1"
    p1 = subprocess.Popen(["bash", str(HEAVY), "sleep", "2"], env=env)
    time.sleep(0.5)
    r = subprocess.run(["timeout", "1", "bash", str(HEAVY), "true"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 124
    p1.wait(timeout=15)


def test_usage_error_without_command(env):
    r = _run([], env)
    assert r.returncode == 2
    assert "用法" in r.stderr


# ---------------- OOM 自牺牲优先级（R30.1） ----------------

def test_default_oom_score_adj_700_visible_in_command(env):
    """真实 /proc/self：heavy.sh 先写自身 oom_score_adj=700，exec 后命令继承。"""
    r = _run(["bash", "-c", "cat /proc/self/oom_score_adj"], env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "700"


def test_oom_score_adj_override_via_env(env):
    env["HEAVY_OOM_SCORE_ADJ"] = "300"
    r = _run(["bash", "-c", "cat /proc/self/oom_score_adj"], env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "300"


@pytest.mark.parametrize("bad", ["abc", "7.5", "-1", "1001"])
def test_rejects_illegal_oom_score_adj(env, tmp_path, bad):
    env["HEAVY_OOM_SCORE_ADJ"] = bad
    marker = tmp_path / "ran"
    r = _run(["bash", "-c", f"touch {marker}"], env)
    assert r.returncode == 5
    assert "HEAVY_OOM_SCORE_ADJ" in r.stderr
    assert not marker.exists()                     # 非法值不执行任何命令
