# 锁箱纪律（Rolling Lockbox）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立全库单一滚动锁箱（最近 12 个月为 OOS）：碰箱评估自动登记、终评唯一+配额、平台硬门拒跑、产物与档案可追溯。

**Architecture:** 内核 `factorlab.core.lockbox`（窗口数学 + SQLite 状态/登记）；平台在 run 家族（factor/compose/strategy/admit/ref）窗口判定处接线（`RunGuard`）；研究侧 `lab/lockbox.py` 薄封装 + manifest/档案字段 + G-LOCKBOX 门交叉审计。

**Tech Stack:** Python 3.13 / sqlite3(WAL) / Typer / pytest；无新依赖。

**Spec:** `knowledge/design/platform/specs/2026-09-21-lockbox-discipline-design.md`

## Global Constraints

- 锁箱定义：`[window_start, window_end]` 含边界；`window_start`=最近完整季末 Qe 的前一年同日+1 后的首个交易日；`window_end`=最新数据日（`<DATA_ROOT>/health/ashare_daily/*.json` 的最大日期）。
- 窗口只在季末 roll 时前移；同 `window_id` 幂等；跨季未 roll → 碰箱运行报 `LOCKBOX_WINDOW_STALE`。
- **未初始化（无 state）时：IS 运行照常；碰箱运行报 `LOCKBOX_NO_STATE`**（先 `factorlab lockbox roll`）。
- 登记 append-only（触发器禁 UPDATE/DELETE，`result_ref` 除外）；错误码：`LOCKBOX_INTENT_REQUIRED`/`LOCKBOX_REASON_REQUIRED`/`LOCKBOX_FINAL_DUPLICATE`/`LOCKBOX_QUOTA_EXCEEDED`/`LOCKBOX_FINAL_REQUIRED`/`LOCKBOX_WINDOW_STALE`/`LOCKBOX_NO_STATE`/`LOCKBOX_ROLL_BACKWARD`。
- 终评默认配额 M=20/窗口；探索不限额。
- 窗口日历源：`<DATA_ROOT>/health/ashare_daily/*.json` 的发布日期集合（与服务 `dataset_version` 同源、离线可测）；**不用 CH trade_cal**。
- 测试不得触碰真实 `$QUANTRESEARCH_ROOT/data/ledger.sqlite`：一律 tmp + `FACTORLAB_LOCKBOX_DB`。
- 每任务收尾：`cd platform && .venv/bin/python -m pytest -q` 相关文件绿 + `make gates` 绿 + 显式文件清单提交（一次一主题）。
- 源码文件不写注释以外的新依赖；中文 docstring 风格随库。

---

### Task 1: 内核——窗口数学与指纹

**Files:**
- Create: `platform/src/factorlab/core/lockbox.py`
- Test: `platform/tests/test_lockbox_window.py`

**Interfaces:**
- Produces: `LockboxError(code,message)`；`LockboxWindow(window_id:str,start:date,end:date)`；`quarter_end_before(as_of)->date`；`window_id_of(qe)->str`；`compute_window(*,as_of,trading_days,data_end)->LockboxWindow`；`role_for(panel_start,panel_end,window)->str`（is/mixed/lockbox）；`latest_data_date(health_root)->date|None`；`candidate_fingerprint(*,artifact_sha256,params,window_id,kind)->str`；`spec_fingerprint(spec_doc:Mapping)->str`；`new_access_id()->str`；`actor()->str`；常量 `ACCESS_KINDS/DEFAULT_QUOTA_FINAL`。

- [ ] **Step 1: 写失败测试**

```python
# platform/tests/test_lockbox_window.py
from __future__ import annotations
import datetime as dt, json
from pathlib import Path
import pytest
from factorlab.core.lockbox import (LockboxError, candidate_fingerprint,
                                    compute_window, latest_data_date,
                                    quarter_end_before, role_for,
                                    spec_fingerprint)

def _days(start: dt.date, n: int) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range(n)]

def test_quarter_end_before():
    assert quarter_end_before(dt.date(2026, 9, 21)) == dt.date(2026, 6, 30)
    assert quarter_end_before(dt.date(2026, 10, 1)) == dt.date(2026, 9, 30)
    assert quarter_end_before(dt.date(2026, 1, 15)) == dt.date(2025, 12, 31)

def test_compute_window_start_is_first_trading_day_after_cutoff():
    # 2026-09-21 → Qe=2026-06-30 → cutoff=2025-07-01（周三）
    days = [dt.date(2025, 6, 30), dt.date(2025, 7, 1), dt.date(2026, 9, 18)]
    w = compute_window(as_of=dt.date(2026, 9, 21), trading_days=days,
                       data_end=dt.date(2026, 9, 18))
    assert w.window_id == "2026Q2"
    assert w.start == dt.date(2025, 7, 1)
    assert w.end == dt.date(2026, 9, 18)

def test_compute_window_roll_at_next_quarter():
    days = [dt.date(2025, 10, 1), dt.date(2026, 9, 18)]
    w = compute_window(as_of=dt.date(2026, 10, 1), trading_days=days,
                       data_end=dt.date(2026, 9, 30))
    assert (w.window_id, w.start) == ("2026Q3", dt.date(2025, 10, 1))

def test_compute_window_rejects_data_before_start():
    with pytest.raises(LockboxError) as e:
        compute_window(as_of=dt.date(2026, 9, 21),
                       trading_days=[dt.date(2026, 1, 5)],
                       data_end=dt.date(2025, 12, 31))
    assert e.value.code == "LOCKBOX_EMPTY_DATA"

def test_role_for_boundaries():
    w = compute_window(as_of=dt.date(2026, 9, 21),
                       trading_days=[dt.date(2025, 7, 1)],
                       data_end=dt.date(2026, 9, 18))
    assert role_for(dt.date(2020, 1, 1), dt.date(2025, 6, 30), w) == "is"
    assert role_for(dt.date(2025, 6, 1), dt.date(2025, 7, 2), w) == "mixed"
    assert role_for(dt.date(2025, 7, 1), dt.date(2026, 9, 18), w) == "lockbox"
    assert role_for(dt.date(2025, 7, 2), dt.date(2026, 9, 18), w) == "lockbox"

def test_latest_data_date(tmp_path: Path):
    d = tmp_path / "ashare_daily"; d.mkdir()
    (d / "2026-09-17.json").write_text("{}", encoding="utf-8")
    (d / "2026-09-18.json").write_text("{}", encoding="utf-8")
    (d / "junk.json").write_text("{}", encoding="utf-8")
    assert latest_data_date(tmp_path) == dt.date(2026, 9, 18)
    assert latest_data_date(tmp_path / "missing") is None

def test_fingerprint_stable_and_param_sensitive():
    f1 = candidate_fingerprint(artifact_sha256="a", params={"x": 1},
                               window_id="2026Q2", kind="final")
    f2 = candidate_fingerprint(artifact_sha256="a", params={"x": 1},
                               window_id="2026Q2", kind="final")
    f3 = candidate_fingerprint(artifact_sha256="a", params={"x": 2},
                               window_id="2026Q2", kind="final")
    assert f1 == f2 and f1 != f3 and len(f1) == 64
    s1 = spec_fingerprint({"b": 2, "a": 1}); s2 = spec_fingerprint({"a": 1, "b": 2})
    assert s1 == s2, "spec 指纹须对键序不敏感（canonical JSON）"
```

