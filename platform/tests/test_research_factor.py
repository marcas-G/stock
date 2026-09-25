"""R31 Task 4：factor 组测试（run 过闸 / admit 三判决 / 复用既有实现）。

断言来源：
- spec `knowledge/design/platform/specs/2026-09-18-research-api-design.md`
  §3（factor 命令表：lint/run/list/show/export/corr/resic/svd/ref/admit/op/catalog）、
  §4（错误码：LINT/NOT_FOUND/BUSY/MEMORY_GUARD）、§5（重命令过闸 + env 注入）、
  §7（禁止行为断言：run 必须过闸、必须产生**新**产物、admit 判决来自真实回归）。
- plan `knowledge/design/platform/plans/2026-09-18-research-api.md` Task 4：
  `factor run` 过 `_guard.guard_heavy`；`factor admit` = lint→（缺产物则 run，经闸）
  →对参考库 corr+resic→verdict（重复 corr_max≥0.95 / 冗余 r2_lib≥0.8 且 resic 不显著
  / |resIC t|<3 时观察 / 达到门槛后可加入）；`ref add/remove` 对参考库文件安全写。

禁止行为证明：run 用真引擎（build_db tmp duckdb）写新 summary（mtime 断言）+
IC 数值；admit 判决来自合成面板真实回归（硬编码 verdict 必败）；ref 写盘后
`load_reference` 真读回。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
from typer.testing import CliRunner

from factorlab.config import settings
from factorlab.research import envelope
from factorlab.research import factor as F
from factorlab.research._guard import GuardError
from factorlab.research.envelope import EXIT_CODES
from factorlab.surfaces.cli.main import app as cli_app

runner = CliRunner()

WEEKS = 8
N = 60
START = datetime.date(2024, 1, 5)  # 周五；每周一行，align_weekly 折叠后每周恰 1 日

FACTOR_COMMANDS = {
    "factor.lint", "factor.run", "factor.list", "factor.show", "factor.export",
    "factor.corr", "factor.resic", "factor.svd", "factor.ref.list",
    "factor.ref.add", "factor.ref.remove", "factor.admit", "factor.op.list",
    "factor.op.doc", "factor.op.add", "factor.op.remove", "factor.catalog",
}

_SPEC = """
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
"""


# ================================================================
# 工具
# ================================================================

def _args(**kw) -> argparse.Namespace:
    kw.setdefault("pretty", False)
    return argparse.Namespace(**kw)


def _run_args(spec_path=None, **over) -> argparse.Namespace:
    base = dict(spec_path=spec_path, universe=None, max_memory="4GB",
                output_dir=None, no_backtest=False, groups=10, set=None,
                chunk_days=None, warmup_days=None, eval_frequency=None,
                wait=False, no_float32=False, pretty=False)
    base.update(over)
    return argparse.Namespace(**base)


def _strict_json(env: envelope.Envelope) -> str:
    """信封必须可严格序列化（NaN/inf/DataFrame 泄漏必败）。"""
    return json.dumps(env.to_doc(), ensure_ascii=False, allow_nan=False)


def _dates(n=WEEKS):
    return [START + datetime.timedelta(weeks=w) for w in range(n)]


def _codes(n=N):
    return [f"{s:06d}" for s in range(n)]


def _basis(k=4):
    """k 个两两正交、同范数、均值≈0 的确定性向量（与 test_reference_library 同构造）。"""
    w = np.arange(1.0, N + 1.0) - (N + 1.0) / 2.0
    out = []
    for p in (1, 3, 5, 7):
        v = w ** p
        for b in out:
            v = v - (v @ b) / (b @ b) * b
        v = v - v.mean()
        out.append(v * (np.linalg.norm(w) / np.linalg.norm(v)))
    return out[:k]


def _tile(v, weeks=WEEKS):
    return np.tile(v, (weeks, 1))


def _write_panel(root: Path, name: str, signal: np.ndarray,
                 fwd: np.ndarray | None = None, *,
                 include_daily_label: bool = True) -> None:
    """results/<name>/panel.parquet：signal + both test label aliases (W, N)."""
    d = Path(root) / name
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "date": [dt for dt in _dates(signal.shape[0]) for _ in _codes(signal.shape[1])],
        "code": _codes(signal.shape[1]) * signal.shape[0],
        "signal": [float(v) for v in signal.ravel()],
    }
    if fwd is not None:
        if include_daily_label:
            data["forward_return_1d"] = [float(v) for v in fwd.ravel()]
        data["forward_return_5d"] = [float(v) for v in fwd.ravel()]
    pl.DataFrame(data).write_parquet(d / "panel.parquet")


def _write_daily_resic_panel(root: Path, name: str, dates: list[datetime.date],
                             signal: np.ndarray, forward: np.ndarray) -> None:
    """Write daily signal and 1d-label panels for resIC cadence tests."""
    d = Path(root) / name
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "date": [dt for dt in dates for _ in _codes()],
        "code": _codes() * len(dates),
        "signal": signal.ravel(),
        "forward_return_1d": forward.ravel(),
        "forward_return_5d": forward.ravel(),
    }).write_parquet(d / "panel.parquet")


def _write_summary(results_dir: Path, name: str, *, ic=0.05, spread=0.02,
                   ts="2026-08-16T12:00:00", version=2, frequency="daily"):
    out = Path(results_dir) / name
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "name": name, "category": "custom", "direction": 1,
        "evaluation": {"version": version, "frequency": frequency,
                       "ic": {"mean": ic},
                       "decile_returns": {"spread": {"ret": spread}}},
        "timestamp": ts,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8")


def _fixture_ref(tmp_path: Path) -> Path:
    """fixture 参考库：daily 2 只 + minute 1 只（跨 scales 禁止行为断言用）。"""
    p = tmp_path / "_fixture_reference.yaml"
    p.write_text(
        "updated: \"2024-01-01\"\n"
        "scales:\n"
        "  daily:\n"
        "    - name: base_a\n"
        "      style: 测试风格A\n"
        "      reason: fixture\n"
        "      added: \"2024-01-01\"\n"
        "    - name: base_b\n"
        "      style: 测试风格B\n"
        "      reason: fixture\n"
        "      added: \"2024-01-01\"\n"
        "  minute:\n"
        "    - name: min_x\n"
        "      style: 分钟测试\n"
        "      reason: fixture（不得进入 daily 对照）\n"
        "      added: \"2024-01-01\"\n",
        encoding="utf-8")
    return p


def _admit_spec(tmp_path: Path, name="cand", formula="signal = close",
                interface: str | None = None,
                target: str | None = None,
                evaluation_frequency: str | None = None) -> Path:
    spec = tmp_path / f"{name}.yaml"
    spec.write_text(f"""
