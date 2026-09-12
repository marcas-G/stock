"""factorlab run 命令测试：help、tmp 平台库端到端落盘（含 evaluation）、--set 变体、错误路径。"""
import json

import polars as pl
import pytest
import yaml
from typer.testing import CliRunner

from factorlab.surfaces.cli.main import app
from test_run_factor import build_db

runner = CliRunner()


def test_run_help():
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    for opt in ("--universe", "--max-memory", "--output-dir", "--backtest", "--no-backtest", "--groups", "--set"):
        assert opt in result.stdout


def test_run_end_to_end(tmp_path, monkeypatch):
    # 平台库风格 tmp 库 + spec → run 落盘（panel/weekly/summary，summary 含 evaluation）
    # 9 个交易日：align_weekly 取周内最后交易日（01-05），其 forward_return_5d 需 t+5
    # （01-12）在面板内——9 天恰好使第 1 个 ISO 周有 2 行有效，n_weeks=1
    build_db(tmp_path, n_days=9)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
formula: |
  signal = close / ts_delay(close, 1) - 1
""", encoding="utf-8")
    # run 命令在调用时读取 settings.platform_db——monkeypatch 指向 tmp 库
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    out_dir = tmp_path / "results" / "demo"
    result = runner.invoke(app, ["run", str(spec_path), "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    assert (out_dir / "panel.parquet").exists()
    assert (out_dir / "weekly.parquet").exists()
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["name"] == "demo"
    assert "evaluation" in summary
    assert summary["evaluation"]["n_weeks"] >= 1
    # 评估信息回显到 stdout
    assert "n_weeks=" in result.stdout


def test_run_universe_override(tmp_path, monkeypatch):
    # --universe 覆盖 spec.codes（6 位代码直通），默认回落 settings.default_universe
    build_db(tmp_path)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    out_dir = tmp_path / "results" / "demo"
    result = runner.invoke(app, ["run", str(spec_path), "--universe", "600519", "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["universe_count"] == 1
    assert summary["codes"] == ["600519.SH"]


def test_run_default_universe_wired(tmp_path, monkeypatch):
    # FACTORLAB_DEFAULT_UNIVERSE（settings.default_universe）接线：--universe 缺省时生效
    build_db(tmp_path)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    monkeypatch.setattr("factorlab.config.settings.default_universe", "000001")
    out_dir = tmp_path / "results" / "demo"
    result = runner.invoke(app, ["run", str(spec_path), "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["universe_count"] == 1
    assert summary["codes"] == ["000001.SZ"]


def test_run_missing_spec(tmp_path):
    result = runner.invoke(app, ["run", str(tmp_path / "nope.yaml")])
    assert result.exit_code != 0
    assert "nope.yaml" in result.output


def test_run_missing_db(tmp_path, monkeypatch):
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "nope.duckdb")
    result = runner.invoke(app, ["run", str(spec_path)])
    assert result.exit_code != 0
    assert "nope.duckdb" in result.output


def test_run_backtest_flag(tmp_path, monkeypatch):
    # --backtest（默认）：summary.evaluation 含 layered_backtest；--output-dir 缺省 results_dir/<name>
    # 9 个交易日：第 1 个 ISO 周（01-05）的 forward 在面板内 → 1 个有效周，
    # 回测期数 = 评估周数（无效周不计，M4b 期数口径）
    build_db(tmp_path, n_days=9)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    monkeypatch.setattr("factorlab.config.settings.results_dir", tmp_path / "results")
    result = runner.invoke(app, ["run", str(spec_path)])
    assert result.exit_code == 0, result.output
    summary = json.loads((tmp_path / "results" / "demo" / "summary.json").read_text(encoding="utf-8"))
    assert "layered_backtest" in summary["evaluation"]
    assert summary["evaluation"]["layered_backtest"]["n_groups"] == 10
    assert summary["evaluation"]["layered_backtest"]["periods"] == summary["evaluation"]["n_weeks"] == 1


def test_run_no_backtest_flag(tmp_path, monkeypatch):
    # --no-backtest：跳过 layered_backtest（评估仍在），weekly 落盘不受开关影响
    build_db(tmp_path)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    out_dir = tmp_path / "out_nobt"
    result = runner.invoke(app, ["run", "--no-backtest", str(spec_path), "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert "evaluation" in summary
    assert "layered_backtest" not in summary["evaluation"]
    assert (out_dir / "weekly.parquet").exists()


def test_run_groups_param(tmp_path, monkeypatch):
    # --groups 传递到 layered_backtest.n_groups
    build_db(tmp_path, n_days=9)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    out_dir = tmp_path / "out_g"
    result = runner.invoke(app, ["run", "--groups", "5", str(spec_path), "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["evaluation"]["layered_backtest"]["n_groups"] == 5


def test_run_groups_invalid_rejected(tmp_path, monkeypatch):
    # 边界：--groups < 2 在 typer 解析期拒绝（分层回测至少 2 档）
    build_db(tmp_path)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    result = runner.invoke(app, ["run", "--groups", "1", str(spec_path)])
    assert result.exit_code != 0
    assert "groups" in result.output


def test_run_weekly_parquet_is_weekly_aligned(tmp_path, monkeypatch):
    # weekly.parquet 为周频对齐面板（行数 = 周数 × 股票数），不再冗余日频（panel.parquet 保留日频）
    build_db(tmp_path, n_days=9)  # 9 交易日 = 2 个 ISO 周（01-05 / 01-12 各为周内最后交易日）
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    out_dir = tmp_path / "out_weekly"
    result = runner.invoke(app, ["run", str(spec_path), "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    weekly = pl.read_parquet(out_dir / "weekly.parquet")
    assert weekly.height == 2 * 2  # 2 周 × 2 只
    daily = pl.read_parquet(out_dir / "panel.parquet")
    assert daily.height == 9 * 2
    assert daily.height > weekly.height


def test_run_empty_universe(tmp_path, monkeypatch):
    build_db(tmp_path)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["999999.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    result = runner.invoke(app, ["run", str(spec_path)])
    assert result.exit_code != 0
    assert "universe" in result.output


def test_run_set_param_variant(tmp_path, monkeypatch):
    # --set win=2 → 变体名 demo_win2 + results 独立目录；默认变体（无 --set）并存不覆盖
    # 覆盖生效证明（计算层面）：默认 win=20 窗口 > 9 交易日 → signal 全 null（ratio=1.0）；
    # --set win=2 → 仅每资产头部 2 行 null（ratio=4/18）
    build_db(tmp_path, n_days=9)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
params: {win: 20}
formula: |
  signal = close / ts_delay(close, ${win}) - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    monkeypatch.setattr("factorlab.config.settings.results_dir", tmp_path / "results")
    # 默认变体：params 原样（win=20 → 全 null）
    result = runner.invoke(app, ["run", str(spec_path)])
    assert result.exit_code == 0, result.output
    default_dir = tmp_path / "results" / "demo"
    assert (default_dir / "summary.json").exists()
    default_summary = json.loads((default_dir / "summary.json").read_text(encoding="utf-8"))
    assert default_summary["signal_null_ratio"] == 1.0
    assert yaml.safe_load(default_summary["spec_yaml"])["params"] == {"win": 20}
    # --set 变体：独立目录 + 覆盖值进入计算
    result = runner.invoke(app, ["run", str(spec_path), "--set", "win=2"])
    assert result.exit_code == 0, result.output
    variant_dir = tmp_path / "results" / "demo_win2"
    assert (variant_dir / "summary.json").exists()
    assert (variant_dir / "panel.parquet").exists()
    summary = json.loads((variant_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["signal_null_ratio"] == pytest.approx(4 / 18, abs=1e-4)  # 2 只 × 头部 2 天 null
    assert yaml.safe_load(summary["spec_yaml"])["params"] == {"win": 2}  # values 合并进 spec.params
    # 变体名回显到 stdout；默认变体目录未受影响
    assert "demo_win2" in result.stdout
    assert (default_dir / "summary.json").exists()


def test_run_set_multiple_typed_values(tmp_path, monkeypatch):
    # --set 可多次；int/float/bool/str 解析；变体名按 k+v 拼接（values 合并进 spec.params）
    build_db(tmp_path)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-09"
params: {win: 20, gain: 2.0, tag: "base"}
formula: |
  signal = close / open - 1 + ${gain} * 0
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    monkeypatch.setattr("factorlab.config.settings.results_dir", tmp_path / "results")
    result = runner.invoke(app, [
        "run", str(spec_path),
        "--set", "win=100", "--set", "gain=2.5",
        "--set", "fast=true", "--set", "tag=abc",
    ])
    assert result.exit_code == 0, result.output
    variant_dir = tmp_path / "results" / "demo_win100_gain2.5_fastTrue_tagabc"
    assert (variant_dir / "summary.json").exists()
    summary = json.loads((variant_dir / "summary.json").read_text(encoding="utf-8"))
    params = yaml.safe_load(summary["spec_yaml"])["params"]
    assert params["win"] == 100 and params["gain"] == 2.5  # int/float 解析
    assert params["fast"] is True and params["tag"] == "abc"  # bool/str 解析
    assert "demo_win100_gain2.5_fastTrue_tagabc" in result.stdout


def test_run_set_bad_format_rejected(tmp_path, monkeypatch):
    # 边界：--set 缺 =（win100）或空值（win=）→ 非 0 退出并提示格式
    build_db(tmp_path)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text("""
