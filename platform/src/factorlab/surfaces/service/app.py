"""FastAPI 作业服务（规格 §5 端点契约 + §7 队列上限；计划 T4）。

端点：POST /jobs(202) · GET /jobs · GET /jobs/{id} · log?tail · result ·
cancel · queue/pause|resume · health · version。
鉴权：token 文件存在 → 全端点要求 `Authorization: Bearer`（401）；不存在 → 仅本机。
错误统一 `{"error": {"code", "message"}}`。
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

from factorlab.surfaces.service.models import JobStatus
from factorlab.surfaces.service.params import JobParamError, validate_job
from factorlab.surfaces.service.store import JobStore

DEFAULT_TOKEN_PATH = Path.home() / ".config" / "factorlab" / "service_token"
DEFAULT_QUEUE_LIMIT = 32
_VERSION_KEYS = ("image_ref", "git_sha", "tag", "uv_lock_hash", "built_at")


def version_info_from_env(env: dict[str, str] | None = None) -> dict[str, str | None]:
    """版本段（规格 §5）：env 注入，缺省 null。"""
    src = os.environ if env is None else env
    mapping = {
        "image_ref": "FACTORLAB_IMAGE_REF",
        "git_sha": "FACTORLAB_GIT_SHA",
        "tag": "FACTORLAB_IMAGE_TAG",
        "uv_lock_hash": "FACTORLAB_UV_LOCK_HASH",
        "built_at": "FACTORLAB_BUILT_AT",
    }
    return {key: (src.get(env_key) or None) for key, env_key in mapping.items()}


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code,
                        content={"error": {"code": code, "message": message}})


def _load_token(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def create_service_app(*, store: JobStore, worker_state: Any, version_info: dict,
                       research_root: Path, token_path: Path | None = None,
                       queue_limit: int = DEFAULT_QUEUE_LIMIT) -> FastAPI:
    """构建作业服务应用（store/worker/版本信息显式传入，可测性单点）。"""
    research_root = Path(research_root)
    token = _load_token(Path(token_path) if token_path else DEFAULT_TOKEN_PATH)
    info = {key: None for key in _VERSION_KEYS}
    info.update(version_info or {})
    app = FastAPI(title="FactorLab Service")

    @app.middleware("http")
    async def require_bearer(request, call_next):
        if token is not None:
            header = request.headers.get("authorization", "")
            supplied = header[7:].strip() if header[:7].lower() == "bearer " else ""
            if not supplied or not secrets.compare_digest(supplied, token):
                return _error(401, "UNAUTHORIZED", "缺少或错误的 Bearer token")
        return await call_next(request)

    @app.post("/jobs", status_code=202)
    def create_job(payload: dict[str, Any] = Body(...)):
        job_type = payload.get("type") if isinstance(payload, dict) else None
        if not isinstance(job_type, str) or not job_type:
            return _error(422, "INVALID_PARAMS", "缺少作业类型 type")
        try:
            params = validate_job(job_type, payload, research_root=research_root)
        except JobParamError as exc:
            return _error(422, "INVALID_PARAMS", str(exc))
        if store.queue_depth() >= queue_limit:
            return _error(429, "QUEUE_FULL", f"队列已满（上限 {queue_limit}）")
        job = store.create(job_type, params, image_ref=info.get("image_ref"))
        return {"job_id": job.id, "status": job.status}

    @app.get("/jobs")
    def list_jobs(status: str | None = None, type: str | None = None,
                  limit: int = 50):
        try:
            jobs = store.list(status=status, type=type, limit=limit)
        except ValueError as exc:
            return _error(422, "INVALID_QUERY", str(exc))
        return [job.to_doc() for job in jobs]

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        job = store.get(job_id)
        if job is None:
            return _error(404, "NOT_FOUND", f"作业不存在: {job_id}")
        return job.to_doc()

    @app.get("/jobs/{job_id}/log")
    def get_log(job_id: str, tail: int = 200):
        job = store.get(job_id)
        if job is None:
            return _error(404, "NOT_FOUND", f"作业不存在: {job_id}")
        if tail < 1:
            return _error(422, "INVALID_QUERY", "tail 必须 >= 1")
        path = Path(job.log_path) if job.log_path else None
        if path is None or not path.is_file():
            return _error(404, "NOT_FOUND", "日志尚未生成")
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return _error(500, "LOG_UNREADABLE", str(exc))
        body = "\n".join(lines[-tail:])
        return PlainTextResponse(body + "\n" if body else "")

    @app.get("/jobs/{job_id}/result")
    def get_result(job_id: str):
        job = store.get(job_id)
        if job is None:
            return _error(404, "NOT_FOUND", f"作业不存在: {job_id}")
        path = Path(job.result_path) if job.result_path else None
        if path is None or not path.is_file():
            return _error(404, "NOT_FOUND", "结果尚未生成")
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _error(500, "RESULT_UNREADABLE", str(exc))
        if not isinstance(doc, dict):
            doc = {"envelope": doc}
        doc["service"] = {
            "image_ref": info.get("image_ref"),
            "git_sha": info.get("git_sha"),
            "uv_lock_hash": info.get("uv_lock_hash"),
            "dataset_version": (job.dataset_version
                                or job.params.get("dataset_version")
                                or info.get("dataset_version")),
            "started": job.started_at,
            "finished": job.finished_at,
        }
        return doc

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job = store.get(job_id)
        if job is None:
            return _error(404, "NOT_FOUND", f"作业不存在: {job_id}")
        if job.status in (JobStatus.cancelled.value, JobStatus.interrupted.value):
            return job.to_doc()  # 幂等
        if job.status in (JobStatus.succeeded.value, JobStatus.failed.value):
            return _error(409, "CONFLICT", f"作业已终态（{job.status}），不可取消")
        worker_state.cancel(job_id)
        fresh = store.get(job_id)
        return fresh.to_doc() if fresh is not None else job.to_doc()

    @app.post("/queue/pause")
    def pause_queue():
        worker_state.paused = True
        return {"paused": True}

    @app.post("/queue/resume")
    def resume_queue():
        worker_state.paused = False
        return {"paused": False}

    @app.get("/health")
    def health():
        try:
            depth = store.queue_depth()
            sqlite_ok = True
        except Exception:  # noqa: BLE001 —— 健康检查不因 DB 异常裸崩
            depth, sqlite_ok = 0, False
        return {"ok": sqlite_ok,
                "worker_busy": bool(getattr(worker_state, "busy", False)),
                "queue_depth": depth,
                "sqlite_ok": sqlite_ok}

    @app.get("/version")
    def version():
        return dict(info)

    return app


__all__ = ["DEFAULT_QUEUE_LIMIT", "DEFAULT_TOKEN_PATH", "create_service_app",
           "version_info_from_env"]
