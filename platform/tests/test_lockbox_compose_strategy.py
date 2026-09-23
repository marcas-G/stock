"""R42 T2：compose / strategy 锁箱接线（最终测试一次语义 + 参数面收敛）。

断言来源：设计 `2026-09-24-final-test-once-discipline-design.md` §3/§5 + T2 brief：
- compose 有效窗口 = 成员 spec `date` 交集；成员缺 date 或交集为空 → 回退已发布日历；
- strategy 有效窗口 = `doc.date`；`run_strategy` 在信号加载前过 guard；
- host（无 `FACTORLAB_PIPELINE`）碰测试段 → `LOCKBOX_TEST_ONLY_FINAL`（零登记零产物）；
- 流水线标记（`FACTORLAB_PIPELINE=1`）→ 每版本登记 1 行 final；同版本二次拒
  （`LOCKBOX_FINAL_DUPLICATE`）；`FACTORLAB_RE_FINAL=1` 重测（cache 命中也声明本次访问）；
- registry/CLI 不再暴露 `--lockbox/--lockbox-reason`；`LockboxError` → 稳定错误码 + 指引；
- 产物挂 `sample`，`mark_result` 回填 `result_ref`。

真实度：真 SQLite（沙箱 tmp）+ 真 CLI/registry/run 链；成员/信号产物为本地合成
artifact（polars 落盘，无 CH/重链）。strategy 样本声明用例只把 M8 回测边界
（`run_backtest`/`save_backtest_result`）替换为记录桩——guard/窗口过滤/组合/
策略 manifest 全部真跑；compose 用例全链真跑。

墙钟鲁棒：窗口/成员 date 全部从 `dt.date.today()` 派生（`_lockbox` 工具），
不写死年份/季度。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from _lockbox import DAYS, WINDOW as W, WINDOW_ID
from _text import strip_ansi
from typer.testing import CliRunner

import test_composite_runner as fx
from factorlab.adapters import lockbox_store as store
from factorlab.adapters.lockbox_store import connect, roll
from factorlab.config import settings
from factorlab.core.composite import load_composite_spec
from factorlab.core.lockbox import LockboxError
from factorlab.core.strategy import load_strategy_doc
from factorlab.surfaces.cli import main as cli_main
from factorlab.surfaces.cli.main import app
from test_run_strategy import _doc_yaml

runner = CliRunner()
_START = W.start
_END = W.end


@pytest.fixture(autouse=True)
def _lockbox_enabled(monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "1")
    monkeypatch.delenv("FACTORLAB_PIPELINE", raising=False)
    monkeypatch.delenv("FACTORLAB_RE_FINAL", raising=False)


# ================================================================
# 沙箱与合成产物
# ================================================================

def _compose_sandbox(tmp_path: Path, monkeypatch) -> Path:
    """compose 日历 seam（T4 helpers）+ 真 SQLite 状态（as_of=真今天）。"""
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(settings, "lockbox_db", db)
    monkeypatch.setattr(cli_main, "_lockbox_published_days", lambda: list(DAYS))
    monkeypatch.setattr(cli_main, "_lockbox_data_end", lambda: W.end)
    conn = connect(db)
    roll(conn, window=W)
    conn.close()
    return db


def _strategy_sandbox(tmp_path: Path, monkeypatch) -> Path:
    """strategy 日历经 adapters 单点 `run_calendar`（app 层无法依赖 surfaces helper）。"""
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(settings, "lockbox_db", db)
    monkeypatch.setattr(store, "run_calendar", lambda: (list(DAYS), W.end))
    conn = connect(db)
    roll(conn, window=W)
    conn.close()
    return db


def _write_member(runs: Path, name: str,
                  window: tuple[dt.date, dt.date] | None, *,
                  offset: float = 0.0, scale: float = 1.0) -> None:
    """合成 factor 成员 artifact；window 非空时把成员 spec 的 date 写进 spec_yaml。"""
    fx.write_factor(runs, name, offset, scale=scale)
    summary_path = runs / name / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if window is None:
        summary.pop("spec_yaml", None)   # 显式清除（模拟缺 date 成员）
    else:
        start, end = window
        summary["spec_yaml"] = yaml.safe_dump(
            {"name": name, "date": {"start": start.isoformat(),
                                    "end": end.isoformat()}})
    summary_path.write_text(json.dumps(summary, ensure_ascii=False),
                            encoding="utf-8")


def _compose_world(tmp_path: Path, monkeypatch, *,
                   a: tuple[dt.date, dt.date] | None = None,
                   b: tuple[dt.date, dt.date] | None = None):
    runs = tmp_path / "runs"
    _write_member(runs, "factor_A", a, offset=0.0)
    _write_member(runs, "factor_B", b, offset=5.0, scale=2.0)
    entry, _ = fx.write_impl(tmp_path, fx._COMPUTE_BODY)
    spec_path = fx.write_spec(tmp_path, "cx_lock", ["factor_A", "factor_B"], entry)
    db = _compose_sandbox(tmp_path, monkeypatch)
    return spec_path, runs, db


def _rows(db: Path) -> list[dict]:
    conn = connect(db)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM lockbox_access ORDER BY rowid").fetchall()]
    finally:
        conn.close()


def _compose(spec_path: Path, runs: Path, *extra: str):
    return runner.invoke(app, ["compose", str(spec_path), "--results-dir", str(runs),
                               *extra])


def _strategy_doc_file(tmp_path: Path, *, start: dt.date, end: dt.date) -> Path:
    p = tmp_path / "lock_strat.yaml"
    p.write_text(_doc_yaml(name="lock_strat", start=start.isoformat(),
                           end=end.isoformat()), encoding="utf-8")
    return p


# ================================================================
# 成员交集（有效窗口单点）
# ================================================================

def test_compose_effective_window_is_member_intersection(tmp_path, monkeypatch):
    """交集 = max(成员 start) / min(成员 end)，互不相交 → None（回退信号）。"""
    _compose_sandbox(tmp_path, monkeypatch)
    runs = tmp_path / "runs"
    _write_member(runs, "factor_A", (_START - dt.timedelta(days=400),
                                     _START - dt.timedelta(days=100)))
    _write_member(runs, "factor_B", (_START - dt.timedelta(days=100),
                                     _START + dt.timedelta(days=50)))
    entry, _ = fx.write_impl(tmp_path, fx._COMPUTE_BODY)
    spec_path = fx.write_spec(tmp_path, "cx_lock", ["factor_A", "factor_B"], entry)
    spec = load_composite_spec(spec_path)

    effective = cli_main._compose_effective_window(spec, runs)
    assert effective == (_START - dt.timedelta(days=100),
                         _START - dt.timedelta(days=100))

    _write_member(runs, "factor_B", (_START - dt.timedelta(days=500),
                                     _START - dt.timedelta(days=450)))
    assert cli_main._compose_effective_window(spec, runs) is None  # 空交集
    _write_member(runs, "factor_B", None)                          # 缺 date
    assert cli_main._compose_effective_window(spec, runs) is None


def test_compose_member_intersection_is_passes_without_registration(
        tmp_path, monkeypatch):
    """交集整段 < window_start → IS 放行、零登记（union 口径会红）。"""
    spec_path, runs, db = _compose_world(
        tmp_path, monkeypatch,
        a=(_START - dt.timedelta(days=400), _START - dt.timedelta(days=100)),
        b=(_START - dt.timedelta(days=100), _START + dt.timedelta(days=50)))

    result = _compose(spec_path, runs)

    assert result.exit_code == 0, result.output
    assert _rows(db) == []
    summary = json.loads((runs / "composites" / "cx_lock" / "summary.json")
                         .read_text(encoding="utf-8"))
    assert summary["sample"]["role"] == "is"
    assert summary["sample"]["conclusion_eligible"] is True


# ================================================================
# host 碰测试段：拒（零登记零产物）
# ================================================================

def test_compose_host_test_segment_rejected_test_only_final(tmp_path, monkeypatch):
    """有效窗口（交集）跨测试段且无流水线标记 → 稳定错误码 + 零产物/零登记。"""
    spec_path, runs, db = _compose_world(
        tmp_path, monkeypatch,
        a=(_START - dt.timedelta(days=401), _START + dt.timedelta(days=50)),
        b=(_START - dt.timedelta(days=100), _START + dt.timedelta(days=50)))

    result = _compose(spec_path, runs)

    out = strip_ansi(result.output)
    assert result.exit_code != 0
    assert "LOCKBOX_TEST_ONLY_FINAL" in out
    assert "xpipe" in out and "admit" in out
    assert "factorlab lockbox status" in out
    assert "flab lockbox" not in out
    assert _rows(db) == []
    assert not (runs / "composites" / "cx_lock" / "artifact.json").exists()


def test_compose_requires_final_when_member_window_missing(tmp_path, monkeypatch):
    """成员缺 date → 回退 published days min/max（跨窗）→ host 仍拒。"""
    spec_path, runs, db = _compose_world(tmp_path, monkeypatch, a=None, b=None)

    result = _compose(spec_path, runs)

    assert result.exit_code != 0
    assert "LOCKBOX_TEST_ONLY_FINAL" in strip_ansi(result.output)
    assert _rows(db) == []


def test_compose_requires_final_when_member_windows_disjoint(tmp_path, monkeypatch):
    """成员 date 交集为空 → 同样回退全域（不静默取 union/一点）。"""
    spec_path, runs, db = _compose_world(
        tmp_path, monkeypatch,
        a=(_START - dt.timedelta(days=400), _START - dt.timedelta(days=300)),
        b=(_START - dt.timedelta(days=200), _START - dt.timedelta(days=100)))

    result = _compose(spec_path, runs)

    assert result.exit_code != 0
    assert "LOCKBOX_TEST_ONLY_FINAL" in strip_ansi(result.output)
    assert _rows(db) == []


# ================================================================
# 流水线标记：最终测试登记（每版本一次 + re-final 重测）
# ================================================================

def test_compose_pipeline_marker_registers_final_and_persists_sample(
        tmp_path, monkeypatch):
    """marker：登记行（command=compose）+ 盘上 summary.sample + result_ref。"""
    monkeypatch.setenv("FACTORLAB_PIPELINE", "1")
    spec_path, runs, db = _compose_world(
        tmp_path, monkeypatch, a=(_START, _END), b=(_START, _END))

    result = _compose(spec_path, runs)

    assert result.exit_code == 0, result.output
    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert (row["kind"], row["window_id"], row["command"]) == (
        "final", WINDOW_ID, "compose")
    assert row["artifact"] == str(spec_path)
    out_dir = runs / "composites" / "cx_lock"
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["sample"]["access_id"] == row["access_id"]
    assert summary["sample"]["role"] == "lockbox"
    assert summary["sample"]["conclusion_eligible"] is True
    assert row["result_ref"] == str(out_dir)


def test_compose_same_version_second_final_rejected(tmp_path, monkeypatch):
    """同版本第二次最终测试 → `LOCKBOX_FINAL_DUPLICATE`，登记行数不变。"""
    monkeypatch.setenv("FACTORLAB_PIPELINE", "1")
    spec_path, runs, db = _compose_world(
        tmp_path, monkeypatch, a=(_START, _END), b=(_START, _END))

    first = _compose(spec_path, runs)
    assert first.exit_code == 0, first.output
    assert len(_rows(db)) == 1

    second = _compose(spec_path, runs)

    assert second.exit_code != 0
    assert "LOCKBOX_FINAL_DUPLICATE" in strip_ansi(second.output)
    assert len(_rows(db)) == 1


def test_compose_re_final_cache_hit_declares_current_access(tmp_path, monkeypatch):
    """`re-final` 重测（第二次 CLI compose）：登记新行 + cache 命中盘上 sample
    指向本次新登记，且本次回填 result_ref；首次登记行不被改写。整块 cache 分支
    去接线即红（summary 仍是首跑 access_id；随机 ULID 使硬编码必败）。"""
    monkeypatch.setenv("FACTORLAB_PIPELINE", "1")
    spec_path, runs, db = _compose_world(
        tmp_path, monkeypatch, a=(_START, _END), b=(_START, _END))
    out_dir = runs / "composites" / "cx_lock"

    first = _compose(spec_path, runs)
    assert first.exit_code == 0, first.output
    rows_first = _rows(db)
    assert len(rows_first) == 1 and rows_first[0]["kind"] == "final"
    assert rows_first[0]["result_ref"] == str(out_dir)

    monkeypatch.setenv("FACTORLAB_RE_FINAL", "1")
    second = _compose(spec_path, runs)

    assert second.exit_code == 0, second.output
    assert "cached=True" in strip_ansi(second.output)
    rows = _rows(db)
    assert len(rows) == 2
    current = rows[1]["access_id"]
    assert current != rows[0]["access_id"]
    assert rows[1]["reason"].endswith("|re-final")
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["sample"]["access_id"] == current
    assert rows[1]["result_ref"] == str(out_dir)      # cache 路径同样回填本次产物
    assert rows[0]["result_ref"] == str(out_dir)      # 首跑登记行保持原值


def test_compose_nested_composite_member_falls_back_to_full_calendar(
        tmp_path, monkeypatch):
    """成员含 `composites/*`（artifact 无 spec date）→ 有效窗口回退全域
    published days min/max，guard 按全域判角色（错误窗点即回退窗口）。"""
    runs = tmp_path / "runs"
    _write_member(runs, "factor_A", (_START, _END))
    entry, _ = fx.write_impl(tmp_path, fx._COMPUTE_BODY)
    spec_path = fx.write_spec(tmp_path, "cx_nested",
                              ["composites/cx_base", "factor_A"], entry)
    db = _compose_sandbox(tmp_path, monkeypatch)

    result = _compose(spec_path, runs)

    out = strip_ansi(result.output)
    assert result.exit_code != 0
    assert "LOCKBOX_TEST_ONLY_FINAL" in out
    # 回退窗口 = [min(published days) ~ data_end]；若按"跳过 composite 成员"
    # 只取 factor_A 会在 [W.start~W.end] 判窗——此处两个端点断言即反证。
    assert str(min(DAYS)) in out
    assert str(W.end) in out
    assert _rows(db) == []
    assert not (runs / "composites" / "cx_nested").exists()


# ================================================================
# run_calendar（execution 层日历单点）直测：真 health 目录扫描
# ================================================================

def test_run_calendar_reads_sorted_health_days_and_data_end(tmp_path):
    dataset = tmp_path / "health" / "ashare_daily"
    dataset.mkdir(parents=True)
    (dataset / "2024-01-05.json").write_text("{}", encoding="utf-8")
    (dataset / "2024-01-02.json").write_text("{}", encoding="utf-8")
    (dataset / "2024-01-04.json").write_text("{}", encoding="utf-8")
    (dataset / "not-a-date.json").write_text("{}", encoding="utf-8")  # 非法名忽略

    days, data_end = store.run_calendar(tmp_path / "health")

    assert days == [dt.date(2024, 1, 2), dt.date(2024, 1, 4), dt.date(2024, 1, 5)]
    assert data_end == dt.date(2024, 1, 5)


def test_run_calendar_empty_or_missing_dir_falls_back_to_today(tmp_path):
    empty = tmp_path / "health" / "ashare_daily"
    empty.mkdir(parents=True)
    today = dt.date.today()

    days, data_end = store.run_calendar(tmp_path / "health")
    assert (days, data_end) == ([], today)

    days, data_end = store.run_calendar(tmp_path / "missing-root")
    assert (days, data_end) == ([], today)


def test_run_calendar_defaults_to_platform_health_root(tmp_path, monkeypatch):
    """缺省 root = DATA_ROOT/health（与 CLI helper 同源）；用假 DATA_ROOT 直测。"""
    from factorlab.core.factio import paths

    monkeypatch.setattr(paths, "DATA_ROOT", str(tmp_path / "data_root"))
    dataset = tmp_path / "data_root" / "health" / "ashare_daily"
    dataset.mkdir(parents=True)
    (dataset / "2024-03-01.json").write_text("{}", encoding="utf-8")

    days, data_end = store.run_calendar()

    assert days == [dt.date(2024, 3, 1)]
    assert data_end == dt.date(2024, 3, 1)


# ================================================================
# strategy：doc.date 窗口与产物声明
# ================================================================

def test_strategy_host_test_segment_rejected(tmp_path, monkeypatch):
    """host 碰测试段：信号加载前直接拒，零登记。"""
    db = _strategy_sandbox(tmp_path, monkeypatch)
    doc_path = _strategy_doc_file(tmp_path, start=_START - dt.timedelta(days=10),
                                  end=_END)
    doc = load_strategy_doc(doc_path)

    from factorlab.app.strategy.run import run_strategy
    with pytest.raises(LockboxError) as e:
        run_strategy(doc, None, dataset=None, results_dir=tmp_path / "results",
                     doc_path=doc_path)

    assert e.value.code == "LOCKBOX_TEST_ONLY_FINAL"
    assert _rows(db) == []


def test_strategy_explicit_final_mode_without_marker_rejected(
        tmp_path, monkeypatch):
    """显式 final_mode=True 但缺流水线标记 → `LOCKBOX_PIPELINE_REQUIRED`，零登记。"""
    db = _strategy_sandbox(tmp_path, monkeypatch)
    doc_path = _strategy_doc_file(tmp_path, start=_START, end=_END)
    doc = load_strategy_doc(doc_path)

    from factorlab.app.strategy.run import run_strategy
    with pytest.raises(LockboxError) as e:
        run_strategy(doc, None, dataset=None, results_dir=tmp_path / "results",
                     doc_path=doc_path, final_mode=True)

    assert e.value.code == "LOCKBOX_PIPELINE_REQUIRED"
    assert _rows(db) == []


def test_strategy_final_mode_manifest_sample_and_result_ref(tmp_path, monkeypatch):
    """marker：真链（M8 回测边界替换为记录桩）——final 登记 + manifest sample
    挂载 + result_ref 回填。"""
    import factorlab.app.strategy.run as SR
    from factorlab.app.strategy.run import run_strategy

    monkeypatch.setenv("FACTORLAB_PIPELINE", "1")
    db = _strategy_sandbox(tmp_path, monkeypatch)
    results = tmp_path / "results"
    signal_dates = (_START, _START + dt.timedelta(days=1), _START + dt.timedelta(days=2))
    fx.write_factor(results, "ws7_doc_chain", dates=signal_dates)
    doc_path = _strategy_doc_file(tmp_path, start=_START, end=_END)
    doc = load_strategy_doc(doc_path)

    called: dict = {}

    def fake_backtest(target, execution, rd, **kw):
        called["rd"] = rd
        return SimpleNamespace(nav_series=None, artifacts=[])

    monkeypatch.setattr(SR, "run_backtest", fake_backtest)
    monkeypatch.setattr(SR, "save_backtest_result",
                        lambda backtest, out_dir: called.setdefault("saved", out_dir))

    res = run_strategy(doc, None, dataset=None, results_dir=results,
                       doc_path=doc_path)

    manifest = json.loads((res.out_dir / "strategy_manifest.json")
                          .read_text(encoding="utf-8"))
    rows = _rows(db)
    assert len(rows) == 1
    assert (rows[0]["kind"], rows[0]["command"]) == ("final", "strategy run")
    assert rows[0]["window_id"] == WINDOW_ID
    assert manifest["sample"]["access_id"] == rows[0]["access_id"]
    assert manifest["sample"]["role"] == "lockbox"
    assert manifest["sample"]["window_id"] == WINDOW_ID
    assert manifest["sample"]["conclusion_eligible"] is True
    assert rows[0]["result_ref"] == str(res.out_dir)
    assert called["saved"] == res.out_dir   # 回填发生在全部产物落盘之后


def test_strategy_run_registry_has_no_lockbox_knobs_and_maps_error(
        tmp_path, monkeypatch):
    """registry 不暴露锁箱参数；parser 拒收；handler 捕获 LockboxError → 稳定错误码。"""
    from factorlab.research import registry
    from factorlab.research import strategy as S

    spec = registry.COMMANDS["strategy.run"]
    assert not [p for p in spec.params if "lockbox" in p.name]
    assert "lockbox" not in spec.defaults and "lockbox_reason" not in spec.defaults
    parser = registry.build_parser(spec)
    with pytest.raises(SystemExit):
        parser.parse_args(["d.yaml", "--lockbox", "final"])

    results = tmp_path / "results"
    (results / "x").mkdir(parents=True)
    (results / "x" / "summary.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "results_dir", results)
    doc_path = tmp_path / "doc.yaml"
    doc_path.write_text(_doc_yaml(signal="x"), encoding="utf-8")

    captured: dict = {}

    def fake_run_strategy(doc, rd, **kw):
        captured.update(kw)
        raise LockboxError("LOCKBOX_TEST_ONLY_FINAL", "评估窗口与锁箱相交")

    monkeypatch.setattr(S, "run_strategy", fake_run_strategy)
    monkeypatch.setattr(S, "guard_heavy", lambda argv, *, wait=False: ({}, "slot"))
    monkeypatch.setattr(S, "release_slots", lambda: None)

    from contextlib import contextmanager

    @contextmanager
    def fake_read_handle():
        yield None      # 读句柄不是本用例断言对象（run_strategy 已被替换）

    monkeypatch.setattr(S, "_read_handle", fake_read_handle)

    env = S.strategy_run(argparse.Namespace(
        doc_path=doc_path, signal=None, dry_run=False, out_dir=None, wait=False,
        json=True, pretty=False, accept_quality=None, override_reason=None))
    assert env.ok is False
    assert env.error["code"] == "LOCKBOX_TEST_ONLY_FINAL", env.error
    assert "factorlab lockbox status" in env.error["hint"]
    assert "flab lockbox" not in env.error["hint"]
    assert "lockbox_intent" not in captured and "lockbox_reason" not in captured
    assert captured["doc_path"] == doc_path


# ================================================================
# env 开关：off → 不读 state、不登记、不拒
# ================================================================

def test_env_off_skips_compose_and_strategy(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "off")
    spec_path, runs, db = _compose_world(
        tmp_path, monkeypatch,
        a=(_START - dt.timedelta(days=401), _START + dt.timedelta(days=50)),
        b=(_START - dt.timedelta(days=100), _START + dt.timedelta(days=50)))

    result = _compose(spec_path, runs)   # 跨箱无 marker：off → 直接 IS 放行
    assert result.exit_code == 0, result.output
    assert _rows(db) == []

    doc_path = _strategy_doc_file(tmp_path, start=_START - dt.timedelta(days=10),
                                  end=_END)
    doc = load_strategy_doc(doc_path)
    from factorlab.app.strategy.run import run_strategy
    with pytest.raises(ValueError):      # 链失败于缺信号，但不是锁箱拒跑
        run_strategy(doc, None, dataset=None, results_dir=tmp_path / "results",
                     doc_path=doc_path)
    assert _rows(db) == []
