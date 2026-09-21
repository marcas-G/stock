"""SQLite(WAL) 作业存储（规格 §6 / 计划 T1）。

并发语义：
- 连接 WAL + busy_timeout，读写并发不互斥；
- `claim_next` 用 `BEGIN IMMEDIATE` 拿写锁，SELECT+UPDATE 同事务 —— 多连接并发
  也只有一个能拿到同一条 queued（另一连接等到提交后看到空队列）；
- 非法状态迁移抛 ValueError（不静默改写）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from factorlab.surfaces.service.models import (JOB_TYPES, Job, JobStatus,
                                               JobType, new_job_id, now_iso)

_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    params TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    exit_code INTEGER,
    error TEXT,
    log_path TEXT,
    result_path TEXT,
    image_ref TEXT,
    dataset_version TEXT,
    pid INTEGER
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created
    ON jobs(status, created_at);
"""

_MIGRATIONS = (
    ("dataset_version", "ALTER TABLE jobs ADD COLUMN dataset_version TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    """旧库补列（部署中已有 jobs.sqlite3 时 CREATE IF NOT EXISTS 不会加列）。"""
    columns = {row["name"] for row in
               conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for column, ddl in _MIGRATIONS:
        if column not in columns:
            conn.execute(ddl)

_RUNNING_FIELDS = ("pid", "log_path", "result_path", "image_ref")


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        type=row["type"],
        status=row["status"],
        params=json.loads(row["params"] or "{}"),
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        exit_code=row["exit_code"],
        error=row["error"],
        log_path=row["log_path"],
        result_path=row["result_path"],
        image_ref=row["image_ref"],
        dataset_version=row["dataset_version"],
        pid=row["pid"],
    )


class JobStore:
    """作业队列持久化（SQLite，WAL；每实例一条连接 + 进程内锁）。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), timeout=10.0, isolation_level=None,
            check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.executescript(_DDL)
        _migrate(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- 创建 / 读取 -------------------------------------------------

    def create(self, type: str | JobType, params: dict[str, Any], *,
               image_ref: str | None = None) -> Job:
        type_value = type.value if isinstance(type, JobType) else type
        if not isinstance(type_value, str) or type_value not in JOB_TYPES:
            raise ValueError(f"未知作业类型: {type_value!r}（可用: {', '.join(JOB_TYPES)}）")
        if not isinstance(params, dict):
            raise ValueError(f"params 必须是对象: {params.__class__.__name__}")
        job = Job(id=new_job_id(),
                  type=type_value, status="queued",
                  params=json.loads(json.dumps(params, ensure_ascii=False)),
                  created_at=now_iso(), image_ref=image_ref)
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, type, status, params, created_at, image_ref)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (job.id, job.type, job.status,
                 json.dumps(params, ensure_ascii=False), job.created_at,
                 job.image_ref))
        return job

    def get(self, id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (id,)).fetchone()
        return _row_to_job(row) if row is not None else None

    def list(self, status: str | None = None, type: str | None = None,
             limit: int = 50) -> list[Job]:
        if status is not None and status not in {s.value for s in JobStatus}:
            raise ValueError(f"未知状态: {status}")
        if type is not None and type not in JOB_TYPES:
            raise ValueError(f"未知作业类型: {type}")
        if not isinstance(limit, int) or limit < 1:
            raise ValueError(f"limit 必须是正整数: {limit!r}")
        clauses, args = [], []
        if status is not None:
            clauses.append("status = ?")
            args.append(status)
        if type is not None:
            clauses.append("type = ?")
            args.append(type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC, rowid DESC"
                " LIMIT ?", args).fetchall()
        return [_row_to_job(r) for r in rows]

    def queue_depth(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'queued'").fetchone()
        return int(row[0])

    # ---- 状态迁移 ----------------------------------------------------

    def claim_next(self, *, dataset_version: str | None = None) -> Job | None:
        """原子取队首：queued→running（SELECT+UPDATE 同一 IMMEDIATE 事务）。

        `dataset_version`（L2）：作业开跑当刻的数据版本，随 claim 冻结入记录。
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM jobs WHERE status = 'queued'"
                    " ORDER BY created_at ASC, rowid ASC LIMIT 1").fetchone()
                if row is None:
                    self._conn.execute("ROLLBACK")
                    return None
                started = now_iso()
                cur = self._conn.execute(
                    "UPDATE jobs SET status = 'running', started_at = ?,"
                    " dataset_version = ?"
                    " WHERE id = ? AND status = 'queued'",
                    (started, dataset_version, row["id"]))
                if cur.rowcount != 1:
                    self._conn.execute("ROLLBACK")
                    return None
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        job = _row_to_job(row)
        job.status = "running"
        job.started_at = started
        job.dataset_version = dataset_version
        return job

    def finish(self, id: str, *, status: str, exit_code: int | None = None,
               error: str | None = None) -> None:
        if status not in ("succeeded", "failed", "cancelled"):
            raise ValueError(f"非法目标状态: {status}")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT status FROM jobs WHERE id = ?",
                                         (id,)).fetchone()
                if row is None:
                    raise ValueError(f"作业不存在: {id}")
                if row["status"] != "running":
                    raise ValueError(f"非法状态迁移: {row['status']} → {status}")
                self._conn.execute(
                    "UPDATE jobs SET status = ?, finished_at = ?, exit_code = ?,"
                    " error = ? WHERE id = ?",
                    (status, now_iso(), exit_code, error, id))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def cancel(self, id: str) -> bool:
        """queued→cancelled（立即）；running/终态返回 False（runner 处理 running）。"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT status FROM jobs WHERE id = ?",
                                         (id,)).fetchone()
                if row is None or row["status"] != "queued":
                    self._conn.execute("ROLLBACK")
                    return False
                self._conn.execute(
                    "UPDATE jobs SET status = 'cancelled', finished_at = ?"
                    " WHERE id = ?", (now_iso(), id))
                self._conn.execute("COMMIT")
                return True
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def requeue_interrupted(self) -> int:
        """启动时 running→interrupted（规格 §6：不自动重跑）。返回迁移数。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status = 'interrupted', finished_at = ?"
                " WHERE status = 'running'", (now_iso(),))
        return int(cur.rowcount)

    def update_running(self, id: str, **fields: Any) -> Job:
        """登记 running 期元数据（pid/日志/结果路径/image_ref）。"""
        unknown = set(fields) - set(_RUNNING_FIELDS)
        if unknown:
            raise ValueError(f"不可写字段: {sorted(unknown)}")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT status FROM jobs WHERE id = ?",
                                         (id,)).fetchone()
                if row is None:
                    raise ValueError(f"作业不存在: {id}")
                if row["status"] != "running":
                    raise ValueError(f"仅 running 可更新元数据（当前 {row['status']}）")
                if fields:
                    sets = ", ".join(f"{k} = ?" for k in fields)
                    self._conn.execute(
                        f"UPDATE jobs SET {sets} WHERE id = ?",
                        (*fields.values(), id))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        job = self.get(id)
        assert job is not None
        return job
