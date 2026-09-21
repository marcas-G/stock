"""T4 FastAPI 服务契约测试（规格 §5 + 计划 T4）。

断言来源：规格 §5 端点/错误码/鉴权、§7 队列上限 32、§10-5 运维语义
（pause 后不出队、resume 恢复、cancel 幂等）与 §6 字段。
TestClient 进程内直测，不真起网络端口。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factorlab.surfaces.service.app import (create_service_app,
                                            version_info_from_env)
from factorlab.surfaces.service.models import JobStatus
from factorlab.surfaces.service.runner import Worker
from factorlab.surfaces.service.store import JobStore

PY = Path(sys.executable)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    (tmp_path / "factor" / "demo").mkdir(parents=True)
    (tmp_path / "composites" / "specs").mkdir(parents=True)
    (tmp_path / "strategy").mkdir()
    (tmp_path / "results").mkdir()
    (tmp_path / "factor" / "demo" / "a.yaml").write_text("name: a\n", encoding="utf-8")
    (tmp_path / "factor" / "b.yaml").write_text("name: b\n", encoding="utf-8")
    (tmp_path / "composites" / "specs" / "c.yaml").write_text("name: c\n", encoding="utf-8")
    (tmp_path / "strategy" / "s.yaml").write_text("name: s\n", encoding="utf-8")
    return tmp_path


def _fake_runner(cmd, log_path, timeout, register):
    log_path.write_text("warning: fake\n"
                        + json.dumps({"ok": True, "command": "factor.run"}) + "\n",
                        encoding="utf-8")
    return 0, False


def make_env(root: Path, *, queue_limit: int = 32, token_path: Path | None = None,
             version_info: dict | None = None, worker: Worker | None = None,
             runner=_fake_runner):
    state = root / "state"
    store = JobStore(state / "jobs.sqlite3")
    if worker is None:
        worker = Worker(store, concurrency=1, log_dir=state / "logs",
                        result_dir=state / "results", python=PY,
                        runner=runner, cli_results_dir=root / "results",
                        cache_dir=state / "cache")
    info = version_info if version_info is not None else {
        "image_ref": "factorlab-svc:testsha", "git_sha": "testsha",
        "tag": "stable", "uv_lock_hash": "lockhash", "built_at": "2026-09-21T00:00:00Z"}
    app = create_service_app(store=store, worker_state=worker, version_info=info,
                             research_root=root, token_path=token_path,
                             queue_limit=queue_limit)
    return TestClient(app), store, worker, app


def test_post_job_returns_202_and_persists(root: Path):
    client, store, _, _ = make_env(root)
    try:
        resp = client.post("/jobs", json={
            "type": "factor_run", "spec": "factor/demo/a.yaml", "set": ["n=5"]})
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "queued"
        job_id = body["job_id"]
        assert store.get(job_id) is not None

        detail = client.get(f"/jobs/{job_id}")
        assert detail.status_code == 200
        doc = detail.json()
        assert doc["type"] == "factor_run"
        assert doc["status"] == "queued"
        assert doc["params"]["spec"] == str((root / "factor" / "demo" / "a.yaml").resolve())
        assert doc["params"]["set"] == ["n=5"]
        assert doc["image_ref"] == "factorlab-svc:testsha"
        assert doc["created_at"] and doc["started_at"] is None
    finally:
        store.close()


@pytest.mark.parametrize("body", [
    {"type": "factor_run", "spec": "../../etc/passwd"},
    {"type": "factor_run", "spec": "strategy/s.yaml"},
    {"type": "factor_run", "spec": "factor/missing.yaml"},
    {"type": "factor_run"},
    {"spec": "factor/demo/a.yaml"},
    {"type": "shell_exec", "spec": "factor/demo/a.yaml"},
    {"type": "compose", "spec": "composites/specs/c.yaml", "set": ["n=1"]},
    {"type": "strategy_run", "doc": "strategy/s.yaml", "accept_quality": "FAIL",
     "override_reason": "x"},
])
def test_invalid_post_is_422_and_not_queued(root: Path, body: dict):
    client, store, _, _ = make_env(root)
    try:
        resp = client.post("/jobs", json=body)
        assert resp.status_code == 422
        err = resp.json()["error"]
        assert err["code"] and err["message"]
        assert store.queue_depth() == 0
    finally:
        store.close()


def test_get_unknown_job_404_envelope(root: Path):
    client, store, _, _ = make_env(root)
    try:
        resp = client.get("/jobs/01ZZZZZZZZZZZZZZZZZZZZZZZZ")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"
    finally:
        store.close()


def test_list_jobs_filters_and_order(root: Path):
    client, store, _, _ = make_env(root)
    try:
        first = client.post("/jobs", json={"type": "factor_run", "spec": "factor/demo/a.yaml"}).json()["job_id"]
        second = client.post("/jobs", json={"type": "compose", "spec": "composites/specs/c.yaml"}).json()["job_id"]
        third = client.post("/jobs", json={"type": "factor_run", "spec": "factor/b.yaml"}).json()["job_id"]
        client.post(f"/jobs/{second}/cancel")

        rows = client.get("/jobs").json()
        assert isinstance(rows, list) and len(rows) == 3
        assert [r["id"] for r in rows] == [third, second, first]

        filtered = client.get("/jobs", params={"type": "factor_run"}).json()
        assert {r["id"] for r in filtered} == {first, third}
        cancelled = client.get("/jobs", params={"status": "cancelled"}).json()
        assert [r["id"] for r in cancelled] == [second]
        limited = client.get("/jobs", params={"limit": 1}).json()
        assert [r["id"] for r in limited] == [third]
    finally:
        store.close()


def test_log_tail_returns_merged_text(root: Path):
    client, store, worker, _ = make_env(root)
    try:
        job_id = client.post("/jobs", json={"type": "factor_run",
                                            "spec": "factor/demo/a.yaml"}).json()["job_id"]
        # queued：日志尚未生成 → 404
        assert client.get(f"/jobs/{job_id}/log").status_code == 404

        assert worker.run_once() is True
        resp = client.get(f"/jobs/{job_id}/log", params={"tail": 1})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.text.strip() == json.dumps({"ok": True, "command": "factor.run"})

        full = client.get(f"/jobs/{job_id}/log").text
        assert "warning: fake" in full
        assert client.get(f"/jobs/{job_id}/log", params={"tail": 0}).status_code == 422
    finally:
        store.close()


def test_result_endpoint_adds_service_section(root: Path):
    client, store, worker, _ = make_env(root)
    try:
        job_id = client.post("/jobs", json={"type": "factor_run",
                                            "spec": "factor/demo/a.yaml"}).json()["job_id"]
        assert client.get(f"/jobs/{job_id}/result").status_code == 404

        assert worker.run_once() is True
        resp = client.get(f"/jobs/{job_id}/result")
        assert resp.status_code == 200
        doc = resp.json()
        assert doc["ok"] is True and doc["command"] == "factor.run", "CLI 信封原样保留"
        service = doc["service"]
        assert service["image_ref"] == "factorlab-svc:testsha"
        assert service["git_sha"] == "testsha"
        assert service["uv_lock_hash"] == "lockhash"
        assert service["dataset_version"] is None
        assert service["started"] and service["finished"]
    finally:
        store.close()


def test_cancel_queued_is_idempotent_and_conflict_on_terminal(root: Path):
    client, store, worker, _ = make_env(root)
    try:
        queued = client.post("/jobs", json={"type": "factor_run",
                                            "spec": "factor/demo/a.yaml"}).json()["job_id"]
        first = client.post(f"/jobs/{queued}/cancel")
        assert first.status_code == 200
        assert first.json()["status"] == "cancelled"
        again = client.post(f"/jobs/{queued}/cancel")
        assert again.status_code == 200
        assert again.json()["status"] == "cancelled"

        done = client.post("/jobs", json={"type": "compose",
                                          "spec": "composites/specs/c.yaml"}).json()["job_id"]
        assert worker.run_once() is True
        assert store.get(done).status == JobStatus.succeeded.value
        conflict = client.post(f"/jobs/{done}/cancel")
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "CONFLICT"

        missing = client.post("/jobs/01ZZZZZZZZZZZZZZZZZZZZZZZZ/cancel")
        assert missing.status_code == 404
    finally:
        store.close()


def test_cancel_running_via_api_kills_and_marks_cancelled(root: Path):
    def sleeper(job, python):
        return [sys.executable, "-c", "import time; time.sleep(30)"]

    state = root / "state"
    store = JobStore(state / "jobs.sqlite3")
    worker = Worker(store, concurrency=1, log_dir=state / "logs",
                    result_dir=state / "results", python=PY,
                    command_builder=sleeper, cli_results_dir=root / "results",
                    cache_dir=state / "cache", term_grace=0.2)
    app = create_service_app(store=store, worker_state=worker,
                             version_info={"image_ref": None, "git_sha": None,
                                           "tag": None, "uv_lock_hash": None,
                                           "built_at": None},
                             research_root=root)
    client = TestClient(app)
    try:
        job_id = client.post("/jobs", json={"type": "factor_run",
                                            "spec": "factor/demo/a.yaml"}).json()["job_id"]
        t = threading.Thread(target=worker.run_once)
        t.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            record = store.get(job_id)
            if record is not None and record.status == "running" and record.pid:
                break
            time.sleep(0.02)
        resp = client.post(f"/jobs/{job_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"
        t.join(timeout=15)
        assert store.get(job_id).status == JobStatus.cancelled.value  # type: ignore[union-attr]
    finally:
        store.close()


def test_pause_blocks_dequeue_and_resume_recovers(root: Path):
    client, store, worker, _ = make_env(root)
    stop = threading.Event()
    thread = threading.Thread(target=worker.run_forever, args=(stop,))
    thread.start()
    try:
        assert client.post("/queue/pause").json() == {"paused": True}
        assert worker.paused is True
        job_id = client.post("/jobs", json={"type": "factor_run",
                                            "spec": "factor/demo/a.yaml"}).json()["job_id"]
        time.sleep(0.3)
        assert store.get(job_id).status == JobStatus.queued.value, "pause 后不得出队"  # type: ignore[union-attr]

        assert client.post("/queue/resume").json() == {"paused": False}
        assert worker.paused is False
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if store.get(job_id).status == JobStatus.succeeded.value:  # type: ignore[union-attr]
                break
            time.sleep(0.02)
        assert store.get(job_id).status == JobStatus.succeeded.value  # type: ignore[union-attr]
    finally:
        stop.set()
        thread.join(timeout=10)
        store.close()


def test_queue_limit_429(root: Path):
    client, store, _, _ = make_env(root)
    try:
        for i in range(32):
            store.create("factor_run", {"spec": f"/r/{i}.yaml"})
        resp = client.post("/jobs", json={"type": "factor_run",
                                          "spec": "factor/demo/a.yaml"})
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "QUEUE_FULL"
        assert store.queue_depth() == 32
    finally:
        store.close()


def test_health_and_version_contract(root: Path):
    client, store, _, _ = make_env(root)
    try:
        health = client.get("/health").json()
        assert health["ok"] is True
        assert health["worker_busy"] is False
        assert health["queue_depth"] == 0
        assert health["sqlite_ok"] is True
        client.post("/jobs", json={"type": "factor_run", "spec": "factor/demo/a.yaml"})
        assert client.get("/health").json()["queue_depth"] == 1

        version = client.get("/version").json()
        assert version == {"image_ref": "factorlab-svc:testsha", "git_sha": "testsha",
                           "tag": "stable", "uv_lock_hash": "lockhash",
                           "built_at": "2026-09-21T00:00:00Z"}
    finally:
        store.close()


def test_version_info_from_env_defaults_to_null(monkeypatch):
    for key in ("FACTORLAB_IMAGE_REF", "FACTORLAB_GIT_SHA", "FACTORLAB_IMAGE_TAG",
                "FACTORLAB_UV_LOCK_HASH", "FACTORLAB_BUILT_AT"):
        monkeypatch.delenv(key, raising=False)
    assert version_info_from_env() == {"image_ref": None, "git_sha": None, "tag": None,
                                       "uv_lock_hash": None, "built_at": None}
    monkeypatch.setenv("FACTORLAB_IMAGE_REF", "factorlab-svc:abc")
    monkeypatch.setenv("FACTORLAB_GIT_SHA", "abc123")
    monkeypatch.setenv("FACTORLAB_UV_LOCK_HASH", "deadbeef")
    info = version_info_from_env()
    assert info["image_ref"] == "factorlab-svc:abc"
    assert info["git_sha"] == "abc123"
    assert info["uv_lock_hash"] == "deadbeef"


def test_bearer_token_required_when_token_file_exists(root: Path, tmp_path: Path):
    token_file = tmp_path / "service_token"
    token_file.write_text("sekret\n", encoding="utf-8")
    client, store, _, _ = make_env(root, token_path=token_file)
    try:
        assert client.get("/health").status_code == 401
        assert client.get("/version").status_code == 401
        assert client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = client.get("/health", headers={"Authorization": "Bearer sekret"})
        assert ok.status_code == 200
        post_ok = client.post("/jobs", json={"type": "factor_run", "spec": "factor/demo/a.yaml"},
                              headers={"Authorization": "Bearer sekret"})
        assert post_ok.status_code == 202
    finally:
        store.close()

    no_token = tmp_path / "missing_token"
    client2, store2, _, _ = make_env(root, token_path=no_token)
    try:
        assert client2.get("/health").status_code == 200, "token 文件不存在 → 仅本机无鉴权"
    finally:
        store2.close()
