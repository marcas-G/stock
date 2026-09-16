import json
from pathlib import Path
from pan_update import state

def test_load_state_corrupt_quarantined(tmp_path):
    p = tmp_path / "pan_state.json"
    p.write_text("{not json", encoding="utf-8")
    s = state.load_state(p)
    assert s["version"] == 1 and s["files"] == {}
    assert list(tmp_path.glob("pan_state.json.corrupt-*"))

def test_save_state_atomic_roundtrip(tmp_path):
    p = tmp_path / "pan_state.json"
    st = {"version": 1, "files": {"a": {"size": 1}}, "stages": {}, "runs": []}
    state.save_state_atomic(p, st)
    assert json.loads(p.read_text())["files"]["a"]["size"] == 1
    assert not list(tmp_path.glob("*.tmp"))

def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "pan_state.json"
    st = {
        "version": 1,
        "files": {"daily/x.zip": {"name": "x.zip", "size": 10, "synced_at": "2026-09-16T00:00:00"}},
        "stages": {"daily": {"last_build": "2026-09-15"}},
        "runs": [{"cmd": "sync", "status": "ok"}],
    }
    state.save_state_atomic(p, st)
    back = state.load_state(p)
    assert back["files"] == st["files"]
    assert back["stages"] == st["stages"]
    assert back["runs"] == st["runs"]
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob("*.corrupt-*"))

def test_diff_new_changed_skipped():
    st = {"version": 1, "files": {"daily/x.zip": {"size": 10}}, "stages": {}, "runs": []}
    entries = [
        {"name": "x.zip", "size": 10, "rel_path": "daily/x.zip"},   # skipped
        {"name": "x.zip", "size": 11, "rel_path": "daily/x.zip"},   # changed
        {"name": "y.zip", "size": 5, "rel_path": "daily/y.zip"},    # new
    ]
    d = state.diff_files(st, "daily", entries)
    assert [e["rel_path"] for e in d.to_fetch] == ["daily/y.zip", "daily/x.zip"]
    assert [e["rel_path"] for e in d.changed] == ["daily/x.zip"]
    assert [e["rel_path"] for e in d.skipped] == ["daily/x.zip"]
