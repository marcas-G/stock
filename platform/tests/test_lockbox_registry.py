"""R42 T1：`lockbox_access` 登记层语义（新登记仅 final；无配额；账本不可改）。

断言来源：设计 `2026-09-24-final-test-once-discipline-design.md` §3/§5 + T1 brief：
- 新登记仅允许 `kind="final"`（历史 exploration 行保留只读，不得再新增）；
- 同 `(window_id, fingerprint)` final 唯一；操作员 `re_final=True` 逃生
  （追加 `|re-final` 审计标记）；
- 每窗配额概念删除：任意多版本 final 均可登记（旧默认 20 上限不再存在）；
- append-only：除 `result_ref` 外任何列 UPDATE / DELETE / INSERT OR REPLACE 均被拒。
"""
from __future__ import annotations
import datetime as dt, sqlite3, threading
from pathlib import Path
import pytest
from factorlab.core.lockbox import LockboxError, LockboxWindow
from factorlab.adapters.lockbox_store import (connect, final_count, final_exists,
                                              register_access, require_final,
                                              update_result_ref)

W = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 9, 18))

def _reg(conn, kind="final", fp="fp1", reason="终评", *, re_final=False):
    return register_access(conn, kind=kind, fingerprint=fp, artifact="factor/x.yaml",
                           params={"set": ["n=20"]}, command="factor run",
                           reason=reason, window=W, tool="factorlab test",
                           re_final=re_final)

def test_register_requires_reason(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    with pytest.raises(LockboxError) as e:
        _reg(conn, reason="  ")
    assert e.value.code == "LOCKBOX_REASON_REQUIRED"

def test_register_rejects_non_final_kind(tmp_path: Path):
    """探索登记已删除：新登记仅 final（历史 exploration 行只读）。"""
    conn = connect(tmp_path / "ledger.sqlite")
    with pytest.raises(ValueError, match="final"):
        _reg(conn, kind="exploration", reason="摸边界")
    assert final_count(conn, W.window_id) == 0
    assert conn.execute("SELECT COUNT(*) FROM lockbox_access").fetchone()[0] == 0

def test_final_unique_per_fingerprint(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    first = _reg(conn, fp="fpA", reason="入库")
    assert final_exists(conn, W.window_id, "fpA")
    assert require_final(conn, window_id=W.window_id, fingerprint="fpA") == first
    with pytest.raises(LockboxError) as e:
        _reg(conn, fp="fpA", reason="重评")
    assert e.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert final_count(conn, W.window_id) == 1, "重复被拒不得计入"

def test_final_re_final_override_appends_audit_marker(tmp_path: Path):
    """操作员逃生：同版本可重测；旧行保留，新行 reason 带 `|re-final`。"""
    conn = connect(tmp_path / "ledger.sqlite")
    first = _reg(conn, fp="fpA", reason="首测")
    second = _reg(conn, fp="fpA", reason="操作员重测", re_final=True)
    assert second != first
    assert final_count(conn, W.window_id) == 2
    reasons = [r[0] for r in conn.execute(
        "SELECT reason FROM lockbox_access WHERE fingerprint='fpA' ORDER BY rowid")]
    assert reasons == ["首测", "操作员重测|re-final"]
    assert require_final(conn, window_id=W.window_id, fingerprint="fpA") == second

def test_final_has_no_quota_cap(tmp_path: Path):
    """配额已删除：无 state 时旧默认 20 上限不得再触发（25 个版本全登记）。"""
    conn = connect(tmp_path / "ledger.sqlite")
    for i in range(25):
        _reg(conn, fp=f"fp{i}", reason="入库")
    assert final_count(conn, W.window_id) == 25
    assert conn.execute("SELECT COUNT(*) FROM lockbox_access"
                        " WHERE kind='final'").fetchone()[0] == 25

def test_final_append_only_and_result_ref(tmp_path: Path):
    conn = connect(tmp_path / "ledger.sqlite")
    aid = _reg(conn, fp="e-final", reason="入库")
    update_result_ref(conn, aid, "/quantresearch/results/platform/x")
    ref = conn.execute("SELECT result_ref FROM lockbox_access"
                       " WHERE access_id=?", (aid,)).fetchone()[0]
    assert ref == "/quantresearch/results/platform/x", "回填必须真实落库"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE lockbox_access SET kind='exploration' WHERE access_id=?", (aid,))
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
            outcomes.append(_reg(conn, fp="race", reason="并发"))
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
