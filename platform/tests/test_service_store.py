"""T1 挖矿服务存储层测试（规格 §6 + 计划 T1）。

断言来源：`knowledge/design/platform/specs/2026-09-21-factorlab-service-design.md` §6
（作业模型字段 / 状态机 queued→running→{succeeded|failed|cancelled} / 重启 running→interrupted
不自动重跑）与计划 T1（claim_next 原子、WAL 并发、非法迁移拒绝）。
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from factorlab.surfaces.service.models import JobStatus, JobType
from factorlab.surfaces.service.store import JobStore

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


@pytest.fixture()
def store(tmp_path: Path) -> JobStore:
    s = JobStore(tmp_path / "jobs.sqlite3")
    yield s
    s.close()


def test_create_returns_queued_job_with_all_model_fields(store: JobStore):
    """规格 §6：创建即 queued；字段齐全（id/type/status/params/created_at）。"""
    job = store.create("factor_run", {"spec": "/r/factor/a.yaml", "set": ["n=20"]})

    assert job.id and ULID_RE.match(job.id), f"id 应为 ULID 形状: {job.id!r}"
    assert job.type == JobType.factor_run.value
    assert job.status == JobStatus.queued.value
    assert job.params == {"spec": "/r/factor/a.yaml", "set": ["n=20"]}
    assert job.created_at
    assert job.started_at is None
    assert job.finished_at is None
    assert job.exit_code is None
    assert job.error is None
    assert job.log_path is None
    assert job.result_path is None
    assert job.image_ref is None
    assert job.pid is None

    # 重开连接（跨进程重启语义）仍读得到
    store.close()
    reopened = JobStore(store.db_path)
    try:
        same = reopened.get(job.id)
        assert same is not None
        assert same.params == job.params
        assert same.status == "queued"
    finally:
        reopened.close()


def test_create_rejects_unknown_type_and_bad_params(store: JobStore):
    with pytest.raises(ValueError):
        store.create("rm_rf", {})
    with pytest.raises(ValueError):
        store.create("factor_run", ["not", "a", "dict"])  # type: ignore[arg-type]


def test_ids_are_unique(store: JobStore):
    ids = {store.create("compose", {"spec": f"/r/{i}.yaml"}).id for i in range(50)}
    assert len(ids) == 50
    assert all(ULID_RE.match(i) for i in ids)


def test_claim_next_is_fifo_and_sets_running(store: JobStore):
    first = store.create("factor_run", {"spec": "/r/a.yaml"})
    second = store.create("compose", {"spec": "/r/b.yaml"})

    claimed = store.claim_next()
    assert claimed is not None
    assert claimed.id == first.id, "FIFO：先入队先出队"
    assert claimed.status == JobStatus.running.value
    assert claimed.started_at is not None
    assert claimed.finished_at is None

    nxt = store.claim_next()
    assert nxt is not None and nxt.id == second.id
    assert store.claim_next() is None, "无 queued 时必须返回 None"


def test_finish_running_job_moves_to_terminal_status(store: JobStore):
    job = store.create("factor_run", {"spec": "/r/a.yaml"})
    claimed = store.claim_next()
    assert claimed is not None
    store.finish(claimed.id, status="succeeded", exit_code=0)

    done = store.get(job.id)
    assert done is not None
    assert done.status == "succeeded"
    assert done.exit_code == 0
    assert done.finished_at is not None
    assert done.error is None

    failed = store.create("compose", {"spec": "/r/b.yaml"})
    assert store.claim_next() is not None
    store.finish(failed.id, status="failed", exit_code=6, error="RUN_FAILED")
    record = store.get(failed.id)
    assert record is not None and record.status == "failed"
    assert record.exit_code == 6 and record.error == "RUN_FAILED"


def test_illegal_transitions_are_rejected(store: JobStore):
    queued = store.create("factor_run", {"spec": "/r/a.yaml"})
    # queued 不能直接 finish（必须先 running）
    with pytest.raises(ValueError):
        store.finish(queued.id, status="succeeded")
    # 不存在的作业
    with pytest.raises(ValueError):
        store.finish("01ZZZZZZZZZZZZZZZZZZZZZZZZ", status="failed")
    # 非法目标状态
    with pytest.raises(ValueError):
        store.finish(queued.id, status="queued")
    # finish 幂等性破坏：已完成不能再 finish
    assert store.claim_next() is not None
    store.finish(queued.id, status="succeeded")
    with pytest.raises(ValueError):
        store.finish(queued.id, status="failed")


def test_cancel_only_transitions_queued(store: JobStore):
    job = store.create("factor_run", {"spec": "/r/a.yaml"})
    assert store.cancel(job.id) is True
    cancelled = store.get(job.id)
    assert cancelled is not None
    assert cancelled.status == JobStatus.cancelled.value
    assert cancelled.finished_at is not None
    # 幂等：再次 cancel 返回 False（不再迁移）
    assert store.cancel(job.id) is False
    # running 由 runner 处理，store.cancel 不越权
    running = store.create("compose", {"spec": "/r/b.yaml"})
    assert store.claim_next() is not None
    assert store.cancel(running.id) is False
    assert store.get(running.id).status == JobStatus.running.value  # type: ignore[union-attr]


def test_requeue_interrupted_marks_running_without_rerun(store: JobStore):
    """规格 §6：服务重启 running→interrupted，且不自动重跑。"""
    a = store.create("factor_run", {"spec": "/r/a.yaml"})
    b = store.create("factor_run", {"spec": "/r/b.yaml"})
    assert store.claim_next() is not None
    assert store.claim_next() is not None
    assert store.queue_depth() == 0

    n = store.requeue_interrupted()
    assert n == 2
    for job_id in (a.id, b.id):
        record = store.get(job_id)
        assert record is not None
        assert record.status == JobStatus.interrupted.value
        assert record.finished_at is not None
    # 关键：interrupted 不回到 queued —— claim_next 拿不到（不自动重跑）
    assert store.claim_next() is None
    # 再次调用无 running 可迁移 → 0
    assert store.requeue_interrupted() == 0


def test_queue_depth_counts_only_queued(store: JobStore):
    for i in range(3):
        store.create("factor_run", {"spec": f"/r/{i}.yaml"})
    assert store.queue_depth() == 3
    assert store.claim_next() is not None
    assert store.queue_depth() == 2, "running 不算队列深度"


def test_list_filters_order_and_limit(store: JobStore):
    a = store.create("factor_run", {"spec": "/r/a.yaml"})
    b = store.create("compose", {"spec": "/r/b.yaml"})
    c = store.create("factor_run", {"spec": "/r/c.yaml"})
    store.cancel(b.id)

    rows = store.list()
    assert [r.id for r in rows] == [c.id, b.id, a.id], "按 created 倒序（新在前）"
    assert [r.id for r in store.list(status="cancelled")] == [b.id]
    assert {r.id for r in store.list(type="factor_run")} == {a.id, c.id}
    assert [r.id for r in store.list(limit=1)] == [c.id]
    with pytest.raises(ValueError):
        store.list(status="bogus")
    with pytest.raises(ValueError):
        store.list(limit=0)


def test_update_running_meta_only_for_running(store: JobStore):
    """T3 依赖：runner 在 running 期登记 pid/日志/结果路径。"""
    job = store.create("factor_run", {"spec": "/r/a.yaml"})
    with pytest.raises(ValueError):
        store.update_running(job.id, pid=123)  # queued 不可写
    assert store.claim_next() is not None
    updated = store.update_running(job.id, pid=4321, log_path="/s/logs/x.log",
                                   result_path="/s/results/x.json",
                                   image_ref="factorlab-svc:abc")
    assert updated.pid == 4321
    assert updated.log_path == "/s/logs/x.log"
    assert updated.result_path == "/s/results/x.json"
    assert updated.image_ref == "factorlab-svc:abc"
    with pytest.raises(ValueError):
        store.update_running(job.id, nope=1)


def test_claim_next_atomic_under_two_connections(tmp_path: Path):
    """T1 关键：两连接并发 claim，只有一个拿到那条 queued。"""
    db = tmp_path / "jobs.sqlite3"
    s1 = JobStore(db)
    s2 = JobStore(db)
    try:
        job = s1.create("factor_run", {"spec": "/r/a.yaml"})
        barrier = threading.Barrier(2)

        def claim(store: JobStore):
            barrier.wait()
            return store.claim_next()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result() for f in [pool.submit(claim, s1), pool.submit(claim, s2)]]

        winners = [r for r in results if r is not None]
        assert len(winners) == 1, f"应恰有一个 claim 成功: {results}"
        assert winners[0].id == job.id
        assert s1.get(job.id).status == "running"  # type: ignore[union-attr]
        assert s2.claim_next() is None
    finally:
        s1.close()
        s2.close()


def test_wal_mode_enabled(tmp_path: Path):
    s = JobStore(tmp_path / "jobs.sqlite3")
    try:
        mode = s._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        s.close()


def test_wal_concurrent_read_write_does_not_explode(tmp_path: Path):
    """WAL 下读写并发：writer 连续 create，reader 同时 list/get，均不抛异常。"""
    db = tmp_path / "jobs.sqlite3"
    writer = JobStore(db)
    reader = JobStore(db)
    errors: list[BaseException] = []
    stop = threading.Event()

    def write_loop():
        try:
            for i in range(60):
                writer.create("factor_run", {"spec": f"/r/{i}.yaml"})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            stop.set()

    def read_loop():
        try:
            while not stop.is_set():
                reader.list(limit=50)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write_loop), threading.Thread(target=read_loop)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, f"WAL 并发读写异常: {errors}"
    assert len(writer.list(limit=100)) == 60
    writer.close()
    reader.close()
