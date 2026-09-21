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

def test_state_persists_across_reconnect(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    conn = connect(db)
    roll(conn, window=_window(), quota_final=7)
    conn.close()
    assert db.exists()
    conn2 = connect(db)
    assert load_state(conn2)["window_id"] == "2026Q2"
    st = status(conn2, trading_days=DAYS, data_end=DAY)
    assert st["window_id"] == "2026Q2" and st["quota_final"] == 7

def test_roll_quota_final_override(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    roll(conn, window=_window(), quota_final=5)
    assert status(conn, trading_days=DAYS, data_end=DAY)["quota_final"] == 5

def test_same_window_roll_with_new_quota(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    roll(conn, window=_window())
    w2, changed = roll(conn, window=_window(), quota_final=5)
    assert changed and w2.window_id == "2026Q2"
    st = status(conn, trading_days=DAYS, data_end=DAY)
    assert st["quota_final"] == 5 and st["final_remaining"] == 5
    _, changed2 = roll(conn, window=_window())
    assert not changed2
    assert status(conn, trading_days=DAYS, data_end=DAY)["quota_final"] == 5

def test_forward_roll(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    roll(conn, window=_window())
    w_next, changed = roll(conn, window=_window(as_of=dt.date(2026, 10, 2)))
    assert changed and w_next.window_id == "2026Q3"
    w = current_window(conn, as_of=dt.date(2026, 10, 2), trading_days=DAYS,
                       data_end=DAY)
    assert w.window_id == "2026Q3" and w.start == dt.date(2025, 10, 1)
    assert load_state(conn)["window_id"] == "2026Q3"

def _insert_access(conn: sqlite3.Connection, access_id: str = "A1") -> None:
    conn.execute(
        "INSERT INTO lockbox_access (access_id, ts_utc, window_id, window_start,"
        " window_end, kind, fingerprint, artifact, params, command, result_ref,"
        " reason, actor, tool) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (access_id, "2026-09-21T00:00:00+00:00", "2026Q2", "2025-07-01",
         "2026-09-18", "final", "fp", "art", "{}", "factorlab run",
         None, "confirm", "u@h", "test"))

def test_access_append_only_trigger(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    _insert_access(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE lockbox_access SET reason = 'changed'"
                     " WHERE access_id = 'A1'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE lockbox_access SET params = '{\"x\": 1}'"
                     " WHERE access_id = 'A1'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE lockbox_access SET actor = 'evil'"
                     " WHERE access_id = 'A1'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM lockbox_access WHERE access_id = 'A1'")
    conn.execute("UPDATE lockbox_access SET result_ref = 'r1'"
                 " WHERE access_id = 'A1'")
    row = conn.execute("SELECT result_ref FROM lockbox_access"
                       " WHERE access_id = 'A1'").fetchone()
    assert row["result_ref"] == "r1"

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
