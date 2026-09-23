"""R42 T2：execute 层硬门（CLI）与 research 门面参数面收敛。

断言来源：设计 `2026-09-24-final-test-once-discipline-design.md` §3/§5 + T2 brief：
- `execute_run` 只认 `final_mode`（无 `lockbox_intent/lockbox_reason`）：
  host（无 `FACTORLAB_PIPELINE`）碰测试段 → `LOCKBOX_TEST_ONLY_FINAL`，零登记零产物；
- `final_mode=True` + `FACTORLAB_PIPELINE=1` → 登记 1 行 final（真台账行 + 版本理由）；
  同版本二次 → `LOCKBOX_FINAL_DUPLICATE`（行数不变）；
- `--lockbox/--lockbox-reason` 从 `factorlab run`/`compose`/`flab factor run` 参数面消失
  （help/describe 无、parser 拒收）；
- `LockboxError` → 研究门面稳定错误码信封（hint 指向 `factorlab lockbox status`）。

真实度：真 typer runner + 真 SQLite（沙箱台账）；只 patch 日历/数据日、
`settings.lockbox_db` 与重链入口（哨兵异常——证 guard 先于重链且登记已落）。
墙钟鲁棒：spec 窗口、沙箱日历、期望 window_id 全部从 `dt.date.today()` 派生
（`_lockbox` 工具）。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from _lockbox import DAYS, WINDOW as W, WINDOW_ID
from _text import strip_ansi
from typer.testing import CliRunner

from factorlab.adapters.lockbox_store import connect, guard_run, roll
from factorlab.config import settings
from factorlab.core.lockbox import LockboxError, LockboxWindow
from factorlab.surfaces.cli import main as cli_main
from factorlab.surfaces.cli.main import app, execute_run

SPEC_TMPL = ("name: x\ncategory: custom\ndirection: 1\n"
             "universe:\n  codes: [\"000001.SZ\"]\n"
             "formula: close\ndate:\n  start: '{start}'\n  end: '{end}'\n")


class _StopChain(Exception):
    """哨兵异常：证明 guard 已放行、重链被短路（不落半成品产物）。"""


@pytest.fixture(autouse=True)
def _lockbox_enabled(monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "1")
    monkeypatch.delenv("FACTORLAB_PIPELINE", raising=False)
    monkeypatch.delenv("FACTORLAB_RE_FINAL", raising=False)


def _stop_chain(monkeypatch) -> list:
    """重链入口替换为哨兵（execute_run 内 `from factorlab.app.run import …` 现取现用）。"""
    seen: list = []

    def boom(spec, ctx):
        seen.append(ctx)
        raise _StopChain

    monkeypatch.setattr("factorlab.app.run.run_factor", boom)
    monkeypatch.setattr("factorlab.app.run.run_factor_minute", boom)
    return seen


def _sandbox(tmp_path: Path, monkeypatch, *,
             start: dt.date | None = None, end: dt.date | None = None):
    qr = tmp_path / "qr"
    (qr / "factor" / "demo").mkdir(parents=True)
    spec = qr / "factor" / "demo" / "x.yaml"
    spec.write_text(SPEC_TMPL.format(start=(start or W.start).isoformat(),
                                     end=(end or W.end).isoformat()),
                    encoding="utf-8")
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(settings, "lockbox_db", db)
    monkeypatch.setattr(cli_main, "_lockbox_published_days", lambda: list(DAYS))
    monkeypatch.setattr(cli_main, "_lockbox_data_end", lambda: W.end)
    CliRunner().invoke(app, ["lockbox", "roll"])   # 初始化状态（as_of=真今天）
    return spec, db


def _rows(db: Path) -> list[dict]:
    conn = connect(db)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM lockbox_access ORDER BY rowid")]
    finally:
        conn.close()


# ================================================================
# execute_run：host 拒 / marker 登记
# ================================================================

def test_execute_is_window_passes_without_registration(tmp_path: Path, monkeypatch):
    """整段训练段：host 直跑也放行（guard=is），零登记。"""
    spec, db = _sandbox(tmp_path, monkeypatch,
                        start=W.start - dt.timedelta(days=400),
                        end=W.start - dt.timedelta(days=1))
    seen = _stop_chain(monkeypatch)
    with pytest.raises(_StopChain):
        execute_run(spec, backtest=False, output_dir=tmp_path / "results")
    assert seen[0].guard.info == {"role": "is"}
    assert _rows(db) == []


def test_execute_host_test_segment_rejected_test_only_final(
        tmp_path: Path, monkeypatch):
    """host（无 marker）碰测试段：直接拒 `LOCKBOX_TEST_ONLY_FINAL`，零登记。"""
    spec, db = _sandbox(tmp_path, monkeypatch)
    with pytest.raises(LockboxError) as e:
        execute_run(spec, backtest=False)
    assert e.value.code == "LOCKBOX_TEST_ONLY_FINAL"
    assert "xpipe" in e.value.message and "admit" in e.value.message
    assert _rows(db) == []


def test_execute_final_mode_registers_one_final_row_then_duplicate(
        tmp_path: Path, monkeypatch):
    """final_mode + marker：登记 1 行 final（版本理由）；同版本二次 → DUPLICATE。"""
    spec, db = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORLAB_PIPELINE", "1")
    _stop_chain(monkeypatch)
    with pytest.raises(_StopChain):
        execute_run(spec, backtest=False, final_mode=True,
                    output_dir=tmp_path / "results")
    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "final"
    assert row["window_id"] == WINDOW_ID
    assert row["command"] == "factor run"
    assert row["reason"].strip(), "登记理由不得为空（审计列 NOT NULL）"
    assert str(spec) == row["artifact"]
    with pytest.raises(LockboxError) as e:
        execute_run(spec, backtest=False, final_mode=True)
    assert e.value.code == "LOCKBOX_FINAL_DUPLICATE"
    assert len(_rows(db)) == 1, "重复被拒不得新增行"


def test_execute_final_mode_derived_from_pipeline_marker(
        tmp_path: Path, monkeypatch):
    """`final_mode=None`（缺省）+ marker → env 推导为最终测试并登记。"""
    from factorlab.adapters.lockbox_store import guard_run as real_guard

    spec, db = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORLAB_PIPELINE", "1")
    calls: list[dict] = []

    def spy_guard(**kw):
        calls.append(kw)
        return real_guard(**kw)

    monkeypatch.setattr("factorlab.adapters.lockbox_store.guard_run", spy_guard)
    _stop_chain(monkeypatch)
    with pytest.raises(_StopChain):
        execute_run(spec, backtest=False, output_dir=tmp_path / "results")
    assert calls and calls[0]["final_mode"] is True, "marker 必须推导 final_mode=True"
    assert len(_rows(db)) == 1 and _rows(db)[0]["kind"] == "final"


def test_execute_explicit_final_mode_without_marker_rejected(
        tmp_path: Path, monkeypatch):
    """显式 final_mode=True 但无 marker → `LOCKBOX_PIPELINE_REQUIRED`，零登记。"""
    spec, db = _sandbox(tmp_path, monkeypatch)
    with pytest.raises(LockboxError) as e:
        execute_run(spec, backtest=False, final_mode=True)
    assert e.value.code == "LOCKBOX_PIPELINE_REQUIRED"
    assert _rows(db) == []


# ================================================================
# 参数面消失：CLI help / registry describe / parser
# ================================================================

def test_cli_run_help_has_no_lockbox_flags():
    r = CliRunner().invoke(app, ["run", "--help"])
    assert r.exit_code == 0, r.output
    assert "--lockbox" not in strip_ansi(r.stdout)


def test_cli_compose_help_has_no_lockbox_flags():
    r = CliRunner().invoke(app, ["compose", "--help"])
    assert r.exit_code == 0, r.output
    assert "--lockbox" not in strip_ansi(r.stdout)


def test_cli_run_rejects_removed_lockbox_flag():
    r = CliRunner().invoke(app, ["run", "x.yaml", "--lockbox", "final"])
    assert r.exit_code == 2, r.output
    assert "--lockbox" in strip_ansi(r.output)


def test_factor_run_registry_and_describe_have_no_lockbox_knobs():
    from factorlab.research import registry
    spec = registry.COMMANDS["factor.run"]
    assert not [p for p in spec.params if "lockbox" in p.name]
    assert "lockbox" not in spec.defaults and "lockbox_reason" not in spec.defaults
    doc = spec.to_doc()
    assert "lockbox" not in json.dumps(doc)
    parser = registry.build_parser(spec)
    with pytest.raises(SystemExit):
        parser.parse_args(["x.yaml", "--lockbox", "final"])


def test_factor_run_forwards_final_mode_default_and_maps_error(
        tmp_path: Path, monkeypatch):
    """门面不再透传 lockbox 参数；`LockboxError` → 稳定错误码 + status 指引。"""
    from factorlab.research import factor as F

    captured: dict = {}

    def fake_execute_run(spec_path_arg, **kw):
        captured.update(kw)
        raise LockboxError("LOCKBOX_TEST_ONLY_FINAL", "评估窗口与锁箱相交")

    monkeypatch.setattr(cli_main, "execute_run", fake_execute_run)
    monkeypatch.setattr(F, "guard_heavy", lambda argv, wait=False: ({}, "slot"))
    monkeypatch.setattr(F, "release_slots", lambda: None)
    spec = tmp_path / "x.yaml"
    spec.write_text("name: x\n", encoding="utf-8")
    env = F.factor_run(F._run_args(spec))
    assert not env.ok
    assert env.error["code"] == "LOCKBOX_TEST_ONLY_FINAL"
    assert "factorlab lockbox status" in env.error["hint"]
    assert "flab lockbox" not in env.error["hint"]
    assert "lockbox_intent" not in captured and "lockbox_reason" not in captured


# ================================================================
# 产物声明：真 run_factor 链的 sample / result_ref
# ================================================================

def test_run_factor_persists_sample_and_result_ref(env, tmp_path, monkeypatch):
    """日频链路真实落盘：`summary.sample` 与登记 result_ref 必须同源可见。

    真 SQLite 台账 + 真 run_factor 链路：去掉 run.py 的 attach/mark 挂接即红
    （存根替换失败点）。合成面板固定 2024 年（数据 fixture 自带），窗口状态
    起点设 2024-01-02 使面板整体落锁箱；window_id 仍按真墙钟季度派生。
    """
    from factorlab.app.run import run_factor
    from test_run_factor import _ctx, _seed, _spec

    monkeypatch.setenv("FACTORLAB_PIPELINE", "1")
    _seed(env)
    db = tmp_path / "ledger.sqlite"
    conn = connect(db)
    roll(conn, window=LockboxWindow(WINDOW_ID, dt.date(2024, 1, 2), W.end))
    conn.close()
    guard = guard_run(panel_start=dt.date(2024, 1, 2), panel_end=dt.date(2024, 1, 9),
                      final_mode=True, reason="集成验证",
                      spec_doc={"name": "demo"}, artifact="factor/demo/x.yaml",
                      command="factor run", tool="factorlab test", db_path=db,
                      trading_days=DAYS, data_end=W.end)
    out_dir = tmp_path / "out"
    ctx = _ctx(env, out_dir)
    ctx.guard = guard
    result = run_factor(_spec(tmp_path), ctx)
    assert result.summary["sample"]["role"] == "lockbox"
    assert result.summary["sample"]["access_id"] == guard.info["access_id"]
    on_disk = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert on_disk["sample"]["access_id"] == guard.info["access_id"]
    row = connect(db).execute(
        "SELECT kind, result_ref FROM lockbox_access WHERE access_id=?",
        (guard.info["access_id"],)).fetchone()
    assert row["kind"] == "final"
    assert row["result_ref"] == str(out_dir)
