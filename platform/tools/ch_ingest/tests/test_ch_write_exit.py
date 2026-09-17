"""R21 TOOLS-I2/I3：run_pool 失败语义 + 主进程记账 + 退出码。

finding：失败分区仍 exit 0（run_pool 返回被忽略）；worker 进程写断点丢标记。
修复语义（测试锁死）：
- 成功单元由**主进程**记账（on_result），失败单元不写标记；
- run_pool 返回失败数，ingest_bars/ingest_tick 非空即 exit 1。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("clickhouse_connect", reason="ch_ingest 属 T1（平台 venv）")

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

from factorlab.ports.batch import BatchReport, Result  # noqa: E402

import ch_state  # noqa: E402
import ch_write  # noqa: E402
import ingest_bars  # noqa: E402
import ingest_tick  # noqa: E402


class _FakeFlock:
    """替身只兑现 BatchFlock 的对外语义（结果回调 + 失败记账）。"""

    def run(self, tasks, worker, **kw):
        rep = BatchReport()
        for t in tasks:
            table, year, month = t.key
            if month == "02":
                rep.failed += 1
                kw["on_result"](t, RuntimeError("boom"))
            else:
                kw["on_result"](t, (f"{table}_{year}{month}", 10))
                rep.done += 1
        return rep


def test_run_pool_marks_success_only_and_returns_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(ch_state, "state_dir", lambda: str(tmp_path / "state.json"))
    monkeypatch.setattr(ch_state, "_PROGRESS", None)
    monkeypatch.setattr(ch_write, "BatchFlock", _FakeFlock)
    tasks = [("bars_1m", "2026", "01"), ("bars_1m", "2026", "02"),
             ("bars_1m", "2026", "03")]
    failed = ch_write.run_pool("bars_1m", tasks)
    assert failed == 1
    assert ch_state.is_done(tasks[0]) is True
    assert ch_state.is_done(tasks[1]) is False, "失败单元不得写完成标记"
    assert ch_state.is_done(tasks[2]) is True


def test_run_pool_all_success_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(ch_state, "state_dir", lambda: str(tmp_path / "state.json"))
    monkeypatch.setattr(ch_state, "_PROGRESS", None)
    monkeypatch.setattr(ch_write, "BatchFlock", _FakeFlock)
    tasks = [("bars_1m", "2026", "01"), ("bars_1m", "2026", "03")]
    assert ch_write.run_pool("bars_1m", tasks) == 0


def test_ingest_bars_exits_nonzero_on_failure(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ingest_bars.py"])
    monkeypatch.setattr(ingest_bars, "discover_tasks",
                        lambda t: [("bars_1m", "2026", "01")])
    monkeypatch.setattr(ingest_bars, "run_pool", lambda t, tasks, **kw: 2)
    with pytest.raises(SystemExit) as e:
        ingest_bars.main()
    assert e.value.code == 1


def test_ingest_bars_exits_zero_on_success(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ingest_bars.py"])
    monkeypatch.setattr(ingest_bars, "discover_tasks",
                        lambda t: [("bars_1m", "2026", "01")])
    monkeypatch.setattr(ingest_bars, "run_pool", lambda t, tasks, **kw: 0)
    with pytest.raises(SystemExit) as e:
        ingest_bars.main()
    assert e.value.code == 0


def test_ingest_tick_exits_nonzero_on_any_table_failure(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ingest_tick.py"])
    monkeypatch.setattr(ingest_tick, "discover_tasks", lambda t: [])
    monkeypatch.setattr(ingest_tick, "run_pool",
                        lambda t, tasks: 1 if t == "tick_trades" else 0)
    with pytest.raises(SystemExit) as e:
        ingest_tick.main()
    assert e.value.code == 1
