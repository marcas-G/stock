"""xscore 流水线 manifest 样本字段单测（锁箱纪律 T9/T12b）。

行为要求：
- `panel_dates`：从 panel npz 的 `dates` 取首/末日期；文件缺失/无 dates/空/非法 → None；
- `write_manifest`：读-合并-原子写；已有字段保留、只更新所列键；无文件则新建；
  真正落盘（重新读文件可见），不留临时文件；
- `lockbox_sample`：真实读 SQLite 台账（store.connect/roll，不 mock）——
  无 state → `window_id=None`、`sample_role="unknown"`；
  有 state → 按 panel 区间与窗口起点判 is/mixed/lockbox，window_id 取台账 state；
  panel 区间不可得 → 回退已发布日历 min/max；
  state 窗口陈旧（跨季未 roll）→ 用 state 窗口声明而非报错；
- `lockbox_register` / `lockbox_finalize`（R42 final-test-once）：流水线 config=候选——
  起点钉死身份（panel **内容 SHA-256** + config **内容 sha**），碰箱（is 除外）登记 final
  （每版本一次：同版本重复时 `replay_ok`（run 产物已在）→ 复用既有 access_id、
  零新增、留"复用"日志；否则 `LOCKBOX_FINAL_DUPLICATE` 并提示改版本/`FACTORLAB_RE_FINAL=1`；
  面板路径/mtime 改变但内容不变仍复用；旧 stat 指纹记录通过 config/panel 内容哈希迁移复用，
  不新增 final；
  `FACTORLAB_RE_FINAL=1` → 操作员重测：新登记行 + reason 追加 `|re-final`）；
  收尾用起点 context 写 manifest（**不得重算 fp**：首尾之间 panel 变化仍单登记单 id）；
  access_id 写入 run/campaign manifest 的 `access_ids`（campaign 既有 ∪ 新 id，不丢旧），
  `result_ref` 回填 run out 目录；`FACTORLAB_LOCKBOX=off`/无 state → 不读不写台账
  （unknown/null/[]，manifest **无** `lockbox_off` 留痕键）；stale state → 抛
  `LOCKBOX_WINDOW_STALE`（不静默解封）；panel 缺失 → 拒以 `artifact_sha256=missing`
  登记（fail-fast）；is 窗口 → 零登记；
- 禁止行为：结果不得是硬编码——roll 前后、不同面板区间输出必须变化。

突变必杀：`lockbox_sample` 换成返回常量、`write_manifest` 不落盘、
`lockbox_register` 不登记/收尾重算 fp/写 ids 丢新 id/重复版本静默复用 → 对应测试失败。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
import sys
import types

import numpy as np
import pytest
import yaml

PIPELINE = pathlib.Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE))
import xlib as lib  # noqa: E402
import research_xscore_lockbox as xscore_lockbox  # noqa: E402

from factorlab.adapters import lockbox_store as store  # noqa: E402
from factorlab.core.lockbox import (  # noqa: E402
    LockboxError,
    LockboxWindow,
    candidate_fingerprint,
)


def test_platform_import_uses_shared_env_helper(monkeypatch):
    calls = []
    env = types.ModuleType("_env")
    env.ensure_platform = lambda: calls.append("ensure_platform")
    factorlab = types.ModuleType("factorlab")
    monkeypatch.setitem(sys.modules, "_env", env)
    monkeypatch.setitem(sys.modules, "factorlab", factorlab)
    monkeypatch.setattr(sys, "path", sys.path.copy())

    xscore_lockbox._ensure_platform_src()

    assert calls == ["ensure_platform"]


def _health(root: pathlib.Path, days: list[str]) -> pathlib.Path:
    d = root / "health" / "ashare_daily"
    d.mkdir(parents=True)
    for day in days:
        (d / f"{day}.json").write_text("{}", encoding="utf-8")
    return root / "health"


def _panel(path: pathlib.Path, days: list[str]) -> pathlib.Path:
    np.savez(path, dates=np.array(days), members=np.array(["f1"]))
    return path


def _ledger(root: pathlib.Path, window: LockboxWindow | None = None) -> pathlib.Path:
    db = root / "ledger.sqlite"
    conn = store.connect(db)
    if window is not None:
        store.roll(conn, window=window)
    conn.close()
    return db


def test_panel_dates_reads_npz_range(tmp_path):
    p = _panel(tmp_path / "panel.npz", ["2023-05-25", "2024-01-02", "2026-07-31"])
    assert lib.panel_dates(p) == (dt.date(2023, 5, 25), dt.date(2026, 7, 31))


def test_panel_dates_missing_or_bad_is_none(tmp_path):
    assert lib.panel_dates(tmp_path / "nope.npz") is None
    no_dates = tmp_path / "no_dates.npz"
    np.savez(no_dates, other=np.array([1]))
    assert lib.panel_dates(no_dates) is None
    empty = tmp_path / "empty.npz"
    np.savez(empty, dates=np.array([], dtype="<U10"))
    assert lib.panel_dates(empty) is None


def test_write_manifest_preserves_existing_and_updates(tmp_path):
    path = tmp_path / "out" / "manifest.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"campaign": "x", "panel_sig": "old"}), encoding="utf-8")
    updates = {"panel_sig": "new", "window_id": None,
               "sample_role": "unknown", "access_ids": []}
    doc = lib.write_manifest(path, updates)
    assert doc == {"campaign": "x", "panel_sig": "new", "window_id": None,
                   "sample_role": "unknown", "access_ids": []}
    assert json.loads(path.read_text(encoding="utf-8")) == doc
    assert list(path.parent.glob("*.tmp")) == []


def test_write_manifest_creates_new_file(tmp_path):
    path = tmp_path / "nested" / "manifest.json"
    doc = lib.write_manifest(path, {"platform_commit": "abc123"})
    assert doc == {"platform_commit": "abc123"}
    assert json.loads(path.read_text(encoding="utf-8")) == {"platform_commit": "abc123"}
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_write_manifest_pair_writes_both_with_same_fields(tmp_path):
    run = tmp_path / "camp" / "run" / "manifest.json"
    camp = tmp_path / "camp" / "manifest.json"
    updates = {"platform_commit": "abc123", "panel_sig": "sig",
               "window_id": None, "sample_role": "unknown", "access_ids": []}
    paths = lib.write_manifest_pair(run, camp, updates)
    assert paths == [run, camp]
    assert json.loads(run.read_text(encoding="utf-8")) == updates
    assert json.loads(camp.read_text(encoding="utf-8")) == updates


def test_write_manifest_pair_preserves_nonempty_access_ids(tmp_path):
    run = tmp_path / "camp" / "run" / "manifest.json"
    camp = tmp_path / "camp" / "manifest.json"
    run.parent.mkdir(parents=True)
    run.write_text(json.dumps({"access_ids": ["RUN-1"], "keep": 1}), encoding="utf-8")
    camp.write_text(json.dumps({"access_ids": ["CAMP-1"]}), encoding="utf-8")
    lib.write_manifest_pair(run, camp, {"sample_role": "lockbox", "access_ids": []})
    run_doc = json.loads(run.read_text(encoding="utf-8"))
    camp_doc = json.loads(camp.read_text(encoding="utf-8"))
    assert run_doc["access_ids"] == ["RUN-1"], "既有非空 access_ids 不得被 [] 清空"
    assert camp_doc["access_ids"] == ["CAMP-1"], "既有非空 access_ids 不得被 [] 清空"
    assert run_doc["sample_role"] == "lockbox" and camp_doc["sample_role"] == "lockbox"
    assert run_doc["keep"] == 1


def test_write_manifest_pair_empty_existing_ids_stay_empty(tmp_path):
    run = tmp_path / "camp" / "run" / "manifest.json"
    camp = tmp_path / "camp" / "manifest.json"
    run.parent.mkdir(parents=True)
    run.write_text(json.dumps({"access_ids": []}), encoding="utf-8")
    lib.write_manifest_pair(run, camp, {"access_ids": []})
    assert json.loads(run.read_text(encoding="utf-8"))["access_ids"] == []
    assert json.loads(camp.read_text(encoding="utf-8"))["access_ids"] == []


def test_lockbox_sample_no_state_is_unknown(tmp_path):
    db = _ledger(tmp_path)
    health = _health(tmp_path, ["2026-07-01", "2026-07-02"])
    got = lib.lockbox_sample(panel_start=dt.date(2024, 1, 1), panel_end=dt.date(2024, 12, 31),
                             today=dt.date(2026, 9, 21), db_path=db, health_root=health)
    assert got == {"window_id": None, "sample_role": "unknown"}


def test_lockbox_sample_roles_from_real_ledger(tmp_path):
    health = _health(tmp_path, ["2025-06-30", "2025-07-01",
                                "2026-07-01", "2026-07-02", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q2", dt.date(2025, 7, 1),
                                         dt.date(2026, 7, 3)))
    cases = {
        (dt.date(2024, 1, 1), dt.date(2025, 6, 30)): "is",
        (dt.date(2025, 6, 1), dt.date(2025, 8, 1)): "mixed",
        (dt.date(2025, 8, 1), dt.date(2026, 1, 1)): "lockbox",
    }
    for (start, end), role in cases.items():
        got = lib.lockbox_sample(panel_start=start, panel_end=end,
                                 today=dt.date(2026, 9, 21), db_path=db, health_root=health)
        assert got == {"window_id": "2026Q2", "sample_role": role}, (start, end)


def test_lockbox_sample_falls_back_to_published_days(tmp_path):
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q2", dt.date(2025, 7, 1),
                                         dt.date(2026, 7, 3)))
    got = lib.lockbox_sample(panel_start=None, panel_end=None,
                             today=dt.date(2026, 9, 21), db_path=db, health_root=health)
    assert got == {"window_id": "2026Q2", "sample_role": "mixed"}


def test_lockbox_sample_stale_state_uses_state_window(tmp_path):
    health = _health(tmp_path, ["2024-06-28", "2024-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q1", dt.date(2024, 7, 1),
                                         dt.date(2025, 6, 30)))
    got = lib.lockbox_sample(panel_start=dt.date(2025, 1, 1), panel_end=dt.date(2025, 12, 31),
                             today=dt.date(2026, 9, 21), db_path=db, health_root=health)
    assert got == {"window_id": "2026Q1", "sample_role": "lockbox"}


# ── T12b 流水线 final 登记 + access_ids 接线（沙箱台账；真 SQLite）────────
# 流水线 config = 候选：起点钉死身份（panel 内容 SHA-256 + config 内容 sha）→ 碰箱幂等
# 登记 final（收尾复用起点 fp，不得重算）；access_id 写入 run/campaign manifest，
# result_ref 回填 run out 目录。off/无 state → 不读不写台账（unknown/[]）；
# stale state → 响亮抛 LOCKBOX_WINDOW_STALE（不静默解封）；panel 缺失 → 拒登记。

def _rows(db: pathlib.Path) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM lockbox_access ORDER BY ts_utc, access_id")]
    finally:
        conn.close()


def _config(tmp_path, name="a.yaml", body="model: m0\n") -> pathlib.Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _begin(tmp_path, db, health, *, config="a.yaml", body="model: m0\n",
           panel_sig="sig-1", panel=None, today=dt.date(2026, 9, 21),
           replay_ok=False) -> dict:
    if panel is None:
        panel = tmp_path / "panel.npz"
        if not panel.is_file():
            panel = _panel(
                panel, ["2025-08-01", "2026-01-01"]
            )
    return lib.lockbox_register(
        panel=panel, panel_sig=panel_sig,
        config_path=_config(tmp_path, config, body),
        db_path=db, health_root=health, today=today, replay_ok=replay_ok)


def _finish(db, *, out, ctx, result_ref=None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    return lib.lockbox_finalize(
        ctx, run_manifest=out / "manifest.json",
        campaign_manifest=out.parent / "manifest.json",
        base_updates={"platform_commit": "deadbeef", "panel_sig": "sig-1"},
        db_path=db, result_ref=result_ref)


def _campaign(tmp_path, *, ids=("OLD-1",)) -> pathlib.Path:
    path = tmp_path / "camp" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"campaign": "camp", "access_ids": list(ids),
                                "keep": 1}), encoding="utf-8")
    return path


def _register_legacy_stat_access(
    db: pathlib.Path,
    panel: pathlib.Path,
    config: pathlib.Path,
    window: LockboxWindow,
    *,
    panel_sig: str,
    attempt_sha256: str | None = None,
    result_ref: str | None = None,
    re_final: bool = False,
) -> str:
    stat = panel.stat()
    legacy_artifact = hashlib.sha256(
        f"{panel.resolve()}:{stat.st_size}:{int(stat.st_mtime)}".encode()
    ).hexdigest()[:16]
    params = {
        "config": lib.file_content_sha(config),
        "panel_sig": panel_sig,
    }
    if attempt_sha256 is not None:
        params["flow_attempt_sha256"] = attempt_sha256
    fingerprint = candidate_fingerprint(
        artifact_sha256=legacy_artifact,
        params={
            key: value
            for key, value in params.items()
            if key != "flow_attempt_sha256"
        },
        window_id=window.window_id,
        kind="final",
    )
    conn = store.connect(db)
    try:
        access_id = store.register_access(
            conn,
            kind="final",
            fingerprint=fingerprint,
            artifact=str(panel),
            params=params,
            command="legacy xscore",
            reason="pipeline:legacy",
            window=window,
            tool="xscore-pipeline",
            re_final=re_final,
        )
        if result_ref is not None:
            store.update_result_ref(conn, access_id, result_ref)
        return access_id
    finally:
        conn.close()


def test_lockbox_register_finalize_registers_final_and_merges_ids(tmp_path):
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q2", dt.date(2025, 7, 1),
                                         dt.date(2026, 7, 3)))
    campaign = _campaign(tmp_path)
    out = tmp_path / "camp" / "run"

    ctx = _begin(tmp_path, db, health)
    assert ctx["sample_role"] == "lockbox" and ctx["window_id"] == "2026Q2"
    assert ctx["access_id"] and ctx["fingerprint"] and ctx["artifact_sha256"] != "missing"
    assert ctx["artifact_sha256"] == lib.file_content_sha256(
        tmp_path / "panel.npz"
    )
    doc = _finish(db, out=out, ctx=ctx, result_ref=str(out))

    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "final" and row["window_id"] == "2026Q2"
    assert row["window_start"] == "2025-07-01"
    assert row["reason"].startswith("pipeline:") and row["reason"].endswith("a.yaml")
    assert row["result_ref"] == str(out)
    assert row["tool"] == "xscore-pipeline"
    assert doc["window_id"] == "2026Q2" and doc["sample_role"] == "lockbox"
    assert doc["access_ids"] == ["OLD-1", row["access_id"]], "campaign = 既有 ∪ 新 id"
    assert doc["keep"] == 1 and doc["platform_commit"] == "deadbeef"
    run_doc = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert run_doc["access_ids"] == [row["access_id"]]
    assert run_doc["sample_role"] == "lockbox" and run_doc["window_id"] == "2026Q2"
    assert json.loads(campaign.read_text(encoding="utf-8")) == doc


def test_file_content_sha256_ignores_path_and_mtime(tmp_path):
    original = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    original_sig = lib.file_content_sha256(original)
    copied = tmp_path / "renamed-panel.npz"
    copied.write_bytes(original.read_bytes())
    os.utime(copied, (1_600_000_000, 1_600_000_000))
    os.utime(original, (1_700_000_000, 1_700_000_000))

    assert lib.file_content_sha256(original) == original_sig
    assert lib.file_content_sha256(copied) == original_sig


def test_lockbox_replay_reuses_legacy_stat_fingerprint_after_panel_touch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(xscore_lockbox, "_ensure_platform_src", lambda: None)
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    window = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 7, 3))
    db = _ledger(tmp_path, window)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    config = _config(tmp_path, body="model: m0\n")
    panel_sig = lib.file_content_sha256(panel)
    legacy_id = _register_legacy_stat_access(
        db, panel, config, window, panel_sig=panel_sig,
        result_ref="/published/composite"
    )

    os.utime(panel, (1_700_000_000, 1_700_000_000))
    replay = lib.lockbox_register(
        panel=panel,
        panel_sig=panel_sig,
        config_path=str(config),
        replay_ok=True,
        published_access_ids=[legacy_id],
        published_panel_sig=panel_sig,
        published_window_id=window.window_id,
        published_sample_role="lockbox",
        published_result_ref="/published/composite",
        db_path=db,
        health_root=health,
        today=dt.date(2026, 9, 21),
    )

    assert replay["access_id"] == legacy_id
    assert len(_rows(db)) == 1, "旧 stat 指纹 replay 不得新增锁箱访问"


def test_lockbox_resume_migrates_only_matching_legacy_attempt(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(xscore_lockbox, "_ensure_platform_src", lambda: None)
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    window = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 7, 3))
    db = _ledger(tmp_path, window)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    config = _config(tmp_path, body="model: m0\n")
    attempt_sha = "a" * 64
    panel_sig = lib.file_content_sha256(panel)
    legacy_id = _register_legacy_stat_access(
        db, panel, config, window, panel_sig=panel_sig,
        attempt_sha256=attempt_sha
    )
    os.utime(panel, (1_700_000_000, 1_700_000_000))

    resumed = lib.lockbox_register(
        panel=panel,
        panel_sig=panel_sig,
        config_path=str(config),
        flow_attempt_sha256=attempt_sha,
        resume_pending=True,
        db_path=db,
        health_root=health,
        today=dt.date(2026, 9, 21),
    )
    assert resumed["access_id"] == legacy_id
    with pytest.raises(LockboxError, match="LOCKBOX_FINAL_DUPLICATE"):
        lib.lockbox_register(
            panel=panel,
            panel_sig=panel_sig,
            config_path=str(config),
            flow_attempt_sha256="b" * 64,
            resume_pending=True,
            db_path=db,
            health_root=health,
            today=dt.date(2026, 9, 21),
        )
    assert len(_rows(db)) == 1, "不同 attempt 不得通过旧指纹重复登记"


def test_legacy_published_replay_uses_manifest_access_id_after_panel_touch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(xscore_lockbox, "_ensure_platform_src", lambda: None)
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    window = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 7, 3))
    db = _ledger(tmp_path, window)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    config = _config(tmp_path, body="model: m0\n")
    out = tmp_path / "camp" / "run"
    out.mkdir(parents=True)
    (out / "REPORT.md").write_text("# published\n", encoding="utf-8")
    legacy_panel_sig = lib.file_sig(panel)
    legacy_id = _register_legacy_stat_access(
        db, panel, config, window, panel_sig=legacy_panel_sig, result_ref=str(out)
    )
    pending_id = _register_legacy_stat_access(
        db, panel, config, window, panel_sig=legacy_panel_sig, re_final=True
    )
    assert pending_id != legacy_id

    os.utime(panel, (1_700_000_000, 1_700_000_000))
    replay = lib.lockbox_register(
        panel=panel,
        panel_sig=lib.file_content_sha256(panel),
        config_path=str(config),
        replay_ok=True,
        published_access_ids=[legacy_id],
        published_panel_sig=legacy_panel_sig,
        published_window_id=window.window_id,
        published_sample_role="lockbox",
        published_result_ref=str(out),
        db_path=db,
        health_root=health,
        today=dt.date(2026, 9, 21),
    )

    assert replay["access_id"] == legacy_id
    assert replay["published_replay"] is True
    assert replay["access_id"] != pending_id
    assert len(_rows(db)) == 2, "旧 published manifest 指向的访问必须原样复用"


def test_published_replay_prefers_latest_re_final_access_id(tmp_path, monkeypatch):
    monkeypatch.setattr(xscore_lockbox, "_ensure_platform_src", lambda: None)
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    window = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 7, 3))
    db = _ledger(tmp_path, window)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    config = _config(tmp_path, body="model: m0\n")
    out = tmp_path / "camp" / "run"
    out.mkdir(parents=True)
    (out / "REPORT.md").write_text("# published\n", encoding="utf-8")
    panel_sig = lib.file_content_sha256(panel)
    old_id = _register_legacy_stat_access(
        db, panel, config, window, panel_sig=panel_sig, result_ref=str(out)
    )
    newest_id = _register_legacy_stat_access(
        db, panel, config, window, panel_sig=panel_sig,
        result_ref=str(out), re_final=True,
    )

    replay = lib.lockbox_register(
        panel=panel,
        panel_sig=panel_sig,
        config_path=str(config),
        replay_ok=True,
        published_access_ids=[old_id, newest_id],
        published_panel_sig=panel_sig,
        published_window_id=window.window_id,
        published_sample_role="lockbox",
        published_result_ref=str(out),
        db_path=db,
        health_root=health,
        today=dt.date(2026, 9, 21),
    )

    assert replay["access_id"] == newest_id
    assert replay["access_id"] != old_id
    assert replay["published_replay"] is True
    assert len(_rows(db)) == 2


def test_published_replay_rejects_manifest_ledger_panel_signature_mismatch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(xscore_lockbox, "_ensure_platform_src", lambda: None)
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    window = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 7, 3))
    db = _ledger(tmp_path, window)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    config = _config(tmp_path, body="model: m0\n")
    out = tmp_path / "camp" / "run"
    out.mkdir(parents=True)
    (out / "REPORT.md").write_text("# published\n", encoding="utf-8")
    old_panel_sig = lib.file_sig(panel)
    access_id = _register_legacy_stat_access(
        db, panel, config, window, panel_sig=old_panel_sig,
        result_ref=str(out),
    )

    with pytest.raises(LockboxError) as exc:
        lib.lockbox_register(
            panel=panel,
            panel_sig=lib.file_content_sha256(panel),
            config_path=str(config),
            replay_ok=True,
            published_access_ids=[access_id],
            published_panel_sig=lib.file_content_sha256(panel),
            published_window_id=window.window_id,
            published_sample_role="lockbox",
            published_result_ref=str(out),
            db_path=db,
            health_root=health,
            today=dt.date(2026, 9, 21),
        )

    assert exc.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert len(_rows(db)) == 1, "签名不匹配时必须拒绝复用，且不能新增锁箱登记"


def test_exact_fingerprint_replay_requires_published_access_id(tmp_path, monkeypatch):
    monkeypatch.setattr(xscore_lockbox, "_ensure_platform_src", lambda: None)
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    window = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 7, 3))
    db = _ledger(tmp_path, window)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    panel_sig = lib.file_content_sha256(panel)
    config = _config(tmp_path, body="model: m0\n")
    out = tmp_path / "camp" / "run"
    ctx = _begin(
        tmp_path, db, health, panel=panel, config=config.name,
        panel_sig=panel_sig,
    )
    _finish(db, out=out, ctx=ctx, result_ref=str(out))
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["access_ids"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(LockboxError) as exc:
        lib.lockbox_register(
            panel=panel,
            panel_sig=panel_sig,
            config_path=str(config),
            replay_ok=True,
            published_access_ids=manifest["access_ids"],
            published_panel_sig=panel_sig,
            published_window_id=window.window_id,
            published_sample_role="lockbox",
            published_result_ref=str(out),
            db_path=db,
            health_root=health,
            today=dt.date(2026, 9, 21),
        )

    rows = _rows(db)
    assert exc.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert len(rows) == 1
    assert rows[0]["result_ref"] == str(out)


def test_stat_fingerprint_replay_rejects_mismatched_result_ref(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(xscore_lockbox, "_ensure_platform_src", lambda: None)
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    window = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 7, 3))
    db = _ledger(tmp_path, window)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    panel_sig = lib.file_content_sha256(panel)
    config = _config(tmp_path, body="model: m0\n")
    original_out = tmp_path / "camp" / "original"
    wrong_out = tmp_path / "camp" / "other"
    access_id = _register_legacy_stat_access(
        db, panel, config, window, panel_sig=panel_sig,
        result_ref=str(original_out),
    )

    with pytest.raises(LockboxError) as exc:
        lib.lockbox_register(
            panel=panel,
            panel_sig=panel_sig,
            config_path=str(config),
            replay_ok=True,
            published_access_ids=[access_id],
            published_panel_sig=panel_sig,
            published_window_id=window.window_id,
            published_sample_role="lockbox",
            published_result_ref=str(wrong_out),
            db_path=db,
            health_root=health,
            today=dt.date(2026, 9, 21),
        )

    rows = _rows(db)
    assert exc.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert len(rows) == 1
    assert rows[0]["access_id"] == access_id
    assert rows[0]["result_ref"] == str(original_out)


def test_legacy_pending_stat_attempt_requires_unchanged_panel_signature(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(xscore_lockbox, "_ensure_platform_src", lambda: None)
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    window = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 7, 3))
    db = _ledger(tmp_path, window)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    config = _config(tmp_path, body="model: m0\n")
    legacy_panel_sig = lib.file_sig(panel)
    legacy_id = _register_legacy_stat_access(
        db, panel, config, window, panel_sig=legacy_panel_sig
    )
    out = tmp_path / "camp" / "run"
    out.mkdir(parents=True)
    (out / "manifest.json").write_text(
        json.dumps({
            "access_ids": [legacy_id],
            "panel_sig": legacy_panel_sig,
            "window_id": window.window_id,
            "sample_role": "lockbox",
        }),
        encoding="utf-8",
    )
    resumed = lib.lockbox_register(
        panel=panel,
        panel_sig=lib.file_content_sha256(panel),
        config_path=str(config),
        replay_ok=True,
        published_access_ids=[legacy_id],
        published_panel_sig=legacy_panel_sig,
        published_window_id=window.window_id,
        published_sample_role="lockbox",
        published_result_ref=str(out),
        db_path=db,
        health_root=health,
        today=dt.date(2026, 9, 21),
    )
    assert resumed["access_id"] == legacy_id
    assert resumed["pending_resume"] is True

    os.utime(panel, (1_700_000_000, 1_700_000_000))

    with pytest.raises(LockboxError, match="LOCKBOX_FINAL_DUPLICATE"):
        lib.lockbox_register(
            panel=panel,
            panel_sig=lib.file_content_sha256(panel),
            config_path=str(config),
            replay_ok=True,
            published_access_ids=[legacy_id],
            published_panel_sig=legacy_panel_sig,
            published_window_id=window.window_id,
            published_sample_role="lockbox",
            published_result_ref=str(out),
            db_path=db,
            health_root=health,
            today=dt.date(2026, 9, 21),
        )

    assert len(_rows(db)) == 1, "无法证明旧 pending 面板未变时必须拒绝新增访问"


def test_re_final_migrating_old_stat_candidate_keeps_audit_marker(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(xscore_lockbox, "_ensure_platform_src", lambda: None)
    monkeypatch.setenv("FACTORLAB_RE_FINAL", "1")
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    window = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 7, 3))
    db = _ledger(tmp_path, window)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    config = _config(tmp_path, body="model: m0\n")
    _register_legacy_stat_access(
        db, panel, config, window, panel_sig=lib.file_sig(panel)
    )
    os.utime(panel, (1_700_000_000, 1_700_000_000))

    replay = lib.lockbox_register(
        panel=panel,
        panel_sig=lib.file_content_sha256(panel),
        config_path=str(config),
        db_path=db,
        health_root=health,
        today=dt.date(2026, 9, 21),
    )

    rows = _rows(db)
    assert len(rows) == 2
    assert replay["access_id"] == rows[-1]["access_id"]
    assert rows[-1]["reason"].endswith("|re-final")


def test_lockbox_register_replay_reuses_id_without_new_registration(tmp_path, capsys):
    """产物已在（replay_ok）→ 复用既有 access_id：零新增、不报错、日志含复用。"""
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q2", dt.date(2025, 7, 1),
                                         dt.date(2026, 7, 3)))
    _campaign(tmp_path)
    out = tmp_path / "camp" / "run"

    ctx1 = _begin(tmp_path, db, health)
    published = _finish(db, out=out, ctx=ctx1, result_ref=str(out))
    rows1 = _rows(db)
    assert len(rows1) == 1
    capsys.readouterr()

    ctx2 = lib.lockbox_register(
        panel=tmp_path / "panel.npz",
        panel_sig=published["panel_sig"],
        config_path=str(_config(tmp_path)),
        replay_ok=True,
        published_access_ids=published["access_ids"],
        published_panel_sig=published["panel_sig"],
        published_window_id=published["window_id"],
        published_sample_role=published["sample_role"],
        published_result_ref=str(out),
        db_path=db,
        health_root=health,
        today=dt.date(2026, 9, 21),
    )
    rows2 = _rows(db)
    assert len(rows2) == 1, "replay 不得新增登记行"
    assert ctx2["access_id"] == ctx1["access_id"] == rows1[0]["access_id"], "复用 access_id"
    assert "复用" in capsys.readouterr().out, "replay 必须留可观察日志"
    doc2 = _finish(db, out=out, ctx=ctx2, result_ref=str(out))
    assert rows2[0]["result_ref"] == str(out)
    assert doc2["access_ids"] == ["OLD-1", rows1[0]["access_id"]]
    assert doc2["access_ids"].count(rows1[0]["access_id"]) == 1, "不得重复计入 id"


def test_lockbox_register_duplicate_without_products_raises(tmp_path):
    """同版本二跑且产物不在（非 replay）→ LOCKBOX_FINAL_DUPLICATE（指引重测），零新增。"""
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q2", dt.date(2025, 7, 1),
                                         dt.date(2026, 7, 3)))

    ctx1 = _begin(tmp_path, db, health)
    assert ctx1["access_id"] and len(_rows(db)) == 1

    with pytest.raises(LockboxError) as ei:
        _begin(tmp_path, db, health)
    assert ei.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert "FACTORLAB_RE_FINAL=1" in str(ei.value), "重复拒绝必须给出重测逃生提示"
    assert len(_rows(db)) == 1, "重复拒绝不得写入新登记"


def test_lockbox_register_re_final_env_appends_audit_row(tmp_path, monkeypatch):
    """FACTORLAB_RE_FINAL=1（操作员重测）：即使产物在也新登记一行，reason 带 |re-final。"""
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q2", dt.date(2025, 7, 1),
                                         dt.date(2026, 7, 3)))
    _campaign(tmp_path)
    out = tmp_path / "camp" / "run"

    ctx1 = _begin(tmp_path, db, health)
    _finish(db, out=out, ctx=ctx1, result_ref=str(out))

    monkeypatch.setenv("FACTORLAB_RE_FINAL", "1")
    ctx2 = _begin(tmp_path, db, health, replay_ok=True)
    rows = _rows(db)

    assert len(rows) == 2, "RE_FINAL 优先于 replay：显式重测必须新增登记行"
    assert ctx2["access_id"] != ctx1["access_id"]
    assert rows[-1]["reason"].endswith("|re-final"), "重测登记必须留审计标记"
    assert rows[-1]["fingerprint"] == ctx1["fingerprint"], "仍是同版本重测"


def test_lockbox_register_finalize_pins_identity_across_panel_rewrite(tmp_path):
    """首尾之间 panel 被重写（内容+mtime 变化）→ 收尾复用起点 fp，仍单登记单 id。"""
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q2", dt.date(2025, 7, 1),
                                         dt.date(2026, 7, 3)))
    _campaign(tmp_path)
    out = tmp_path / "camp" / "run"
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])

    ctx = _begin(tmp_path, db, health, panel=panel)
    assert len(_rows(db)) == 1
    panel.write_text("rewritten", encoding="utf-8")
    os.utime(panel, (1_600_000_000, 1_600_000_000))
    assert lib.file_sig(panel) != ctx["artifact_sha256"], "测试前提：panel 身份已变"
    doc = _finish(db, out=out, ctx=ctx, result_ref=str(out))

    rows = _rows(db)
    assert len(rows) == 1, "收尾不得按新 panel 身份重复登记"
    assert rows[0]["access_id"] == ctx["access_id"]
    assert doc["access_ids"] == ["OLD-1", ctx["access_id"]]
    assert doc["access_ids"].count(ctx["access_id"]) == 1


def test_lockbox_register_no_state_unknown_zero_registration(tmp_path):
    db = _ledger(tmp_path)
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    out = tmp_path / "camp" / "run"

    ctx = _begin(tmp_path, db, health)
    doc = _finish(db, out=out, ctx=ctx)

    assert ctx["window_id"] is None and ctx["sample_role"] == "unknown"
    assert ctx["access_id"] is None
    assert doc["access_ids"] == []
    assert _rows(db) == [], "无 state 不得登记"
    run_doc = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert run_doc["access_ids"] == [] and run_doc["sample_role"] == "unknown"


def test_lockbox_register_is_window_zero_registration(tmp_path):
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q2", dt.date(2025, 7, 1),
                                         dt.date(2026, 7, 3)))
    panel = _panel(tmp_path / "panel.npz", ["2024-01-02", "2025-06-30"])
    out = tmp_path / "camp" / "run"

    ctx = _begin(tmp_path, db, health, panel=panel)
    doc = _finish(db, out=out, ctx=ctx)

    assert ctx["sample_role"] == "is" and ctx["window_id"] == "2026Q2"
    assert ctx["access_id"] is None
    assert doc["access_ids"] == []
    assert _rows(db) == [], "is 窗口不得登记"


def test_lockbox_register_missing_panel_fails_fast(tmp_path):
    """panel 缺失 → 拒绝以 artifact_sha256=missing 登记（fail-fast，零登记）。"""
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q2", dt.date(2025, 7, 1),
                                         dt.date(2026, 7, 3)))
    with pytest.raises(FileNotFoundError) as ei:
        lib.lockbox_register(panel=tmp_path / "absent-panel.npz", panel_sig="sig-1",
                             config_path=_config(tmp_path), db_path=db,
                             health_root=health, today=dt.date(2026, 9, 21))
    assert "missing" in str(ei.value)
    assert _rows(db) == []


def test_lockbox_env_off_skips_ledger_io_without_trace_field(tmp_path, monkeypatch):
    """FACTORLAB_LOCKBOX=off → 与无 state 同：不读/不写台账、unknown/null/[]；
    R42 起不再写 `lockbox_off` 留痕键（ctx/manifest 均无）。"""
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    db = tmp_path / "never-created.sqlite"
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "off")
    out = tmp_path / "camp" / "run"

    ctx = _begin(tmp_path, db, health)
    doc = _finish(db, out=out, ctx=ctx)

    assert ctx["window_id"] is None and ctx["sample_role"] == "unknown"
    assert ctx["access_id"] is None
    assert "lockbox_off" not in ctx, "off 留痕字段已删（R42）"
    assert not db.exists(), "off 不得连台账（connect 会建 schema/文件）"
    assert doc["access_ids"] == [] and doc["sample_role"] == "unknown"
    assert "lockbox_off" not in doc, "manifest 不得再写 lockbox_off"
    run_doc = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert "lockbox_off" not in run_doc


def test_lockbox_register_stale_state_fails_with_roll_hint(tmp_path):
    """跨季未 roll → 严格 current_window 抛 stale（不静默按旧窗登记）。"""
    health = _health(tmp_path, ["2024-06-28", "2024-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q1", dt.date(2024, 7, 1),
                                         dt.date(2025, 6, 30)))
    with pytest.raises(LockboxError) as ei:
        lib.lockbox_register(
            panel=_panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"]),
            panel_sig="sig-1", config_path=_config(tmp_path), db_path=db,
            health_root=health, today=dt.date(2026, 9, 21))
    assert ei.value.code == "LOCKBOX_WINDOW_STALE"
    assert "factorlab lockbox roll" in str(ei.value)
    assert _rows(db) == []


# ── flows 接线（fake prefect shim；T10/T12b）────────────────────────────
# flows.py 顶层 import prefect（平台 venv 无此依赖）——测试注入最小替身，
# 真执行 `flows._register_lockbox` / `flows._write_manifest`，锁死
# 「flow 开始登记 final、run + campaign 双写、既有键保留、access_ids 并集、
# result_ref 回填」的接线（改成不登记/不双写/清空 ids 此测试必红）。

def _fake_prefect(monkeypatch):
    import functools
    import types

    class _Future:
        def __init__(self, value=None):
            self._value = value

        def result(self):
            return self._value

    class _FakeTask:
        def __init__(self, fn):
            self.fn = fn
            functools.update_wrapper(self, fn)

        def __call__(self, *args, **kwargs):
            return self.fn(*args, **kwargs)

        def submit(self, *args, **kwargs):
            return _Future(self.fn(*args, **kwargs))

    class _FakeFlow:
        def __init__(self, fn):
            self.fn = fn
            functools.update_wrapper(self, fn)

        def __call__(self, *args, **kwargs):
            return self.fn(*args, **kwargs)

        def with_options(self, **kwargs):
            return self

    def task(*args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return _FakeTask(args[0])
        return lambda fn: _FakeTask(fn)

    def flow(*args, **kwargs):
        return lambda fn: _FakeFlow(fn)

    class ThreadPoolTaskRunner:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    prefect = types.ModuleType("prefect")
    prefect.flow, prefect.task = flow, task
    prefect_flows = types.ModuleType("prefect.flows")
    prefect_flows.flow = flow
    prefect_tasks = types.ModuleType("prefect.tasks")
    prefect_tasks.task = task
    prefect_runners = types.ModuleType("prefect.task_runners")
    prefect_runners.ThreadPoolTaskRunner = ThreadPoolTaskRunner
    prefect.task_runners = prefect_runners
    monkeypatch.setitem(sys.modules, "prefect", prefect)
    monkeypatch.setitem(sys.modules, "prefect.flows", prefect_flows)
    monkeypatch.setitem(sys.modules, "prefect.tasks", prefect_tasks)
    monkeypatch.setitem(sys.modules, "prefect.task_runners", prefect_runners)


def _load_flows(monkeypatch):
    import importlib.util
    _fake_prefect(monkeypatch)
    spec = importlib.util.spec_from_file_location("xscore_flows_t10", PIPELINE / "flows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _minimal_cfg(tmp_path, out):
    return {"out": str(out), "panel": str(tmp_path / "absent-panel.npz"),
            "panel_sig": "sig-1", "config_path": "configs/x.yaml"}


def _wire_ledger(monkeypatch, flows, db, health):
    """注入沙箱台账/日历（生产缺省走 settings/DATA_ROOT，测试绝不碰真台账）。"""
    real_begin = flows.lib.lockbox_register
    real_finish = flows.lib.lockbox_finalize

    def begin(**kwargs):
        kwargs["db_path"] = db
        kwargs["health_root"] = health
        kwargs.setdefault("today", dt.date(2026, 9, 21))
        return real_begin(**kwargs)

    def finish(ctx, **kwargs):
        kwargs["db_path"] = db
        return real_finish(ctx, **kwargs)

    monkeypatch.setattr(flows.lib, "lockbox_register", begin)
    monkeypatch.setattr(flows.lib, "lockbox_finalize", finish)


def test_flows_write_manifest_dual_writes_and_preserves_campaign_ids(tmp_path, monkeypatch):
    flows = _load_flows(monkeypatch)
    monkeypatch.setattr(flows.lib, "git_commit", lambda: "deadbeef")
    db = _ledger(tmp_path)  # 未初始化：unknown/[]、零登记
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    _wire_ledger(monkeypatch, flows, db, health)
    out = tmp_path / "camp" / "run"
    out.mkdir(parents=True)
    campaign = tmp_path / "camp" / "manifest.json"
    campaign.write_text(json.dumps({"campaign": "camp", "access_ids": ["CAMP-1"],
                                    "keep": 1}), encoding="utf-8")
    run_manifest = out / "manifest.json"
    run_manifest.write_text(json.dumps({"access_ids": [], "run_keep": 2}), encoding="utf-8")

    cfg = _minimal_cfg(tmp_path, out)
    ctx = flows._register_lockbox(cfg)
    result = flows._write_manifest(cfg, ctx)

    run_doc = json.loads(run_manifest.read_text(encoding="utf-8"))
    camp_doc = json.loads(campaign.read_text(encoding="utf-8"))
    assert run_doc["platform_commit"] == camp_doc["platform_commit"] == "deadbeef"
    assert run_doc["sample_role"] == camp_doc["sample_role"] == "unknown"
    assert run_doc["panel_sig"] == camp_doc["panel_sig"] == "sig-1"
    assert run_doc["config_path"] == camp_doc["config_path"] == "configs/x.yaml"
    assert camp_doc["access_ids"] == ["CAMP-1"], "campaign 非空 access_ids 不得被清空"
    assert run_doc["access_ids"] == []
    assert camp_doc["keep"] == 1 and run_doc["run_keep"] == 2
    assert str(run_manifest) in result and str(campaign) in result
    assert _rows(db) == []


def test_flows_write_manifest_fresh_pair_identical(tmp_path, monkeypatch):
    flows = _load_flows(monkeypatch)
    monkeypatch.setattr(flows.lib, "git_commit", lambda: "cafe1234")
    db = _ledger(tmp_path)
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    _wire_ledger(monkeypatch, flows, db, health)
    out = tmp_path / "camp2" / "run"
    out.mkdir(parents=True)
    cfg = _minimal_cfg(tmp_path, out)
    ctx = flows._register_lockbox(cfg)
    flows._write_manifest(cfg, ctx)
    run_doc = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    camp_doc = json.loads((out.parent / "manifest.json").read_text(encoding="utf-8"))
    assert run_doc == camp_doc
    assert run_doc["access_ids"] == [] and run_doc["sample_role"] == "unknown"


def test_flows_register_lockbox_final_and_backfill_result_ref(tmp_path, monkeypatch):
    flows = _load_flows(monkeypatch)
    monkeypatch.setattr(flows.lib, "git_commit", lambda: "feedface")
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q2", dt.date(2025, 7, 1),
                                         dt.date(2026, 7, 3)))
    _wire_ledger(monkeypatch, flows, db, health)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    out = tmp_path / "camp" / "run"
    out.mkdir(parents=True)
    campaign = tmp_path / "camp" / "manifest.json"
    campaign.write_text(json.dumps({"campaign": "camp", "access_ids": ["OLD-1"]}),
                        encoding="utf-8")
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(
        {"panel": str(panel), "out": str(out), "data": {"ensure": False},
         "groups": {"g": "*"}, "models": ["M0a"]}), encoding="utf-8")
    ledger_at_compute = []
    monkeypatch.setattr(flows, "_run",
                        lambda step: ledger_at_compute.append(_rows(db)))
    monkeypatch.setattr(flows, "_resolve_groups", lambda cfg: {"g": [0]})

    report = flows.xscore_pipeline(str(cfg_path))  # 真跑 flow 主体（计算 step 打桩）

    assert report == str(out / "REPORT.md")
    assert ledger_at_compute and len(ledger_at_compute[0]) == 1, \
        "flow 开始即登记 final（计算 step 执行时台账已可见；不登记必红）"
    rows = _rows(db)
    assert len(rows) == 1 and rows[0]["kind"] == "final"
    assert rows[0]["access_id"] == ledger_at_compute[0][0]["access_id"], "收尾幂等复用"
    assert rows[0]["window_id"] == "2026Q2"
    assert rows[0]["reason"] == f"pipeline:{cfg_path}"
    assert rows[0]["result_ref"] == str(out), "result_ref 回填为 run out 目录"
    run_doc = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    camp_doc = json.loads(campaign.read_text(encoding="utf-8"))
    assert run_doc["platform_commit"] == camp_doc["platform_commit"] == "feedface"
    assert run_doc["sample_role"] == camp_doc["sample_role"] == "lockbox"
    assert run_doc["window_id"] == camp_doc["window_id"] == "2026Q2"
    assert run_doc["panel_sig"] == lib.file_content_sha256(panel)
    assert run_doc["access_ids"] == [rows[0]["access_id"]]
    assert camp_doc["access_ids"] == ["OLD-1", rows[0]["access_id"]]
    assert camp_doc["campaign"] == "camp"


def test_flows_replay_published_legacy_stat_output_after_touch_without_rescoring(
    tmp_path, monkeypatch
):
    flows = _load_flows(monkeypatch)
    monkeypatch.setattr(flows.lib, "git_commit", lambda: "feedface")
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    window = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 7, 3))
    db = _ledger(tmp_path, window)
    _wire_ledger(monkeypatch, flows, db, health)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    out = tmp_path / "camp" / "run"
    out.mkdir(parents=True)
    (out / "REPORT.md").write_text("# existing report\n", encoding="utf-8")
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(
        {"panel": str(panel), "out": str(out), "data": {"ensure": False},
         "groups": {"g": "*"}, "models": ["M0a"]}), encoding="utf-8")
    legacy_panel_sig = flows.lib.file_sig(panel)
    access_id = _register_legacy_stat_access(
        db, panel, cfg_path, window, panel_sig=legacy_panel_sig, result_ref=str(out)
    )
    (out / "manifest.json").write_text(
        json.dumps({
            "access_ids": [access_id],
            "panel_sig": legacy_panel_sig,
            "window_id": window.window_id,
            "sample_role": "lockbox",
            "config_path": str(cfg_path),
        }),
        encoding="utf-8",
    )
    os.utime(panel, (1_700_000_000, 1_700_000_000))
    ran = []
    monkeypatch.setattr(flows, "_run", lambda step: ran.append(step))
    monkeypatch.setattr(flows, "_resolve_groups", lambda cfg: {"g": [0]})

    report = flows.xscore_pipeline(str(cfg_path))

    assert report == str(out / "REPORT.md")
    assert ran == [], "完整旧 report replay 必须复用产物，不能按触碰后的 panel 重算"
    assert len(_rows(db)) == 1
    assert _rows(db)[0]["access_id"] == access_id


def test_flows_reject_published_access_id_with_wrong_result_ref_before_compute(
    tmp_path, monkeypatch
):
    flows = _load_flows(monkeypatch)
    monkeypatch.setattr(flows.lib, "git_commit", lambda: "feedface")
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    window = LockboxWindow("2026Q2", dt.date(2025, 7, 1), dt.date(2026, 7, 3))
    db = _ledger(tmp_path, window)
    _wire_ledger(monkeypatch, flows, db, health)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    out = tmp_path / "camp" / "run"
    out.mkdir(parents=True)
    (out / "REPORT.md").write_text("# old report\n", encoding="utf-8")
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(
        {"panel": str(panel), "out": str(out), "data": {"ensure": False},
         "groups": {"g": "*"}, "models": ["M0a"]}), encoding="utf-8")
    legacy_panel_sig = flows.lib.file_sig(panel)
    access_id = _register_legacy_stat_access(
        db, panel, cfg_path, window, panel_sig=legacy_panel_sig,
        result_ref=str(tmp_path / "camp" / "another-run"),
    )
    (out / "manifest.json").write_text(
        json.dumps({
            "access_ids": [access_id],
            "panel_sig": legacy_panel_sig,
            "window_id": window.window_id,
            "sample_role": "lockbox",
            "config_path": str(cfg_path),
        }),
        encoding="utf-8",
    )
    ran = []
    monkeypatch.setattr(flows, "_run", lambda step: ran.append(step))
    monkeypatch.setattr(flows, "_resolve_groups", lambda cfg: {"g": [0]})

    with pytest.raises(LockboxError) as exc:
        flows.xscore_pipeline(str(cfg_path))

    assert exc.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert ran == [], "错误 result_ref 不得降级到重算路径"
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["access_id"] == access_id
    assert rows[0]["result_ref"] == str(tmp_path / "camp" / "another-run")


def test_flows_pins_candidate_across_panel_rewrite(tmp_path, monkeypatch):
    """首尾之间 panel 被重写（内容+mtime 变化）→ 仍单登记、campaign 单 id。

    收尾必须复用起点 fp/access_id；按新 panel 身份重算会双登记/双耗配额（回归红）。
    """
    flows = _load_flows(monkeypatch)
    monkeypatch.setattr(flows.lib, "git_commit", lambda: "feedface")
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q2", dt.date(2025, 7, 1),
                                         dt.date(2026, 7, 3)))
    _wire_ledger(monkeypatch, flows, db, health)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    out = tmp_path / "camp" / "run"
    out.mkdir(parents=True)
    campaign = tmp_path / "camp" / "manifest.json"
    campaign.write_text(json.dumps({"campaign": "camp", "access_ids": ["OLD-1"]}),
                        encoding="utf-8")
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(
        {"panel": str(panel), "out": str(out), "data": {"ensure": False},
         "groups": {"g": "*"}, "models": ["M0a"]}), encoding="utf-8")

    def fake_run(step):
        panel.write_text("rewritten", encoding="utf-8")
        os.utime(panel, (1_600_000_000, 1_600_000_000))

    monkeypatch.setattr(flows, "_run", fake_run)
    monkeypatch.setattr(flows, "_resolve_groups", lambda cfg: {"g": [0]})

    flows.xscore_pipeline(str(cfg_path))

    rows = _rows(db)
    assert len(rows) == 1, "首尾必须复用起点候选（不得按重写后的 panel 重复登记）"
    assert rows[0]["result_ref"] == str(out)
    camp_doc = json.loads(campaign.read_text(encoding="utf-8"))
    assert camp_doc["access_ids"] == ["OLD-1", rows[0]["access_id"]]
    assert camp_doc["access_ids"].count(rows[0]["access_id"]) == 1


def test_flows_replay_duplicate_and_re_final(tmp_path, monkeypatch, capsys):
    """流水线接线（R42）：首跑登记 1 行；同 config 二跑（产物在）→ replay 零新增 + 日志复用；
    产物被删后二跑 → DUPLICATE；`FACTORLAB_RE_FINAL=1` → 新增行（reason 带 |re-final）。"""
    flows = _load_flows(monkeypatch)
    monkeypatch.setattr(flows.lib, "git_commit", lambda: "feedface")
    health = _health(tmp_path, ["2025-06-30", "2025-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q2", dt.date(2025, 7, 1),
                                         dt.date(2026, 7, 3)))
    _wire_ledger(monkeypatch, flows, db, health)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    out = tmp_path / "camp" / "run"
    out.mkdir(parents=True)
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(
        {"panel": str(panel), "out": str(out), "data": {"ensure": False},
         "groups": {"g": "*"}, "models": ["M0a"]}), encoding="utf-8")
    monkeypatch.setattr(flows, "_run", lambda step: None)
    monkeypatch.setattr(flows, "_resolve_groups", lambda cfg: {"g": [0]})

    flows.xscore_pipeline(str(cfg_path))
    assert len(_rows(db)) == 1, "首跑登记 final 1 行"

    capsys.readouterr()
    flows.xscore_pipeline(str(cfg_path))
    assert len(_rows(db)) == 1, "产物在：同 config 二跑 replay 零新增"
    assert "复用" in capsys.readouterr().out, "replay 必须留日志（可观察）"

    (out / "manifest.json").unlink()
    with pytest.raises(LockboxError) as ei:
        flows.xscore_pipeline(str(cfg_path))
    assert ei.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert len(_rows(db)) == 1, "产物被删的重复版本不得静默复用"

    monkeypatch.setenv("FACTORLAB_RE_FINAL", "1")
    flows.xscore_pipeline(str(cfg_path))
    rows = _rows(db)
    assert len(rows) == 2, "RE_FINAL 操作员重测：新增登记行"
    assert rows[-1]["reason"].endswith("|re-final")


def test_flows_stale_state_fails_before_compute(tmp_path, monkeypatch):
    """跨季未 roll：flow 在 data_prep/计算前抛 LOCKBOX_WINDOW_STALE，零登记零 manifest。"""
    flows = _load_flows(monkeypatch)
    health = _health(tmp_path, ["2024-06-28", "2024-07-01", "2026-07-03"])
    db = _ledger(tmp_path, LockboxWindow("2026Q1", dt.date(2024, 7, 1),
                                         dt.date(2025, 6, 30)))
    _wire_ledger(monkeypatch, flows, db, health)
    panel = _panel(tmp_path / "panel.npz", ["2025-08-01", "2026-01-01"])
    out = tmp_path / "camp" / "run"
    out.mkdir(parents=True)
    campaign = tmp_path / "camp" / "manifest.json"
    original = {"campaign": "camp", "access_ids": ["OLD-1"], "keep": 1}
    campaign.write_text(json.dumps(original), encoding="utf-8")
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(
        {"panel": str(panel), "out": str(out), "data": {"ensure": False},
         "groups": {"g": "*"}, "models": ["M0a"]}), encoding="utf-8")
    ran = []
    monkeypatch.setattr(flows, "_run", lambda step: ran.append(step))
    monkeypatch.setattr(flows, "_resolve_groups", lambda cfg: {"g": [0]})

    with pytest.raises(LockboxError) as ei:
        flows.xscore_pipeline(str(cfg_path))

    assert ei.value.code == "LOCKBOX_WINDOW_STALE"
    assert "factorlab lockbox roll" in str(ei.value)
    assert ran == [], "stale 必须在任何计算 step 前 fail-fast"
    assert _rows(db) == []
    assert not (out / "manifest.json").exists()
    assert json.loads(campaign.read_text(encoding="utf-8")) == original
