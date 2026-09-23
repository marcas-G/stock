from __future__ import annotations
import datetime as dt
from pathlib import Path
import sqlite3
import pytest
from factorlab.core.lockbox import LockboxError, compute_window
from factorlab.adapters.lockbox_store import (connect, current_window,
                                              latest_data_date, load_state,
                                              published_days, roll, status)

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

def test_roll_no_longer_accepts_quota_final(tmp_path: Path):
    """R42：配额参数已删除（`--quota-final` 在 CLI 侧同步移除）。"""
    conn = connect(tmp_path / "ledger.sqlite")
    with pytest.raises(TypeError):
        roll(conn, window=_window(), quota_final=5)

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
    """R42：仅 initialized/window_id/window_start/window_end/is_end/finals_total。"""
    conn = connect(tmp_path / "ledger.sqlite")
    roll(conn, window=_window())
    st = status(conn, trading_days=DAYS, data_end=DAY)
    assert set(st) == {"initialized", "window_id", "window_start", "window_end",
                       "is_end", "finals_total"}
    assert st["initialized"] is True and st["window_id"] == "2026Q2"
    assert st["window_start"] == "2025-07-01"
    assert st["window_end"] == "2026-09-18"
    assert st["finals_total"] == 0

def test_status_uninitialized(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    assert status(conn, trading_days=DAYS, data_end=DAY) == {"initialized": False}

def test_status_finals_total_counts_final_only(tmp_path: Path):
    """历史 exploration 行保留（只读）；终评计数只认 final，无配额剩余概念。"""
    conn = connect(tmp_path / "ledger.sqlite")
    roll(conn, window=_window())
    conn.execute(
        "INSERT INTO lockbox_access (access_id, ts_utc, window_id, window_start,"
        " window_end, kind, fingerprint, artifact, params, command, result_ref,"
        " reason, actor, tool) VALUES ('E1','2026-09-21T00:00:00+00:00','2026Q2',"
        " '2025-07-01','2026-09-18','exploration','fp-e','a','{}','cmd',NULL,"
        " '历史探索','u@h','test')")
    conn.execute(
        "INSERT INTO lockbox_access (access_id, ts_utc, window_id, window_start,"
        " window_end, kind, fingerprint, artifact, params, command, result_ref,"
        " reason, actor, tool) VALUES ('F1','2026-09-21T00:00:01+00:00','2026Q2',"
        " '2025-07-01','2026-09-18','final','fp-f','a','{}','cmd',NULL,"
        " '终评','u@h','test')")
    st = status(conn, trading_days=DAYS, data_end=DAY)
    assert st["finals_total"] == 1

def test_status_is_end_previous_trading_day(tmp_path: Path):
    """§12/§13：is_end = window_start 前一交易日（非日历前一天）。"""
    conn = connect(tmp_path / "ledger.sqlite")
    days = [dt.date(2025, 6, 27), dt.date(2025, 7, 7), DAY]
    # window_start=2025-07-07（周一）；日历前一天=周日 07-06，前一交易日=周五 06-27
    roll(conn, window=compute_window(as_of=dt.date(2026, 9, 21), trading_days=days,
                                     data_end=DAY))
    st = status(conn, trading_days=days, data_end=DAY)
    assert st["window_start"] == "2025-07-07" and st["is_end"] == "2025-06-27"


def test_status_is_end_fallback_when_no_earlier_trading_day(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    roll(conn, window=_window())  # window_start=2025-07-01；序列中无更早交易日
    st = status(conn, trading_days=DAYS, data_end=DAY)
    assert st["window_start"] == "2025-07-01" and st["is_end"] == "2025-06-30"


def test_state_persists_across_reconnect(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    conn = connect(db)
    roll(conn, window=_window())
    conn.close()
    assert db.exists()
    conn2 = connect(db)
    assert load_state(conn2)["window_id"] == "2026Q2"
    st = status(conn2, trading_days=DAYS, data_end=DAY)
    assert st["window_id"] == "2026Q2" and st["finals_total"] == 0


def test_roll_leaves_legacy_quota_column_untouched(tmp_path: Path):
    """迁移兼容：state 表 quota_final 列保留但 roll 不读不写。"""
    conn = connect(tmp_path / "ledger.sqlite")
    conn.execute(
        "INSERT INTO lockbox_state (id, window_id, window_start, quota_final,"
        " rolled_at) VALUES (1,'2026Q2','2025-07-01',7,'2026-09-21T00:00:00+00:00')")
    w_next, changed = roll(conn, window=_window(as_of=dt.date(2026, 10, 2)))
    assert changed and w_next.window_id == "2026Q3"
    assert load_state(conn)["quota_final"] == 7, "历史配额值不得被 roll 改写"


def test_forward_roll(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    roll(conn, window=_window())
    w_next, changed = roll(conn, window=_window(as_of=dt.date(2026, 10, 2)))
    assert changed and w_next.window_id == "2026Q3"
    w = current_window(conn, as_of=dt.date(2026, 10, 2), trading_days=DAYS,
                       data_end=DAY)
    assert w.window_id == "2026Q3" and w.start == dt.date(2025, 10, 1)
    assert load_state(conn)["window_id"] == "2026Q3"

def _write_access(conn: sqlite3.Connection, access_id: str = "A1", *,
                  or_replace: bool = False) -> None:
    verb = "INSERT OR REPLACE" if or_replace else "INSERT"
    conn.execute(
        f"{verb} INTO lockbox_access (access_id, ts_utc, window_id,"
        " window_start, window_end, kind, fingerprint, artifact, params,"
        " command, result_ref, reason, actor, tool)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (access_id, "2026-09-21T00:00:00+00:00", "2026Q2", "2025-07-01",
         "2026-09-18", "final", "fp", "art", "{}", "factorlab run",
         None, "confirm", "u@h", "test"))

_ACCESS_IMMUTABLE_COLUMNS = (
    ("access_id", "A2"),
    ("ts_utc", "2026-09-22T00:00:00+00:00"),
    ("window_id", "2026Q3"),
    ("window_start", "2025-10-01"),
    ("window_end", "2026-09-19"),
    ("kind", "exploration"),
    ("fingerprint", "fp2"),
    ("artifact", "art2"),
    ("params", "{\"x\": 1}"),
    ("command", "factorlab run --x"),
    ("reason", "explore"),
    ("actor", "evil@h"),
    ("tool", "evil"),
)

@pytest.mark.parametrize("column,value", _ACCESS_IMMUTABLE_COLUMNS)
def test_access_update_any_column_aborts(tmp_path: Path, column: str,
                                        value: str):
    conn = connect(tmp_path / "ledger.sqlite")
    _write_access(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(f"UPDATE lockbox_access SET {column} = ?"
                     " WHERE access_id = 'A1'", (value,))
    row = conn.execute("SELECT * FROM lockbox_access"
                       " WHERE access_id = 'A1'").fetchone()
    assert row is not None and row["reason"] == "confirm"

def test_access_delete_aborts_and_result_ref_update_allowed(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    _write_access(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM lockbox_access WHERE access_id = 'A1'")
    conn.execute("UPDATE lockbox_access SET result_ref = 'r1'"
                 " WHERE access_id = 'A1'")
    row = conn.execute("SELECT * FROM lockbox_access"
                       " WHERE access_id = 'A1'").fetchone()
    assert row["result_ref"] == "r1" and row["reason"] == "confirm"

def test_access_insert_or_replace_aborts(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    _write_access(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        _write_access(conn, or_replace=True)
    row = conn.execute("SELECT * FROM lockbox_access"
                       " WHERE access_id = 'A1'").fetchone()
    assert row is not None and row["reason"] == "confirm"

def test_published_days_and_latest_data_date(tmp_path: Path):
    d = tmp_path / "ashare_daily"; d.mkdir()
    (d / "2026-09-18.json").write_text("{}", encoding="utf-8")
    (d / "2026-09-17.json").write_text("{}", encoding="utf-8")
    (d / "junk.json").write_text("{}", encoding="utf-8")
    assert published_days(tmp_path) == [dt.date(2026, 9, 17),
                                        dt.date(2026, 9, 18)]
    assert latest_data_date(tmp_path) == dt.date(2026, 9, 18)
    assert published_days(tmp_path / "missing") == []
    assert latest_data_date(tmp_path / "missing") is None


def test_state_delete_and_replace_abort(tmp_path: Path):
    """E3：state 禁 DELETE/REPLACE（防手改窗口直删）。"""
    import sqlite3
    conn = connect(tmp_path / "ledger.sqlite")
    roll(conn, window=_window())
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM lockbox_state")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT OR REPLACE INTO lockbox_state"
                     " VALUES (1, '2020Q1', '2020-01-01', 99, 'x')")
    assert load_state(conn)["window_id"] == "2026Q2"
