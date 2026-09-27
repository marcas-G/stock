"""R31 Task 5：strategy + report 组（plan `2026-09-18-research-api.md` Task 5）。

断言来源：
- spec `knowledge/design/platform/specs/2026-09-18-research-api-design.md`：
  §3（strategy lint/run/list/show/export/capacity/cost；report list/show/url/serve）、
  §4（错误码 STRATEGY_FAILED/NOT_FOUND/LINT/USAGE/BUSY/MEMORY_GUARD）、
  §5（run 过 heavy 闸 + env 注入）、§7（禁止行为断言：真产物、过闸、真读面）。
- plan Task 5：run 过 `_guard.guard_heavy`（mock 断言）；无信号产物 → `NOT_FOUND`
  +hint（先 `flab factor run`）；`--signal` 指定；`--dry-run` 不落盘；
  capacity/cost 读回测产物返回字段（`capacity_proxy`/`cost_net_report` 真实调用）；
  report `url` 不启服务；`serve` 以 `create_app` 断言（不长跑）。

禁止行为证明：strategy run 用真引擎（env 双腿）产新 target/nav/manifest（mtime +
可 load 回读）；capacity 用真 daily amount 读数 + 独立换手序列做算术；cost 的 net
annual < gross annual 来自真 `cost_net_report`；存根（硬编码 nav/容量/判决）必败。
"""

from __future__ import annotations

import argparse
import datetime
import json
import time
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from factorlab.config import settings
from factorlab.research import envelope, registry
from factorlab.research import report as R
from factorlab.research import strategy as S
from factorlab.research._guard import GuardError
from factorlab.research.envelope import EXIT_CODES
from factorlab.surfaces.cli.main import app as cli_app

runner = CliRunner()

STRATEGY_COMMANDS = {
    "strategy.lint", "strategy.run", "strategy.list", "strategy.show",
    "strategy.export", "strategy.capacity", "strategy.cost",
}
REPORT_COMMANDS = {"report.list", "report.show", "report.url", "report.serve"}


# ================================================================
# 工具
# ================================================================

def _args(**kw) -> argparse.Namespace:
    kw.setdefault("pretty", False)
    kw.setdefault("json", True)
    return argparse.Namespace(**kw)


def _run_args(doc_path=None, **over) -> argparse.Namespace:
    base = dict(doc_path=doc_path, signal=None, dry_run=False, out_dir=None,
                wait=False, pretty=False, json=True)
    base.update(over)
    return argparse.Namespace(**base)


def _strict_json(env: envelope.Envelope) -> str:
    """信封必须可严格序列化（NaN/inf/DataFrame 泄漏必败）。"""
    return json.dumps(env.to_doc(), ensure_ascii=False, allow_nan=False)


def _point_open_read(monkeypatch, env) -> None:
    """门面真开读句柄（不是 mock）：按 env 腿指向同一假库。"""
    from factorlab.app import bootstrap
    real = bootstrap.open_read
    if env.backend == "duckdb":
        monkeypatch.setattr(bootstrap, "open_read",
                            lambda: real(db_path=env.path))
    else:
        monkeypatch.setattr(bootstrap, "open_read",
                            lambda: real(data_backend="ch"))


def _mock_guard(monkeypatch, tmp_path) -> list:
    calls: list = []

    def fake_guard(argv, *, wait=False):
        calls.append(("guard", list(argv), wait))
        return ({"FACTORLAB_MAX_MEMORY": "8GB",
                 "FACTORLAB_MIN_AVAILABLE_MEMORY": "6GB",
                 "OMP_NUM_THREADS": "8", "POLARS_MAX_THREADS": "8"},
                tmp_path / "heavy.1.lock")

    monkeypatch.setattr(S, "guard_heavy", fake_guard)
    monkeypatch.setattr(S, "release_slots", lambda: calls.append(("release",)))
    monkeypatch.setattr("factorlab.app.memory.apply_address_space_limit",
                        lambda b, **kw: None)
    return calls


