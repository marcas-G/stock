"""平台侧原子写单点（`adapters/atomicio.py`，R13）。

来历：同一条协议在平台里有**四份**实现（`parquet_artifacts` / `strategy_artifacts` /
`results_fs.write_run_outputs` / `batch_flock._write_state|_write_marker`），`execution_store`
还需要第五份。收成一份时顺带修掉一个**真实回归**：`tempfile.mkstemp` 创建的 tmp 是 0600，
`os.replace` 之后产物权限就变成 0600（同目录普通写出的文件是 0664）——跨用户/组读直接失败。
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import polars as pl
import pytest

from factorlab.adapters import atomicio


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_write_bytes_and_text_roundtrip(tmp_path):
    b = atomicio.atomic_write_bytes(tmp_path / "a.bin", b"\x00\x01")
    t = atomicio.atomic_write_text(tmp_path / "b.txt", "hello")
    assert b.read_bytes() == b"\x00\x01" and t.read_text(encoding="utf-8") == "hello"
    assert not [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]


def test_write_parquet_roundtrip(tmp_path):
    df = pl.DataFrame({"a": [1, 2, 3]})
    p = atomicio.atomic_write_parquet(df, tmp_path / "x.parquet")
    assert pl.read_parquet(p).height == 3


def test_permissions_match_plain_created_file(tmp_path):
    """产物权限必须与"普通创建"一致（回归：mkstemp 的 0600 会传给产物）。"""
    plain = tmp_path / "plain.txt"
    plain.write_text("x", encoding="utf-8")
    got = atomicio.atomic_write_bytes(tmp_path / "out.bin", b"x")
    assert _mode(got) == _mode(plain), f"{oct(_mode(got))} vs {oct(_mode(plain))}"


def test_failure_leaves_no_target_and_no_tmp(tmp_path):
    target = tmp_path / "deep" / "y.parquet"

    class Boom:
        def write_parquet(self, path):        # noqa: ANN001
            Path(path).write_bytes(b"partial")
            raise OSError("disk full")

    with pytest.raises(OSError):
        atomicio.atomic_write_parquet(Boom(), target)
    assert not target.exists(), "失败不得留下目标文件"
    assert [p.name for p in target.parent.iterdir() if ".tmp" in p.name] == []


def test_overwrite_existing_is_atomic(tmp_path):
    p = atomicio.atomic_write_text(tmp_path / "s.json", '{"v": 1}')
    atomicio.atomic_write_text(p, '{"v": 2}')
    assert p.read_text(encoding="utf-8") == '{"v": 2}'


def test_atomic_write_with_writer_callable(tmp_path):
    """兼容 M7-04A 的 `atomic_write(path, writer)` 形态（writer 收 tmp 路径）。"""
    seen: list[Path] = []

    def writer(tmp: Path) -> None:
        seen.append(tmp)
        tmp.write_bytes(b"payload")

    out = atomicio.atomic_write(tmp_path / "w.bin", writer)
    assert out.read_bytes() == b"payload"
    assert seen and seen[0] != out and ".tmp" in seen[0].name
