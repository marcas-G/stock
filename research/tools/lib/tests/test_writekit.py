"""写侧单点（lib/writekit.py）：标记 / 锁 / state / 原子落盘。

全部 hermetic（tmp 目录），覆盖正常 + 边界 + 错误路径；并断言"不该发生的事"：
半截文件不落地、锁被占时不静默通过、state 写入是原子的。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest

from lib import writekit as W


# ── 完成标记 ────────────────────────────────────────────────────
def test_success_marker_lifecycle(tmp_path):
    assert not W.has_success(tmp_path)
    p = W.mark_success(tmp_path)
    assert p.name == "_SUCCESS" and W.has_success(tmp_path)
    assert p.read_bytes() == b""          # 空文件（与历史约定一致）


def test_success_marker_creates_dir(tmp_path):
    d = tmp_path / "a" / "b"
    W.mark_success(d)
    assert (d / "_SUCCESS").is_file()


# ── state（JSON 单形态，原子写）──────────────────────────────────
def test_state_roundtrip_and_atomicity(tmp_path):
    assert W.load_state(tmp_path) == {}
    W.save_state(tmp_path, {"month": {"20260610": True}})
    assert W.load_state(tmp_path) == {"month": {"20260610": True}}
    # 原子：不留 tmp 残渣
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftovers == []
    # 覆盖写
    W.save_state(tmp_path, {"x": 1})
    assert W.load_state(tmp_path) == {"x": 1}


def test_state_handles_empty_file(tmp_path):
    (tmp_path / "state.json").write_text("", encoding="utf-8")
    assert W.load_state(tmp_path) == {}   # 崩溃留下的空文件不炸


# ── 锁 ─────────────────────────────────────────────────────────
def test_lock_released_after_context(tmp_path):
    lock = tmp_path / ".lock"
    with W.FileLock(lock):
        assert lock.exists()
    with W.FileLock(lock):   # 释放后可再次获取
        pass


def test_lock_busy_is_not_silent(tmp_path):
    """占用中再取锁 → LockBusy（不静默进入并发写）。用子进程持有锁。"""
    lock = tmp_path / ".lock"
    code = (
        "import sys, time; sys.path.insert(0, %r);"
        "from lib.writekit import FileLock;"
        "l = FileLock(%r); l.__enter__(); print('HELD', flush=True); time.sleep(3)"
        % (str(Path(__file__).resolve().parents[2]), str(lock))
    )
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "HELD"
        with pytest.raises(W.LockBusy):
            with W.FileLock(lock):
                pass
    finally:
        proc.kill()
        proc.wait()


# ── 原子落盘 ────────────────────────────────────────────────────
def test_atomic_write_df_and_no_partial_on_failure(tmp_path):
    df = pl.DataFrame({"a": [1, 2, 3]})
    p = W.atomic_write_df(df, tmp_path / "sub" / "x.parquet")
    assert p.is_file() and pl.read_parquet(p).height == 3
    assert [f.name for f in p.parent.iterdir() if ".tmp" in f.name] == []

    class Boom:
        def write_parquet(self, path):        # 模拟写盘中途失败
            Path(path).write_bytes(b"partial")
            raise OSError("disk full")

    target = tmp_path / "sub" / "y.parquet"
    with pytest.raises(OSError):
        W.atomic_write_df(Boom(), target)     # type: ignore[arg-type]
    assert not target.exists(), "失败时不得留下目标文件"
    assert [f.name for f in target.parent.iterdir() if ".tmp" in f.name] == [], "不得留 tmp 残渣"


def test_atomic_write_bytes(tmp_path):
    p = W.atomic_write_bytes(b'{"a": 1}', tmp_path / "m.json")
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1}
    assert os.path.getsize(p) == 8


# ── 旧 state 目录迁移 ─────────────────────────────────────────────
def test_migrate_legacy_done_dir(tmp_path):
    legacy = tmp_path / "state.json"
    legacy.mkdir()
    for k in ("bars_1m_202501", "tick_orders_202502"):
        (legacy / f"{k}.done").write_text("ok", encoding="utf-8")
    got = W.migrate_legacy_done_dir(legacy)
    assert got == {"bars_1m_202501": True, "tick_orders_202502": True}
    assert not legacy.exists()                                   # 原名让位给 JSON
    aside = tmp_path / "state.json.legacy-20260915"
    assert aside.is_dir() and len(list(aside.glob("*.done"))) == 2   # 留档
    # 迁移后可正常写 JSON
    W.save_state(tmp_path, got)
    assert W.load_state(tmp_path) == got


def test_migrate_is_idempotent_on_json(tmp_path):
    W.save_state(tmp_path, {"a": True})
    assert W.migrate_legacy_done_dir(tmp_path / "state.json") == {}
    assert W.load_state(tmp_path) == {"a": True}


def test_migrate_missing_path_is_noop(tmp_path):
    assert W.migrate_legacy_done_dir(tmp_path / "nope") == {}
