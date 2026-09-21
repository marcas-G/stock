"""锁箱纪律内核（设计：knowledge/design/platform/specs/2026-09-21-lockbox-discipline-design.md）。

窗口：`[window_start, window_end]`（含边界）。window_start 只在季末 roll 时前移；
window_end = 最新数据日。登记 append-only（见 Task 3）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import socket
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ACCESS_KINDS = ("exploration", "final")
DEFAULT_QUOTA_FINAL = 20
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class LockboxError(Exception):
    """锁箱违规（稳定错误码见设计文档
    knowledge/design/platform/specs/2026-09-21-lockbox-discipline-design.md）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LockboxWindow:
    window_id: str
    start: dt.date
    end: dt.date


def quarter_end_before(as_of: dt.date) -> dt.date:
    """as_of 之前最近一个完整日历季末（严格早于 as_of）。"""
    quarter = (as_of.month - 1) // 3
    end_month = quarter * 3
    year = as_of.year
    if end_month == 0:
        year, end_month = year - 1, 12
    day = {3: 31, 6: 30, 9: 30, 12: 31}[end_month]
    return dt.date(year, end_month, day)


def window_id_of(quarter_end: dt.date) -> str:
    return f"{quarter_end.year}Q{(quarter_end.month - 1) // 3 + 1}"


def window_sort_key(window_id: str) -> tuple[int, int]:
    year, quarter = window_id.split("Q")
    return int(year), int(quarter)


def compute_window(*, as_of: dt.date, trading_days: Sequence[dt.date],
                   data_end: dt.date) -> LockboxWindow:
    """窗口起点 = 首个 ≥ cutoff 的交易日；输入序列无需有序（内部排序）。"""
    quarter_end = quarter_end_before(as_of)
    cutoff = quarter_end.replace(year=quarter_end.year - 1) + dt.timedelta(days=1)
    start = next((d for d in sorted(trading_days) if d >= cutoff), None)
    if start is None:
        raise LockboxError("LOCKBOX_NO_CALENDAR", f"交易日历无 ≥ {cutoff} 的交易日")
    if data_end < start:
        raise LockboxError("LOCKBOX_EMPTY_DATA",
                           f"最新数据日 {data_end} 早于窗口起点 {start}")
    return LockboxWindow(window_id_of(quarter_end), start, data_end)


def role_for(panel_start: dt.date, panel_end: dt.date,
             window: LockboxWindow) -> str:
    """is：整段 < window.start；lockbox：整段 ≥ window.start；mixed：跨边界。"""
    if panel_end < window.start:
        return "is"
    if panel_start >= window.start:
        return "lockbox"
    return "mixed"


def latest_data_date(health_root: Path) -> dt.date | None:
    """<health_root>/ashare_daily/*.json 的最大日期（分区文件名即交易日）。"""
    dataset_dir = Path(health_root) / "ashare_daily"
    if not dataset_dir.is_dir():
        return None
    days: list[dt.date] = []
    for path in dataset_dir.glob("*.json"):
        try:
            days.append(dt.date.fromisoformat(path.stem))
        except ValueError:
            continue
    return max(days) if days else None


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def spec_fingerprint(spec_doc: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(spec_doc).encode("utf-8")).hexdigest()


def candidate_fingerprint(*, artifact_sha256: str, params: Mapping[str, Any],
                          window_id: str, kind: str) -> str:
    payload = {"artifact": artifact_sha256, "kind": kind,
               "params": dict(params), "window_id": window_id}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def new_access_id() -> str:
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    chars: list[str] = []
    for _ in range(26):
        chars.append(_ULID_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def actor() -> str:
    return f"{os.getuid()}@{socket.gethostname()}"


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
    WHEN NEW.access_id != OLD.access_id OR NEW.ts_utc != OLD.ts_utc
      OR NEW.window_id != OLD.window_id OR NEW.kind != OLD.kind
      OR NEW.fingerprint != OLD.fingerprint OR NEW.reason != OLD.reason
    BEGIN SELECT RAISE(ABORT, 'lockbox_access is append-only'); END;
"""


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
            return LockboxWindow(state["window_id"],
                                 dt.date.fromisoformat(state["window_start"]),
                                 window.end), False
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
            f"状态窗口 {state['window_id']} 落后于当前季度 {expected.window_id}："
            "先 `factorlab lockbox roll`（解封旧窗并入 IS）")
    return LockboxWindow(state["window_id"],
                         dt.date.fromisoformat(state["window_start"]), data_end)


def status(conn: sqlite3.Connection, *, trading_days: Sequence[dt.date],
           data_end: dt.date) -> dict[str, Any]:
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