def _setup(env, tmp_path, monkeypatch, *, run=True, doc_kw=None, doc_text=None):
    """真 signal 产物（可选）+ 门面 settings + 策略 YAML；返回 (doc_path, results)。"""
    from test_run_strategy import (_SIGNAL_NAME, _doc_yaml, _factor_spec,
                                   _tables)
    from factorlab.app.context import RunContext
    from factorlab.app.run import run_factor
    from factorlab.core.spec import load_spec

    results = tmp_path / "results"
    env.seed(_tables())
    if run:
        run_factor(load_spec(_factor_spec(tmp_path)),
                   RunContext(data_backend=env.backend,
                              output_dir=results / _SIGNAL_NAME,
                              db_path=getattr(env, "path", None)))
    monkeypatch.setattr(settings, "results_dir", results)
    _point_open_read(monkeypatch, env)
    doc_path = tmp_path / "doc.yaml"
    doc_path.write_text(doc_text if doc_text is not None
                        else _doc_yaml(**(doc_kw or {})), encoding="utf-8")
    return doc_path, results


def _run_ok(env, tmp_path, monkeypatch, **setup_kw):
    doc_path, results = _setup(env, tmp_path, monkeypatch, **setup_kw)
    _mock_guard(monkeypatch, tmp_path)
    e = S.strategy_run(_run_args(doc_path))
    assert e.ok, e.error
    return e, doc_path, results


def _expected_turnover(target, codes) -> list[float]:
    """测试独立复算 0.5*Σ|Δw|（与实现分离——存档对比用）。"""
    weights = {d: {c: 0.0 for c in codes} for d in target.decision_dates}
    for d, c, w in target.frame.iter_rows():
        weights[d][c] = float(w)
    prev = {c: 0.0 for c in codes}
    series = []
    for d in target.decision_dates:
        cur = weights[d]
        series.append(0.5 * sum(abs(cur[c] - prev[c]) for c in codes))
        prev = cur
    return series


def _write_summary(results_dir: Path, name: str, *, ic=0.05, spread=0.02,
                   ts="2026-08-16T12:00:00") -> None:
    out = Path(results_dir) / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps({
        "name": name, "category": "custom", "direction": 1,
        "evaluation": {"version": 2, "frequency": "daily",
                       "ic": {"mean": ic},
                       "decile_returns": {"spread": {"ret": spread}}},
        "timestamp": ts,
    }, ensure_ascii=False), encoding="utf-8")


# ================================================================
# strategy run：过闸 + env 注入 + 真产物
# ================================================================

def test_strategy_run_real_chain_passes_guard_and_persists(env, tmp_path,
                                                           monkeypatch):
    doc_path, results = _setup(env, tmp_path, monkeypatch)
    calls = _mock_guard(monkeypatch, tmp_path)
    max_memory_before = settings.max_memory

    t0 = time.time()
    e = S.strategy_run(_run_args(doc_path))

    assert e.ok, e.error
    assert calls[0] == ("guard", ["strategy", "run", str(doc_path)], False)
    assert ("release",) in calls, "run 结束必须释放槽"
    assert settings.max_memory == max_memory_before  # 闸退出后恢复进入前配置

    # 真产物（硬编码/旧数据必败：mtime + load 回读 + 与返回值一致）
    out = Path(e.artifacts["strategy_dir"])
    assert out == Path(results) / "strategies" / "ws7_doc"
    assert (out / "strategy_manifest.json").exists()
    assert (out / "manifest.json").exists()
    assert (out / "nav" / "nav_series.parquet").stat().st_mtime >= t0 - 1
    assert (out / "run.log").exists()

    from factorlab.adapters.execution_store import load_backtest_result
    from factorlab.adapters.strategy_artifacts import load_strategy_artifacts
    bundle = load_strategy_artifacts(out)
    loaded = load_backtest_result(out)
    assert e.data["name"] == "ws7_doc"
    assert e.data["signal"] == "ws7_doc_chain"
    assert e.data["decisions"] == len(bundle.target.decision_dates) == 4
    assert e.data["execution_events"] == loaded.nav_series.frame.height == 4
    assert e.data["fills"] == sum(a.fills.frame.height
                                  for a in loaded.artifacts)
    navs = loaded.nav_series.frame["nav"].to_list()
    assert e.data["nav"]["first"] == pytest.approx(navs[0])
    assert e.data["nav"]["last"] == pytest.approx(navs[-1])
    _strict_json(e)