- [ ] **Step 2: 跑测试见红**

Run: `cd platform && .venv/bin/python -m pytest tests/test_lockbox_window.py -q`
Expected: `ModuleNotFoundError: factorlab.core.lockbox`

- [ ] **Step 3: 实现**

```python
# platform/src/factorlab/core/lockbox.py
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
from pathlib import Path
from typing import Any, Mapping, Sequence

ACCESS_KINDS = ("exploration", "final")
DEFAULT_QUOTA_FINAL = 20
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class LockboxError(Exception):
    """锁箱违规（稳定错误码见模块 docstring 的 Global Constraints）。"""

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
    quarter_end = quarter_end_before(as_of)
    cutoff = quarter_end.replace(year=quarter_end.year - 1) + dt.timedelta(days=1)
    start = next((d for d in trading_days if d >= cutoff), None)
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
```

- [ ] **Step 4: 跑测试见绿**

Run: `cd platform && .venv/bin/python -m pytest tests/test_lockbox_window.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add platform/src/factorlab/core/lockbox.py platform/tests/test_lockbox_window.py
git commit -m "feat(lockbox): 窗口数学/角色判定/指纹内核（T1）"
```

---

### Task 2: 内核——状态与 roll

**Files:**
- Modify: `platform/src/factorlab/core/lockbox.py`
- Test: `platform/tests/test_lockbox_state.py`

**Interfaces:**
- Consumes: T1。
- Produces: `connect(db_path)->sqlite3.Connection`（WAL+schema+row_factory）；`load_state(conn)->dict|None`；`roll(conn,*,window,quota_final=None,now=None)->tuple[LockboxWindow,bool]`；`current_window(conn,*,as_of,trading_days,data_end)->LockboxWindow`（无 state→`LOCKBOX_NO_STATE`；跨季→`LOCKBOX_WINDOW_STALE`）；`status(conn,*,trading_days,data_end)->dict`。

- [ ] **Step 1: 写失败测试**

```python
# platform/tests/test_lockbox_state.py
from __future__ import annotations
import datetime as dt
from pathlib import Path
import pytest
from factorlab.core.lockbox import (LockboxError, compute_window, connect,
                                    current_window, load_state, roll, status)

DAY = dt.date(2026, 9, 18)
DAYS = [dt.date(2025, 7, 1), dt.date(2025, 10, 1), DAY]

def _window(as_of=dt.date(2026, 9, 21), data_end=DAY):
    return compute_window(as_of=as_of, trading_days=DAYS, data_end=data_end)

def test_roll_then_idempotent(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    w, changed = roll(conn, window=_window())
    assert changed and load_state(conn)["window_id"] == "2026Q2"
    w2, changed2 = roll(conn, window=_window())
    assert not changed2 and w2.start == w.start

def test_roll_rejects_backward(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    roll(conn, window=_window(as_of=dt.date(2026, 10, 1),
                              data_end=dt.date(2026, 9, 30)))
    with pytest.raises(LockboxError) as e:
        roll(conn, window=_window())
    assert e.value.code == "LOCKBOX_ROLL_BACKWARD"

def test_current_window_requires_state(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    with pytest.raises(LockboxError) as e:
        current_window(conn, as_of=dt.date(2026, 9, 21), trading_days=DAYS,
                       data_end=DAY)
    assert e.value.code == "LOCKBOX_NO_STATE"

def test_current_window_stale_across_quarter(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    roll(conn, window=_window())
    with pytest.raises(LockboxError) as e:
        current_window(conn, as_of=dt.date(2026, 10, 2), trading_days=DAYS,
                       data_end=DAY)
    assert e.value.code == "LOCKBOX_WINDOW_STALE"

def test_status_shape(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    roll(conn, window=_window())
    st = status(conn, trading_days=DAYS, data_end=DAY)
    assert st["window_id"] == "2026Q2" and st["quota_final"] == 20
    assert st["window_start"] == "2025-07-01"
    assert st["window_end"] == "2026-09-18"
    assert st["final_used"] == 0 and st["final_remaining"] == 20
```

- [ ] **Step 2: 见红** — Run: `cd platform && .venv/bin/python -m pytest tests/test_lockbox_state.py -q` → `ImportError: cannot import name 'connect'`

- [ ] **Step 3: 实现（追加到 `core/lockbox.py`）**

```python
import sqlite3

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
```

> `final_count` 在 Task 3 实现；本步先在文件末尾加占位实现（Task 3 会替换为真体，测试本步不调用）：

