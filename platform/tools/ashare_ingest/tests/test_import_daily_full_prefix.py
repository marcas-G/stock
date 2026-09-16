"""网盘命名全量快照识别（`19910101至` 前缀 + 旧 `07月31日` 标记兼容）。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-5-brief.md
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §4
（全量识别从“含 07月31日 字样”改为 `19910101至` 前缀，旧标记保留兼容）

T1（平台 venv）：缺 factorlab/openpyxl 时 skip 而非假通过。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("factorlab", reason="ashare_ingest 属 T1（平台 venv）")
pytest.importorskip("openpyxl", reason="import_daily 需 openpyxl")

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import import_daily as idl  # noqa: E402


def test_full_snapshot_prefix_detected():
    class P:
        def __init__(self, n): self.name = n
    fulls = [P("19910101至20260831A股日k线.zip"), P("19910101至上月底07月31日A股日k线.zip")]
    incr = [P("2026-09-01至2026-09-16A股日k线.zip")]
    assert all(idl._is_full_snapshot(p) for p in fulls)
    assert not any(idl._is_full_snapshot(p) for p in incr)

def test_newest_full_selected():
    class P:
        def __init__(self, n): self.name = n
    ps = [P("19910101至20260731A股日k线.zip"), P("19910101至20260831A股日k线.zip")]
    assert idl._newest_full(ps).name == "19910101至20260831A股日k线.zip"


# —— 增补守卫（红→绿，经 mutation 证明判别力）：旧标记兼容 / _build_tasks 选最新 ——

def test_legacy_mark_recognized_without_prefix():
    """无 `19910101至` 前缀但含旧标记 `07月31日` 的本地快照仍算全量（兼容）。"""
    class P:
        def __init__(self, n): self.name = n
    assert idl._is_full_snapshot(P("本地快照07月31日A股日k线.zip"))


def test_build_tasks_prefers_newest_full_snapshot(tmp_path):
    """两份全量共存时 `_build_tasks` 取结束日最大的一份。"""
    names = ("19910101至20260731A股日k线.zip", "19910101至20260831A股日k线.zip")
    for name in names:
        with zipfile.ZipFile(tmp_path / name, "w") as z:
            z.writestr("A股日k线/000001.xlsx", b"stub")
    tasks = idl._build_tasks(tmp_path)
    full_files = {t[2] for t in tasks if t[0] == 0}
    assert full_files == {str(tmp_path / names[1])}
    assert not any(t[0] == 1 for t in tasks), "两份都是全量，不得误入增量"


def test_build_tasks_includes_incremental_zip(tmp_path):
    """全量之外的非前缀 zip 作为增量进入任务（priority=1），不得被忽略。"""
    full = "19910101至20260731A股日k线.zip"
    incr = "2026-08-01至2026-08-31A股日k线.zip"
    for name in (full, incr):
        with zipfile.ZipFile(tmp_path / name, "w") as z:
            z.writestr("A股日k线/000001.xlsx", b"stub")
    tasks = idl._build_tasks(tmp_path)
    got = sorted({(t[0], Path(t[2]).name) for t in tasks})
    assert got == [(0, full), (1, incr)]


# —— 修复轮 1（评审 Important I2 + Minor）：多增量覆盖筛选 + 前缀语义 ——

def _zip_with(path, member="A股日k线/000001.xlsx", payload=b"stub"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(member, payload)


def _incr_names(tasks):
    return sorted({Path(t[2]).name for t in tasks if t[0] == 1})


def test_increments_keep_only_end_after_full_and_drop_contained(tmp_path):
    """①全量 end=20260731 + 9/1-9/15 + 9/1-9/16 → 仅保留覆盖更全的 9/1-9/16。"""
    _zip_with(tmp_path / "19910101至20260731A股日k线.zip")
    _zip_with(tmp_path / "2026-09-01至2026-09-15A股日k线.zip")
    _zip_with(tmp_path / "2026-09-01至2026-09-16A股日k线.zip")
    tasks = idl._build_tasks(tmp_path)
    assert _incr_names(tasks) == ["2026-09-01至2026-09-16A股日k线.zip"]


def test_increments_non_overlapping_all_kept(tmp_path):
    """②互不重叠的两段增量都保留（不因只取首个而静默漏数据）。"""
    _zip_with(tmp_path / "19910101至20260731A股日k线.zip")
    _zip_with(tmp_path / "2026-09-01至2026-09-15A股日k线.zip")
    _zip_with(tmp_path / "2026-09-16至2026-09-20A股日k线.zip")
    tasks = idl._build_tasks(tmp_path)
    assert _incr_names(tasks) == ["2026-09-01至2026-09-15A股日k线.zip",
                                  "2026-09-16至2026-09-20A股日k线.zip"]


def test_increment_not_after_full_end_dropped(tmp_path):
    """③增量整体不超出全量 end（end <= full_end）→ 丢弃，不重复解析。"""
    _zip_with(tmp_path / "19910101至20260731A股日k线.zip")
    _zip_with(tmp_path / "2026-07-01至2026-07-31A股日k线.zip")
    tasks = idl._build_tasks(tmp_path)
    assert _incr_names(tasks) == []
    assert {t[0] for t in tasks} == {0}


def test_no_full_zip_still_fails_loud(tmp_path):
    """④无全量快照（仅增量）→ 维持既有 SystemExit（不静默只跑增量）。"""
    _zip_with(tmp_path / "2026-09-01至2026-09-16A股日k线.zip")
    with pytest.raises(SystemExit, match="no full daily kline zip"):
        idl._build_tasks(tmp_path)


def test_old_named_full_without_date_keeps_increments(tmp_path):
    """旧命名全量无结束日可比 → 可解析增量保守保留（不静默丢）。"""
    _zip_with(tmp_path / "本地快照07月31日A股日k线.zip")
    _zip_with(tmp_path / "2026-09-01至2026-09-15A股日k线.zip")
    tasks = idl._build_tasks(tmp_path)
    assert _incr_names(tasks) == ["2026-09-01至2026-09-15A股日k线.zip"]


def test_prefix_requires_start_of_name():
    """Minor：`19910101至` 是前缀（startswith）而非任意子串。"""
    class P:
        def __init__(self, n): self.name = n
    assert not idl._is_full_snapshot(P("A股日k线-19910101至20260731.zip"))
