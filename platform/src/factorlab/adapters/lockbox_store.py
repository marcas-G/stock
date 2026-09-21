"""锁箱存储（SQLite）：状态表 + 季度 roll + 登记表 schema。

窗口数学/指纹在 `core.lockbox`（纯核）；本模块只做文件/DB IO：
- `connect`：WAL + schema（`lockbox_state` 单行 + `lockbox_access` append-only）
- `roll`：状态推进（拒绝倒退；同窗幂等，配额可显式更新）
- `current_window`：state 与当前季度一致性检查（无 state/陈旧各有稳定错误码）
- `published_days`/`latest_data_date`：health 已发布日目录扫描
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any, Sequence

from factorlab.core.lockbox import (DEFAULT_QUOTA_FINAL, LockboxError,
                                    LockboxWindow, compute_window,
                                    window_sort_key)

_DDL = """
CREATE TABLE IF NOT EXISTS lockbox_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    window_id TEXT NOT NULL,
    window_start TEXT NOT NULL,
    quota_final INTEGER NOT NULL DEFAULT 20,
    rolled_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lockbox_access (
    access_id TEXT PRIMARY KEY,
    ts_utc TEXT NOT NULL,
    window_id TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('exploration','final')),
    fingerprint TEXT NOT NULL,
    artifact TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    command TEXT NOT NULL,
    result_ref TEXT,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    tool TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lockbox_win_kind_fp
    ON lockbox_access(window_id, kind, fingerprint);
CREATE TRIGGER IF NOT EXISTS lockbox_access_no_delete
    BEFORE DELETE ON lockbox_access
    BEGIN SELECT RAISE(ABORT, 'lockbox_access is append-only'); END;
CREATE TRIGGER IF NOT EXISTS lockbox_access_no_update
    BEFORE UPDATE ON lockbox_access
    WHEN NEW.access_id != OLD.access_id
      OR NEW.ts_utc != OLD.ts_utc
      OR NEW.window_id != OLD.window_id
      OR NEW.window_start != OLD.window_start
      OR NEW.window_end != OLD.window_end
      OR NEW.kind != OLD.kind
      OR NEW.fingerprint != OLD.fingerprint
      OR NEW.artifact != OLD.artifact
      OR NEW.params != OLD.params
      OR NEW.command != OLD.command
      OR NEW.reason != OLD.reason
      OR NEW.actor != OLD.actor
      OR NEW.tool != OLD.tool
    BEGIN SELECT RAISE(ABORT, 'lockbox_access is append-only'); END;
"""


def published_days(health_root: Path) -> list[dt.date]:
    """<health_root>/ashare_daily/*.json 的文件名日期（升序；非法文件名忽略）。"""
    dataset_dir = Path(health_root) / "ashare_daily"
    if not dataset_dir.is_dir():
        return []
    days: list[dt.date] = []
    for path in dataset_dir.glob("*.json"):
        try:
            days.append(dt.date.fromisoformat(path.stem))
        except ValueError:
            continue
    return sorted(days)


def latest_data_date(health_root: Path) -> dt.date | None:
    """最新已发布数据日；目录缺失或无可解析文件名返回 None。"""
    days = published_days(health_root)
    return max(days) if days else None


def connect(db_path: Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(_DDL)
    return conn


def load_state(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM lockbox_state WHERE id = 1").fetchone()
    return dict(row) if row is not None else None


def roll(conn: sqlite3.Connection, *, window: LockboxWindow,
         quota_final: int | None = None,
         now: dt.datetime | None = None) -> tuple[LockboxWindow, bool]:
    state = load_state(conn)
    if state is not None:
        if window_sort_key(window.window_id) < window_sort_key(state["window_id"]):
            raise LockboxError("LOCKBOX_ROLL_BACKWARD",
                               f"窗口倒退：state={state['window_id']} < roll={window.window_id}")
        if window.window_id == state["window_id"]:
            if quota_final is None or int(quota_final) == int(state["quota_final"]):
                return LockboxWindow(state["window_id"],
                                     dt.date.fromisoformat(state["window_start"]),
                                     window.end), False
            window = LockboxWindow(state["window_id"],
                                   dt.date.fromisoformat(state["window_start"]),
                                   window.end)
    quota = int(quota_final if quota_final is not None
                else (state or {}).get("quota_final", DEFAULT_QUOTA_FINAL))
    now = now or dt.datetime.now(dt.timezone.utc)
    conn.execute(
        "INSERT OR REPLACE INTO lockbox_state"
        " (id, window_id, window_start, quota_final, rolled_at) VALUES (1,?,?,?,?)",
        (window.window_id, window.start.isoformat(), quota,
         now.isoformat(timespec="seconds")))
    return window, True


def current_window(conn: sqlite3.Connection, *, as_of: dt.date,
                   trading_days: Sequence[dt.date],
                   data_end: dt.date) -> LockboxWindow:
    state = load_state(conn)
    if state is None:
        raise LockboxError("LOCKBOX_NO_STATE",
                           "锁箱未初始化：先 `factorlab lockbox roll`（IS 运行不需要）")
    expected = compute_window(as_of=as_of, trading_days=trading_days,
                              data_end=data_end)
    if expected.window_id != state["window_id"]:
        raise LockboxError(
            "LOCKBOX_WINDOW_STALE",
            f"状态窗口 {state['window_id']} 与当前季度窗口 {expected.window_id} 不一致："
            "先 `factorlab lockbox roll` 对齐（解封旧窗并入 IS）")
    return LockboxWindow(state["window_id"],
                         dt.date.fromisoformat(state["window_start"]), data_end)


def status(conn: sqlite3.Connection, *, trading_days: Sequence[dt.date],
           data_end: dt.date) -> dict[str, Any]:
    """锁箱状态摘要；未初始化（无 state）返回 `{"initialized": False}`。"""
    state = load_state(conn)
    if state is None:
        return {"initialized": False}
    used = final_count(conn, state["window_id"])
    return {
        "initialized": True,
        "window_id": state["window_id"],
        "window_start": state["window_start"],
        "window_end": data_end.isoformat(),
        "quota_final": int(state["quota_final"]),
        "final_used": used,
        "final_remaining": max(0, int(state["quota_final"]) - used),
        "rolled_at": state["rolled_at"],
    }


def final_count(conn: sqlite3.Connection, window_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM lockbox_access WHERE window_id = ? AND kind = 'final'",
        (window_id,)).fetchone()
    return int(row[0])
