"""D5（R30 Task 2）= R07-D6 收口：dead-signal fail-loud。

依据：`knowledge/design/platform/specs/2026-09-16-factorlab-eval-metrics-v2-design.md`
§2 D5（`signal_null_ratio ≥ 0.99` → 显式字段 + 非零退出）与 interface 评估节
（`evaluation.dead_signal` / 阈值常量）。

实测背景（R08 labels `05_dead_signal_cli.out.txt`）：`signal = 1/pb`（CH 空列）
曾以 `n_weeks=0` + `EXIT_CODE=0` 静默等价于"无效因子"，summary 无任何 dead 字段。

禁止行为断言：字段必须来自真实行计数（不同 null 数给不同 ratio，硬编码存根必败）；
正常因子不得出现 `dead_signal` 键、publish 不得抛错（零行为变化）；CLI 必须非零退出
且 summary 已在磁盘可审计（不是"静默跳过评估"）。
"""
from __future__ import annotations

import datetime as dt
import json

import polars as pl
import pytest
from typer.testing import CliRunner

from factorlab.app.context import RunContext
from factorlab.app.evaluate import evaluate_run, publish_run
from factorlab.core.engine.compute import FactorResult
from factorlab.core.eval.metrics import (DEAD_SIGNAL_NULL_RATIO, DeadSignalError,
                                         dead_signal_report)
from factorlab.core.spec import FactorSpec, UniverseSpec
from factorlab.surfaces.cli.main import app

runner = CliRunner()


def _spec(name="dead_signal_probe", outputs=None):
    return FactorSpec(name=name, category="custom", direction=1,
                      universe=UniverseSpec(codes=["000001.SZ"]),
                      formula="signal = 1 / pb", outputs=outputs)


def _panel(n_rows=200, n_null=200, signal_col="signal", outputs=None):
    """signal 列按 n_null 行为 null（其余为有效值）；labels 正常。

    outputs 给定时逐输出列：`signal_col` 按 n_null 置空，其余输出列恒有效
    （用于"一个输出死、另一个活着"的多输出场景）。
    """
    rows = []
    per_day = 20
    for i in range(n_rows):
        day = dt.date(2024, 1, 2) + dt.timedelta(days=i // per_day)
        valid = i < (n_rows - n_null)
        value = (0.1 + 0.001 * i) if valid else None
        row = {"date": day, "code": f"{i % per_day:06d}",
               "signal": value,
               "forward_return_1d": 0.001 * (i % 7),
               "forward_return_5d": 0.002 * (i % 7)}
        if outputs:
            row[signal_col] = value
            for o in outputs:
                if o != signal_col:
                    row[o] = 0.2 + 0.001 * i
        rows.append(row)
    return pl.DataFrame(rows)


def _result(panel, spec):
    return FactorResult(spec=spec, signal_artifact=None, label_artifact=None, panel=panel)


# ── 真实行计数（非硬编码）：报告函数 ────────────────────────────────────────
def test_dead_signal_report_uses_real_row_counts():
    dead = dead_signal_report(_panel(200, 200))
    assert dead["dead_signal"] is True
    assert dead["signal_null_ratio"] == pytest.approx(1.0)
    assert dead["null_rows"] == 200 and dead["total_rows"] == 200
    assert dead["threshold"] == DEAD_SIGNAL_NULL_RATIO == 0.99

    edge = dead_signal_report(_panel(200, 198))     # 0.99 恰在阈值 → dead
    assert edge["signal_null_ratio"] == pytest.approx(0.99)
    assert edge["dead_signal"] is True

    below = dead_signal_report(_panel(200, 197))    # 0.985 < 0.99 → 不标记
    assert below["signal_null_ratio"] == pytest.approx(0.985)
    assert below["dead_signal"] is False
    assert below["null_rows"] == 197


# ── 评估出口：字段落 evaluation + 响亮 note；publish 落盘后非零失败 ───────────
def test_dead_signal_marks_evaluation_and_publish_raises_loudly(tmp_path):
    spec = _spec()
    result = _result(_panel(200, 200), spec)
    ctx = RunContext(output_dir=tmp_path / "dead")
    outcome = evaluate_run(result, spec, ctx)

    assert outcome.evaluation.get("dead_signal") is True
    assert outcome.dead_signal is not None
    note = " ".join(outcome.notes)
    assert "死信号" in note and "signal_null_ratio=1.0" in note
    assert "n_weeks" in note  # 不再把 n_weeks=0 静默当结论

    with pytest.raises(DeadSignalError, match="signal_null_ratio"):
        publish_run(result, outcome, ctx)
    summary = json.loads((tmp_path / "dead" / "summary.json").read_text(encoding="utf-8"))
    assert summary["evaluation"]["dead_signal"] is True   # 落盘可审计
    assert summary["evaluation"]["n_weeks"] == 0


def test_normal_factor_zero_change_no_dead_key_no_raise(tmp_path):
    """正常因子（含少量 null）零行为变化：无 dead 键、publish 正常、ratio 真实。"""
    spec = _spec(name="normal_signal")
    panel = _panel(200, 2)
    outcome = evaluate_run(_result(panel, spec), spec, RunContext())
    assert "dead_signal" not in outcome.evaluation
    assert outcome.dead_signal is None
    assert not any("死信号" in n for n in outcome.notes)
    publish_run(_result(panel, spec), outcome, RunContext(output_dir=tmp_path / "ok"))
    assert (tmp_path / "ok" / "summary.json").is_file()


def test_below_threshold_ratio_does_not_trigger(tmp_path):
    spec = _spec(name="mostly_null")
    outcome = evaluate_run(_result(_panel(200, 197), spec), spec, RunContext())
    assert "dead_signal" not in outcome.evaluation
    assert outcome.dead_signal is None


def test_multi_output_dead_output_marked_and_fails(tmp_path):
    outputs = ["signal", "sig_live"]
    panel = _panel(200, 200, signal_col="signal", outputs=outputs)
    spec = _spec(name="multi_dead", outputs=outputs)
    result = _result(panel, spec)
    ctx = RunContext(output_dir=tmp_path / "multi")
    outcome = evaluate_run(result, spec, ctx)
    assert outcome.evaluation["outputs"]["signal"]["dead_signal"] is True
    assert "dead_signal" not in outcome.evaluation["outputs"]["sig_live"]
    with pytest.raises(DeadSignalError, match="signal_null_ratio"):
        publish_run(result, outcome, ctx)
    summary = json.loads((tmp_path / "multi" / "summary.json").read_text(encoding="utf-8"))
    assert summary["evaluation"]["outputs"]["signal"]["dead_signal"] is True
    assert "dead_signal" not in summary["evaluation"]["outputs"]["sig_live"]


# ── CLI 真入口：非零退出 + 落盘字段（monkeypatch run_factor，聚焦评估出口）────
def test_cli_run_dead_signal_exits_nonzero_with_summary_field(tmp_path, monkeypatch):
    spec_path = tmp_path / "dead.yaml"
    spec_path.write_text("""
name: dead_cli
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
formula: |
  signal = 1 / pb
""", encoding="utf-8")
    dead_panel = _panel(200, 200)

    def fake_run(spec, ctx):
        return _result(dead_panel, spec)

    monkeypatch.setattr("factorlab.app.run.run_factor", fake_run)
    out_dir = tmp_path / "results" / "dead_cli"
    result = runner.invoke(app, ["run", str(spec_path), "--output-dir", str(out_dir)])
    assert result.exit_code == 1, result.output
    assert "死信号" in result.output
    assert "signal_null_ratio=1.0" in result.output
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["evaluation"]["dead_signal"] is True
