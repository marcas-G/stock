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
