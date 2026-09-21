"""R40 T5：execute 层锁箱守卫（guard_run/RunGuard）单元断言。

断言来源：设计 §3/§7/§10 + 计划 T5 brief：
- IS 段不要求意图、不登记；
- 碰箱缺意图/理由 → 稳定错误码；
- exploration 登记 + `summary["sample"].access_id` 与登记一致 + result_ref 回填；
- mixed 角色识别；未初始化（无 state）IS 放行、碰箱 `LOCKBOX_NO_STATE`；
- 启用且空日历 → `LOCKBOX_NO_CALENDAR`。

墙钟鲁棒（T5 修复轮1）：日历/窗口/期望值全部从 `dt.date.today()` +
`quarter_end_before`/`window_id_of` 动态派生，不写死年份/季度——guard 内
`current_window` 用真墙钟，固定日期会在跨季后必红。

禁止行为证明：IS/拒跑/无 state 路径后 `lockbox_access` 零行（真 SQLite 计数）。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from _lockbox import DAYS, TODAY, WINDOW as W, WINDOW_ID
from factorlab.adapters.lockbox_store import connect, guard_run, roll
from factorlab.core.lockbox import LockboxError


@pytest.fixture(autouse=True)
def _lockbox_enabled(monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "1")



def _db(tmp_path: Path, *, initialized: bool = True) -> Path:
    db = tmp_path / "ledger.sqlite"
    conn = connect(db)
    if initialized:
        roll(conn, window=W)
    conn.close()
    return db


def _guard(tmp_path, *, start, end, intent=None, reason=None,
           initialized=True, days=None):
    return guard_run(panel_start=start, panel_end=end, intent=intent, reason=reason,
                     spec_doc={"name": "x"}, artifact="factor/x.yaml",
                     command="factor run", tool="factorlab test",
                     db_path=_db(tmp_path, initialized=initialized),
                     trading_days=DAYS if days is None else days,
                     data_end=W.end)


def _count(db: Path) -> int:
    return connect(db).execute("SELECT COUNT(*) FROM lockbox_access").fetchone()[0]


def test_window_fixture_matches_wall_clock_quarter():
    assert W.window_id == WINDOW_ID
    assert W.end == TODAY and W.start <= TODAY


def test_is_run_needs_no_intent_no_registration(tmp_path: Path):
    g = _guard(tmp_path, start=W.start - dt.timedelta(days=400),
               end=W.start - dt.timedelta(days=1))
    assert g.info == {"role": "is"}
    assert _count(_db(tmp_path)) == 0


def test_lockbox_run_requires_intent(tmp_path: Path):
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, start=W.start, end=W.end)
    assert e.value.code == "LOCKBOX_INTENT_REQUIRED"
    assert _count(_db(tmp_path)) == 0


def test_lockbox_run_requires_reason(tmp_path: Path):
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, start=W.start, end=W.end, intent="exploration")
    assert e.value.code == "LOCKBOX_REASON_REQUIRED"
    assert _count(_db(tmp_path)) == 0


def test_exploration_registers_and_attaches(tmp_path: Path):
    g = _guard(tmp_path, start=W.start, end=W.end,
               intent="exploration", reason="摸边界")
    assert g.info["role"] == "lockbox"
    assert g.info["window_id"] == WINDOW_ID
    assert g.info["window_start"] == W.start.isoformat()
    assert g.info["window_end"] == W.end.isoformat()
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
    g = _guard(tmp_path, start=W.start - dt.timedelta(days=30),
               end=W.start + dt.timedelta(days=2),
               intent="exploration", reason="跨界")
    assert g.info["role"] == "mixed"


def test_disabled_env_returns_is_without_touching_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "off")
    db = _db(tmp_path)
    g = guard_run(panel_start=W.start, panel_end=W.end,
                  intent=None, reason=None, spec_doc={"name": "x"},
                  artifact="factor/x.yaml", command="factor run",
                  tool="factorlab test", db_path=db, trading_days=[], data_end=W.end)
    assert g.info == {"role": "is"}
    assert _count(db) == 0


def test_no_state_is_passes_without_registration(tmp_path: Path):
    g = _guard(tmp_path, initialized=False,
               start=W.start - dt.timedelta(days=400),
               end=W.start - dt.timedelta(days=1))
    assert g.info == {"role": "is"}
    assert _count(_db(tmp_path)) == 0


def test_no_state_lockbox_raises_no_state_without_registration(tmp_path: Path):
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, initialized=False, start=W.start, end=W.end,
               intent="exploration", reason="摸边界")
    assert e.value.code == "LOCKBOX_NO_STATE"
    assert _count(_db(tmp_path)) == 0


def test_empty_calendar_is_loud(tmp_path: Path):
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, start=W.start, end=W.end, days=[])
    assert e.value.code == "LOCKBOX_NO_CALENDAR"
    assert _count(_db(tmp_path)) == 0
