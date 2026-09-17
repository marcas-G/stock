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
from factorlab.app.run import run_factor
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


# ── R30 fix 波裁定：全 NaN（非有限）signal 同样 fail loud（null-only 是漏网）──
def _nan_panel(n_rows=200, value=float("nan")):
    return _panel(n_rows, 0).with_columns(pl.lit(value).alias("signal"))


def test_all_nan_signal_triggers_dead_signal_with_real_counts():
    """全 NaN signal：is_finite=false 行同样计空——存根（只数 null）必败。"""
    report = dead_signal_report(_nan_panel(200))
    assert report["dead_signal"] is True
    assert report["signal_null_ratio"] == pytest.approx(1.0)
    assert report["null_rows"] == 0             # 纯 null 计数仍真实（审计分离）
    assert report["nonfinite_rows"] == 200      # NaN 行计数
    assert report["total_rows"] == 200

    inf_report = dead_signal_report(_nan_panel(200, float("inf")))
    assert inf_report["dead_signal"] is True
    assert inf_report["signal_null_ratio"] == pytest.approx(1.0)


def test_mixed_nan_ratio_is_fraction_not_null_only():
    """50% NaN + 0 null → ratio 0.5（<0.99 不触发）——证明 NaN 真进分母分子。"""
    panel = _panel(200, 0).with_columns(
        pl.when(pl.int_range(pl.len()) < 100)
        .then(float("nan")).otherwise(pl.col("signal")).alias("signal"))
    report = dead_signal_report(panel)
    assert report["signal_null_ratio"] == pytest.approx(0.5)
    assert report["null_rows"] == 0
    assert report["nonfinite_rows"] == 100
    assert report["dead_signal"] is False


def test_evaluate_run_all_nan_marks_dead_and_publish_fails(tmp_path):
    spec = _spec(name="nan_signal")
    panel = _nan_panel(200)
    result = _result(panel, spec)
    ctx = RunContext(output_dir=tmp_path / "nan")
    outcome = evaluate_run(result, spec, ctx)
    assert outcome.evaluation.get("dead_signal") is True
    assert outcome.dead_signal is not None
    note = " ".join(outcome.notes)
    assert "signal_null_ratio=1.0" in note
    # 复评 Minor：全 NaN 不得显示「0/200 行为空」自相矛盾——无效行 = null + 非有限
    assert "无效行 200/200" in note
    assert "null 0 + 非有限 200" in note
    with pytest.raises(DeadSignalError, match="signal_null_ratio") as ei:
        publish_run(result, outcome, ctx)
    assert "无效行 200/200" in str(ei.value)
    assert "null 0 + 非有限 200" in str(ei.value)


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
    # 复评 Minor：纯 null 场景同样分列可审计（无效行 = null + 非有限）
    assert "无效行 200/200" in note
    assert "null 200 + 非有限 0" in note

    with pytest.raises(DeadSignalError, match="signal_null_ratio") as ei:
        publish_run(result, outcome, ctx)
    assert "无效行 200/200" in str(ei.value)
    assert "null 200 + 非有限 0" in str(ei.value)
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


def test_interface_dead_signal_nonfinite_contract():
    """R30 fix 波裁定：interface 必须写「空值 = null 或非有限」与分列审计字段。"""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "knowledge" / "contracts"
            / "interface.md").read_text(encoding="utf-8")
    assert "非有限" in text, "interface 缺「空值 = null 或非有限（NaN/±inf）」口径"
    assert "nonfinite_rows" in text, "interface 缺 nonfinite_rows 审计字段"