```python
def final_count(conn: sqlite3.Connection, window_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM lockbox_access WHERE window_id = ? AND kind = 'final'",
        (window_id,)).fetchone()
    return int(row[0])
```

- [ ] **Step 4: 跑绿** — `cd platform && .venv/bin/python -m pytest tests/test_lockbox_window.py tests/test_lockbox_state.py -q` → PASS

- [ ] **Step 5: 提交**

```bash
git add platform/src/factorlab/core/lockbox.py platform/tests/test_lockbox_state.py
git commit -m "feat(lockbox): 状态表与季度 roll/陈旧判定（T2）"
```

---

### Task 3: 内核——登记、配额、终评校验

**Files:**
- Modify: `platform/src/factorlab/core/lockbox.py`
- Test: `platform/tests/test_lockbox_registry.py`

**Interfaces:**
- Produces: `register_access(conn,*,kind,fingerprint,artifact,params,command,reason,window,tool,result_ref=None)->str`；`update_result_ref(conn,access_id,result_ref)`；`final_count(conn,window_id)`；`final_exists(conn,window_id,fingerprint)`；`require_final(conn,*,window_id,fingerprint)->str`；`RunGuard`（见 T5 接口注释，本任务先产出 registry 原语）。

- [ ] **Step 1: 写失败测试**

```python
# platform/tests/test_lockbox_registry.py
from __future__ import annotations
import datetime as dt, sqlite3
from pathlib import Path
import pytest
from factorlab.core.lockbox import (LockboxError, LockboxWindow, connect,
                                    final_count, final_exists, register_access,
                                    require_final, update_result_ref)

W = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 9, 18))

def _reg(conn, kind="exploration", fp="fp1", reason="探索"):
    return register_access(conn, kind=kind, fingerprint=fp, artifact="factor/x.yaml",
                           params={"set": ["n=20"]}, command="factor run",
                           reason=reason, window=W, tool="factorlab test")

def test_register_requires_reason(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    with pytest.raises(LockboxError) as e:
        _reg(conn, reason="  ")
    assert e.value.code == "LOCKBOX_REASON_REQUIRED"

def test_final_unique_per_fingerprint(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    first = _reg(conn, kind="final", fp="fpA", reason="入库")
    assert final_exists(conn, W.window_id, "fpA")
    assert require_final(conn, window_id=W.window_id, fingerprint="fpA") == first
    with pytest.raises(LockboxError) as e:
        _reg(conn, kind="final", fp="fpA", reason="重评")
    assert e.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert final_count(conn, W.window_id) == 1, "重复被拒不得计入"

def test_quota_exhausted(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    for i in range(3):
        _reg(conn, kind="final", fp=f"fp{i}", reason="入库")
    with pytest.raises(LockboxError) as e:
        # state 未建时用默认 20；显式建 state 后改 3
        conn.execute("INSERT OR REPLACE INTO lockbox_state VALUES (1,'2026Q2',"
                     "'2025-07-01',3,'2026-09-21T00:00:00+00:00')")
        _reg(conn, kind="final", fp="fpX", reason="超额")
    assert e.value.code == "LOCKBOX_QUOTA_EXCEEDED"

def test_exploration_unlimited_and_append_only(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    for i in range(25):
        _reg(conn, kind="exploration", fp=f"e{i}", reason="探索")
    aid = _reg(conn, kind="exploration", fp="e-final", reason="探索")
    update_result_ref(conn, aid, "/quantresearch/results/platform/x")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE lockbox_access SET kind='final' WHERE access_id=?", (aid,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM lockbox_access WHERE access_id=?", (aid,))

def test_require_final_missing(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    with pytest.raises(LockboxError) as e:
        require_final(conn, window_id=W.window_id, fingerprint="nope")
    assert e.value.code == "LOCKBOX_FINAL_REQUIRED"
```

- [ ] **Step 2: 见红** — Run: `cd platform && .venv/bin/python -m pytest tests/test_lockbox_registry.py -q` → `ImportError: register_access`

- [ ] **Step 3: 实现（追加；并替换 T2 的 `final_count` 占位为同体真实现）**

```python
def register_access(conn, *, kind: str, fingerprint: str, artifact: str,
                    params: Mapping[str, Any], command: str, reason: str,
                    window: LockboxWindow, tool: str,
                    result_ref: str | None = None) -> str:
    if kind not in ACCESS_KINDS:
        raise ValueError(f"未知访问类型 {kind!r}；可选 {ACCESS_KINDS}")
    if not (reason or "").strip():
        raise LockboxError("LOCKBOX_REASON_REQUIRED", "锁箱访问必须给出非空理由")
    if kind == "final":
        if final_exists(conn, window.window_id, fingerprint):
            raise LockboxError("LOCKBOX_FINAL_DUPLICATE",
                               f"候选 {fingerprint[:12]}… 在窗口 {window.window_id} 已有终评")
        state = load_state(conn)
        quota = int((state or {}).get("quota_final", DEFAULT_QUOTA_FINAL))
        if final_count(conn, window.window_id) >= quota:
            raise LockboxError("LOCKBOX_QUOTA_EXCEEDED",
                               f"窗口 {window.window_id} 终评配额 {quota} 已用尽")
    access_id = new_access_id()
    conn.execute(
        "INSERT INTO lockbox_access (access_id, ts_utc, window_id, window_start,"
        " window_end, kind, fingerprint, artifact, params, command, result_ref,"
        " reason, actor, tool) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (access_id, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
         window.window_id, window.start.isoformat(), window.end.isoformat(),
         kind, fingerprint, artifact, _canonical(dict(params)), command,
         result_ref, reason.strip(), actor(), tool))
    return access_id


def update_result_ref(conn, access_id: str, result_ref: str) -> None:
    conn.execute("UPDATE lockbox_access SET result_ref = ? WHERE access_id = ?",
                 (result_ref, access_id))


def final_exists(conn, window_id: str, fingerprint: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM lockbox_access WHERE window_id = ? AND kind = 'final'"
        " AND fingerprint = ? LIMIT 1", (window_id, fingerprint)).fetchone()
    return row is not None


def require_final(conn, *, window_id: str, fingerprint: str) -> str:
    row = conn.execute(
        "SELECT access_id FROM lockbox_access WHERE window_id = ? AND kind = 'final'"
        " AND fingerprint = ? LIMIT 1", (window_id, fingerprint)).fetchone()
    if row is None:
        raise LockboxError(
            "LOCKBOX_FINAL_REQUIRED",
            f"窗口 {window_id} 缺终评登记（候选 {fingerprint[:12]}…）："
            "先 `flab factor run <spec> --lockbox final --lockbox-reason <理由>`")
    return str(row[0])
```

