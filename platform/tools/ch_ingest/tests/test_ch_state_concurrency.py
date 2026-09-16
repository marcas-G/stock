"""R21 TOOLS-I2：断点并发安全。

finding：checkpoint 由 worker 进程 read-modify-write 无合并/锁，8 worker 存活 1。
修复语义（测试锁死）：
- 并发 mark_done 后所有完成任务都在（锁 + 新鲜读改写）；
- mark_done 必须重读磁盘再合并（外部进程写入不被本地缓存覆盖）。
"""
from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("clickhouse_connect", reason="ch_ingest 属 T1（平台 venv）")

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import ch_state  # noqa: E402


def _mark_worker(args):
    """spawn worker：所有子进程对齐同一 wall-clock 起点后并发 mark_done。"""
    state_path, table, year, month, start_at = args
    import time as _time

    import ch_state as cs
    cs.state_dir = lambda: state_path       # spawn 子进程内独立绑定
    cs._PROGRESS = None
    while _time.time() < start_at:
        _time.sleep(0.005)
    cs.mark_done((table, year, month))
    return 0


def test_concurrent_mark_done_keeps_all(tmp_path):
    state = str(tmp_path / "state.json")
    tasks = [("bars_1m", "2026", f"{i:02d}") for i in range(8)]
    ctx = mp.get_context("spawn")
    start_at = time.time() + 3.0
    with ctx.Pool(8) as pool:
        pool.map(_mark_worker, [(state, *t, start_at) for t in tasks])
    data = json.loads(Path(state).read_text(encoding="utf-8"))
    assert set(data) == {f"bars_1m_2026{i:02d}" for i in range(8)}, \
        f"并发 mark_done 丢标记：{sorted(data)}"


def test_mark_done_rereads_state_file(tmp_path, monkeypatch):
    """本地缓存（主进程已加载）不得覆盖外部进程新写入的 key。"""
    state = tmp_path / "state.json"
    monkeypatch.setattr(ch_state, "state_dir", lambda: str(state))
    monkeypatch.setattr(ch_state, "_PROGRESS", None)
    ch_state.mark_done(("t", "1", "01"))
    external = json.loads(state.read_text(encoding="utf-8"))
    external["t_102"] = True     # 外部进程新增 key（不模拟破坏性整写）
    state.write_text(json.dumps(external), encoding="utf-8")
    ch_state.mark_done(("t", "1", "03"))
    assert json.loads(state.read_text(encoding="utf-8")) == {
        "t_101": True, "t_102": True, "t_103": True}
