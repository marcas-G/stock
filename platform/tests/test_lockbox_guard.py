"""R42 T1：execute 层锁箱守卫（guard_run/RunGuard）单元断言（最终测试一次语义）。

断言来源：设计 `2026-09-24-final-test-once-discipline-design.md` §3/§6 + T1 brief：
- 探索只准训练段：任何碰测试段的非最终测试 → `LOCKBOX_TEST_ONLY_FINAL`，零登记；
- 最终测试每版本一次：同版本二次 → `LOCKBOX_FINAL_DUPLICATE`（行数不变）；
  操作员 `FACTORLAB_RE_FINAL=1` → 允许再登记（reason 追加 `|re-final` 审计标记）；
- 最终测试必须经流水线/入库车道：缺 `FACTORLAB_PIPELINE=1` → `LOCKBOX_PIPELINE_REQUIRED`；
- 版本指纹 = spec 指纹 + `params={"final_test": True}` + window_id（改 spec 内容=新版本可再测）；
- 无配额概念：登记 final 不读 state.quota_final、无上限；
- `FACTORLAB_LOCKBOX=off` 短路：不读不写台账（连 sqlite 文件都不创建）。

墙钟鲁棒：日历/窗口/期望值全部从 `dt.date.today()` 派生（`_lockbox` 工具），
不写死年份/季度（guard 内 `current_window` 用真墙钟）。

禁止行为证明：拒跑/IS 路径后 `lockbox_access` 零行（真 SQLite 计数）；
off 路径后 db 文件不存在；登记行指纹与版本指纹一致（禁止硬编码/旧身份）。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from _lockbox import DAYS, TODAY, WINDOW as W, WINDOW_ID
from factorlab.adapters.lockbox_store import (
    connect,
    finalize_attempt_result,
    guard_run,
    roll,
)
from factorlab.core.lockbox import LockboxError, candidate_fingerprint, spec_fingerprint

SPEC = {"name": "x"}


@pytest.fixture(autouse=True)
def _lockbox_enabled(monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "1")
    monkeypatch.setenv("FACTORLAB_PIPELINE", "1")
    monkeypatch.delenv("FACTORLAB_RE_FINAL", raising=False)


def _db(tmp_path: Path, *, initialized: bool = True) -> Path:
    db = tmp_path / "ledger.sqlite"
    conn = connect(db)
    if initialized:
        roll(conn, window=W)
    conn.close()
    return db


def _guard(tmp_path, *, start, end, final_mode=False, reason=None,
           initialized=True, days=None, spec_doc=None):
    return guard_run(panel_start=start, panel_end=end, final_mode=final_mode,
                     reason=reason, spec_doc=dict(spec_doc or SPEC),
                     artifact="factor/x.yaml", command="factor run",
                     tool="factorlab test",
                     db_path=_db(tmp_path, initialized=initialized),
                     trading_days=DAYS if days is None else days,
                     data_end=W.end)


def _count(db: Path) -> int:
    conn = connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM lockbox_access").fetchone()[0]
    finally:
        conn.close()


def _rows(db: Path) -> list[dict]:
    conn = connect(db)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM lockbox_access"
                                              " ORDER BY rowid")]
    finally:
        conn.close()


def _expected_fp(spec_doc: dict) -> str:
    return candidate_fingerprint(artifact_sha256=spec_fingerprint(spec_doc),
                                 params={"final_test": True},
                                 window_id=WINDOW_ID, kind="final")


def test_window_fixture_matches_wall_clock_quarter():
    assert W.window_id == WINDOW_ID
    assert W.end == TODAY and W.start <= TODAY


# ── IS（训练段）：放行、零登记 ──────────────────────────────────────

def test_is_run_passes_without_registration(tmp_path: Path):
    g = _guard(tmp_path, start=W.start - dt.timedelta(days=400),
               end=W.start - dt.timedelta(days=1))
    assert g.info == {"role": "is"}
    assert _count(_db(tmp_path)) == 0


def test_is_run_passes_even_in_final_mode_without_marker(tmp_path: Path,
                                                         monkeypatch):
    """训练段不碰测试段：final_mode 也无需流水线标记、不登记。"""
    monkeypatch.delenv("FACTORLAB_PIPELINE", raising=False)
    g = _guard(tmp_path, final_mode=True, start=W.start - dt.timedelta(days=400),
               end=W.start - dt.timedelta(days=1))
    assert g.info == {"role": "is"}
    assert _count(_db(tmp_path)) == 0


# ── 探索碰测试段：直接拒（零登记） ─────────────────────────────────

def test_lockbox_exploration_rejected_with_zero_registration(tmp_path: Path):
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, start=W.start, end=W.end)
    assert e.value.code == "LOCKBOX_TEST_ONLY_FINAL"
    assert "xpipe" in e.value.message and "admit" in e.value.message
    assert _count(_db(tmp_path)) == 0


def test_mixed_exploration_rejected_with_zero_registration(tmp_path: Path):
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, start=W.start - dt.timedelta(days=30),
               end=W.start + dt.timedelta(days=2))
    assert e.value.code == "LOCKBOX_TEST_ONLY_FINAL"
    assert _count(_db(tmp_path)) == 0


# ── 最终测试：流水线标记 → 唯一 → 审计 → 版本 ───────────────────────

def test_final_without_pipeline_marker_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("FACTORLAB_PIPELINE", raising=False)
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
               reason="入库终评")
    assert e.value.code == "LOCKBOX_PIPELINE_REQUIRED"
    assert _count(_db(tmp_path)) == 0


def test_final_with_empty_reason_rejected(tmp_path: Path):
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, final_mode=True, start=W.start, end=W.end, reason="  ")
    assert e.value.code == "LOCKBOX_REASON_REQUIRED"
    assert _count(_db(tmp_path)) == 0


def test_final_registers_one_row_with_version_fingerprint(tmp_path: Path):
    g = _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
               reason="流水线终评")
    assert g.info["role"] == "lockbox"
    assert g.info["window_id"] == WINDOW_ID
    assert g.info["window_start"] == W.start.isoformat()
    assert g.info["window_end"] == W.end.isoformat()
    rows = _rows(_db(tmp_path))
    assert len(rows) == 1
    row = rows[0]
    assert g.info["access_id"] == row["access_id"]
    assert row["kind"] == "final"
    assert row["fingerprint"] == _expected_fp(SPEC), "版本指纹必须可复算（禁旧 intent 身份）"
    assert row["reason"] == "流水线终评"
    summary: dict = {}
    g.attach(summary)
    assert summary["sample"]["access_id"] == row["access_id"]
    g.mark_result("/quantresearch/results/platform/x")
    assert _rows(_db(tmp_path))[0]["result_ref"] == \
        "/quantresearch/results/platform/x"


def test_final_same_version_second_time_rejected(tmp_path: Path):
    first = _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
                   reason="首轮终评")
    assert _count(_db(tmp_path)) == 1
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
               reason="重跑")
    assert e.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert "FACTORLAB_RE_FINAL" in e.value.message
    rows = _rows(_db(tmp_path))
    assert len(rows) == 1 and rows[0]["access_id"] == first.info["access_id"]


def test_pipeline_retry_reuses_only_an_unfinished_final_access(
        tmp_path: Path, monkeypatch):
    """Exact-version recovery reuses the original access until an artifact is marked."""
    attempt_sha = "a" * 64
    monkeypatch.setenv("FACTORLAB_LOCKBOX_ATTEMPT_SHA256", attempt_sha)
    first = _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
                   reason="首轮终评")
    monkeypatch.setenv("FACTORLAB_LOCKBOX_REPLAY", "1")

    replay = _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
                    reason="失败后续跑")

    assert replay.info["access_id"] == first.info["access_id"]
    assert _count(_db(tmp_path)) == 1
    row = _rows(_db(tmp_path))[0]
    assert row["result_ref"] is None
    import json
    assert json.loads(row["params"])["flow_attempt_sha256"] == attempt_sha

    replay.mark_result("/results/final")
    with pytest.raises(LockboxError) as exc:
        _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
               reason="已完成后的重跑")
    assert exc.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert _count(_db(tmp_path)) == 1


def test_re_final_env_allows_new_row_with_audit_marker(tmp_path: Path,
                                                       monkeypatch):
    first = _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
                   reason="首轮终评")
    monkeypatch.setenv("FACTORLAB_RE_FINAL", "1")
    second = _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
                    reason="操作员重测")
    assert _count(_db(tmp_path)) == 2
    assert second.info["access_id"] != first.info["access_id"]
    rows = _rows(_db(tmp_path))
    assert [r["kind"] for r in rows] == ["final", "final"]
    assert rows[-1]["reason"] == "操作员重测|re-final"
    assert rows[0]["fingerprint"] == rows[1]["fingerprint"], "重测=同版本"


def test_pipeline_retry_does_not_reuse_completed_access(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX_ATTEMPT_SHA256", "a" * 64)
    first = _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
                   reason="首轮终评")
    first.mark_result("/results/final")
    monkeypatch.setenv("FACTORLAB_LOCKBOX_REPLAY", "1")

    with pytest.raises(LockboxError) as exc:
        _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
               reason="应拒绝")

    assert exc.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert _count(_db(tmp_path)) == 1


def test_pipeline_retry_requires_the_same_attempt_sha(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX_ATTEMPT_SHA256", "a" * 64)
    _guard(tmp_path, final_mode=True, start=W.start, end=W.end, reason="首测")
    monkeypatch.setenv("FACTORLAB_LOCKBOX_ATTEMPT_SHA256", "b" * 64)
    monkeypatch.setenv("FACTORLAB_LOCKBOX_REPLAY", "1")

    with pytest.raises(LockboxError) as exc:
        _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
               reason="错误 attempt")

    assert exc.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert _count(_db(tmp_path)) == 1


def test_attempt_result_backfill_is_identity_bound_and_idempotent(
        tmp_path: Path, monkeypatch):
    attempt_sha = "c" * 64
    monkeypatch.setenv("FACTORLAB_LOCKBOX_ATTEMPT_SHA256", attempt_sha)
    guard = _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
                   reason="首轮终评")
    db = _db(tmp_path)
    conn = connect(db)
    try:
        finalize_attempt_result(
            conn,
            access_id=guard.info["access_id"],
            attempt_sha256=attempt_sha,
            result_ref="/results/factor/v1",
        )
        finalize_attempt_result(
            conn,
            access_id=guard.info["access_id"],
            attempt_sha256=attempt_sha,
            result_ref="/results/factor/v1",
        )
        with pytest.raises(ValueError, match="attempt"):
            finalize_attempt_result(
                conn,
                access_id=guard.info["access_id"],
                attempt_sha256="d" * 64,
                result_ref="/results/factor/v1",
            )
        with pytest.raises(ValueError, match="different|不同"):
            finalize_attempt_result(
                conn,
                access_id=guard.info["access_id"],
                attempt_sha256=attempt_sha,
                result_ref="/results/factor/other",
            )
    finally:
        conn.close()
    assert _rows(db)[0]["result_ref"] == "/results/factor/v1"


def test_re_final_env_without_existing_final_is_plain_registration(
        tmp_path: Path, monkeypatch):
    """RE_FINAL=1 但无既有登记：属首次登记，不追加审计标记。"""
    monkeypatch.setenv("FACTORLAB_RE_FINAL", "1")
    _guard(tmp_path, final_mode=True, start=W.start, end=W.end, reason="首测")
    rows = _rows(_db(tmp_path))
    assert len(rows) == 1 and rows[0]["reason"] == "首测"


def test_changed_spec_content_is_new_version_and_can_register(tmp_path: Path):
    _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
           reason="v1", spec_doc={"name": "x", "params": {"n": 20}})
    _guard(tmp_path, final_mode=True, start=W.start, end=W.end,
           reason="v2", spec_doc={"name": "x", "params": {"n": 21}})
    rows = _rows(_db(tmp_path))
    assert len(rows) == 2
    assert rows[0]["fingerprint"] != rows[1]["fingerprint"]


# ── 未初始化 / 空日历 ──────────────────────────────────────────────

def test_no_state_is_passes_without_registration(tmp_path: Path):
    g = _guard(tmp_path, initialized=False,
               start=W.start - dt.timedelta(days=400),
               end=W.start - dt.timedelta(days=1))
    assert g.info == {"role": "is"}
    assert _count(_db(tmp_path)) == 0


def test_no_state_exploration_rejected_test_only_final(tmp_path: Path):
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, initialized=False, start=W.start, end=W.end)
    assert e.value.code == "LOCKBOX_TEST_ONLY_FINAL"
    assert _count(_db(tmp_path)) == 0


def test_no_state_final_rejected_no_state(tmp_path: Path):
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, initialized=False, final_mode=True,
               start=W.start, end=W.end, reason="终评")
    assert e.value.code == "LOCKBOX_NO_STATE"
    assert _count(_db(tmp_path)) == 0


def test_empty_calendar_is_loud(tmp_path: Path):
    with pytest.raises(LockboxError) as e:
        _guard(tmp_path, start=W.start, end=W.end, days=[])
    assert e.value.code == "LOCKBOX_NO_CALENDAR"
    assert _count(_db(tmp_path)) == 0


# ── 总开关 off：不读不写 ────────────────────────────────────────────

def test_disabled_env_returns_unknown_without_touching_ledger(
        tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "off")
    db = tmp_path / "ledger.sqlite"
    g = guard_run(panel_start=W.start, panel_end=W.end, final_mode=True,
                  reason=None, spec_doc=SPEC, artifact="factor/x.yaml",
                  command="factor run", tool="factorlab test", db_path=db,
                  trading_days=[], data_end=W.end)
    assert g.info == {"role": "unknown"}
    assert not db.exists(), "off 短路必须连台账文件都不创建"