name: {name}
category: custom
direction: 1
universe:
  codes: ["000001.SZ", "600519.SH"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
{f"interface: {interface}" if interface is not None else ""}
{f"target: {target}" if target is not None else ""}
{f"evaluation_frequency: {evaluation_frequency}" if evaluation_frequency is not None else ""}
formula: |
  {formula}
""", encoding="utf-8")
    return spec


def _admit_args(spec_path, **over):
    base = dict(spec_path=spec_path, scales=None, wait=False, pretty=False)
    base.update(over)
    return argparse.Namespace(**base)


@pytest.mark.parametrize(
    "spec_doc, expected",
    [
        ({"evaluation_frequency": "daily", "target": "forward_return_20d"},
         ("daily", "forward_return_1d")),
        ({"evaluation_frequency": "weekly", "target": "forward_return_20d"},
         ("weekly", "forward_return_20d")),
        ({"evaluation_frequency": "weekly"},
         ("weekly", "forward_return_5d")),
        ({"evaluation_frequency": "daily", "interface": "bars_1m"},
         ("daily", "forward_return_1d")),
        (None, ("weekly", "forward_return_5d")),
    ],
)
def test_admission_diagnostic_options_follow_spec_cadence(spec_doc, expected):
    assert F._diagnostic_options(spec_doc) == expected


# ================================================================
# factor run：过闸 + env 注入 + 真产物
# ================================================================

def test_factor_run_passes_guard_injects_env_and_writes_new_artifacts(
        tmp_path, monkeypatch):
    from test_run_factor import build_db
    build_db(tmp_path, n_days=9)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text(_SPEC, encoding="utf-8")
    monkeypatch.setattr(settings, "platform_db", tmp_path / "q.duckdb")
    monkeypatch.setattr(settings, "results_dir", tmp_path / "results")
    monkeypatch.setattr(settings, "max_memory", None)
    monkeypatch.setattr(settings, "min_available_memory", None)
    monkeypatch.setattr("factorlab.app.memory.apply_address_space_limit",
                        lambda b, **kw: None)

    calls: list = []

    def fake_guard(argv, *, wait=False):
        calls.append(("guard", list(argv), wait))
        return ({"FACTORLAB_MAX_MEMORY": "8GB",
                 "FACTORLAB_MIN_AVAILABLE_MEMORY": "6GB",
                 "OMP_NUM_THREADS": "8", "POLARS_MAX_THREADS": "8"},
                tmp_path / "heavy.1.lock")

    monkeypatch.setattr(F, "guard_heavy", fake_guard)
    monkeypatch.setattr(F, "release_slots", lambda: calls.append(("release",)))

    # spy：证明闸 env 真注入到 run 执行期（不是"只返回不动"）
    from factorlab.app import run as app_run
    real_run = app_run.run_factor
    seen: dict = {}

    def spy(spec, ctx):
        seen["max_memory"] = settings.max_memory
        seen["min_available"] = settings.min_available_memory
        seen["omp"] = os.environ.get("OMP_NUM_THREADS")
        return real_run(spec, ctx)

    monkeypatch.setattr(app_run, "run_factor", spy)

    t0 = time.time()
    env = F.factor_run(_run_args(spec_path))

    assert env.ok, env.error
    assert calls[0][0] == "guard"
    assert calls[0][1][:2] == ["factor", "run"] and str(spec_path) in calls[0][1]
    assert ("release",) in calls
    # 闸 env 真进入 run 执行期（run 内解析为字节；本机默认化不会是 8GB/6GB——注入可证）
    assert seen == {"max_memory": 8 * 1024**3, "min_available": 6 * 1024**3,
                    "omp": "8"}
    assert settings.max_memory is None and settings.min_available_memory is None

    # 新产物（硬编码/旧数据必败：mtime + 真 IC）
    run_dir = Path(env.artifacts["run_dir"])
    summary_path = Path(env.artifacts["summary"])
    log_path = Path(env.artifacts["log"])
    assert run_dir == tmp_path / "results" / "demo"
    assert summary_path.exists() and log_path.exists()
    assert summary_path.stat().st_mtime >= t0 - 1
    assert "demo" in log_path.read_text(encoding="utf-8")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["evaluation"]["version"] == 2
    ev = env.data["evaluation"]
    assert ev["version"] == 2 and ev["frequency"] == "daily"
    assert ev["ic"]["mean"] == pytest.approx(1.0, abs=1e-9)
    for key in ("decile_spread", "turnover", "coverage", "ic_decay"):
        assert key in ev
    _strict_json(env)


@pytest.mark.parametrize("code", ["BUSY", "MEMORY_GUARD"])
def test_factor_run_guard_error_maps_to_envelope(tmp_path, monkeypatch, code):
    def fake_guard(argv, *, wait=False):
        raise GuardError(code, f"{code} 测试", hint="hint-x")

    monkeypatch.setattr(F, "guard_heavy", fake_guard)
    env = F.factor_run(_run_args(tmp_path / "x.yaml"))
    assert env.ok is False
    assert env.error["code"] == code
    assert env.error["hint"] == "hint-x"
    _strict_json(env)


def test_factor_run_real_guard_acquires_and_releases_slot(tmp_path, monkeypatch):
    """真闸（非 mock）：槽文件落 `FACTORLAB_HEAVY_LOCK_DIR`，跑完释放。"""
    from test_run_factor import build_db
    from factorlab.research import _guard
    build_db(tmp_path, n_days=9)
    spec_path = tmp_path / "demo.yaml"
    spec_path.write_text(_SPEC, encoding="utf-8")
    monkeypatch.setattr(settings, "platform_db", tmp_path / "q.duckdb")
    monkeypatch.setattr(settings, "results_dir", tmp_path / "results")
    monkeypatch.setattr(settings, "max_memory", None)
    monkeypatch.setattr(settings, "min_available_memory", None)
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("FACTORLAB_HEAVY_LOCK_DIR", str(lock_dir))
    monkeypatch.setenv("FACTORLAB_GUARD_NICE", "0")
    monkeypatch.setattr(_guard.psutil, "virtual_memory",
                        lambda: SimpleNamespace(available=64 * 1024**3,
                                                total=64 * 1024**3))
    monkeypatch.setattr("factorlab.app.memory.apply_address_space_limit",
                        lambda b, **kw: None)

    env = F.factor_run(_run_args(spec_path))
    assert env.ok, env.error
    assert (lock_dir / "heavy.1.lock").exists()
    assert _guard._HELD == []  # 真释放（否则后续重任务 BUSY）


def test_factor_run_missing_spec_is_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "guard_heavy",
                        lambda argv, wait=False: ({}, tmp_path / "l"))
    monkeypatch.setattr(F, "release_slots", lambda: None)
    env = F.factor_run(_run_args(tmp_path / "nope.yaml"))
    assert env.ok is False
    assert env.error["code"] == "NOT_FOUND"


# ================================================================
# factor lint / list / show
# ================================================================

def test_factor_lint_ok_and_lint_error(tmp_path):
    good = _admit_spec(tmp_path, name="demo", formula="signal = close / open - 1")
    env = F.factor_lint(_args(spec_paths=[good], all=False, strategy=False))
    assert env.ok, env.error
    assert env.data["results"][0]["name"] == "demo"

    bad = _admit_spec(tmp_path, name="bad", formula="signal = no_such_op_zzz(close)")
    env_bad = F.factor_lint(_args(spec_paths=[bad], all=False, strategy=False))
    assert env_bad.ok is False
    assert env_bad.error["code"] == "LINT"
    assert "bad.yaml" in env_bad.error["message"]
    _strict_json(env_bad)


def test_factor_list_reflects_results_dir_content(tmp_path, monkeypatch):
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    _write_summary(rd, "alpha", ic=0.05, ts="2026-08-16T12:00:00")
    env = F.factor_list(_args())
    assert env.ok
    assert [r["name"] for r in env.data["factors"]] == ["alpha"]

    _write_summary(rd, "beta", ic=0.07, ts="2026-08-17T12:00:00")
    env2 = F.factor_list(_args())
    assert [r["name"] for r in env2.data["factors"]] == ["beta", "alpha"]
    assert env2.data["factors"][0]["ic_mean"] == pytest.approx(0.07)


def test_factor_show_reads_summary_and_missing_is_not_found(tmp_path, monkeypatch):
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    _write_summary(rd, "alpha")
    env = F.factor_show(_args(name="alpha"))
    assert env.ok
    assert env.data["name"] == "alpha"
    assert env.data["summary"]["evaluation"]["ic"]["mean"] == pytest.approx(0.05)

    missing = F.factor_show(_args(name="ghost"))
    assert missing.ok is False
    assert missing.error["code"] == "NOT_FOUND"
    assert missing.error["hint"]
    _strict_json(missing)


def test_factor_show_corrupt_summary_is_data_error(tmp_path, monkeypatch):
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    broken = rd / "broken"
    broken.mkdir(parents=True)
    (broken / "summary.json").write_text("{not json", encoding="utf-8")
    env = F.factor_show(_args(name="broken"))
    assert env.ok is False
    assert env.error["code"] == "DATA"


# ================================================================
# factor export
# ================================================================

@pytest.mark.parametrize("fmt", ["parquet", "csv", "json"])
def test_factor_export_formats(tmp_path, monkeypatch, fmt):
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    _write_panel(rd, "alpha", _tile(_basis(1)[0]))
    out = tmp_path / f"alpha.{fmt}"
    env = F.factor_export(_args(name="alpha", format=fmt, out=out))
    assert env.ok, env.error
    assert Path(env.data["path"]) == out and out.exists()
    assert env.data["n_rows"] == WEEKS * N
    if fmt == "parquet":
        assert pl.read_parquet(out).height == WEEKS * N
    elif fmt == "csv":
        assert pl.read_csv(out).height == WEEKS * N
    else:
        assert json.loads(out.read_text(encoding="utf-8"))


def test_factor_export_missing_is_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "results_dir", tmp_path / "results")
    env = F.factor_export(_args(name="ghost", format="parquet", out=None))
    assert env.ok is False
    assert env.error["code"] == "NOT_FOUND"


# ================================================================
# factor corr / resic / svd（合成面板真实数值）
# ================================================================

def test_factor_corr_real_rank_values(tmp_path, monkeypatch):
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    vs = _basis(2)
    x = _tile(vs[0])
    y = _tile(vs[0]) + 1e-6 * _tile(vs[1])   # 近复制
    z = _tile(vs[1])                          # 独立
    fwd = np.zeros((WEEKS, N))
    for name, sig in (("x", x), ("y", y), ("z", z)):
        _write_panel(rd, name, sig, fwd)
    env = F.factor_corr(_args(names=["x", "y", "z"], against=None,
                              out=None, limit=None, inline=False))
    assert env.ok, env.error

    def pair(a, b):
        return next(r for r in env.data["rows"]
                    if {r["factor_a"], r["factor_b"]} == {a, b})

    assert pair("x", "y")["rank_corr"] > 0.99
    assert abs(pair("x", "z")["rank_corr"]) < 0.5


def test_factor_corr_missing_panel_is_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "results_dir", tmp_path / "results")
    _write_panel(tmp_path / "results", "x", _tile(_basis(1)[0]),
                 np.zeros((WEEKS, N)))
    env = F.factor_corr(_args(names=["x"], against="ghost",
                              out=None, limit=None, inline=False))
    assert env.ok is False
    assert env.error["code"] == "NOT_FOUND"


def test_factor_resic_against_reference_incremental(tmp_path, monkeypatch):
    ref = _fixture_ref(tmp_path)
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    vs = _basis(3)
    rng = np.random.default_rng(5)
    cand = _tile(vs[1])
    fwd = 0.5 * cand + rng.uniform(-20.0, 20.0, size=(WEEKS, N))
    _write_panel(rd, "base_a", _tile(vs[0]), fwd)
    _write_panel(rd, "base_b", _tile(vs[2]), fwd)
    _write_panel(rd, "cand", cand, fwd)

    env = F.factor_resic(_args(names=["cand"], target=None, min_stocks=None,
                               against="reference"))
    assert env.ok, env.error
    assert env.data["base"] == ["base_a", "base_b"]  # minute 不混入
    c = env.data["candidates"][0]
    assert c["name"] == "cand"
    assert c["corr_max"] < 0.7
    assert c["resic_t"] >= 2.0        # 真回归（硬编码 0 必败）
    assert c["verdict"] in ("可加入", "观察", "冗余")
    assert "weekly" not in c          # DataFrame 不得进 JSON
    _strict_json(env)


def test_factor_resic_single_without_target_is_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "results_dir", tmp_path / "results")
    env = F.factor_resic(_args(names=["a"], target=None, min_stocks=None,
                               against=None))
    assert env.ok is False
    assert env.error["code"] == "USAGE"


def test_factor_resic_mutual_mode(tmp_path, monkeypatch):
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    vs = _basis(2)
    fwd = 0.3 * _tile(vs[0])
    _write_panel(rd, "a", _tile(vs[0] + vs[1]), fwd)
    _write_panel(rd, "b", _tile(vs[1]), fwd)
    env = F.factor_resic(_args(names=["a", "b"], target=None, min_stocks=None,
                               against=None))
    assert env.ok, env.error
    assert env.data["mode"] == "mutual"
    assert {f["name"] for f in env.data["factors"]} == {"a", "b"}
    assert "weekly" not in env.data["group"]
    _strict_json(env)


def test_factor_resic_daily_horizon_uses_each_date_and_weekly_remains_default(
        tmp_path, monkeypatch):
    """1d resIC must use daily panels when requested; omitted frequency stays weekly."""
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    dates = [
        datetime.date(2024, 1, 8) + datetime.timedelta(days=i)
        for i in (0, 1, 2, 3, 4, 7, 8, 9, 10, 11)
    ]
    vs = _basis(3)
    base = np.tile(vs[0], (len(dates), 1))
    candidate = np.tile(vs[0] + vs[1], (len(dates), 1))
    forward = np.tile(0.4 * vs[1] + 0.1 * vs[2], (len(dates), 1))
    _write_daily_resic_panel(rd, "base", dates, base, forward)
    _write_daily_resic_panel(rd, "candidate", dates, candidate, forward)

    daily = F.factor_resic(_args(
        names=["base"], target="candidate", against=None, min_stocks=30,
        frequency="daily"))
    assert daily.ok, daily.error
    assert daily.data["frequency"] == "daily"
    assert daily.data["fwd_col"] == "forward_return_1d"
    assert daily.data["factors"][0]["n_periods"] == len(dates)

    # Existing callers without the new options retain the old weekly/5d behavior.
    weekly = F.factor_resic(_args(
        names=["base"], target="candidate", against=None, min_stocks=30))
    assert weekly.ok, weekly.error
    assert weekly.data["frequency"] == "weekly"
    assert weekly.data["fwd_col"] == "forward_return_5d"
    assert weekly.data["factors"][0]["n_weeks"] == 2

    explicit_weekly = F.factor_resic(_args(
        names=["base"], target="candidate", against=None, min_stocks=30,
        frequency="weekly"))
    assert explicit_weekly.ok, explicit_weekly.error
    assert explicit_weekly.data["frequency"] == "weekly"
    assert explicit_weekly.data["fwd_col"] == "forward_return_5d"


def test_factor_resic_explicit_horizon_selects_forward_column(tmp_path, monkeypatch):
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    dates = [datetime.date(2024, 1, 8) + datetime.timedelta(days=i)
             for i in (0, 1, 2, 3, 4)]
    vs = _basis(2)
    forward = np.tile(vs[1], (len(dates), 1))
    _write_daily_resic_panel(rd, "base", dates,
                             np.tile(vs[0], (len(dates), 1)), forward)
    _write_daily_resic_panel(rd, "candidate", dates,
                             np.tile(vs[0] + vs[1], (len(dates), 1)), forward)

    env = F.factor_resic(_args(
        names=["base"], target="candidate", against=None, min_stocks=30,
        frequency="daily", horizon=5, fwd_col=None))
    assert env.ok, env.error
    assert env.data["fwd_col"] == "forward_return_5d"
    assert env.data["factors"][0]["n_periods"] == len(dates)


def test_factor_resic_forward_col_overrides_horizon(tmp_path, monkeypatch):
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    dates = [datetime.date(2024, 1, 8) + datetime.timedelta(days=i)
             for i in (0, 1, 2, 3, 4)]
    vs = _basis(2)
    forward = np.tile(vs[1], (len(dates), 1))
    _write_daily_resic_panel(rd, "base", dates,
                             np.tile(vs[0], (len(dates), 1)), forward)
    _write_daily_resic_panel(rd, "candidate", dates,
                             np.tile(vs[0] + vs[1], (len(dates), 1)), forward)

    env = F.factor_resic(_args(
        names=["base"], target="candidate", against=None, min_stocks=30,
        frequency="daily", horizon=5, fwd_col="forward_return_1d"))
    assert env.ok, env.error
    assert env.data["fwd_col"] == "forward_return_1d"
    assert env.data["factors"][0]["n_periods"] == len(dates)


def test_factor_resic_against_reference_accepts_daily_1d_mode(tmp_path, monkeypatch):
    ref = _fixture_ref(tmp_path)
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    dates = [
        datetime.date(2024, 1, 8) + datetime.timedelta(days=i)
        for i in (0, 1, 2, 3, 4, 7, 8, 9, 10, 11)
    ]
    vs = _basis(3)
    forward = np.tile(0.4 * vs[2] + 0.1 * vs[1], (len(dates), 1))
    for name, signal in (
            ("base_a", np.tile(vs[0], (len(dates), 1))),
            ("base_b", np.tile(vs[1], (len(dates), 1))),
            ("cand", np.tile(vs[2], (len(dates), 1)))):
        _write_daily_resic_panel(rd, name, dates, signal, forward)

    env = F.factor_resic(_args(
        names=["cand"], target=None, min_stocks=30, against="reference",
        frequency="daily"))
    assert env.ok, env.error
    assert env.data["base"] == ["base_a", "base_b"]
    assert env.data["frequency"] == "daily"
    assert env.data["fwd_col"] == "forward_return_1d"
    assert env.data["candidates"][0]["n_periods"] == len(dates)


@pytest.mark.parametrize("kwargs, message", [
    ({"horizon": 0}, "horizon"),
    ({"frequency": "monthly"}, "frequency"),
])
def test_factor_resic_rejects_invalid_horizon_or_frequency(
        tmp_path, monkeypatch, kwargs, message):
    monkeypatch.setattr(settings, "results_dir", tmp_path / "results")
    args = dict(names=["a"], target="b", against=None, min_stocks=None,
                frequency="weekly", horizon=None, fwd_col=None)
    args.update(kwargs)
    env = F.factor_resic(_args(**args))
    assert not env.ok
    assert env.error["code"] == "USAGE"
    assert message in env.error["message"]


def test_factor_resic_rejects_missing_forward_column(tmp_path, monkeypatch):
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    _write_panel(rd, "base", _tile(_basis(1)[0]), np.zeros((WEEKS, N)),
                 include_daily_label=False)
    _write_panel(rd, "candidate", _tile(_basis(1)[0]), np.zeros((WEEKS, N)),
                 include_daily_label=False)
    env = F.factor_resic(_args(
        names=["base"], target="candidate", against=None, min_stocks=None,
        frequency="daily", horizon=None, fwd_col="forward_return_1d"))
    assert not env.ok
    assert env.error["code"] == "DATA"
    assert "forward_return_1d" in env.error["message"]


def test_factor_svd_reference_default_and_all(tmp_path, monkeypatch):
    ref = _fixture_ref(tmp_path)
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    vs = _basis(3)
    for name, v in (("base_a", vs[0]), ("base_b", vs[1]), ("extra", vs[2])):
        _write_panel(rd, name, _tile(v), np.zeros((WEEKS, N)))

    env = F.factor_svd(_args(names=[], all=False, weeks=15))
    assert env.ok, env.error
    assert env.data["source"] == "参考库 daily"
    assert {x["name"] for x in env.data["loadings"]} == {"base_a", "base_b"}
    assert len(env.data["singular_values"]) >= 1

    env_all = F.factor_svd(_args(names=[], all=True, weeks=15))
    assert env_all.ok
    assert "extra" in {x["name"] for x in env_all.data["loadings"]}


# ================================================================
# factor admit：三判决（合成面板真实回归）
# ================================================================

def _admit_setup(tmp_path, monkeypatch, *, cand_signal, fwd, vs):
    ref = _fixture_ref(tmp_path)
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    _write_panel(rd, "base_a", _tile(vs[0]), fwd)
    _write_panel(rd, "base_b", _tile(vs[2]), fwd)
    _write_panel(rd, "min_x", _tile(vs[3]), fwd)  # minute 成员不得进 daily 对照
    _write_panel(rd, "cand", cand_signal, fwd)
    _write_summary(rd, "cand")
    return _admit_spec(tmp_path)


@pytest.mark.parametrize(
    "interface, requested_scales, expected_scales",
    [
        (None, None, "daily"),
        ("daily", None, "daily"),
        ("bars_1m", None, "minute"),
        ("bars_1m", "daily", "daily"),
        ("daily", "minute", "minute"),
    ],
)
def test_factor_admit_scales_follow_spec_interface_unless_explicit(
        tmp_path, monkeypatch, interface, requested_scales, expected_scales):
    """Scale inference must not enter the lockbox final-test lane in this test."""
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(_fixture_ref(tmp_path)))
    spec = _admit_spec(tmp_path, name="admit_scale_probe", interface=interface)
    called = {}

    def fake_final_test_gate(spec_doc, spec_path, *, reason, command,
                             scales, base, wait):
        called["scales"] = scales
        called["base"] = base
        return {
            "diagnostics": {
                "corr_max": 0.1, "r2_lib": 0.1,
                "resic_t": 3.0, "resic_mean": 0.1, "n_weeks": 4,
            },
            "executed": False,
            "path": str(tmp_path / "test_diagnostics.json"),
        }

    monkeypatch.setattr(F, "_final_test_gate", fake_final_test_gate)
    env = F.factor_admit(_admit_args(spec, scales=requested_scales))
    assert env.ok, env.error
    assert called["scales"] == expected_scales
    assert env.data["scales"] == expected_scales
    expected_base = ["min_x"] if expected_scales == "minute" else ["base_a", "base_b"]
    assert called["base"] == expected_base


def test_factor_admit_independent_is_can_join(tmp_path, monkeypatch):
    vs = _basis(4)
    rng = np.random.default_rng(11)
    cand = _tile(vs[1])
    fwd = 0.5 * cand + rng.uniform(-20.0, 20.0, size=(WEEKS, N))
    spec = _admit_setup(tmp_path, monkeypatch, cand_signal=cand, fwd=fwd, vs=vs)

    env = F.factor_admit(_admit_args(spec))
    assert env.ok, env.error
    assert env.data["ran"] is False
    assert env.data["base"] == ["base_a", "base_b"]
    assert env.data["verdict"] == "可加入"
    assert env.data["corr_max"] < 0.7
    assert env.data["r2_lib"] < 0.8
    assert env.data["resic"]["t"] >= 3.0
    assert env.data["建议"]
    _strict_json(env)


@pytest.mark.parametrize(
    "resic_t, expected",
    [
        (2.999, "观察"),
        (3.0, "可加入"),
        (-3.0, "可加入"),
        (float("nan"), "观察"),
        (float("inf"), "观察"),
        (float("-inf"), "观察"),
    ],
)
def test_admit_verdict_uses_absolute_admission_floor(resic_t, expected):
    assert F._admit_verdict(corr_max=0.1, r2_lib=0.1,
                            resic_t=resic_t, retention=0.8) == expected


@pytest.mark.parametrize(
    "field, diagnostics",
    [
        ("corr_max", {"corr_max": True, "r2_lib": 0.1,
                      "resic_t": 3.0, "retention": 0.8}),
        ("r2_lib", {"corr_max": 0.1, "r2_lib": True,
                    "resic_t": 3.0, "retention": 0.8}),
        ("resic_t", {"corr_max": 0.1, "r2_lib": 0.1,
                     "resic_t": True, "retention": 0.8}),
        ("retention", {"corr_max": 0.1, "r2_lib": 0.1,
                       "resic_t": 3.0, "retention": True}),
    ],
)
def test_admit_verdict_rejects_boolean_diagnostics(field, diagnostics):
    assert F._admit_verdict(**diagnostics) == "观察", field


@pytest.mark.parametrize(
    "corr_max, retention, expected",
    [
        (0.8, 0.8, "观察"),
        (0.1, 0.49, "观察"),
    ],
)
def test_admit_requires_d10_independence_and_retention(
        corr_max, retention, expected):
    assert F._admit_verdict(corr_max=corr_max, r2_lib=0.1,
                            resic_t=3.0, retention=retention) == expected


def test_factor_admit_reports_watch_below_reference_admission_floor(
        tmp_path, monkeypatch):
    spec = _admit_spec(tmp_path)
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(_fixture_ref(tmp_path)))
    monkeypatch.setattr(settings, "results_dir", tmp_path / "results")

    def fake_final_test_gate(*args, **kwargs):
        return {
            "diagnostics": {
                "corr_max": 0.1, "r2_lib": 0.1, "resic_t": 2.999,
                "resic_mean": 0.01, "n_weeks": 8, "retention": 0.8,
            },
            "executed": False,
            "path": str(tmp_path / "test_diagnostics.json"),
        }

    monkeypatch.setattr(F, "_final_test_gate", fake_final_test_gate)
    env = F.factor_admit(_admit_args(spec))
    assert env.ok, env.error
    assert env.data["verdict"] == "观察"


def test_factor_admit_near_relative_is_redundant(tmp_path, monkeypatch):
    vs = _basis(4)
    rng = np.random.default_rng(3)
    cand = _tile(0.93 * vs[0] + 0.368 * vs[3])   # corr≈0.93 → r2_lib 高、残差无增量
    fwd = 0.5 * _tile(vs[0]) + rng.uniform(-0.02, 0.02, size=(WEEKS, N))
    spec = _admit_setup(tmp_path, monkeypatch, cand_signal=cand, fwd=fwd, vs=vs)

    env = F.factor_admit(_admit_args(spec))
    assert env.ok, env.error
    assert env.data["r2_lib"] >= 0.8
    assert env.data["corr_max"] < 0.95
    assert env.data["verdict"] == "冗余"


def test_factor_admit_near_duplicate_is_duplicate(tmp_path, monkeypatch):
    vs = _basis(4)
    cand = _tile(vs[0]) + 1e-6 * _tile(vs[3])    # corr≈1 → 重复
    fwd = 0.5 * _tile(vs[0])
    spec = _admit_setup(tmp_path, monkeypatch, cand_signal=cand, fwd=fwd, vs=vs)

    env = F.factor_admit(_admit_args(spec))
    assert env.ok, env.error
    assert env.data["corr_max"] >= 0.95
    assert env.data["verdict"] == "重复"
    assert env.data["建议"]


def test_factor_admit_bad_spec_is_lint(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(_fixture_ref(tmp_path)))
    monkeypatch.setattr(settings, "results_dir", tmp_path / "results")
    bad = _admit_spec(tmp_path, name="bad", formula="signal = no_such_op_zzz(close)")
    env = F.factor_admit(_admit_args(bad))
    assert env.ok is False
    assert env.error["code"] == "LINT"


def test_factor_admit_runs_when_artifact_missing(tmp_path, monkeypatch):
    vs = _basis(4)
    rng = np.random.default_rng(11)
    fwd = 0.5 * _tile(vs[1]) + rng.uniform(-20.0, 20.0, size=(WEEKS, N))
    ref = _fixture_ref(tmp_path)
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    _write_panel(rd, "base_a", _tile(vs[0]), fwd)
    _write_panel(rd, "base_b", _tile(vs[2]), fwd)
    spec = _admit_spec(tmp_path)  # cand 产物缺失

    called: list = []

    def fake_run(run_args):
        called.append(run_args.spec_path)
        _write_panel(rd, "cand", _tile(vs[1]), fwd)
        _write_summary(rd, "cand")
        return envelope.ok("factor.run", {"name": "cand"})

    monkeypatch.setattr(F, "factor_run", fake_run)
    env = F.factor_admit(_admit_args(spec))
    assert called == [spec]
    assert env.ok, env.error
    assert env.data["ran"] is True


def test_factor_admit_lockbox_off_uses_spec_cadence_and_target(
        tmp_path, monkeypatch):
    """关闭锁箱时，准入诊断仍须遵循候选 spec 的 weekly/target 口径。"""
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "off")
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(_fixture_ref(tmp_path)))
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    vs = _basis(3)
    fwd = np.tile(vs[1], (WEEKS, 1))
    for name, signal in (
            ("base_a", _tile(vs[0])),
            ("base_b", _tile(vs[2])),
            ("cand", _tile(vs[1]))):
        _write_panel(rd, name, signal, fwd)
    _write_summary(rd, "cand")
    spec = _admit_spec(
        tmp_path, target="forward_return_20d", evaluation_frequency="weekly")

    seen: dict[str, object] = {}

    def fake_diag(candidates, results_dir, base, **kwargs):
        seen.update(candidates=list(candidates), results_dir=Path(results_dir),
                    base=list(base), **kwargs)
        return {"kind": "incremental", "base": list(base), "candidates": [{
            "name": candidates[0], "base": list(base), "corr_max": 0.1,
            "r2_lib": 0.1, "resic_t": 3.2, "resic_mean": 0.2,
            "n_weeks": 8, "retention": 0.8, "verdict": "可加入",
        }]}

    monkeypatch.setattr(
        "factorlab.app.analysis.cross_section.incremental_diagnostics", fake_diag)

    env = F.factor_admit(_admit_args(spec))

    assert env.ok, env.error
    assert env.data["diagnostic_frequency"] == "weekly"
    assert env.data["diagnostic_fwd_col"] == "forward_return_20d"
    assert seen["candidates"] == ["cand"]
    assert seen["base"] == ["base_a", "base_b"]
    assert seen["frequency"] == "weekly"
    assert seen["fwd_col"] == "forward_return_20d"


# ================================================================
# factor ref list/add/remove（安全写）
# ================================================================

_REF_YAML = """\
# 参考库头注释（安全写必须保留）
updated: "2024-01-01"
scales:
  daily:
    - name: base_a
      style: "风格A"
      reason: "理由A"
      added: "2024-01-01"
  minute:
    - name: min_x
      style: "风格M"
      reason: "理由M"
      added: "2024-01-01"
"""


def test_factor_ref_list_entries(tmp_path, monkeypatch):
    ref = tmp_path / "_reference.yaml"
    ref.write_text(_REF_YAML, encoding="utf-8")
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    env = F.factor_ref_list(_args(scales=None))
    assert env.ok, env.error
    assert env.data["path"] == str(ref)
    assert [e["name"] for e in env.data["scales"]["daily"]] == ["base_a"]
    assert [e["name"] for e in env.data["scales"]["minute"]] == ["min_x"]


def test_factor_ref_add_backup_comment_and_roundtrip(tmp_path, monkeypatch):
    from factorlab.app.analysis.reference import load_reference
    from factorlab.research import registry
    ref = tmp_path / "_reference.yaml"
    ref.write_text(_REF_YAML, encoding="utf-8")
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    vs = _basis(3)
    cand = _tile(vs[1])
    rng = np.random.default_rng(11)
    fwd = 0.5 * cand + rng.uniform(-20.0, 20.0, size=(WEEKS, N))
    _write_panel(rd, "base_a", _tile(vs[0]), fwd)
    _write_panel(rd, "new_f", cand, fwd)
    from factorlab.app.analysis.cross_section import incremental_diagnostics
    expected = incremental_diagnostics(["new_f"], rd, base=["base_a"])["candidates"][0]
    ns = registry.build_parser(registry.COMMANDS["factor.ref.add"]).parse_args([
        "new_f", "--style", "新风格", "--reason", "入选理由",
        "--added", "2026-09-25", "--entry-corr-max", "0.31",
        "--entry-resic-t", "99.0"])
    env = F.factor_ref_add(ns)
    assert env.ok, env.error
    assert env.data["name"] == "new_f" and env.data["scales"] == "daily"

    backup = Path(env.data["backup"])
    assert backup.exists() and backup.read_text(encoding="utf-8") == _REF_YAML
    text = ref.read_text(encoding="utf-8")
    assert "# 参考库头注释" in text          # 注释保留（整库 dump 会丢）
    ref_map = load_reference(ref)            # 写后真读回（校验通过）
    assert [e.name for e in ref_map["daily"]] == ["base_a", "new_f"]
    e = ref_map["daily"][1]
    assert e.style == "新风格" and e.reason == "入选理由"
    assert e.added == "2026-09-25"
    assert e.entry_corr_max == pytest.approx(expected["corr_max"])
    assert e.entry_resic_t == pytest.approx(expected["resic_t"])
    assert e.entry_resic_t >= 3.0
    assert [x.name for x in ref_map["minute"]] == ["min_x"]


def test_factor_ref_add_lockbox_off_uses_spec_cadence_and_target(
        tmp_path, monkeypatch):
    """lockbox-off ref add 也不能退回 weekly/5d 默认诊断。"""
    from factorlab import config
    from factorlab.app.analysis import reference
    from factorlab.research import registry

    monkeypatch.setenv("FACTORLAB_LOCKBOX", "off")
    ref = _fixture_ref(tmp_path)
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    qr = tmp_path / "qr"
    spec_dir = qr / "factor" / "demo"
    spec_dir.mkdir(parents=True)
    spec = _admit_spec(
        tmp_path, name="weekly_cand", target="forward_return_20d",
        evaluation_frequency="weekly")
    canonical = spec_dir / "weekly_cand.yaml"
    canonical.write_text(spec.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(config.settings, "research_root", qr)
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    vs = _basis(3)
    fwd = np.tile(vs[1], (WEEKS, 1))
    _write_panel(rd, "base_a", _tile(vs[0]), fwd)
    _write_panel(rd, "base_b", _tile(vs[2]), fwd)
    _write_panel(rd, "weekly_cand", _tile(vs[1]), fwd)

    seen: dict[str, object] = {}

    def fake_diag(candidates, results_dir, base, **kwargs):
        seen.update(candidates=list(candidates), results_dir=Path(results_dir),
                    base=list(base), **kwargs)
        return {"kind": "incremental", "base": list(base), "candidates": [{
            "name": candidates[0], "base": list(base), "corr_max": 0.1,
            "r2_lib": 0.1, "resic_t": 3.2, "resic_mean": 0.2,
            "n_weeks": 8, "retention": 0.8, "verdict": "可加入",
        }]}

    monkeypatch.setattr(
        "factorlab.app.analysis.cross_section.incremental_diagnostics", fake_diag)
    ns = registry.build_parser(registry.COMMANDS["factor.ref.add"]).parse_args([
        "weekly_cand", "--style", "新风格", "--reason", "入选理由"])

    env = F.factor_ref_add(ns)

    assert env.ok, env.error
    assert seen["candidates"] == ["weekly_cand"]
    assert seen["base"] == ["base_a", "base_b"]
    assert seen["frequency"] == "weekly"
    assert seen["fwd_col"] == "forward_return_20d"
    assert reference.load_reference(ref)["daily"][-1].name == "weekly_cand"


@pytest.mark.parametrize(
    "requested_scales, expected_scales",
    [
        (None, "minute"),
        ("daily", "daily"),
    ],
)
def test_factor_ref_add_scale_follows_spec_interface_unless_explicit(
        tmp_path, monkeypatch, requested_scales, expected_scales):
    from factorlab import config
    from factorlab.app.analysis import reference
    from factorlab.research import registry

    monkeypatch.setenv("FACTORLAB_LOCKBOX", "on")
    ref = _fixture_ref(tmp_path)
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    qr = tmp_path / "qr"
    spec_dir = qr / "factor" / "demo"
    spec_dir.mkdir(parents=True)
    spec = _admit_spec(tmp_path, name="minute_cand", interface="bars_1m")
    (spec_dir / "minute_cand.yaml").write_text(
        spec.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(config.settings, "research_root", qr)

    seen = {}

    def fake_final_test_gate(spec_doc, spec_path, *, reason, command,
                             scales, base, wait=False):
        seen.update(scales=scales, base=base)
        return {
            "diagnostics": {
                "corr_max": 0.1, "r2_lib": 0.1, "resic_t": 3.2,
                "retention": 0.8,
            },
            "executed": False,
            "path": str(tmp_path / "test_diagnostics.json"),
        }

    monkeypatch.setattr(F, "_final_test_gate", fake_final_test_gate)
    parser = registry.build_parser(registry.COMMANDS["factor.ref.add"])
    argv = ["minute_cand", "--style", "分钟", "--reason", "独立候选"]
    if requested_scales is not None:
        argv.extend(["--scales", requested_scales])
    ns = parser.parse_args(argv)

    env = F.factor_ref_add(ns)

    assert env.ok, env.error
    assert seen["scales"] == expected_scales
    expected_base = ["min_x"] if expected_scales == "minute" else ["base_a", "base_b"]
    assert seen["base"] == expected_base
    assert env.data["scales"] == expected_scales
    assert reference.load_reference(ref)[expected_scales][-1].name == "minute_cand"


def test_factor_ref_add_can_initialize_empty_scale_as_seed(
        tmp_path, monkeypatch):
    from factorlab.app.analysis.reference import load_reference
    from factorlab.research import registry

    ref = tmp_path / "_reference.yaml"
    ref.write_text(
        'updated: "2024-01-01"\n'
        "scales:\n"
        "  daily:\n"
        "    - name: base_a\n"
        "      style: 日线\n"
        "      reason: fixture\n"
        '      added: "2024-01-01"\n'
        "  minute: [] # empty group\n",
        encoding="utf-8")
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    parser = registry.build_parser(registry.COMMANDS["factor.ref.add"])
    ns = parser.parse_args([
        "minute_seed", "--scales", "minute", "--style", "分钟种子",
        "--reason", "初始化分钟参考组",
        "--entry-corr-max", "0.99", "--entry-resic-t", "99"])

    env = F.factor_ref_add(ns)

    assert env.ok, env.error
    assert env.data["verdict"] == "种子"
    minute = load_reference(ref)["minute"]
    assert [entry.name for entry in minute] == ["minute_seed"]
    assert minute[0].entry_corr_max is None
    assert minute[0].entry_resic_t is None
    assert "# empty group" in ref.read_text(encoding="utf-8")


def test_factor_ref_add_rejects_bad_scale_and_duplicate(tmp_path, monkeypatch):
    from factorlab.app.analysis.reference import load_reference
    from factorlab.research import registry
    ref = tmp_path / "_reference.yaml"
    ref.write_text(_REF_YAML, encoding="utf-8")
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    parser = registry.build_parser(registry.COMMANDS["factor.ref.add"])

    bad = parser.parse_args(["x", "--style", "s", "--reason", "r",
                             "--scales", "weekly"])
    env_bad = F.factor_ref_add(bad)
    assert env_bad.ok is False
    assert env_bad.error["code"] == "LINT"

    dup = parser.parse_args(["base_a", "--style", "s", "--reason", "r"])
    env_dup = F.factor_ref_add(dup)
    assert env_dup.ok is False
    assert env_dup.error["code"] == "LINT"
    assert ref.read_text(encoding="utf-8") == _REF_YAML  # 拒绝路径零写入
    assert not (tmp_path / "_reference.yaml.bak").exists()
    assert [e.name for e in load_reference(ref)["daily"]] == ["base_a"]


def test_factor_ref_remove_and_missing(tmp_path, monkeypatch):
    from factorlab.app.analysis.reference import load_reference
    from factorlab.research import registry
    ref = tmp_path / "_reference.yaml"
    ref.write_text(_REF_YAML, encoding="utf-8")
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    parser = registry.build_parser(registry.COMMANDS["factor.ref.remove"])

    env = F.factor_ref_remove(parser.parse_args(["base_a"]))
    assert env.ok, env.error
    assert Path(env.data["backup"]).exists()
    ref_map = load_reference(ref)
    assert ref_map["daily"] == []
    assert [x.name for x in ref_map["minute"]] == ["min_x"]
    assert "# 参考库头注释" in ref.read_text(encoding="utf-8")

    missing = F.factor_ref_remove(parser.parse_args(["ghost"]))
    assert missing.ok is False
    assert missing.error["code"] == "NOT_FOUND"


# ================================================================
# factor op / catalog
# ================================================================

_PLUGIN_SRC = '''
import polars as pl
from factorlab.core.ops.registry import factor_op


@factor_op("ts_tail_ratio", kind="ts", version="0.1.0")
def ts_tail_ratio(x: pl.Expr, n: int) -> pl.Expr:
    """(90 分位 − 10 分位) / 标准差。"""
    spread = x.rolling_quantile(0.9, window_size=n) - x.rolling_quantile(0.1, window_size=n)
    return spread / x.rolling_std(window_size=n)
'''


def test_factor_op_list_and_doc(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "plugin_dir", tmp_path / "plugins")
    env = F.factor_op_list(_args(catalog=False))
    assert env.ok, env.error
    names = {r["name"] for r in env.data["ops"]}
    assert {"ts_mean", "cs_demean", "ts_delay"} <= names

    catalog = F.factor_op_list(_args(catalog=True))
    assert catalog.ok
    cat_names = {r["name"] for r in catalog.data["ops"]}
    assert "BBANDS" in cat_names and len(cat_names) > 100

    doc = F.factor_op_doc(_args(name="ts_mean"))
    assert doc.ok and doc.data["name"] == "ts_mean" and doc.data["doc"]

    missing = F.factor_op_doc(_args(name="totally_missing_op"))
    assert missing.ok is False and missing.error["code"] == "NOT_FOUND"


def test_factor_op_add_and_remove_real_plugin(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    monkeypatch.setattr(settings, "plugin_dir", plugin_dir)
    src = tmp_path / "myops.py"
    src.write_text(_PLUGIN_SRC, encoding="utf-8")

    env = F.factor_op_add(_args(path=src, force=False))
    assert env.ok, env.error
    assert env.data["registered"] == ["ts_tail_ratio"]
    assert (plugin_dir / "myops.py").exists()   # 真拷贝（存根必败）

    doc = F.factor_op_doc(_args(name="ts_tail_ratio"))
    assert doc.ok and doc.data["kind"] == "ts"

    rm = F.factor_op_remove(_args(name="ts_tail_ratio"))
    assert rm.ok and rm.data["disabled"] == "ts_tail_ratio"
    missing = F.factor_op_remove(_args(name="ghost_op"))
    assert missing.ok is False and missing.error["code"] == "NOT_FOUND"


def test_factor_catalog_json_markdown_and_out(tmp_path):
    env = F.factor_catalog(_args(format="json", out=None))
    assert env.ok, env.error
    assert env.data["catalog"]["schema_version"]
    assert "open_surface" in env.data["catalog"]

    md = F.factor_catalog(_args(format="markdown", out=None))
    assert md.ok and "# FactorLab" in md.data["markdown"]

    out = tmp_path / "catalog.json"
    env_out = F.factor_catalog(_args(format="json", out=out))
    assert env_out.ok and out.exists()
    assert env_out.data["path"] == str(out)


# ================================================================
# registry / CLI 契约
# ================================================================

def test_factor_commands_registered_with_schemas():
    from factorlab.research import registry
    assert FACTOR_COMMANDS <= set(registry.COMMANDS)
    for name in sorted(FACTOR_COMMANDS):
        doc = registry.COMMANDS[name].to_doc()
        assert doc["description"], name
        assert doc["examples"], name
        assert doc["output_schema"], name
    resic = registry.COMMANDS["factor.resic"].to_doc()
    assert {p["name"] for p in resic["params"]} >= {
        "frequency", "horizon", "fwd_col"}
    assert resic["defaults"]["frequency"] is None
    admit = registry.COMMANDS["factor.admit"].to_doc()
    assert admit["defaults"]["scales"] is None
    assert "bars_1m" in admit["description"]


def test_factor_run_positional_and_flags_parse():
    from factorlab.research import registry
    parser = registry.build_parser(registry.COMMANDS["factor.run"])
    ns = parser.parse_args(["x.yaml", "--no-backtest", "--wait", "--groups", "5"])
    assert str(ns.spec_path) == "x.yaml"
    assert ns.no_backtest is True and ns.wait is True and ns.groups == 5


def test_cli_research_factor_lint_single_json(tmp_path):
    spec = _admit_spec(tmp_path, name="demo", formula="signal = close / open - 1")
    result = runner.invoke(cli_app, ["research", "factor", "lint", str(spec), "--json"])
    assert result.exit_code == 0, result.output
    assert len(result.stdout.strip().splitlines()) == 1
    doc = json.loads(result.stdout)
    assert doc["ok"] is True and doc["command"] == "factor.lint"
    assert doc["data"]["results"][0]["name"] == "demo"


def test_cli_research_factor_lint_bad_exit_code_3(tmp_path):
    spec = _admit_spec(tmp_path, name="bad", formula="signal = no_such_op_zzz(close)")
    result = runner.invoke(cli_app, ["research", "factor", "lint", str(spec), "--json"])
    assert result.exit_code == EXIT_CODES["LINT"] == 3
    assert json.loads(result.stdout)["error"]["code"] == "LINT"


def test_cli_research_factor_show_missing_exit_code_9(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "results_dir", tmp_path / "results")
    result = runner.invoke(cli_app, ["research", "factor", "show", "ghost", "--json"])
    assert result.exit_code == EXIT_CODES["NOT_FOUND"] == 9
    doc = json.loads(result.stdout)
    assert doc["ok"] is False and doc["error"]["code"] == "NOT_FOUND"


def test_cli_research_factor_show_missing_positional_is_usage():
    result = runner.invoke(cli_app, ["research", "factor", "show", "--json"])
    assert result.exit_code == EXIT_CODES["USAGE"]
    assert json.loads(result.stdout)["error"]["code"] == "USAGE"


def test_cli_research_factor_resic_daily_flags_and_self_description(
        tmp_path, monkeypatch):
    rd = tmp_path / "results"
    monkeypatch.setattr(settings, "results_dir", rd)
    dates = [
        datetime.date(2024, 1, 8) + datetime.timedelta(days=i)
        for i in (0, 1, 2, 3, 4, 7, 8, 9, 10, 11)
    ]
    vs = _basis(2)
    forward = np.tile(vs[1], (len(dates), 1))
    _write_daily_resic_panel(rd, "base", dates,
                             np.tile(vs[0], (len(dates), 1)), forward)
    _write_daily_resic_panel(rd, "candidate", dates,
                             np.tile(vs[0] + vs[1], (len(dates), 1)), forward)

    result = runner.invoke(cli_app, [
        "research", "factor", "resic", "base", "--target", "candidate",
        "--frequency", "daily", "--json",
    ])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["data"]["frequency"] == "daily"
    assert doc["data"]["fwd_col"] == "forward_return_1d"
    assert doc["data"]["factors"][0]["n_periods"] == len(dates)

    describe = runner.invoke(cli_app, [
        "research", "describe", "--json", "--command", "factor.resic"])
    assert describe.exit_code == 0, describe.output
    described = json.loads(describe.stdout)["data"]
    assert {p["name"] for p in described["params"]} >= {
        "frequency", "horizon", "fwd_col"}
    assert described["defaults"]["frequency"] is None


def test_cli_research_factor_admit_help_and_describe_scale_autodetect():
    from factorlab.research import registry

    parser = registry.build_parser(registry.COMMANDS["factor.admit"])
    assert parser.parse_args(["candidate.yaml"]).scales is None
    assert parser.parse_args(
        ["candidate.yaml", "--scales", "minute"]).scales == "minute"

    help_result = runner.invoke(cli_app, [
        "research", "factor", "admit", "--help"])
    assert help_result.exit_code == 0, help_result.output
    assert "bars_1m" in help_result.stdout
    assert "minute" in help_result.stdout
    assert "显式 --scales" in help_result.stdout

    describe = runner.invoke(cli_app, [
        "research", "describe", "--json", "--command", "factor.admit"])
    assert describe.exit_code == 0, describe.output
    doc = json.loads(describe.stdout)["data"]
    assert doc["defaults"]["scales"] is None
    scales = next(p for p in doc["params"] if p["name"] == "scales")
    assert "bars_1m" in scales["help"]
    assert "minute" in scales["help"]


def test_cli_research_factor_ref_add_help_and_describe_scale_autodetect():
    from factorlab.research import registry

    parser = registry.build_parser(registry.COMMANDS["factor.ref.add"])
    assert parser.parse_args([
        "candidate", "--style", "分钟", "--reason", "独立候选"]).scales is None

    help_result = runner.invoke(cli_app, [
        "research", "factor", "ref", "add", "--help"])
    assert help_result.exit_code == 0, help_result.output
    assert "bars_1m" in help_result.stdout
    assert "minute" in help_result.stdout
    assert "显式 --scales" in help_result.stdout

    describe = runner.invoke(cli_app, [
        "research", "describe", "--json", "--command", "factor.ref.add"])
    assert describe.exit_code == 0, describe.output
    doc = json.loads(describe.stdout)["data"]
    assert doc["defaults"]["scales"] is None
    scales = next(p for p in doc["params"] if p["name"] == "scales")
    assert "bars_1m" in scales["help"]
    assert "minute" in scales["help"]


def test_cli_research_describe_factor_run():
    result = runner.invoke(cli_app, ["research", "describe", "--json",
                                     "--command", "factor.run"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"]["name"] == "factor.run"
