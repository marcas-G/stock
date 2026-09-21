from __future__ import annotations
import datetime as dt, sqlite3
from pathlib import Path
import pytest
from factorlab.core.lockbox import LockboxError, LockboxWindow
from factorlab.adapters.lockbox_store import (connect, final_count, final_exists,
                                              register_access, require_final,
                                              update_result_ref)

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