- [ ] **Step 4: 跑绿** — `cd platform && .venv/bin/python -m pytest tests/test_lockbox_registry.py tests/test_lockbox_state.py -q` → PASS

- [ ] **Step 5: 提交**

```bash
git add platform/src/factorlab/core/lockbox.py platform/tests/test_lockbox_registry.py
git commit -m "feat(lockbox): 登记/唯一性/配额/append-only（T3）"
```

---

### Task 4: 配置与 `factorlab lockbox status|roll`

**Files:**
- Modify: `platform/src/factorlab/config.py`（约 65-90 行 `Settings` 字段区）
- Modify: `platform/src/factorlab/surfaces/cli/main.py`（`app` 定义区，约 26-31 行）
- Test: `platform/tests/test_lockbox_cli.py`

**Interfaces:**
- Consumes: T1-T3。
- Produces: `settings.lockbox_db: Path`（env `FACTORLAB_LOCKBOX_DB`）；CLI `factorlab lockbox status [--json]` / `roll [--as-of YYYY-MM-DD] [--quota-final N]`；模块级 `_lockbox_trading_days()` 供测试 monkeypatch。

- [ ] **Step 1: 写失败测试**

```python
# platform/tests/test_lockbox_cli.py
from __future__ import annotations
import datetime as dt, json
from pathlib import Path
from typer.testing import CliRunner
from factorlab.surfaces.cli import main as cli_main
from factorlab.surfaces.cli.main import app

DAYS = [dt.date(2025, 7, 1), dt.date(2026, 9, 18)]

def test_lockbox_roll_then_status(tmp_path: Path, monkeypatch):
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setenv("FACTORLAB_LOCKBOX_DB", str(db))
    monkeypatch.setattr(cli_main, "_lockbox_published_days", lambda: DAYS)
    monkeypatch.setattr(cli_main, "_lockbox_data_end", lambda: dt.date(2026, 9, 18))
    monkeypatch.setattr(cli_main, "_lockbox_today", lambda: dt.date(2026, 9, 21))
    runner = CliRunner()
    r1 = runner.invoke(app, ["lockbox", "roll"])
    assert r1.exit_code == 0, r1.output
    r2 = runner.invoke(app, ["lockbox", "status", "--json"])
    assert r2.exit_code == 0, r2.output
    doc = json.loads(r2.output.strip().splitlines()[-1])
    assert doc["window_id"] == "2026Q2" and doc["final_remaining"] == 20
    assert doc["is_end"] == "2025-06-30"
```

- [ ] **Step 2: 见红** — `cd platform && .venv/bin/python -m pytest tests/test_lockbox_cli.py -q` → `No such command 'lockbox'`

- [ ] **Step 3: 实现**

`config.py` 在 `results_dir` 字段邻近加：

```python
def default_lockbox_db() -> Path:
    return default_research_root() / "data" / "ledger.sqlite"

# Settings 内：
    lockbox_db: Path = Field(default_factory=default_lockbox_db)  # FACTORLAB_LOCKBOX_DB
```

`main.py` 在 `app.add_typer(research_app, ...)` 之后加：

```python
lockbox_app = typer.Typer(no_args_is_help=True)
app.add_typer(lockbox_app, name="lockbox")


def _lockbox_published_days() -> list[dt.date]:
    """锁箱日历 = health 已发布日期（与服务 dataset_version 同源；离线可测）。"""
    from factorlab.core.factio.paths import DATA_ROOT
    from factorlab.core.lockbox import latest_data_date  # noqa: F401  （文档锚）
    days: list[dt.date] = []
    root = Path(DATA_ROOT) / "health" / "ashare_daily"
    for p in sorted(root.glob("*.json")):
        try:
            days.append(dt.date.fromisoformat(p.stem))
        except ValueError:
            continue
    return days


def _lockbox_data_end() -> dt.date | None:
    from factorlab.core.factio.paths import DATA_ROOT
    from factorlab.core.lockbox import latest_data_date
    return latest_data_date(Path(DATA_ROOT) / "health")


def _lockbox_today() -> dt.date:
    return dt.date.today()


@lockbox_app.command("status")
def lockbox_status(json_out: bool = typer.Option(False, "--json")) -> None:
    """锁箱窗口/配额/剩余（未初始化时 initialized=false）。"""
    from factorlab.core import lockbox as lb
    conn = lb.connect(settings.lockbox_db)
    data_end = _lockbox_data_end() or _lockbox_today()
    doc = lb.status(conn, trading_days=_lockbox_published_days(), data_end=data_end)
    if doc.get("initialized"):
        doc["is_end"] = (dt.date.fromisoformat(doc["window_start"])
                         - dt.timedelta(days=1)).isoformat()
    if json_out:
        console.print_json(json.dumps(doc, ensure_ascii=False))
    else:
        console.print(doc)
    if not doc.get("initialized"):
        raise typer.Exit(code=1)


@lockbox_app.command("roll")
def lockbox_roll(
    as_of: str | None = typer.Option(None, "--as-of", help="ISO 日期（缺省今天）"),
    quota_final: int | None = typer.Option(None, "--quota-final", min=1),
) -> None:
    """季度滚动（幂等；拒绝倒退）。"""
    from factorlab.core import lockbox as lb
    as_of_date = dt.date.fromisoformat(as_of) if as_of else _lockbox_today()
    window = lb.compute_window(as_of=as_of_date, trading_days=_lockbox_published_days(),
                               data_end=_lockbox_data_end() or as_of_date)
    conn = lb.connect(settings.lockbox_db)
    rolled, changed = lb.roll(conn, window=window, quota_final=quota_final)
    console.print(f"[lockbox] window={rolled.window_id} start={rolled.start} "
                  f"end={rolled.end} {'已更新' if changed else '无变化（幂等）'}")
```

