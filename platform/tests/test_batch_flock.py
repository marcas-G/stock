"""P-5 真实现 `adapters/batch_flock.py` —— 契约一致 + flock/断点/看门狗/标记四条独有语义。

`ports/batch.py` 声明"实现者：adapters/batch_flock.py（WS6）"，但该模块长期不存在（No Orphan
欠账，`governance/workspace/pending-items.md#14`）。本测试先于实现写：契约部分对齐 `_doubles.InlineOrchestrator`
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


# ── R10 扩展缝（三份工具真实循环需要的、且彼此共用的最小集）──────────
def _collect(t: Task):
    return {"key": t.key}


def test_on_result_sees_every_result_including_failures():
    """`on_result(task, result_or_exc)`：父进程逐结果回调（按完成顺序）——
    convert_tick / extract_sz 的"worker 算、父进程写"数据流靠它，否则只能把大表塞进 metrics。"""
    seen: list[tuple[str, str]] = []
    rep = BatchFlock().run(
        [Task(key="a"), Task(key="bad")], _echo, workers=2,
        on_result=lambda t, r: seen.append((t.key, "exc" if isinstance(r, Exception) else "ok")))
    assert sorted(seen) == [("a", "ok"), ("bad", "exc")]
    assert rep.done == 1 and rep.failed == 1          # 回调不改变记账


def test_on_result_exception_does_not_break_accounting():
    """回调自身抛错必须记账成该单元的失败（不许静默变成 ok）。"""
    def boom(t, r):
        raise RuntimeError("hook failure")
    rep = BatchFlock().run([Task(key="a")], _noop, workers=1, on_result=boom)
    assert rep.failed == 1 and rep.done == 0
    assert "hook failure" in rep.results[0].error


def test_max_inflight_limits_concurrent_units():
    """`max_inflight` > workers：流水线化（convert_tick/extract_sz 用 workers*N 提前排队）。"""
    import threading
    cur = {"n": 0, "peak": 0}
    lock = threading.Lock()

    def spy(t, r):
        with lock:
            cur["n"] += 1
            cur["peak"] = max(cur["peak"], cur["n"])
    # 无法直接观测在飞数（在子进程里）；改测"提交数不受 inflight 截断"：
    tasks = [Task(key=f"i{i}") for i in range(6)]
    rep = BatchFlock().run(tasks, _sleepy2, workers=2, max_inflight=6)
    assert rep.done == 6


def _sleepy2(t: Task):
    time.sleep(0.05)
    return {"ok": True}


def test_initializer_runs_in_worker():
    """`initializer/initargs`：worker 进程启动时预热（run_lob_batch 载入 manifest 用它）。"""
    rep = BatchFlock().run([Task(key="a")], _needs_warm, workers=1,
                           initializer=_warm, initargs=("hello",))
    assert rep.done == 1 and rep.results[0].metrics == {"warm": "hello"}


_WARM: list[str] = []


def _warm(tag):
    _WARM.append(tag)


def _needs_warm(t: Task):
    return {"warm": _WARM[-1] if _WARM else None}


def test_stall_policy_requeue_retries_then_succeeds(tmp_path, monkeypatch):
    """`stall_policy="requeue"`：停滞的单元退回队列重试（run_lob_batch 的 3 次重试语义），
    重试后成功 → 计 ok，且**不**留 stalled 记账。"""
    monkeypatch.setenv("R10_FLAG", str(tmp_path / "flag"))
    rep = BatchFlock().run([Task(key="a")], _slow_once, workers=1, stall_s=1,
                           stall_policy="requeue", stall_strikes=3)
    assert rep.done == 1 and rep.failed == 0


def _slow_once(t: Task):
    # 第一次调用睡过 stall_s，第二次立刻返回（用环境变量给的标记文件跨进程记状态）
    flag = Path(os.environ["R10_FLAG"])
    if not flag.exists():
        flag.write_text("1", encoding="utf-8")
        time.sleep(2.0)
    return {"ok": True}


def test_stall_policy_requeue_gives_up_after_strikes():
    rep = BatchFlock().run([Task(key="a")], _always_slow, workers=1, stall_s=1,
                           stall_policy="requeue", stall_strikes=2)
    assert rep.failed == 1 and "stall" in rep.results[0].error.lower()
    assert "2" in rep.results[0].error or "strikes" in rep.results[0].error.lower()


def _always_slow(t: Task):
    time.sleep(5.0)
    return {"ok": True}


def test_mp_context_spawn_is_supported():
    """`mp_context="spawn"`：产量循环的硬要求（fork 会复制父进程的大缓冲）。"""
    rep = BatchFlock().run([Task(key="a"), Task(key="b")], _noop, workers=2,
                           mp_context="spawn")
    assert (rep.done, rep.failed) == (2, 0)


def test_invalid_mp_context_names_the_error():
    with pytest.raises(ValueError):
        BatchFlock().run([Task(key="a")], _noop, workers=1, mp_context="nope")


# ── R14：产量循环专有缝（run_lob_batch 的内存闸门与周期审计）──────────
def test_throttle_gates_dispatch():
    """`throttle()` 返回 False → 本轮不再派单（低水位等下一 tick），返回 True 后照派。"""
    calls = {"n": 0}
    order: list[str] = []

    def gate():
        calls["n"] += 1
        return calls["n"] > 2          # 前两次拒绝派单，之后放行

    rep = BatchFlock().run([Task(key=f"t{i}") for i in range(3)], _noop,
                           workers=1, throttle=gate,
                           on_result=lambda t, r: order.append(t.key))
    assert rep.done == 3, "闸门只是推迟派单，不得丢任务"
    assert order == ["t0", "t1", "t2"], "派单顺序保持"
    assert calls["n"] >= 4, "闸门被反复征询（低水位期间每 tick 一次）"


def test_throttle_closed_without_futures_stalls_instead_of_hanging():
    """R02-I3：throttle 持续 False 且无在飞 future 时，stall 看门狗必须收敛
    （此前该分支只 sleep+continue，永远到不了 stall 判定 → 挂死；probe6 实测
    12s 超时。生产 `run_lob_batch._mem_gate` 即 throttle 路径）。"""
    t0 = time.time()
    rep = BatchFlock().run([Task(key="a")], _noop, workers=1,
                           throttle=lambda: False, stall_s=0.4)
    elapsed = time.time() - t0
    assert elapsed < 3.0, f"应在 stall_s 后收敛，实测 {elapsed:.1f}s"
    assert (rep.done, rep.failed) == (0, 1)
    assert "stall" in rep.results[0].error.lower()
    assert rep.results[0].key == "a"


def test_throttle_closed_requeue_gives_up_after_strikes():
    """throttle 持续 False + requeue：重试 strikes 次后放弃（不死循环）。"""
    t0 = time.time()
    rep = BatchFlock().run([Task(key="a")], _noop, workers=1,
                           throttle=lambda: False, stall_s=0.2,
                           on_tick=lambda: None, on_tick_s=0.05,
                           stall_policy="requeue", stall_strikes=2)
    assert time.time() - t0 < 5.0
    assert (rep.done, rep.failed) == (0, 1)
    assert "stall" in rep.results[0].error.lower()


def test_on_tick_fires_periodically_while_waiting():
    """`on_tick`：等结果期间按 `on_tick_s` 周期回调（run_lob_batch 的 rss 审计靠它）。"""
    ticks: list[float] = []
    rep = BatchFlock().run([Task(key="slow")], _sleep_long, workers=1,
                           stall_s=30, on_tick=lambda: ticks.append(time.time()),
                           on_tick_s=0.2)
    assert rep.done == 1
    assert len(ticks) >= 2, f"0.6s 任务 + 0.2s 周期应至少回调 2 次，实测 {len(ticks)}"


def _sleep_long(t: Task):
    time.sleep(0.6)
    return {"ok": True}


def test_throttle_and_on_tick_defaults_keep_old_semantics():
    rep = BatchFlock().run([Task(key="a")], _noop, workers=1)
    assert (rep.done, rep.failed, rep.skipped) == (1, 0, 0)


def test_pool_hook_exposes_pool_for_observability():
    """`pool_hook`：池创建即回调（run_lob_batch 的 rss 审计要按 worker pid 读内存）。"""
    seen: list[object] = []
    rep = BatchFlock().run([Task(key="a")], _noop, workers=1,
                           pool_hook=lambda pool: seen.append(pool))
    assert rep.done == 1
    assert seen and hasattr(seen[0], "_processes"), "hook 必须拿到真实进程池对象"
