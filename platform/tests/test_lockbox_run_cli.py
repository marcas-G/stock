"""R40 T5：execute 层硬门（CLI）与 research 门面透传。

断言来源：设计 §7/§10 + 计划 T5 brief：
- 相交窗口无 `--lockbox` → `LOCKBOX_INTENT_REQUIRED`，且零登记；
- 有意图无理由 → `LOCKBOX_REASON_REQUIRED`；
- exploration 在重链前登记（登记行真实存在；后续链路因无库必败）；
- `flab factor run` registry 暴露并透传两参数，`LockboxError` → 信封稳定错误码。

真实度：真 typer runner + 真 SQLite（沙箱）；只 patch 日历/数据日与
`settings.lockbox_db`（与真台账隔离）。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from _text import strip_ansi
from typer.testing import CliRunner

from factorlab.adapters.lockbox_store import connect, guard_run, roll
from factorlab.config import settings
from factorlab.core.lockbox import LockboxError, LockboxWindow
from factorlab.surfaces.cli import main as cli_main
from factorlab.surfaces.cli.main import app, execute_run

SPEC = ("name: x\ncategory: custom\ndirection: 1\n"
        "universe:\n  codes: [\"000001.SZ\"]\n"
        "formula: close\ndate:\n  start: '2026-01-05'\n"
        "  end: '2026-09-18'\n")


@pytest.fixture(autouse=True)
def _lockbox_enabled(monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "1")


def _sandbox(tmp_path: Path, monkeypatch):
    qr = tmp_path / "qr"
    (qr / "factor" / "demo").mkdir(parents=True)
    spec = qr / "factor" / "demo" / "x.yaml"
    spec.write_text(SPEC, encoding="utf-8")
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(settings, "lockbox_db", db)
    monkeypatch.setattr(cli_main, "_lockbox_published_days",
                        lambda: [dt.date(2026, 1, 5), dt.date(2026, 9, 18)])
    monkeypatch.setattr(cli_main, "_lockbox_data_end", lambda: dt.date(2026, 9, 18))
    monkeypatch.setattr(cli_main, "_lockbox_today", lambda: dt.date(2026, 9, 21))
    CliRunner().invoke(app, ["lockbox", "roll"])   # 初始化状态
    return spec, db


def test_execute_refuses_without_intent(tmp_path: Path, monkeypatch):
    spec, db = _sandbox(tmp_path, monkeypatch)
    with pytest.raises(LockboxError) as e:
        execute_run(spec, backtest=False)
    assert e.value.code == "LOCKBOX_INTENT_REQUIRED"
    assert connect(db).execute(
        "SELECT COUNT(*) FROM lockbox_access").fetchone()[0] == 0


def test_execute_refuses_without_reason(tmp_path: Path, monkeypatch):
    spec, _ = _sandbox(tmp_path, monkeypatch)
    with pytest.raises(LockboxError) as e:
        execute_run(spec, backtest=False, lockbox_intent="exploration")
    assert e.value.code == "LOCKBOX_REASON_REQUIRED"


def test_execute_registers_exploration_before_heavy_chain(tmp_path: Path, monkeypatch):
    spec, db = _sandbox(tmp_path, monkeypatch)
    with pytest.raises(Exception):   # 无库 → 后续链路必败；关键看登记已落
        execute_run(spec, backtest=False, lockbox_intent="exploration",
                    lockbox_reason="单元验证")
    row = connect(db).execute(
        "SELECT kind, window_id FROM lockbox_access").fetchone()
    assert (row["kind"], row["window_id"]) == ("exploration", "2026Q2")


def test_cli_run_help_exposes_lockbox_flags():
    r = CliRunner().invoke(app, ["run", "--help"])
    assert r.exit_code == 0, r.output
    out = strip_ansi(r.stdout)
    assert "--lockbox" in out and "--lockbox-reason" in out


def test_factor_run_registry_exposes_lockbox_knobs():
    from factorlab.research import registry
    spec = registry.COMMANDS["factor.run"]
    assert {"lockbox", "lockbox_reason"} <= {p.name for p in spec.params}
    assert spec.defaults["lockbox"] is None
    assert spec.defaults["lockbox_reason"] is None
    ns = registry.build_parser(spec).parse_args(
        ["x.yaml", "--lockbox", "exploration", "--lockbox-reason", "摸边界"])
    assert (ns.lockbox, ns.lockbox_reason) == ("exploration", "摸边界")


def test_factor_run_forwards_lockbox_and_maps_error(tmp_path: Path, monkeypatch):
    from factorlab.research import factor as F

    captured: dict = {}

    def fake_execute_run(spec_path_arg, **kw):
        captured.update(kw)
        raise LockboxError("LOCKBOX_INTENT_REQUIRED", "评估窗口与锁箱相交")

    monkeypatch.setattr(cli_main, "execute_run", fake_execute_run)
    monkeypatch.setattr(F, "guard_heavy", lambda argv, wait=False: ({}, "slot"))
    monkeypatch.setattr(F, "release_slots", lambda: None)
    spec = tmp_path / "x.yaml"
    spec.write_text("name: x\n", encoding="utf-8")
    env = F.factor_run(F._run_args(spec, lockbox="exploration",
                                   lockbox_reason="摸边界"))
    assert not env.ok
    assert env.error["code"] == "LOCKBOX_INTENT_REQUIRED"
    assert "lockbox status" in env.error["hint"]
    assert captured["lockbox_intent"] == "exploration"
    assert captured["lockbox_reason"] == "摸边界"


def test_run_factor_persists_sample_and_result_ref(env, tmp_path, monkeypatch):
    """日频链路真实落盘：`summary.sample` 与登记 result_ref 必须同源可见。

    真 SQLite 台账 + 真 run_factor 链路：去掉 run.py 的 attach/mark 挂接即红
    （存根替换失败点）。
    """
    from factorlab.app.run import run_factor
    from test_run_factor import _ctx, _seed, _spec

    _seed(env)
    db = tmp_path / "ledger.sqlite"
    days = [dt.date(2025, 7, 1), dt.date(2026, 9, 18)]
    conn = connect(db)
    roll(conn, window=LockboxWindow("2026Q2", dt.date(2024, 1, 2),
                                    dt.date(2026, 9, 18)))
    conn.close()
    guard = guard_run(panel_start=dt.date(2024, 1, 2), panel_end=dt.date(2024, 1, 9),
                      intent="exploration", reason="集成验证",
                      spec_doc={"name": "demo"}, artifact="factor/demo/x.yaml",
                      command="factor run", tool="factorlab test", db_path=db,
                      trading_days=days, data_end=dt.date(2026, 9, 18))
    out_dir = tmp_path / "out"
    ctx = _ctx(env, out_dir)
    ctx.guard = guard
    result = run_factor(_spec(tmp_path), ctx)
    assert result.summary["sample"]["role"] == "lockbox"
    assert result.summary["sample"]["access_id"] == guard.info["access_id"]
    on_disk = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert on_disk["sample"]["access_id"] == guard.info["access_id"]
    row = connect(db).execute(
        "SELECT result_ref FROM lockbox_access WHERE access_id=?",
        (guard.info["access_id"],)).fetchone()
    assert row["result_ref"] == str(out_dir)