> 若 `factorlab.surfaces.cli.helpers.read_handle` 不存在，用 `platform/src/factorlab/adapters/read/__init__.py` 的 `open_read()`（与 `research/health.py::read_handle` 同源）封装；实现时以实际导出为准，不许自造。

- [ ] **Step 4: 跑绿** — `cd platform && .venv/bin/python -m pytest tests/test_lockbox_cli.py -q` → PASS

- [ ] **Step 5: 提交**

```bash
git add platform/src/factorlab/config.py platform/src/factorlab/surfaces/cli/main.py platform/tests/test_lockbox_cli.py
git commit -m "feat(lockbox): lockbox_db 配置与 status/roll CLI（T4）"
```

---

### Task 5: run 硬门接线（execute 层统一）与产物声明

**Files:**
- Modify: `platform/src/factorlab/core/lockbox.py`（`RunGuard` + `guard_run`）
- Modify: `platform/src/factorlab/app/context.py`（`RunContext.guard`）
- Modify: `platform/src/factorlab/surfaces/cli/main.py`（`execute_run` 在 `spec = load_spec` 之后 guard；顶层 `run` 两选项；复用 T4 helpers）
- Modify: `platform/src/factorlab/app/run.py`（summary 组装后 `ctx.guard.attach(summary)`；产物落盘后 `ctx.guard.mark_result(str(run_dir))`）
- Modify: `platform/src/factorlab/research/factor.py`（`_RUN_PARAMS` 两参数；`factor_run` 透传 + `LockboxError` → 信封映射）
- Test: `platform/tests/test_lockbox_guard.py`、`platform/tests/test_lockbox_run_cli.py`

**Interfaces:**
- Consumes: T1-T4。
- Produces: `RunGuard(info/attach/mark_result/register_final)`；`guard_run(panel_start, panel_end, intent, reason, spec_doc, artifact, command, tool, db_path, trading_days, data_end)->RunGuard`；`RunContext.guard`；`execute_run(..., lockbox_intent=None, lockbox_reason=None)`；`summary["sample"]`。

- [ ] **Step 1: 写失败测试（单元）**（同前 T5 节：`test_lockbox_guard.py` 四个用例保持原样——`guard_run` 直接调用）

- [ ] **Step 2: 写失败测试（execute 层，无 DB 依赖）**

```python
# platform/tests/test_lockbox_run_cli.py
from __future__ import annotations
import datetime as dt, json
from pathlib import Path
import pytest
from typer.testing import CliRunner
from factorlab.config import settings
from factorlab.core.lockbox import LockboxError, connect
from factorlab.surfaces.cli import main as cli_main
from factorlab.surfaces.cli.main import app, execute_run

SPEC = ("name: x\nformula: close\ndate:\n  start: '2026-01-05'\n"
        "  end: '2026-09-18'\n")

def _sandbox(tmp_path: Path, monkeypatch):
    qr = tmp_path / "qr"
    (qr / "factor" / "demo").mkdir(parents=True)
    spec = qr / "factor" / "demo" / "x.yaml"
    spec.write_text(SPEC, encoding="utf-8")
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(settings, "lockbox_db", db)
    monkeypatch.setattr(cli_main, "_lockbox_published_days",
                        lambda: [dt.date(2026, 1, 5), dt.date(2026, 9, 18)])
    monkeypatch.setattr(cli_main, "_lockbox_data_end", lambda: dt.date(2026, 9, 18))
    monkeypatch.setattr(cli_main, "_lockbox_today", lambda: dt.date(2026, 9, 21))
    CliRunner().invoke(app, ["lockbox", "roll"])   # 初始化状态
    return spec, db

def test_execute_refuses_without_intent(tmp_path: Path, monkeypatch):
    spec, db = _sandbox(tmp_path, monkeypatch)
    with pytest.raises(LockboxError) as e:
        execute_run(spec, backtest=False)
    assert e.value.code == "LOCKBOX_INTENT_REQUIRED"
    assert connect(db).execute(
        "SELECT COUNT(*) FROM lockbox_access").fetchone()[0] == 0

def test_execute_refuses_without_reason(tmp_path: Path, monkeypatch):
    spec, _ = _sandbox(tmp_path, monkeypatch)
    with pytest.raises(LockboxError) as e:
        execute_run(spec, backtest=False, lockbox_intent="exploration")
    assert e.value.code == "LOCKBOX_REASON_REQUIRED"

def test_execute_registers_exploration_before_heavy_chain(tmp_path: Path, monkeypatch):
    spec, db = _sandbox(tmp_path, monkeypatch)
    with pytest.raises(Exception):   # 无库 → 后续链路必败；关键看登记已落
        execute_run(spec, backtest=False, lockbox_intent="exploration",
                    lockbox_reason="单元验证")
    row = connect(db).execute(
        "SELECT kind, window_id FROM lockbox_access").fetchone()
    assert (row["kind"], row["window_id"]) == ("exploration", "2026Q2")
```

- [ ] **Step 3: 见红** — `cd platform && .venv/bin/python -m pytest tests/test_lockbox_guard.py tests/test_lockbox_run_cli.py -q` → `ImportError: guard_run` / `TypeError: execute_run() got unexpected keyword`

- [ ] **Step 4: 实现**

`core/lockbox.py` 追加 `RunGuard`/`guard_run`（代码同本计划上一版：`connect`/`current_window`（NO_STATE 时退回 `compute_window`）/`role_for`/`register_access`；`RunGuard.register_final` 保留给 T7）。

