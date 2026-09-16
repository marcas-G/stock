"""网盘命名全量快照识别（`19910101至` 前缀 + 旧 `07月31日` 标记兼容）。

需求源：.superpowers/sdd/2026-09-16-pan-data-update-plan/task-5-brief.md
设计：knowledge/design/workspace/2026-09-16-pan-data-update-design.md §4
（全量识别从“含 07月31日 字样”改为 `19910101至` 前缀，旧标记保留兼容）

T1（平台 venv）：缺 factorlab/openpyxl 时 skip 而非假通过。
"""
from __future__ import annotations

import sys
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
