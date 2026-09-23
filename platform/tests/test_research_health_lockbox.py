"""`flab health` 锁箱段（初始化/陈旧/损坏降级）——health 是探活入口。

断言来源：design specs/2026-09-24-final-test-once-discipline-design.md §5（锁箱段
简化：无配额/探索计数）与 R42 T5 裁定：
- 未初始化 → `{initialized: false}` + warning（碰箱会被拒）；
- roll 后段字段齐全且与 `lockbox_store.status` 一致——六字段
  `initialized/window_id/window_start/window_end/is_end/finals_total`；
  配额/探索旧字段（quota_final/final_used/exploration_used/final_remaining）不得残留；
- state.window_id != 当季 `compute_window` → warning 含 roll 指引；
- DB 打不开/损坏 → 该段 `{initialized:false, degraded:<原因>}` + warning，
  **不得**让 health 整体 DATA 失败（探活入口）。
"""

from __future__ import annotations

import datetime as dt
from contextlib import closing

from factorlab.adapters import lockbox_store as store
from factorlab.config import settings
from factorlab.core.lockbox import LockboxWindow, compute_window
from factorlab.research import health as H, registry

from test_research_data import _basic_seed
from test_research_health import _args, _point_open_read, _seed


def _days() -> list[dt.date]:
    """锁箱日历（逐日，覆盖任意季界；与真实 health 目录解耦，离线可测）。"""
    out: list[dt.date] = []
    day = dt.date(2020, 1, 1)
    while day <= dt.date(2027, 12, 31):
        out.append(day)
        day += dt.timedelta(days=1)
    return out


def _setup(env, tmp_path, monkeypatch) -> None:
    _seed(env)
    _point_open_read(monkeypatch, env)
    monkeypatch.setattr(settings, "lockbox_db", tmp_path / "ledger.sqlite")
    days = _days()
    monkeypatch.setattr(store, "published_days", lambda root: list(days))
    monkeypatch.setattr(store, "latest_data_date", lambda root: days[-1])


def test_lockbox_uninitialized_warns_roll(env, tmp_path, monkeypatch):
    _setup(env, tmp_path, monkeypatch)

    e = H.health(_args())

    assert e.ok, e.error
    assert e.data["lockbox"] == {"initialized": False}
    assert any("未初始化" in w and "factorlab lockbox roll" in w
               and "flab lockbox" not in w for w in e.warnings), e.warnings


def test_lockbox_section_after_roll_matches_status(env, tmp_path, monkeypatch):
    _setup(env, tmp_path, monkeypatch)
    days = _days()
    window = compute_window(as_of=dt.date.today(), trading_days=days,
                            data_end=days[-1])
    with closing(store.connect(settings.lockbox_db)) as conn:
        store.roll(conn, window=window)
        store.register_access(conn, kind="final", fingerprint="fp-health-final-1",
                              artifact="factor/x.yaml",
                              params={"final_test": True},
                              command="factor run", reason="最终测试", window=window,
                              tool="factorlab test")
        expected = store.status(conn, trading_days=days, data_end=days[-1])

    e = H.health(_args())

    assert e.ok, e.error
    section = e.data["lockbox"]
    assert section["initialized"] is True
    assert section["window_id"] == window.window_id
    assert expected["finals_total"] == 1
    # R42：终评实计数进状态段（存根硬编码 0/20 必败）
    assert section["finals_total"] == 1
    assert set(section) == {"initialized", "window_id", "window_start",
                            "window_end", "is_end", "finals_total"}, section
    assert not (set(section) & {"quota_final", "final_used", "exploration_used",
                                "final_remaining"}), "配额/探索旧字段不得残留"
    for key in ("window_start", "window_end", "is_end", "finals_total"):
        assert section[key] == expected[key], key
    assert not any("陈旧" in w for w in e.warnings), e.warnings


def test_lockbox_stale_window_warns_roll(env, tmp_path, monkeypatch):
    _setup(env, tmp_path, monkeypatch)
    stale = LockboxWindow("2000Q1", dt.date(1999, 12, 31),
                          dt.date(1999, 12, 31))
    with closing(store.connect(settings.lockbox_db)) as conn:
        store.roll(conn, window=stale)

    e = H.health(_args())

    assert e.ok, e.error
    assert e.data["lockbox"]["window_id"] == "2000Q1"
    assert any("锁箱窗口陈旧" in w and "factorlab lockbox roll" in w
               and "flab lockbox" not in w for w in e.warnings), e.warnings


def test_lockbox_corrupt_db_degrades_without_failing_health(
        env, tmp_path, monkeypatch):
    _setup(env, tmp_path, monkeypatch)
    settings.lockbox_db.write_bytes(b"\x00 not a sqlite database " * 64)

    e = H.health(_args())

    assert e.ok is True, "锁箱库损坏不得拖垮 health 探活"
    section = e.data["lockbox"]
    assert section["initialized"] is False
    assert section.get("degraded"), section
    assert any("锁箱" in w and "factorlab lockbox roll" in w
               and "flab lockbox" not in w for w in e.warnings), e.warnings
    assert e.data["connectivity"]["ok"] is True  # 降级只影响 lockbox 段


def test_health_registry_schema_declares_lockbox():
    doc = registry.COMMANDS["health"].to_doc()
    props = doc["output_schema"]["properties"]
    assert "lockbox" in props, "health output_schema 必须声明 lockbox 段"
    lockbox_props = props["lockbox"]["properties"]
    for key in ("finals_total", "is_end"):
        assert key in lockbox_props, f"锁箱段 schema 必须声明 {key}"
    assert not (set(lockbox_props) & {"quota_final", "final_used",
                                      "exploration_used", "final_remaining"}), \
        "配额/探索旧字段不得留在 schema"
    assert "锁箱" in doc["description"]