`app/context.py`：

```python
    guard: Any = None   # R40：execute 层锁箱守卫（RunGuard；None=未启用）
```

`surfaces/cli/main.py::execute_run`：在 `spec = load_spec(spec_path)` + `--set` 覆盖与 `variant` 计算之后、`profiler`/`ctx` 创建之前插入：

```python
    from factorlab.core import lockbox as _lb
    _days = _lockbox_published_days()
    _data_end = _lockbox_data_end() or dt.date.today()
    _start = (dt.date.fromisoformat(spec.date.start) if spec.date.start
              else (min(_days) if _days else dt.date(1970, 1, 1)))
    _end = (dt.date.fromisoformat(spec.date.end) if spec.date.end
            else _data_end)
    guard = _lb.guard_run(
        panel_start=min(_start, _end), panel_end=max(_start, _end),
        intent=lockbox_intent, reason=lockbox_reason,
        spec_doc=spec.model_dump(mode="json"), artifact=str(spec_path),
        command="factor run", tool=f"factorlab {__version__}",
        db_path=settings.lockbox_db, trading_days=_days, data_end=_data_end)
```

（`execute_run` 签名增 `lockbox_intent: str | None = None, lockbox_reason: str | None = None`；`ctx = RunContext(..., guard=guard)`。）

`app/run.py`：`_run_factor` 的 summary 组装处加 `if getattr(ctx, "guard", None) is not None: ctx.guard.attach(summary)`；`run_dir` 确定后 `ctx.guard.mark_result(str(run_dir))`。分钟链同函数级处理在 T6。

顶层 `@app.command("run")` 与 `research/factor.py::_RUN_PARAMS` 加两参数（代码同前版；`factor_run` 捕获 `LockboxError` 转 `envelope.fail("factor.run", exc.code, exc.message, hint="`flab lockbox status`")`）。

- [ ] **Step 5: 跑绿（含平台全量）**

Run: `cd platform && .venv/bin/python -m pytest tests/test_lockbox_guard.py tests/test_lockbox_run_cli.py -q && .venv/bin/python -m pytest -q`
Expected: 全绿（无 state 时 IS 放行，不影响既有测试）

- [ ] **Step 6: 提交**

```bash
git add platform/src/factorlab/core/lockbox.py platform/src/factorlab/app/context.py platform/src/factorlab/surfaces/cli/main.py platform/src/factorlab/app/run.py platform/src/factorlab/research/factor.py platform/tests/test_lockbox_guard.py platform/tests/test_lockbox_run_cli.py
git commit -m "feat(lockbox): execute 层硬门/自动登记/summary.sample（T5）"
```

---

### Task 6: 分钟链与其余 run 路径的守卫挂接

**Files:**
- Modify: `platform/src/factorlab/app/run.py`（`run_factor_minute` 的 summary 与产物处）
- Test: `platform/tests/test_lockbox_minute.py`

**Interfaces:**
- Consumes: T5（guard 在 `execute_run` 已按 spec 窗口统一执行，日频/分钟同门）。
- Produces: 分钟链同样落 `summary["sample"]` 与 `result_ref` 回填。

- [ ] **Step 1: 失败测试**：monkeypatch `factorlab.core.lockbox.guard_run` 记录入参，调 `execute_run`（spec 为 `interface: bars_1m`，窗口已知），断言 `panel_start/end` 与 spec.date 一致、`ctx.guard` 非空。
- [ ] **Step 2: 见红**
- [ ] **Step 3: 实现**：`run_factor_minute` 与 `_run_factor` 相同两处挂接（attach/mark_result）。
- [ ] **Step 4: 跑绿**：`cd platform && .venv/bin/python -m pytest tests/test_lockbox_minute.py -q && .venv/bin/python -m pytest -q`
- [ ] **Step 5: 提交**

```bash
git add platform/src/factorlab/app/run.py platform/tests/test_lockbox_minute.py
git commit -m "feat(lockbox): 分钟链守卫挂接（T6）"
```

---

### Task 7: admit / ref add 终评校验

**Files:**
- Modify: `platform/src/factorlab/research/factor.py`（`factor_admit` 523、`factor_ref_add` 703）
- Test: `platform/tests/test_lockbox_admit.py`

**Interfaces:**
- Consumes: T5 的 `RunGuard.register_final`、`spec_fingerprint`。
- Produces: `factor admit` / `factor ref add` 在窗口碰箱时：有终评→放行；无终评但有 `--lockbox-reason`→补终评（走配额）；否则 `LOCKBOX_FINAL_REQUIRED`。

- [ ] **Step 1: 写失败测试**

```python
# platform/tests/test_lockbox_admit.py
"""以 CliRunner 调 `factor admit/ref add`（沙箱 QR）：无终评登记 → FINAL_REQUIRED；
带 --lockbox-reason → 登记后放行（admit 依赖真数据，测试止于"门先于重链"：
在 FINAL_REQUIRED 场景断言无产物；在补登记场景断言登记行出现后进入下一段错误
（如产物缺失）而非锁箱错误）。"""
```

- [ ] **Step 2-4:** 见红 → 实现（在 admit/ref add 取到 spec 与窗口后调用：

```python
def _lockbox_gate_for_admit(spec_doc, spec_path, *, reason, command, tool):
    from factorlab.core import lockbox as lb
    if not settings.lockbox_db.exists():
        return None  # 未初始化：IS-only 系统照旧
    conn = lb.connect(settings.lockbox_db)
    try:
        window = lb.current_window(conn, as_of=dt.date.today(),
                                   trading_days=..., data_end=...)
        role = lb.role_for(panel_start, panel_end, window)
        if role == "is":
            return None
        fp = lb.candidate_fingerprint(artifact_sha256=lb.spec_fingerprint(spec_doc),
                                      params={"intent": "final"},
                                      window_id=window.window_id, kind="final")
        try:
            lb.require_final(conn, window_id=window.window_id, fingerprint=fp)
        except lb.LockboxError:
            if not reason:
                raise lb.LockboxError("LOCKBOX_FINAL_REQUIRED", ...)
            lb.register_access(conn, kind="final", fingerprint=fp,
                               artifact=str(spec_path), params={"intent": "final"},
                               command=command, reason=reason, window=window, tool=tool)
    finally:
        conn.close()
```