name: demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    for kv in ("win100", "win="):
        result = runner.invoke(app, ["run", str(spec_path), "--set", kv])
        assert result.exit_code != 0, kv
        assert "--set" in result.output
        assert kv in result.output


def test_run_help_chunk_options():
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    for opt in ("--chunk-days", "--warmup-days"):
        assert opt in result.stdout


def test_run_chunked_end_to_end(tmp_path, monkeypatch):
    # --chunk-days 接线：分块跑通并落盘（9 天、chunk 2 → 5 块）
    build_db(tmp_path, n_days=9)
    spec_path = tmp_path / "demo_chunk.yaml"
    spec_path.write_text("""
name: demo_chunk
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
formula: |
  signal = close / ts_delay(close, 1) - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    out_dir = tmp_path / "results" / "demo_chunk"
    result = runner.invoke(app, [
        "run", str(spec_path), "--chunk-days", "2", "--warmup-days", "1",
        "--output-dir", str(out_dir), "--no-backtest"])
    assert result.exit_code == 0, result.output
    assert (out_dir / "panel.parquet").exists()
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["panel_rows"] == 9 * 2


def test_run_spec_target_20d_wired(tmp_path, monkeypatch):
    # spec.target=forward_return_20d → evaluation.target 与 IC 数值对 20d 列成立；
    # stdout 不含"暂未接线"占位提示（该提示已随接线删除）
    build_db(tmp_path, n_days=24)  # 24 交易日 → 前 4 日有 20d 标签
    spec_path = tmp_path / "demo20.yaml"
    spec_path.write_text("""
name: demo20
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-02-02"
target: forward_return_20d
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    out_dir = tmp_path / "results" / "demo20"
    result = runner.invoke(app, ["run", str(spec_path), "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    assert "暂未接线" not in result.output
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["evaluation"]["target"] == "forward_return_20d"
    ic_mean = summary["evaluation"]["ic"]["mean"]
    assert ic_mean == ic_mean  # 非 nan（有 20d 标签周）
    assert summary["evaluation"]["n_weeks"] >= 1
    assert "layered_backtest" in summary["evaluation"]


def test_run_default_target_is_5d(tmp_path, monkeypatch):
    # 回归：spec 不写 target（默认 5d）→ evaluation.target=="forward_return_5d"
    build_db(tmp_path, n_days=9)
    spec_path = tmp_path / "demo5.yaml"
    spec_path.write_text("""
name: demo5
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    out_dir = tmp_path / "results" / "demo5"
    result = runner.invoke(app, ["run", str(spec_path), "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["evaluation"]["target"] == "forward_return_5d"


# ================================================================
# WS3 多输出逐输出评估入口（收口设计 §6；断言来源 dsl-shape §3.2）
# ================================================================

_WS3_MULTI_YAML = """
name: demo_m
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
outputs: [{outs}]
formula: |
{formula}
"""


def _run_multi(tmp_path, monkeypatch, out_name, outputs, formula, *extra_args):
    """多输出 spec 通过 CLI run 落盘，返回 (exit_code, stdout, summary dict)。"""
    build_db(tmp_path, n_days=9)  # 9 交易日 = 2 个 ISO 周；首周 fwd5 标签在面板内
    spec_path = tmp_path / "multi.yaml"
    spec_path.write_text(_WS3_MULTI_YAML.format(
        outs=", ".join(outputs),
        formula="\n".join(f"  {line}" for line in formula)),
        encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    out_dir = tmp_path / "results" / out_name
    result = runner.invoke(app, ["run", str(spec_path), "--output-dir", str(out_dir), *extra_args])
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8")) if result.exit_code == 0 else None
    return result, out_dir, summary


def test_run_multi_output_eval_per_output(tmp_path, monkeypatch):
    # M1：outputs [a, b]（无字面 signal）→ evaluation = {"outputs": {a: 评估, b: 评估}}，
    # 每输出含 ic/n_weeks/layered_backtest；顶层无 ic 键（非共享单评估）
    # 红态（现状）：run 在 evaluate_factor_weekly 缺 signal 列报错 exit 1
    result, out_dir, summary = _run_multi(
        tmp_path, monkeypatch, "demo_m",
        ["a", "b"],
        ["a = close / open - 1", "b = ts_delay(close, 1) / close - 1"])
    assert result.exit_code == 0, result.output
    ev = summary["evaluation"]
    assert set(ev) == {"outputs"}  # 多输出 → 顶层仅 outputs（legacy 顶层键结构不混入）
    assert list(ev["outputs"]) == ["a", "b"]  # 声明序保留
    for o in ("a", "b"):
        ev_o = ev["outputs"][o]
        assert ev_o["target"] == "forward_return_5d"
        assert ev_o["n_weeks"] == 1
        assert ev_o["ic"]["mean"] == ev_o["ic"]["mean"]  # 非 nan（有有效周）
        bt = ev_o["layered_backtest"]
        assert bt["periods"] == ev_o["n_weeks"] == 1  # 无效周不计（M4b 期数口径）
        assert bt["n_groups"] == 10
    # console 逐输出一行
    assert "__a: n_weeks=1" in result.output
    assert "__b: n_weeks=1" in result.output


def test_run_multi_output_literal_signal_first_class(tmp_path, monkeypatch):
    # M3+M5：outputs 含字面 "signal" 是一等输出；每输出评估数值来自该输出自己的列
    # （signal 与 neg 互为相反数 → IC 必须异号；共用首输出评估的存根必败）
    result, out_dir, summary = _run_multi(
        tmp_path, monkeypatch, "demo_ms",
        ["signal", "neg"],
        ["signal = close / open - 1", "neg = -1 * (close / open - 1)"])
    assert result.exit_code == 0, result.output
    outs = summary["evaluation"]["outputs"]
    assert list(outs) == ["signal", "neg"]  # 字面 signal 是一等输出（无隐式主信号歧义）
    ic_sig = outs["signal"]["ic"]["mean"]
    ic_neg = outs["neg"]["ic"]["mean"]
    # 价格单调序列（build_db 梯形价）→ 与 fwd5 完全单调：IC=+1/-1，非退化数据
    assert abs(ic_sig - 1.0) < 1e-9 and abs(ic_neg + 1.0) < 1e-9
    assert ic_neg == pytest.approx(-ic_sig, abs=1e-9)
    # 逐输出落盘面板/周频 + 独立重算：每输出评估 = 该列直接 rust_ic 值（禁止共用/硬编码）
    import math
    import polars as pl
    from factorlab.core.eval.layered import layered_backtest
    from factorlab.adapters.rust_ic import evaluate_factor_weekly
    panel = pl.read_parquet(out_dir / "panel.parquet")
    weekly = pl.read_parquet(out_dir / "weekly.parquet")
    for o in ("signal", "neg"):
        w = weekly.select(["date", "code", o, "forward_return_5d"]).rename({o: "signal"})
        expect = evaluate_factor_weekly(w, "demo_ms", 1, target="forward_return_5d", weekly=w)
        expect["layered_backtest"] = layered_backtest(w, 1, forward_col="forward_return_5d")
        got = outs[o]
        assert got["target"] == expect["target"]
        assert got["n_weeks"] == expect["n_weeks"]
        assert math.isnan(got["decile_returns"]["spread"]["ret"]) or \
            got["ic"]["mean"] == pytest.approx(expect["ic"]["mean"], abs=1e-9)
        assert got["ic"]["mean"] == pytest.approx(expect["ic"]["mean"], abs=1e-9)


def test_run_multi_output_groups_param_per_output(tmp_path, monkeypatch):
    # M1 变体：--groups 5 → 每输出 layered_backtest.n_groups 均为 5（逐输出独立入口参数生效）
    result, out_dir, summary = _run_multi(
        tmp_path, monkeypatch, "demo_mg",
        ["a", "b"],
        ["a = close / open - 1", "b = ts_delay(close, 1) / close - 1"],
        "--groups", "5")
    assert result.exit_code == 0, result.output
    for o in ("a", "b"):
        assert summary["evaluation"]["outputs"][o]["layered_backtest"]["n_groups"] == 5


def test_run_multi_output_no_backtest_flag(tmp_path, monkeypatch):
    # 边界：--no-backtest → 每输出无 layered_backtest（评估仍在，逐输出）
    result, out_dir, summary = _run_multi(
        tmp_path, monkeypatch, "demo_mnb",
        ["a", "b"],
        ["a = close / open - 1", "b = ts_delay(close, 1) / close - 1"],
        "--no-backtest")
    assert result.exit_code == 0, result.output
    assert (out_dir / "weekly.parquet").exists()
    for o in ("a", "b"):
        ev_o = summary["evaluation"]["outputs"][o]
        assert ev_o["n_weeks"] >= 1
        assert "layered_backtest" not in ev_o


def test_run_legacy_single_output_eval_structure_lock(tmp_path, monkeypatch):
    # M2 回归锁：legacy（outputs == ["signal"]）evaluation 顶层键与收口前逐键一致
    # （键集合为收口前实测快照）；绝无 "outputs" 映射结构混入
    build_db(tmp_path, n_days=9)
    spec_path = tmp_path / "legacy.yaml"
    spec_path.write_text("""
name: demo_l
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
formula: |
  signal = close / open - 1
""", encoding="utf-8")
    monkeypatch.setattr("factorlab.config.settings.platform_db", tmp_path / "q.duckdb")
    out_dir = tmp_path / "results" / "demo_l"
    result = runner.invoke(app, ["run", str(spec_path), "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    ev = summary["evaluation"]
    # 收口前（b2b6977^）实测键快照：多输出改造不得改动 legacy 顶层结构
    assert sorted(ev) == sorted(["coverage", "decile_returns", "direction", "factor",
                                 "factor_name", "ic", "layered_backtest", "n_stocks_avg",
                                 "n_weeks", "pearson_ic", "target", "turnover"])
    assert "outputs" not in ev
    assert ev["target"] == "forward_return_5d"
    assert ev["n_weeks"] == 1
    assert ev["ic"]["mean"] == pytest.approx(1.0, abs=1e-9)
    assert ev["layered_backtest"]["periods"] == ev["n_weeks"]


def test_run_minute_spec_dispatches_to_minute_chain(ch_db, tmp_path, monkeypatch):
    """interface: bars_1m spec → CLI run 分派 run_factor_minute（summary 带
    runtime_semantics/interface/grid_rows_per_day）；产物/评估链与日频同构落盘。
    分派前（run_factor 直调）在 interface 门即 ValueError → exit 1（红）。"""
    from test_minute_engine import _SAMPLE, _seed
    _seed(ch_db)
    monkeypatch.setattr("factorlab.config.settings.data_backend", "ch")
    spec_path = tmp_path / "minute_demo.yaml"
    spec_path.write_text(f"""
name: minute_demo
category: custom
direction: 1
interface: bars_1m
adjustment: raw
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "{_SAMPLE[0].isoformat()}"
  end: "{_SAMPLE[-1].isoformat()}"
formula: |
  signal = day_last(close)
""", encoding="utf-8")
    out_dir = tmp_path / "results" / "minute_demo"
    result = runner.invoke(app, ["run", str(spec_path), "--output-dir",
                                 str(out_dir)])
    assert result.exit_code == 0, result.output
    for f in ("panel.parquet", "weekly.parquet", "summary.json"):
        assert (out_dir / f).exists()
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["interface"] == "bars_1m"
    assert summary["runtime_semantics"] == "minute_intraday_fold_v1"
    assert summary["grid_rows_per_day"] == 240
    assert summary["adjustment"] == "raw"
    assert "evaluation" in summary          # 周频对齐/评估/分层链对分钟折日面板零改动复用
    assert "n_weeks=" in result.stdout
    panel = pl.read_parquet(out_dir / "panel.parquet")
    assert panel.height == 12               # 2 code × 6 交易日（无停牌）
    assert panel["signal"].null_count() == 0