# ── run 链 summary 同源：全 NaN 真跑（env 双腿）→ signal_null_ratio=1.0 ─────
def _seed_constant_prices(env):
    """恒定价格序列（8 日）：ts_std_dev=0 → 除法 inf；窗口未满行 null——
    非 null 的 inf 正是旧口径（只数 null）漏网路径。"""
    dates = [dt.date(2024, 1, 2) + dt.timedelta(days=i) for i in range(8)]
    env.seed({
        "daily": ([("ts_code", "str"), ("trade_date", "date"), ("open", "f64"),
                   ("high", "f64"), ("low", "f64"), ("close", "f64"),
                   ("vol", "f64"), ("amount", "f64")],
                  [("000001.SZ", d.strftime("%Y%m%d"), 10.0, 10.1, 9.9, 10.0,
                    1e6, 1e7) for d in dates]),
        "adj_factor": ([("ts_code", "str"), ("trade_date", "date"),
                        ("adj_factor", "f64")],
                       [("000001.SZ", d.strftime("%Y%m%d"), 1.0) for d in dates]),
        "trade_cal": ([("exchange", "str"), ("cal_date", "date"), ("is_open", "i64")],
                      [("SSE", d.strftime("%Y%m%d"), 1) for d in dates]),
        "stock_basic": ([("ts_code", "str"), ("symbol", "str"), ("exchange", "str"),
                         ("list_date", "date"), ("industry", "str"), ("market", "str")],
                        [("000001.SZ", "000001", "SZSE", "20240101", "x", "主板")]),
        "stock_st": ([("ts_code", "str"), ("name", "str"), ("trade_date", "date"),
                      ("type", "str"), ("type_name", "str")], []),
    })


def test_real_run_all_nan_signal_summary_ratio_and_dead(env, tmp_path):
    """summary.signal_null_ratio 必须把非有限（NaN）计入空值——否则与 D5 判定分裂。"""
    _seed_constant_prices(env)
    spec = FactorSpec(name="nan_real", category="custom", direction=1,
                      universe=UniverseSpec(codes=["000001.SZ"]),
                      formula="signal = close / ts_std_dev(close, 3)")
    kw = {"db_path": env.path} if env.backend == "duckdb" else {}
    ctx = RunContext(data_backend=env.backend, output_dir=tmp_path / "nan_run",
                     warmup_days=2, **kw)
    result = run_factor(spec, ctx)
    panel = result.panel
    # 价格恒定 → 窗口 std=0 → 除法 inf；窗口未满行 null——非 null 的 inf 是旧口径漏网
    assert int(panel["signal"].is_finite().fill_null(False).sum()) == 0
    assert panel["signal"].null_count() > 0
    assert result.summary["signal_null_ratio"] == pytest.approx(1.0), \
        "summary signal_null_ratio 未计入非有限（与 D5 判定分裂）"
    outcome = evaluate_run(result, spec, ctx)
    assert outcome.evaluation.get("dead_signal") is True


# ── 复评 Minor：多输出逐输出 ratio 与 D5 同源（非有限计入）──────────────────
def test_multi_output_summary_null_ratio_counts_nonfinite(env, tmp_path):
    """signals[o].null_ratio 旧实现只数 null——恒 inf 的输出会得 warmup-null
    占比而非 1.0，与 D5 判定分裂；b 全有限校验不得过度计数。"""
    _seed_constant_prices(env)
    spec = FactorSpec(name="multi_nan", category="custom", direction=1,
                      universe=UniverseSpec(codes=["000001.SZ"]),
                      formula="a = close / ts_std_dev(close, 3)\nb = close",
                      outputs=["a", "b"])
    kw = {"db_path": env.path} if env.backend == "duckdb" else {}
    ctx = RunContext(data_backend=env.backend, output_dir=tmp_path / "multi_nan",
                     warmup_days=2, **kw)
    result = run_factor(spec, ctx)
    assert result.signal_artifact is None   # 多输出无单列 signal artifact
    stats = result.summary["signals"]
    assert set(stats) == {"a", "b"}
    assert stats["a"]["rows"] == stats["b"]["rows"] == result.summary["panel_rows"]
    assert stats["a"]["null_ratio"] == pytest.approx(1.0), \
        "signals[a].null_ratio 未计入非有限（与 D5 判定分裂）"
    assert stats["b"]["null_ratio"] == 0.0   # b 全有限——不得过度计数
    assert "signal_null_ratio" not in result.summary   # 多输出无顶层单点
