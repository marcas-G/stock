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
import time
from dataclasses import dataclass
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