@pytest.mark.parametrize("code", ["BUSY", "MEMORY_GUARD"])
def test_strategy_run_guard_error_maps_to_envelope(tmp_path, monkeypatch, code):
    def fake_guard(argv, *, wait=False):
        raise GuardError(code, f"{code} 测试", hint="hint-x")

    monkeypatch.setattr(S, "guard_heavy", fake_guard)
    results = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", results)
    # 信号存在性预检先于占闸：提供最小 summary 占位（只查 is_file）
    (results / "x").mkdir(parents=True)
    (results / "x" / "summary.json").write_text("{}", encoding="utf-8")
    doc = tmp_path / "doc.yaml"
    doc.write_text("name: s\nsignal: x\ndirection: 1\n"
                   "portfolio: {top_k: 2}\nexecution: {timing: NEXT_OPEN, "
                   "initial_cash: 1000000.0}\n"
                   'date: {start: "2024-01-02", end: "2024-01-05"}\n',
                   encoding="utf-8")
    e = S.strategy_run(_run_args(doc))
    assert e.ok is False
    assert e.error["code"] == code
    assert e.error["hint"] == "hint-x"
    _strict_json(e)


def test_strategy_run_missing_signal_is_not_found_with_factor_hint(
        env, tmp_path, monkeypatch):
    doc_path, _results = _setup(env, tmp_path, monkeypatch, run=False)
    calls = _mock_guard(monkeypatch, tmp_path)

    e = S.strategy_run(_run_args(doc_path))
    assert e.ok is False
    assert e.error["code"] == "NOT_FOUND"
    assert "flab factor run" in (e.error["hint"] or "")
    assert "ws7_doc_chain" in e.error["message"]
    assert calls == [], "缺信号产物不得先占闸"
    _strict_json(e)


def test_strategy_run_signal_override_uses_named_artifact(env, tmp_path,
                                                          monkeypatch):
    from test_run_strategy import _doc_yaml
    doc_path, _results = _setup(
        env, tmp_path, monkeypatch,
        doc_text=_doc_yaml().replace("signal: ws7_doc_chain",
                                     "signal: ghost_signal"))
    _mock_guard(monkeypatch, tmp_path)

    missing = S.strategy_run(_run_args(doc_path))
    assert missing.ok is False and missing.error["code"] == "NOT_FOUND"

    e = S.strategy_run(_run_args(doc_path, signal="ws7_doc_chain"))
    assert e.ok, e.error
    assert e.data["name"] == "ws7_doc"          # 产物名仍按策略名
    assert e.data["signal"] == "ws7_doc_chain"  # 覆盖在解析产物前生效


def test_strategy_run_missing_composite_signal_dispatches_precheck(
        env, tmp_path, monkeypatch):
    """Plan CX-C4 T3：composite 引用预检按 kind 分派到 composites/<name>/ 布局。

    修复 T1 遗留（`research/strategy.py` 入口预检固定按 factor 布局查 summary →
    composite 误报）。禁止行为门：预检失败不得占 heavy 闸（与 factor 同语义）。
    """
    from test_run_strategy import _doc_yaml
    doc_path, results = _setup(
        env, tmp_path, monkeypatch, run=False,
        doc_text=_doc_yaml(signal="composites/cx_demo", name="ws7_cx"))
    calls = _mock_guard(monkeypatch, tmp_path)

    e = S.strategy_run(_run_args(doc_path))
    assert e.ok is False
    assert e.error["code"] == "NOT_FOUND"
    assert "cx_demo" in e.error["message"]
    assert str(Path(results) / "composites" / "cx_demo") in e.error["message"]
    hint = e.error["hint"] or ""
    assert "factorlab compose" in hint, f"composite 预检 hint 应指向 compose: {hint!r}"
    assert "flab factor run" not in hint, "composite 引用不得提示 factor 命令"
    assert calls == [], "缺 composite 产物不得先占闸"
    assert not (Path(results) / "strategies" / "ws7_cx").exists()
    _strict_json(e)


