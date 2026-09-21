from __future__ import annotations
import datetime as dt, sqlite3, threading
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
    assert final_count(conn, W.window_id) == 3, "被拒 final 不得留行"
    rejected = conn.execute("SELECT COUNT(*) FROM lockbox_access"
                            " WHERE fingerprint = 'fpX'").fetchone()[0]
    assert rejected == 0

def test_exploration_not_counted_toward_quota(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    conn.execute("INSERT OR REPLACE INTO lockbox_state VALUES (1,'2026Q2',"
                 "'2025-07-01',1,'2026-09-21T00:00:00+00:00')")
    for i in range(5):
        _reg(conn, kind="exploration", fp=f"q-e{i}", reason="探索")
    _reg(conn, kind="final", fp="q-f", reason="入库")
    assert final_count(conn, W.window_id) == 1
    with pytest.raises(LockboxError) as e:
        _reg(conn, kind="final", fp="q-f2", reason="再评")
    assert e.value.code == "LOCKBOX_QUOTA_EXCEEDED"

def test_exploration_unlimited_and_append_only(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    for i in range(25):
        _reg(conn, kind="exploration", fp=f"e{i}", reason="探索")
    aid = _reg(conn, kind="exploration", fp="e-final", reason="探索")
    update_result_ref(conn, aid, "/quantresearch/results/platform/x")
    ref = conn.execute("SELECT result_ref FROM lockbox_access"
                       " WHERE access_id=?", (aid,)).fetchone()[0]
    assert ref == "/quantresearch/results/platform/x", "回填必须真实落库"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE lockbox_access SET kind='final' WHERE access_id=?", (aid,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM lockbox_access WHERE access_id=?", (aid,))

def test_require_final_missing(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    with pytest.raises(LockboxError) as e:
        require_final(conn, window_id=W.window_id, fingerprint="nope")
    assert e.value.code == "LOCKBOX_FINAL_REQUIRED"

def test_update_result_ref_unknown_id(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    with pytest.raises(ValueError, match="未知 access_id"):
        update_result_ref(conn, "NOPE", "/quantresearch/results/platform/x")

def test_final_concurrent_same_fingerprint(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    connect(db).close()
    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def worker() -> None:
        conn = connect(db)
        try:
            barrier.wait()
            outcomes.append(_reg(conn, kind="final", fp="race", reason="并发"))
        except Exception as e:  # noqa: BLE001 - 裸异常也要进断言
            outcomes.append(e)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(outcomes) == 2, outcomes
    ok = [o for o in outcomes if isinstance(o, str)]
    bad = [o for o in outcomes if isinstance(o, Exception)]
    assert len(ok) == 1, outcomes
    assert len(bad) == 1, outcomes
    assert isinstance(bad[0], LockboxError), f"不得抛裸异常: {bad[0]!r}"
    assert bad[0].code == "LOCKBOX_FINAL_DUPLICATE"
    conn = connect(db)
    assert final_count(conn, W.window_id) == 1
    rows = conn.execute("SELECT COUNT(*) FROM lockbox_access"
                        " WHERE kind='final' AND fingerprint='race'").fetchone()[0]
    assert rows == 1, "同 fp final 只能落 1 行"
