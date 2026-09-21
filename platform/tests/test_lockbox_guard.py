"""R40 T5：execute 层锁箱守卫（guard_run/RunGuard）单元断言。

断言来源：设计 §3/§7/§10 + 计划 T5 brief：
- IS 段不要求意图、不登记；
- 碰箱缺意图/理由 → 稳定错误码；
- exploration 登记 + `summary["sample"].access_id` 与登记一致 + result_ref 回填；
- mixed 角色识别。

禁止行为证明：IS 运行后 `lockbox_access` 零行（真 SQLite 计数）；拒跑不写登记。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from factorlab.adapters.lockbox_store import connect, guard_run, roll
from factorlab.core.lockbox import LockboxError, compute_window


@pytest.fixture(autouse=True)
def _lockbox_enabled(monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "1")


DAYS = [dt.date(2025, 7, 1), dt.date(2026, 9, 18)]
W = compute_window(as_of=dt.date(2026, 9, 21), trading_days=DAYS,
                   data_end=dt.date(2026, 9, 18))


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "ledger.sqlite"
    conn = connect(db)
    roll(conn, window=W)
    conn.close()
    return db


def _guard(tmp_path, *, start, end, intent=None, reason=None):
    return guard_run(panel_start=start, panel_end=end, intent=intent, reason=reason,
                     spec_doc={"name": "x"}, artifact="factor/x.yaml",
                     command="factor run", tool="factorlab test",
                     db_path=_db(tmp_path), trading_days=DAYS, data_end=W.end)


def test_is_run_needs_no_intent_no_registration(tmp_path: Path):
    g = _guard(tmp_path, start=dt.date(2024, 1, 1), end=dt.date(2025, 6, 30))
    assert g.info == {"role": "is"}
    conn = connect(_db(tmp_path))
    assert conn.execute("SELECT COUNT(*) FROM lockbox_access").fetchone()[0] == 0


def test_lockbox_run_requires_intent(tmp_path: Path):
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, start=dt.date(2026, 1, 1), end=dt.date(2026, 9, 18))
    assert e.value.code == "LOCKBOX_INTENT_REQUIRED"


def test_lockbox_run_requires_reason(tmp_path: Path):
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, start=dt.date(2026, 1, 1), end=dt.date(2026, 9, 18),
               intent="exploration")
    assert e.value.code == "LOCKBOX_REASON_REQUIRED"


def test_exploration_registers_and_attaches(tmp_path: Path):
    g = _guard(tmp_path, start=dt.date(2026, 1, 1), end=dt.date(2026, 9, 18),
               intent="exploration", reason="摸边界")
    assert g.info["role"] == "lockbox" and g.info["window_id"] == "2026Q2"
    summary = {}
    g.attach(summary)
    assert summary["sample"]["access_id"] == g.info["access_id"]
    g.mark_result("/quantresearch/results/platform/x")
    conn = connect(_db(tmp_path))
    row = conn.execute("SELECT kind, result_ref FROM lockbox_access"
                       " WHERE access_id=?", (g.info["access_id"],)).fetchone()
    assert (row["kind"], row["result_ref"]) == (
        "exploration", "/quantresearch/results/platform/x")


def test_mixed_role(tmp_path: Path):
    g = _guard(tmp_path, start=dt.date(2025, 6, 1), end=dt.date(2025, 7, 2),
               intent="exploration", reason="跨界")
    assert g.info["role"] == "mixed"


def test_disabled_env_returns_is_without_touching_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "off")
    db = _db(tmp_path)
    g = guard_run(panel_start=dt.date(2026, 1, 1), panel_end=dt.date(2026, 9, 18),
                  intent=None, reason=None, spec_doc={"name": "x"},
                  artifact="factor/x.yaml", command="factor run",
                  tool="factorlab test", db_path=db, trading_days=[], data_end=W.end)
    assert g.info == {"role": "is"}
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM lockbox_access").fetchone()[0] == 0