def test_strategy_run_composite_precheck_passes_with_artifact(
        env, tmp_path, monkeypatch):
    """composite 产物就位 → 入口预检放行并跑完整链（真产物，非 dry-run）。"""
    from test_run_strategy import _composite_panel, _doc_yaml, _write_composite
    doc_path, results = _setup(
        env, tmp_path, monkeypatch, run=False,
        doc_text=_doc_yaml(signal="composites/cx_demo", name="ws7_cx"))
    _write_composite(results, "cx_demo", _composite_panel())
    calls = _mock_guard(monkeypatch, tmp_path)

    e = S.strategy_run(_run_args(doc_path))
    assert e.ok, e.error
    assert calls and calls[0][0] == "guard", "预检通过后必须占闸"
    assert e.data["name"] == "ws7_cx"
    assert e.data["signal"] == "cx_demo"
    out = Path(results) / "strategies" / "ws7_cx"
    assert (out / "strategy_manifest.json").is_file()
    assert (out / "nav" / "nav_series.parquet").is_file()
    # 禁止行为：composite 预检不得因 factor 目录同名 summary 而放行
    # （本用例无 <results>/cx_demo/summary.json，若误走 factor 分支必 NOT_FOUND）
    assert not (Path(results) / "cx_demo").exists()
    _strict_json(e)


def test_strategy_run_dry_run_has_no_side_effects(env, tmp_path, monkeypatch):
    doc_path, results = _setup(env, tmp_path, monkeypatch)
    calls = _mock_guard(monkeypatch, tmp_path)
    opened: list = []
    from factorlab.app import bootstrap
    real = bootstrap.open_read
    monkeypatch.setattr(bootstrap, "open_read",
                        lambda: opened.append(1) or real())
    monkeypatch.setattr("factorlab.app.memory.apply_address_space_limit",
                        lambda b, **kw: opened.append("rlimit"))

    e = S.strategy_run(_run_args(doc_path, dry_run=True))
    assert e.ok, e.error
    assert e.data["name"] == "ws7_doc"
    assert e.data["doc"]["strategy"]["signal_name"] == "ws7_doc_chain"
    assert e.data["doc"]["date"]["start"] == "2024-01-02"
    assert e.data["doc"]["strategy"]["selection"]["k"] == 2
    assert calls == [] and opened == []
    assert not (Path(results) / "strategies").exists()
    _strict_json(e)


def test_strategy_run_max_hold_fails_loud(env, tmp_path, monkeypatch):
    from test_run_strategy import _doc_yaml
    text = _doc_yaml() + "rules: {stop_loss: null, take_profit: null, max_hold: 2}\n"
    doc_path, _results = _setup(env, tmp_path, monkeypatch, doc_text=text)
    calls = _mock_guard(monkeypatch, tmp_path)

    e = S.strategy_run(_run_args(doc_path))
    assert e.ok is False
    assert e.error["code"] == "STRATEGY_FAILED"
    hint = (e.error["hint"] or "")
    assert "research/tools/strategies" in hint or "研究侧" in hint
    assert calls == [], "不支持的规则不得静默忽略/占闸"
    _strict_json(e)


# ================================================================
# strategy lint / list / show / export
# ================================================================

def test_strategy_lint_ok_bad_missing(tmp_path):
    from test_run_strategy import _doc_yaml
    good = tmp_path / "good.yaml"
    good.write_text(_doc_yaml(), encoding="utf-8")
    e = S.strategy_lint(_args(doc_paths=[good]))
    assert e.ok, e.error
    assert e.data["results"][0]["name"] == "ws7_doc"

    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nsignal: y\ndirection: 7\n", encoding="utf-8")
    e_bad = S.strategy_lint(_args(doc_paths=[bad]))
    assert e_bad.ok is False and e_bad.error["code"] == "LINT"
    _strict_json(e_bad)

    e_missing = S.strategy_lint(_args(doc_paths=[tmp_path / "nope.yaml"]))
    assert e_missing.ok is False
    assert e_missing.error["code"] == "NOT_FOUND"

    e_usage = S.strategy_lint(_args(doc_paths=[]))
    assert e_usage.ok is False and e_usage.error["code"] == "USAGE"


