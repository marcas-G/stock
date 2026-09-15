"""P-5 真实现 `adapters/batch_flock.py` —— 契约一致 + flock/断点/看门狗/标记四条独有语义。

`ports/batch.py` 声明"实现者：adapters/batch_flock.py（WS6）"，但该模块长期不存在（No Orphan
欠账，`docs/pending-items.md#14`）。本测试先于实现写：契约部分对齐 `_doubles.InlineOrchestrator`
（失败记账不中断、断点跳过），额外锁住只有真实现才有的行为——**锁被占时零执行**、
**停滞看门狗按时收敛且不静默吞任务**、**失败时不得落 `_SUCCESS`**、**state 原子写回**。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from factorlab.adapters.batch_flock import BatchFlock, BatchLocked
from factorlab.ports.batch import BatchOrchestrator, BatchReport, Task


# ── 参与进程池的 worker 必须是模块级可 pickle 函数 ──
def _echo(t: Task):
    if t.key == "bad":
        raise RuntimeError("boom")
    if t.key == "slow":
        time.sleep(1.5)
    return {"rows": len(t.key), "key": t.key}


def _noop(t: Task):
    return {"ok": True}


def test_implements_the_port():
    assert isinstance(BatchFlock(), BatchOrchestrator)


def test_accounting_matches_inline_contract():
    """与 InlineOrchestrator 同语义：ok/failed 计数、失败不中断、metrics 透传。"""
    rep = BatchFlock().run(
        [Task(key="a"), Task(key="b"), Task(key="bad"), Task(key="c")], _echo,
        workers=2)
    assert isinstance(rep, BatchReport)
    assert (rep.done, rep.failed, rep.skipped) == (3, 1, 0)
    assert {r.key: r.status for r in rep.results}["bad"] == "failed"
    assert "boom" in [r.error for r in rep.results if r.status == "failed"][0]
    assert [r.metrics for r in rep.results if r.key == "a"] == [{"rows": 1, "key": "a"}]


def test_state_skips_done_keys_and_is_written_back(tmp_path):
    st = tmp_path / "state.json"
    st.write_text(json.dumps({"done": ["a"]}), encoding="utf-8")
    seen: list[str] = []
    rep = BatchFlock().run([Task(key="a"), Task(key="b")], _echo, workers=1,
                           state_path=st)
    assert (rep.done, rep.skipped) == (1, 1)
    assert rep.results[0].status == "skipped" and rep.results[0].key == "a"
    written = json.loads(st.read_text(encoding="utf-8"))["done"]
    assert set(written) == {"a", "b"}, "state 必须写回新完成的 key"
    assert "a" in written, "既有的已完成 key 不得丢（否则断点续跑重跑老任务）"
    assert not [p for p in tmp_path.iterdir() if ".tmp" in p.name]


def test_lock_busy_means_zero_execution(tmp_path):
    """锁被占 → 抛 `BatchLocked` 且**一个任务都不许跑**（单写者纪律）。"""
    lock = tmp_path / ".lock"
    lock.write_text("", encoding="utf-8")
    import fcntl
    f = open(lock, "a")
    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(BatchLocked):
            BatchFlock().run([Task(key="a")], _noop, workers=1, lock_path=lock)
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


def test_stall_watchdog_reports_instead_of_hanging():
    """停滞看门狗：stall_s 内无任何完成 → 该任务记账为 stalled 并按时返回（不静默吞）。"""
    t0 = time.time()
    rep = BatchFlock().run([Task(key="slow")], _echo, workers=1, stall_s=1)
    elapsed = time.time() - t0
    assert elapsed < 1.4, f"应在 stall_s 后立即收敛，实测 {elapsed:.1f}s"
    assert rep.failed == 1 and rep.done == 0
    assert "stall" in rep.results[0].error.lower()


def test_success_marker_only_when_all_ok(tmp_path):
    mark = tmp_path / "_SUCCESS"
    ok = BatchFlock().run([Task(key="a"), Task(key="b")], _noop, workers=2,
                          success_marker=mark)
    assert ok.failed == 0 and mark.is_file() and mark.read_bytes() == b""
    mark.unlink()
    bad = BatchFlock().run([Task(key="a"), Task(key="bad")], _echo, workers=2,
                           success_marker=mark)
    assert bad.failed == 1
    assert not mark.exists(), "有失败单元时不得落 _SUCCESS（否则消费侧会当整批完成）"


def test_workers_actually_parallelise():
    """workers=4 跑 4 个各睡 0.3s 的任务：串行需 ≥1.2s，并行应明显更快。"""
    tasks = [Task(key=f"p{i}") for i in range(4)]
    t0 = time.time()
    BatchFlock().run(tasks, _sleepy, workers=4)
    assert time.time() - t0 < 1.0


def _sleepy(t: Task):
    time.sleep(0.3)
    return {"ok": True}


def test_empty_task_list_is_ok(tmp_path):
    rep = BatchFlock().run([], _noop, workers=1, success_marker=tmp_path / "_SUCCESS")
    assert (rep.done, rep.failed, rep.skipped) == (0, 0, 0)
    assert (tmp_path / "_SUCCESS").is_file(), "空批=无事可做，仍应落标记（消费侧口径）"