）→ 跑绿（含平台全量）→ 提交 `feat(lockbox): admit/ref add 终评校验（T7）`。

- [ ] **Step 5: 提交**

```bash
git add platform/src/factorlab/research/factor.py platform/tests/test_lockbox_admit.py
git commit -m "feat(lockbox): admit/ref add 终评校验（T7）"
```

---

### Task 8: compose / strategy 接线

**Files:**
- Modify: `platform/src/factorlab/app/composite/evaluate.py`（入口处）
- Modify: `platform/src/factorlab/app/strategy/run.py`（doc.date 解析后）
- Modify: `platform/src/factorlab/surfaces/cli/main.py:477`（compose 选项）、`platform/src/factorlab/research/strategy.py:599-620`（strategy run 选项）
- Test: `platform/tests/test_lockbox_compose_strategy.py`

**Interfaces:**
- Consumes: T5（`guard_run`/`RunGuard`）与 T4 helpers（`_lockbox_published_days/_lockbox_data_end`）。
- Produces: compose/strategy 同样 `--lockbox/--lockbox-reason`；有效窗口=composite 成员 spec 窗口交集 / strategy `doc.date`；在各自 execute 入口（compose 在 `surfaces/cli/main.py:477` 主体、strategy 在 `app/strategy/run.py`）调用 guard。

- [ ] **Step 1: 失败测试**（成员交集与 doc.date 的角色判定；无 intent 拒、`is` 放行）
- [ ] **Step 2: 见红**
- [ ] **Step 3: 实现**（composite：读成员 spec 的 `date` 求交集 → guard_run；strategy：`doc.date` → guard_run；运行产物 summary 挂 `sample`）
- [ ] **Step 4: 跑绿**：`cd platform && .venv/bin/python -m pytest tests/test_lockbox_compose_strategy.py -q && .venv/bin/python -m pytest -q`
- [ ] **Step 5: 提交**

```bash
git add platform/src/factorlab/app/composite/evaluate.py platform/src/factorlab/app/strategy/run.py platform/src/factorlab/surfaces/cli/main.py platform/src/factorlab/research/strategy.py platform/tests/test_lockbox_compose_strategy.py
git commit -m "feat(lockbox): compose/strategy 接线（T8）"
```

---

### Task 9: 研究侧库与 manifest 字段

**Files:**
- Create: `quantresearch/lab/lockbox.py`（产物区，不入 git）
- Modify: `governance/ops/research_tidy.py`（`check_results` 增字段校验）
- Test: `governance/ops/tests/test_research_tidy.py`（扩展现有）

**Interfaces:**
- Produces: `lab.lockbox.status()/window()/register(...)`；tidy 对 `results/<campaign>/manifest.json` 必填 `window_id/sample_role/access_ids/platform_commit`（error）。

- [ ] **Step 1: 失败测试**（tidy：缺字段 → error；齐全 → 无该 error；`--allow-missing-manifest` 仅豁免"文件缺失"一类）
- [ ] **Step 2: 见红**
- [ ] **Step 3: 实现 tidy 字段校验 + 写 `lab/lockbox.py`**

```python
# quantresearch/lab/lockbox.py（薄封装；平台 venv 运行）
"""锁箱访问帮手：研究脚本/experiments 统一登记入口（契约见 interface.md §9/锁箱章）。"""
import sys
from pathlib import Path
PLATFORM_SRC = Path("/data/students/gaolei/stock/platform/src")
if str(PLATFORM_SRC) not in sys.path:
    sys.path.insert(0, str(PLATFORM_SRC))
from factorlab.config import settings          # noqa: E402
from factorlab.core import lockbox as _lb      # noqa: E402
from factorlab.core.factio.paths import DATA_ROOT  # noqa: E402

def status() -> dict:
    conn = _lb.connect(settings.lockbox_db)
    try:
        days, end = _calendar_and_end()
        return _lb.status(conn, trading_days=days, data_end=end)
    finally:
        conn.close()
# window()/register() 同构；_calendar_and_end() 复用平台交易日历与 health 最新日
```

- [ ] **Step 4: 跑绿**：`cd platform && .venv/bin/python -m pytest ../governance/ops/tests/test_research_tidy.py -q`；手工 `lab/lockbox.py status()` 打印窗口
- [ ] **Step 5: 提交**

```bash
git add governance/ops/research_tidy.py governance/ops/tests/test_research_tidy.py
git commit -m "feat(lockbox): manifest 必填样本字段 + 研究侧封装（T9）"
```

---

### Task 10: G-LOCKBOX 门 + G-ANNOTATE 扩展 + 档案模板

**Files:**
- Create: `governance/ops/check_lockbox.py`（`--root/--offline/--selftest/--json`）
- Modify: `governance/ops/gates.sh`（离线/宿主两分支 + 负向自检）
- Modify: `governance/evidence/verification/R21/EVID/annotate_factor_archives.py`（新档必填 `sample_role`，按 `updated_ts>=2026-09-21` grandfather）
- Modify: `quantresearch/dossiers/factors/_template.md`（front matter 增字段）
- Test: `governance/ops/tests/test_check_lockbox.py`、扩展 `test_annotate_factor_archives.py`

**Interfaces:**
- Produces: G-LOCKBOX 判据输出与 gate 接线；`--selftest` 造假必被抓。

- [ ] **Step 1: 失败测试**（造假 3 类：manifest 缺字段 / 档案缺 sample_role / access_id 不存在或探索冒充终评）
- [ ] **Step 2: 见红**
- [ ] **Step 3: 实现**：checker 逻辑（宿主段连 `settings.lockbox_db` 读登记；离线段仅格式校验）；`gates.sh`：