def test_strategy_list_reflects_artifacts_and_empty(env, tmp_path, monkeypatch):
    e_run, _doc, results = _run_ok(env, tmp_path, monkeypatch)
    e = S.strategy_list(_args())
    assert e.ok, e.error
    assert e.data["n"] == 1
    row = e.data["strategies"][0]
    assert row["name"] == "ws7_doc"
    assert row["signal"] == "ws7_doc_chain"
    assert row["n_events"] == 4
    assert row["created_at"]
    assert row["nav_last"] == pytest.approx(e_run.data["nav"]["last"])
    assert row["total_return"] == pytest.approx(
        e_run.data["nav"]["last"] / e_run.data["nav"]["first"] - 1.0)

    # 缺 manifest 的目录不算策略产物（不静默当空产物）
    (Path(results) / "strategies" / "garbage").mkdir(parents=True)
    assert S.strategy_list(_args()).data["n"] == 1

    monkeypatch.setattr(settings, "results_dir", tmp_path / "empty")
    assert S.strategy_list(_args()).data["n"] == 0


def test_strategy_show_reads_artifacts_and_missing_is_not_found(
        env, tmp_path, monkeypatch):
    e_run, _doc, results = _run_ok(env, tmp_path, monkeypatch)
    e = S.strategy_show(_args(name="ws7_doc"))
    assert e.ok, e.error
    assert e.data["name"] == "ws7_doc"
    assert e.data["spec"]["signal_name"] == "ws7_doc_chain"
    assert e.data["spec"]["selection"]["k"] == 2
    assert e.data["nav"]["events"] == 4
    assert e.data["nav"]["last"] == pytest.approx(e_run.data["nav"]["last"])
    assert e.data["turnover"]["mean"] > 0
    assert e.data["cost"]["layer"] == "strategy"
    assert e.data["cost"]["periods"] == 4
    _strict_json(e)

    missing = S.strategy_show(_args(name="ghost"))
    assert missing.ok is False and missing.error["code"] == "NOT_FOUND"
    traversal = S.strategy_show(_args(name="../results"))
    assert traversal.ok is False and traversal.error["code"] == "NOT_FOUND"


@pytest.mark.parametrize("fmt", ["parquet", "csv", "json"])
def test_strategy_export_nav_formats(env, tmp_path, monkeypatch, fmt):
    _e, _doc, _results = _run_ok(env, tmp_path, monkeypatch)
    out = tmp_path / f"nav.{fmt}"
    e = S.strategy_export(_args(name="ws7_doc", kind="nav", format=fmt, out=out))
    assert e.ok, e.error
    assert Path(e.data["path"]) == out and out.exists()
    assert e.data["n_rows"] == 4
    if fmt == "parquet":
        assert pl.read_parquet(out).height == 4
    elif fmt == "csv":
        assert pl.read_csv(out).height == 4
    else:
        assert json.loads(out.read_text(encoding="utf-8"))


def test_strategy_export_target_and_usage(env, tmp_path, monkeypatch):
    _e, _doc, _results = _run_ok(env, tmp_path, monkeypatch)
    e = S.strategy_export(_args(name="ws7_doc", kind="target",
                                format="parquet", out=tmp_path / "t.parquet"))
    assert e.ok, e.error
    frame = pl.read_parquet(e.data["path"])
    assert "target_weight" in frame.columns and frame.height > 0

    usage = S.strategy_export(_args(name="ws7_doc", kind="bogus",
                                    format="parquet", out=None))
    assert usage.ok is False and usage.error["code"] == "USAGE"

    missing = S.strategy_export(_args(name="ghost", kind="nav",
                                      format="parquet", out=None))
    assert missing.ok is False and missing.error["code"] == "NOT_FOUND"


# ================================================================
# strategy capacity / cost（真读面 + 真 metric 调用）
# ================================================================

