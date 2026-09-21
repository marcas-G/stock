"""R40 T6：分钟链守卫挂接（execute 层窗口 + run.py 产物声明/回填）。

断言来源：设计 §7/§10 + 计划 T6 brief + T5 已落地的 execute 层统一 guard：
- `execute_run` 的 guard 窗口端点必须取 spec.date 声明（固定历史窗口）；
- `interface: bars_1m` 必须分派到 `run_factor_minute`，且 `ctx.guard` 原样到达
  （分钟链与日频同门）；
- `_lockbox_attach`/`_lockbox_mark_result`：guard 在 → `attach(summary)` /
  `mark_result(str(output_dir))`；guard 为 None → 零动作（summary 不变、无 IO）；
- 分钟链真实落盘（假 CH）：`summary["sample"]` 进盘上 summary、回填调用发生
  （摘除 run.py 分钟两处挂接即红——变异验证见 task-6-report.md）。

不依赖真实 state/日历：`lockbox_store.guard_run` 整体 patch（`_lockbox_guard_for_execute`
按模块属性调用 → 生效），CLI 日历/数据日 seam 一并固定；分钟集成测试用 `ch_db`
假 CH + 记录型 guard，不读真实台账。
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

import factorlab.app.run as app_run
from factorlab.adapters import lockbox_store
from factorlab.app.context import RunContext
from factorlab.app.run import (_lockbox_attach, _lockbox_mark_result,
                               run_factor_minute)
from factorlab.surfaces.cli import main as cli_main
from factorlab.surfaces.cli.main import execute_run

FIXED_START = dt.date(2024, 1, 2)
FIXED_END = dt.date(2024, 1, 9)
FIXED_DAYS = [FIXED_START, FIXED_END]

# 最小分钟 spec：字段取自 test_minute_engine._spec（interface: bars_1m 必填项）
SPEC_TEXT = f"""\
name: lockbox_minute
category: custom
direction: 1
interface: bars_1m
adjustment: raw
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "{FIXED_START.isoformat()}"
  end: "{FIXED_END.isoformat()}"
formula: |
  signal = close
"""


class _StopChain(Exception):
    """哨兵异常：证明 fake 分钟链被调用，短路真实重链。"""


class _RecordingGuard:
    """记录 attach/mark_result 调用；attach 写可辨识 sample 供盘上断言。"""

    sample = {"role": "lockbox", "access_id": "acc-t6"}

    def __init__(self) -> None:
        self.attached: list[dict] = []
        self.marked: list[str] = []

    def attach(self, summary: dict) -> None:
        summary["sample"] = dict(self.sample)
        self.attached.append(summary)

    def mark_result(self, result_ref: str) -> None:
        self.marked.append(result_ref)


def test_execute_minute_guard_window_and_ctx(tmp_path, monkeypatch):
    """guard 端点 == spec.date 窗口；哨兵 guard 原样进 fake 分钟链（同门）。"""
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "1")
    spec_path = tmp_path / "minute.yaml"
    spec_path.write_text(SPEC_TEXT, encoding="utf-8")
    sentinel = _RecordingGuard()
    calls: list[dict] = []

    def fake_guard_run(**kw):
        calls.append(kw)
        return sentinel

    monkeypatch.setattr(lockbox_store, "guard_run", fake_guard_run)
    monkeypatch.setattr(cli_main, "_lockbox_published_days",
                        lambda: list(FIXED_DAYS))
    monkeypatch.setattr(cli_main, "_lockbox_data_end", lambda: FIXED_END)
    seen: list[tuple] = []

    def fake_minute(spec, ctx):
        seen.append((spec, ctx))
        raise _StopChain

    monkeypatch.setattr(app_run, "run_factor_minute", fake_minute)

    with pytest.raises(_StopChain):
        execute_run(spec_path, backtest=False, output_dir=tmp_path / "out",
                    lockbox_intent="exploration", lockbox_reason="T6 单元验证")

    assert len(calls) == 1
    kw = calls[0]
    assert (kw["panel_start"], kw["panel_end"]) == (FIXED_START, FIXED_END)
    assert kw["intent"] == "exploration"
    assert kw["reason"] == "T6 单元验证"
    assert kw["spec_doc"]["name"] == "lockbox_minute"
    assert kw["artifact"] == str(spec_path)
    assert kw["command"] == "factor run"
    assert kw["trading_days"] == FIXED_DAYS
    assert kw["data_end"] == FIXED_END
    assert len(seen) == 1
    spec, ctx = seen[0]
    assert spec.interface == "bars_1m"
    assert ctx.guard is sentinel


def test_lockbox_attach_delegates_summary(tmp_path):
    guard = _RecordingGuard()
    ctx = RunContext(output_dir=tmp_path / "out", guard=guard)
    summary = {"name": "x"}
    _lockbox_attach(ctx, summary)
    assert len(guard.attached) == 1
    assert guard.attached[0] is summary
    assert summary["sample"] == guard.sample


def test_lockbox_attach_noop_without_guard(tmp_path):
    ctx = RunContext(output_dir=tmp_path / "out")
    summary = {"name": "x"}
    _lockbox_attach(ctx, summary)
    assert summary == {"name": "x"}


def test_lockbox_mark_result_delegates_str_output_dir(tmp_path):
    guard = _RecordingGuard()
    ctx = RunContext(output_dir=tmp_path / "out", guard=guard)
    _lockbox_mark_result(ctx)
    assert guard.marked == [str(tmp_path / "out")]
    assert not (tmp_path / "out").exists()


def test_lockbox_mark_result_noop_without_guard(tmp_path):
    ctx = RunContext(output_dir=tmp_path / "out")
    _lockbox_mark_result(ctx)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("formula, outputs, variant", [
    ("signal = day_last(close)", None, "single"),
    ("a = day_last(close)\nb = eod_close", "a,b", "multi"),
], ids=["single", "multi"])
def test_minute_chain_persists_sample_and_marks_result(
        ch_db, tmp_path, formula, outputs, variant):
    """真实分钟链（假 CH）单/多输出两分支：attach 在落盘前、mark_result 在落盘后。"""
    from test_minute_engine import _ctx, _seed, _spec

    _seed(ch_db)
    spec = _spec(tmp_path, f"lockbox_minute_{variant}", formula, outputs=outputs)
    out_dir = tmp_path / "out"
    guard = _RecordingGuard()
    ctx = _ctx(out_dir)
    ctx.guard = guard
    result = run_factor_minute(spec, ctx)
    assert result.summary["sample"] == guard.sample
    on_disk = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert on_disk["sample"] == guard.sample
    assert guard.marked == [str(out_dir)]
