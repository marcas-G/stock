"""xscore 流水线 manifest 样本字段单测（锁箱纪律 T9）。

行为要求（T9 裁定）：
- `panel_dates`：从 panel npz 的 `dates` 取首/末日期；文件缺失/无 dates/空/非法 → None；
- `write_manifest`：读-合并-原子写；已有字段保留、只更新所列键；无文件则新建；
  真正落盘（重新读文件可见），不留临时文件；
- `lockbox_sample`：真实读 SQLite 台账（store.connect/roll，不 mock）——
  无 state → `window_id=None`、`sample_role="unknown"`；
  有 state → 按 panel 区间与窗口起点判 is/mixed/lockbox，window_id 取台账 state；
  panel 区间不可得 → 回退已发布日历 min/max；
  state 窗口陈旧（跨季未 roll）→ 用 state 窗口声明而非报错；
- 禁止行为：结果不得是硬编码——roll 前后、不同面板区间输出必须变化。

突变必杀：`lockbox_sample` 换成返回常量、`write_manifest` 不落盘 → 对应测试失败。
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import numpy as np

PIPELINE = pathlib.Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE))
import xlib as lib  # noqa: E402

from factorlab.adapters import lockbox_store as store  # noqa: E402
from factorlab.core.lockbox import LockboxWindow  # noqa: E402


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


# ── flows 双写接线（fake prefect shim；T10 修复轮1）────────────────────
# flows.py 顶层 import prefect（平台 venv 无此依赖）——测试注入最小替身，
# 真执行 `flows._write_manifest`，锁死「run + campaign 双写、既有键保留、
# access_ids 非空不清空」的接线（改回单写或清空 access_ids 此测试必红）。

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


def test_flows_write_manifest_dual_writes_and_preserves_campaign_ids(tmp_path, monkeypatch):
    flows = _load_flows(monkeypatch)
    monkeypatch.setattr(flows.lib, "git_commit", lambda: "deadbeef")
    monkeypatch.setattr(flows.lib, "lockbox_sample",
                        lambda **kwargs: {"window_id": None, "sample_role": "unknown"})
    out = tmp_path / "camp" / "run"
    out.mkdir(parents=True)
    campaign = tmp_path / "camp" / "manifest.json"
    campaign.write_text(json.dumps({"campaign": "camp", "access_ids": ["CAMP-1"],
                                    "keep": 1}), encoding="utf-8")
    run_manifest = out / "manifest.json"
    run_manifest.write_text(json.dumps({"access_ids": [], "run_keep": 2}), encoding="utf-8")

    result = flows._write_manifest(_minimal_cfg(tmp_path, out))

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


def test_flows_write_manifest_fresh_pair_identical(tmp_path, monkeypatch):
    flows = _load_flows(monkeypatch)
    monkeypatch.setattr(flows.lib, "git_commit", lambda: "cafe1234")
    monkeypatch.setattr(flows.lib, "lockbox_sample",
                        lambda **kwargs: {"window_id": "2026Q2", "sample_role": "is"})
    out = tmp_path / "camp2" / "run"
    out.mkdir(parents=True)
    flows._write_manifest(_minimal_cfg(tmp_path, out))
    run_doc = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    camp_doc = json.loads((out.parent / "manifest.json").read_text(encoding="utf-8"))
    assert run_doc == camp_doc
    assert run_doc["access_ids"] == [] and run_doc["sample_role"] == "is"