def test_strategy_capacity_real_amount_and_turnover(env, tmp_path, monkeypatch):
    _e, _doc, results = _run_ok(env, tmp_path, monkeypatch)
    from factorlab.adapters.strategy_artifacts import load_strategy_artifacts
    target = load_strategy_artifacts(Path(results) / "strategies" / "ws7_doc").target
    codes = sorted(target.frame["code"].unique().to_list())
    series = _expected_turnover(target, codes)
    mean_turnover = sum(series) / len(series)

    e = S.strategy_capacity(_args(name="ws7_doc", participation_rate=0.1))
    assert e.ok, e.error
    assert e.data["one_side_turnover"] == pytest.approx(mean_turnover)
    assert e.data["participation_rate"] == pytest.approx(0.1)
    assert e.data["formula"] == (
        "capacity = avg_amount × participation_rate / one_side_turnover")
    assert e.data["layer"] == "strategy"
    # 真 daily amount 读数（duckdb 读面千元→元 ×1000；ch 面原生元）
    assert len(e.data["per_code"]) == len(codes)
    seen_amounts = []
    for row in e.data["per_code"]:
        assert row["avg_amount"] in (pytest.approx(1e6), pytest.approx(1e9))
        assert row["capacity"] == pytest.approx(
            row["avg_amount"] * 0.1 / mean_turnover)
        seen_amounts.append(row["avg_amount"])
    assert e.data["aggregate"]["n"] == len(codes)
    assert e.data["aggregate"]["min"] == pytest.approx(
        min(seen_amounts) * 0.1 / mean_turnover)
    assert e.data["aggregate"]["mean"] == pytest.approx(
        sum(seen_amounts) / len(seen_amounts) * 0.1 / mean_turnover)
    assert e.data["aggregate"]["median"] == pytest.approx(
        sorted(seen_amounts)[len(seen_amounts) // 2] * 0.1 / mean_turnover)
    _strict_json(e)

    missing = S.strategy_capacity(_args(name="ghost", participation_rate=0.1))
    assert missing.ok is False and missing.error["code"] == "NOT_FOUND"


def test_strategy_cost_real_cost_net_report(env, tmp_path, monkeypatch):
    e_run, _doc, _results = _run_ok(env, tmp_path, monkeypatch)
    e = S.strategy_cost(_args(name="ws7_doc", cost_rate=0.001,
                              periods_per_year=None))
    assert e.ok, e.error
    report = e.data["report"]
    assert report["layer"] == "strategy"       # 真实 cost_net_report 输出
    assert report["cost_rate"] == pytest.approx(0.001)
    assert report["periods"] == 4
    assert report["periods_per_year"] == 252   # daily 策略
    assert report["annual_turnover"] == pytest.approx(
        e.data["turnover"]["mean"] * 252)
    assert report["total_cost"] == pytest.approx(
        0.001 * sum(e.data["turnover"]["series"]))
    # 成本后净值严格低于成本前（成本费率来自入参，不是硬编码 0）
    assert report["net"]["annual_return"] < report["gross"]["annual_return"]
    assert report["net_nav"][-1] < report["gross_nav"][-1]
    # gross_nav 归一化（基期 1.0）：末值 == 策略总收益倍数
    assert report["gross_nav"][-1] == pytest.approx(
        e_run.data["nav"]["last"] / e_run.data["nav"]["first"])
    _strict_json(e)

    missing = S.strategy_cost(_args(name="ghost", cost_rate=0.001,
                                    periods_per_year=None))
    assert missing.ok is False and missing.error["code"] == "NOT_FOUND"


# ================================================================
# report list / show / url / serve
# ================================================================

def test_report_list_and_show(tmp_path, monkeypatch):
    results = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", results)
    _write_summary(results, "alpha", ic=0.05, ts="2026-08-16T12:00:00")
    _write_summary(results, "beta", ic=0.07, ts="2026-08-17T12:00:00")

    e = R.report_list(_args())
    assert e.ok, e.error
    assert e.data["n"] == 2
    assert [r["name"] for r in e.data["reports"]] == ["beta", "alpha"]
    assert e.data["reports"][0]["ic_mean"] == pytest.approx(0.07)
    assert all("_sort" not in r for r in e.data["reports"])

    show = R.report_show(_args(name="alpha"))
    assert show.ok, show.error
    assert show.data["name"] == "alpha"
    assert show.data["summary"]["evaluation"]["ic"]["mean"] == pytest.approx(0.05)
    assert show.data["url_path"] == "/factor/alpha"

    missing = R.report_show(_args(name="ghost"))
    assert missing.ok is False and missing.error["code"] == "NOT_FOUND"


def test_report_url_does_not_start_server(tmp_path, monkeypatch):
    results = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", results)
    _write_summary(results, "alpha")
    import uvicorn

    def explode(*a, **kw):
        raise AssertionError("report url 不得启动服务")

    monkeypatch.setattr(uvicorn, "run", explode)

    e = R.report_url(_args(name="alpha", base="http://127.0.0.1:8000"))
    assert e.ok, e.error
    assert e.data["url"] == "http://127.0.0.1:8000/factor/alpha"
    assert e.data["path"] == "/factor/alpha"
    assert Path(e.artifacts["summary"]).exists()

    custom = R.report_url(_args(name="alpha", base="http://0.0.0.0:9999"))
    assert custom.data["url"] == "http://0.0.0.0:9999/factor/alpha"

    missing = R.report_url(_args(name="ghost", base="http://127.0.0.1:8000"))
    assert missing.ok is False and missing.error["code"] == "NOT_FOUND"


def test_report_serve_builds_app_and_delegates_uvicorn(tmp_path, monkeypatch):
    results = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", results)
    _write_summary(results, "alpha")
    import uvicorn

    calls: dict = {}

    def fake_run(app, **kw):
        calls["app"] = app
        calls["kw"] = kw

    monkeypatch.setattr(uvicorn, "run", fake_run)

    e = R.report_serve(_args(port=8123, host="127.0.0.1"))
    assert e.ok, e.error
    assert calls["kw"] == {"host": "127.0.0.1", "port": 8123}
    paths = {getattr(route, "path", None) for route in calls["app"].routes}
    assert "/" in paths and "/factor/{name}" in paths  # 真 create_app
    assert e.data["url"] == "http://127.0.0.1:8123/"
    assert e.data["reports_dir"] == str(results)
    _strict_json(e)


# ================================================================
# registry / CLI 契约
# ================================================================

def test_strategy_report_commands_registered_with_schemas():
    assert STRATEGY_COMMANDS <= set(registry.COMMANDS)
    assert REPORT_COMMANDS <= set(registry.COMMANDS)
    for name in sorted(STRATEGY_COMMANDS | REPORT_COMMANDS):
        doc = registry.COMMANDS[name].to_doc()
        assert doc["description"], name
        assert doc["examples"], name
        assert doc["output_schema"], name


def test_strategy_run_positional_and_flags_parse():
    parser = registry.build_parser(registry.COMMANDS["strategy.run"])
    ns = parser.parse_args(["x.yaml", "--signal", "sig", "--dry-run", "--wait"])
    assert str(ns.doc_path) == "x.yaml"
    assert ns.signal == "sig" and ns.dry_run is True and ns.wait is True


def test_cli_research_strategy_and_report_single_json(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "results_dir", tmp_path / "results")

    result = runner.invoke(cli_app, ["research", "strategy", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert len(result.stdout.strip().splitlines()) == 1
    doc = json.loads(result.stdout)
    assert doc["ok"] is True and doc["command"] == "strategy.list"
    assert doc["data"]["n"] == 0

    report = runner.invoke(cli_app, ["research", "report", "list", "--json"])
    assert report.exit_code == 0, report.output
    assert json.loads(report.stdout)["command"] == "report.list"


def test_cli_research_report_url_missing_exit_code_9(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "results_dir", tmp_path / "results")
    result = runner.invoke(cli_app, ["research", "report", "url", "ghost",
                                     "--json"])
    assert result.exit_code == EXIT_CODES["NOT_FOUND"] == 9
    assert json.loads(result.stdout)["error"]["code"] == "NOT_FOUND"


def test_cli_research_strategy_show_missing_positional_is_usage():
    result = runner.invoke(cli_app, ["research", "strategy", "show", "--json"])
    assert result.exit_code == EXIT_CODES["USAGE"]
    assert json.loads(result.stdout)["error"]["code"] == "USAGE"