```bash
  echo "[G-LOCKBOX] 样本声明与锁箱登记一致"
  skip_offline "G-LOCKBOX（宿主段：需产物区/台账）" "锁箱登记在 $PRODUCT_ROOT/data/ledger.sqlite"
```

  宿主段：

```bash
    if out=$("$PLATFORM/.venv/bin/python" governance/ops/check_lockbox.py --root "$PRODUCT_ROOT" 2>&1); then ok "$out"; else bad "$out"; fi
    "$PLATFORM/.venv/bin/python" governance/ops/check_lockbox.py --selftest >/dev/null || bad "G-LOCKBOX 负向自检失败"
```

- [ ] **Step 4: 跑绿**：`python governance/ops/check_lockbox.py --selftest && make gates`
- [ ] **Step 5: 提交**

```bash
git add governance/ops/check_lockbox.py governance/ops/gates.sh governance/evidence/verification/R21/EVID/annotate_factor_archives.py governance/ops/tests/test_check_lockbox.py governance/ops/tests/test_annotate_factor_archives.py
git commit -m "feat(lockbox): G-LOCKBOX 门与档案字段（T10）"
```

---

### Task 11: `flab health` 锁箱段 + 季度提醒

**Files:**
- Modify: `platform/src/factorlab/research/health.py`（`health()` 组装 + registry output_schema）
- Create: `governance/ops/lockbox-reminder.sh`、`governance/ops/install_lockbox_timer.sh`
- Test: `governance/ops/tests/test_lockbox_reminder_sh.py`（脚本 rc/输出）与 health 段单测

**Interfaces:**
- Produces: `health.lockbox = {initialized, window_id, window_start, window_end, quota_final, final_used, final_remaining}`（未初始化 `{initialized:false}`，warning 提示 `lockbox roll`）；timer `OnCalendar=*-01,04,07,10-01 09:00`。

- [ ] **Step 1: 失败测试**（health 含 lockbox 段；reminder 在陈旧时 rc=1 并打印 roll 指引）
- [ ] **Step 2: 见红**
- [ ] **Step 3: 实现**（health 段复用 core.lockbox.status；reminder 脚本 `factorlab lockbox status --json`，非 0 → 打印提醒并 `exit 1`；安装脚本幂等 + 记录方式）
- [ ] **Step 4: 跑绿**：`cd platform && .venv/bin/python -m pytest tests/test_research_health*.py -q`；`bash governance/ops/lockbox-reminder.sh; echo rc=$?`
- [ ] **Step 5: 提交**

```bash
git add platform/src/factorlab/research/health.py governance/ops/lockbox-reminder.sh governance/ops/install_lockbox_timer.sh governance/ops/tests/test_lockbox_reminder_sh.py platform/tests/test_research_health_lockbox.py
git commit -m "feat(lockbox): health 段与季度提醒（T11）"
```

---

### Task 12: 文档同步、证据 R40 与端到端验收

**Files:**
- Modify: `knowledge/contracts/interface.md`（新增"锁箱与样本声明"章）、`knowledge/handbooks/factor-mining-playbook.md` §4.1、
  `.claude/skills/factor-mine/SKILL.md` §7/§8、`governance/evidence/reviews/STATUS.md`
- Modify: `quantresearch/CONVENTIONS.md`（manifest/档案字段；`data/ledger.sqlite` 增锁箱表说明）
- Create: `governance/evidence/verification/R40/README.md`

- [ ] **Step 1: 文档改动**（按 spec §12 逐条；interface 契约含 CLI 参数、错误码、`summary.sample` 字段、登记表结构）
- [ ] **Step 2: 端到端验收（真沙箱 + 真宿主）**，按 spec §10 逐条留证：

```bash
# 1) 初始化 + IS 直跑（无登记）
flab lockbox roll
flab factor run $QR/factor/...（IS spec）→ summary.sample.role=is；ledger 行数不变
# 2) 碰箱无 intent → 拒（错误码；产物不存在）
# 3) exploration → 成功 + 1 行登记 + summary.sample 一致
# 4) final 二次 → DUPLICATE；配额（临时把窗口 quota 调 1 复验）→ EXCEEDED
# 5) admit 无终评 → FINAL_REQUIRED
# 6) 跨季（--as-of 注入下季）未 roll → WINDOW_STALE
# 7) make gates / verify-fast / verify-deep（宿主段）全绿
```

- [ ] **Step 3: 证据落盘** R40（命令+原始输出+结论+指路；造假负例）
- [ ] **Step 4: 提交**

```bash
git add knowledge/contracts/interface.md knowledge/handbooks/factor-mining-playbook.md .claude/skills/factor-mine/SKILL.md governance/evidence/reviews/STATUS.md governance/evidence/verification/R40/README.md
git commit -m "docs(lockbox): 契约/手册/技能同步 + R40 验收证据（T12）"
```

---

## Self-Review

- **Spec 覆盖**：§4 窗口→T1/T2；§5 登记→T3；§6 配额→T3/T7；§7 平台接线→T5/T6/T7/T8；§8 研究侧→T9；§9 门与测试→T10（+各任务单测）；§10 验收→T12；§12 运维文档→T11/T12；§13 迁移→T12 的 INIT 步骤与文档说明。
- **占位符扫描**：T5 的 `spec_path` 注入方式与 T8 具体接线点需要实现者按当前作用域落位（已在任务内给出判定与唯一新增字段），无 TBD。
- **类型一致性**：`RunGuard.info/attach/mark_result`、`guard_run` 参数、`lockbox_db`、错误码在 T5/T7/T8 引用一致；`spec_fingerprint` 为终评指纹基（T7 与 T5 备注一致）。
- **风险**：现有 175 个 end=2026-07-31 的 spec 生效后需 intent；T12 文档将给出 `lockbox status --json` 的 `is_end` 迁移指引。
