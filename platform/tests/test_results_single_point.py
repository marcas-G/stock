"""results/ 产物单点（R12）：布局路径、读、**原子**写、panel 探针都在 adapters。

门（`test_architecture.py::test_results_io_only_in_adapters`）保证 app/surfaces 不再绕过；
这里测单点本身的行为，尤其是 publish 的原子性（原先直写，崩在中途会留半截 summary.json）。
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from factorlab.adapters import results_fs
from factorlab.adapters.panel_store import ParquetPanelStore


def _weekly() -> pl.DataFrame:
    return pl.DataFrame({"date": ["2024-01-05"], "code": ["000001.SZ"],
                         "signal": [0.1], "forward_return_5d": [0.02]})


def test_layout_paths_are_single_point(tmp_path):
    """布局文件名只在 results_fs 出现（调用方拼路径必须经这三个函数）。"""
    assert results_fs.panel_path(tmp_path, "f1") == tmp_path / "f1" / "panel.parquet"
    assert results_fs.weekly_path(tmp_path, "f1") == tmp_path / "f1" / "weekly.parquet"
    assert results_fs.summary_path(tmp_path, "f1") == tmp_path / "f1" / "summary.json"


def test_read_weekly_roundtrip_and_missing(tmp_path):
    out = tmp_path / "f1"
    out.mkdir()
    _weekly().write_parquet(results_fs.weekly_path(tmp_path, "f1"))
    got = results_fs.read_weekly(tmp_path, "f1")
    assert got.columns == _weekly().columns and got.height == 1
    with pytest.raises(FileNotFoundError) as exc:
        results_fs.read_weekly(tmp_path, "nope")
    assert "weekly.parquet" in str(exc.value)


def test_write_run_outputs_is_atomic(tmp_path, monkeypatch):
    """发布单点：两个文件一起写；**失败不留目标文件、不留 tmp**（原先直写无此保证）。"""
    out = tmp_path / "f1"
    summary = {"name": "f1", "evaluation": {"n_weeks": 3}}
    results_fs.write_run_outputs(out, weekly=_weekly(), summary=summary)
    assert pl.read_parquet(out / "weekly.parquet").height == 1
    assert json.loads((out / "summary.json").read_text(encoding="utf-8")) == summary
    assert [p.name for p in out.iterdir() if ".tmp" in p.name] == []

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", boom)
    out2 = tmp_path / "f2"
    with pytest.raises(OSError):
        results_fs.write_run_outputs(out2, weekly=_weekly(), summary=summary)
    assert not (out2 / "weekly.parquet").exists(), "失败不得留下目标文件"
    assert [p.name for p in out2.iterdir() if ".tmp" in p.name] == [], "不得留 tmp 残渣"


def test_panel_store_load_dates_is_date_only(tmp_path):
    """correlation 抽样周只需要 date 列——不得为此把整张 panel 读进来。"""
    d = tmp_path / "f1"
    d.mkdir()
    pl.DataFrame({"date": ["2024-01-05", "2024-01-12"], "code": ["A", "A"],
                  "signal": [1.0, 2.0]}).write_parquet(d / "panel.parquet")
    store = ParquetPanelStore()
    got = store.load_dates(tmp_path, "f1")
    assert got.columns == ["date"] and got.height == 2


def test_panel_store_load_dates_missing_matches_panel_missing(tmp_path):
    from factorlab.ports.panel_store import panel_missing
    store = ParquetPanelStore()
    with pytest.raises(type(panel_missing(tmp_path, "nope"))):
        store.load_dates(tmp_path, "nope")
