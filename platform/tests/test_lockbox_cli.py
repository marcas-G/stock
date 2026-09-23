from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from typer.testing import CliRunner

from factorlab.adapters import lockbox_store as store
from factorlab.surfaces.cli import main as cli_main
from factorlab.surfaces.cli.main import app

DAYS = [dt.date(2025, 7, 1), dt.date(2026, 9, 18)]
DAYS_Q3 = [dt.date(2025, 7, 1), dt.date(2025, 10, 1), dt.date(2026, 9, 18)]
DAYS_GAP = [dt.date(2025, 6, 27), dt.date(2025, 7, 7), dt.date(2026, 9, 18)]


def _patch_cli(monkeypatch, days: list[dt.date], data_end: dt.date | None,
               today: dt.date) -> None:
    monkeypatch.setattr(cli_main, "_lockbox_published_days", lambda: days)
    monkeypatch.setattr(cli_main, "_lockbox_data_end", lambda: data_end)
    monkeypatch.setattr(cli_main, "_lockbox_today", lambda: today)


def test_lockbox_roll_then_status(tmp_path: Path, monkeypatch):
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(cli_main.settings, "lockbox_db", db)
    _patch_cli(monkeypatch, DAYS, dt.date(2026, 9, 18), dt.date(2026, 9, 21))
    runner = CliRunner()
    r1 = runner.invoke(app, ["lockbox", "roll"])
    assert r1.exit_code == 0, r1.output
    r2 = runner.invoke(app, ["lockbox", "status", "--json"])
    assert r2.exit_code == 0, r2.output
    doc = json.loads(r2.output.strip().splitlines()[-1])
    assert set(doc) == {"initialized", "window_id", "window_start", "window_end",
                        "is_end", "finals_total"}
    assert doc["window_id"] == "2026Q2" and doc["finals_total"] == 0
    assert doc["is_end"] == "2025-06-30"
    assert doc["window_end"] == "2026-09-18"
    # 幂等：同窗再 roll 不改状态行（手工改旧 rolled_at，第二次 roll 必须不重写）
    conn = store.connect(db)
    conn.execute("UPDATE lockbox_state SET rolled_at = ? WHERE id = 1",
                 ("2000-01-01T00:00:00+00:00",))
    r3 = runner.invoke(app, ["lockbox", "roll"])
    assert r3.exit_code == 0, r3.output
    assert "无变化" in r3.output
    conn2 = store.connect(db)
    state = store.load_state(conn2)
    assert state["window_id"] == "2026Q2"
    assert state["rolled_at"] == "2000-01-01T00:00:00+00:00"


def test_lockbox_status_is_end_previous_trading_day(tmp_path: Path, monkeypatch):
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(cli_main.settings, "lockbox_db", db)
    _patch_cli(monkeypatch, DAYS_GAP, dt.date(2026, 9, 18), dt.date(2026, 9, 21))
    runner = CliRunner()
    assert runner.invoke(app, ["lockbox", "roll"]).exit_code == 0
    r = runner.invoke(app, ["lockbox", "status", "--json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.output.strip().splitlines()[-1])
    assert doc["window_start"] == "2025-07-07"
    # 前一交易日（周五 06-27）≠ 日历前一天（周日 07-06）
    assert doc["is_end"] == "2025-06-27"


def test_lockbox_status_uninitialized_exits_1(tmp_path: Path, monkeypatch):
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(cli_main.settings, "lockbox_db", db)
    _patch_cli(monkeypatch, DAYS, dt.date(2026, 9, 18), dt.date(2026, 9, 21))
    r = CliRunner().invoke(app, ["lockbox", "status", "--json"])
    assert r.exit_code == 1, r.output
    doc = json.loads(r.output.strip().splitlines()[-1])
    assert doc["initialized"] is False
    assert "is_end" not in doc
    # 未初始化不得伪造/写入 state 行
    conn = store.connect(db)
    assert store.load_state(conn) is None


def test_lockbox_roll_as_of_advances_window(tmp_path: Path, monkeypatch):
    """`--as-of` 把窗口推进到对应季度；state 真实落库，quota 列不读不写。"""
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(cli_main.settings, "lockbox_db", db)
    _patch_cli(monkeypatch, DAYS_Q3, dt.date(2026, 9, 18), dt.date(2026, 9, 21))
    runner = CliRunner()
    r = runner.invoke(app, ["lockbox", "roll", "--as-of", "2026-10-01"])
    assert r.exit_code == 0, r.output
    # 状态真实落库（非仅打印）
    conn = store.connect(db)
    state = store.load_state(conn)
    assert state["window_id"] == "2026Q3" and state["window_start"] == "2025-10-01"
    assert int(state["quota_final"]) == 20, "quota_final 列保留但不得被写入"
    r2 = runner.invoke(app, ["lockbox", "status", "--json"])
    assert r2.exit_code == 0, r2.output
    doc = json.loads(r2.output.strip().splitlines()[-1])
    assert doc["window_id"] == "2026Q3" and doc["window_start"] == "2025-10-01"
    # is_end=start 前最后一个交易日（稀疏日历中为 2025-07-01）
    assert doc["is_end"] == "2025-07-01"
    assert doc["finals_total"] == 0


def test_default_lockbox_db_and_env_override(tmp_path: Path, monkeypatch):
    from factorlab.config import Settings, default_lockbox_db
    monkeypatch.setenv("QUANTRESEARCH_ROOT", str(tmp_path))
    monkeypatch.delenv("FACTORLAB_LOCKBOX_DB", raising=False)
    assert default_lockbox_db() == tmp_path / "data" / "ledger.sqlite"
    assert Settings().lockbox_db == tmp_path / "data" / "ledger.sqlite"
    override = tmp_path / "custom" / "ledger.sqlite"
    monkeypatch.setenv("FACTORLAB_LOCKBOX_DB", str(override))
    assert Settings().lockbox_db == override


def test_quota_final_flag_removed_and_admin_has_no_effect(tmp_path: Path,
                                                          monkeypatch):
    """R42：`--quota-final` 与 `FACTORLAB_LOCKBOX_ADMIN` 一并删除——未知参数拒收；
    即便设了 admin 环境变量也不产生任何 write（参数解析失败先于 connect）。"""
    db = tmp_path / "l.sqlite"
    monkeypatch.setattr(cli_main.settings, "lockbox_db", db)
    monkeypatch.setattr(cli_main, "_lockbox_published_days", lambda: DAYS)
    monkeypatch.setattr(cli_main, "_lockbox_data_end", lambda: dt.date(2026, 9, 18))
    monkeypatch.setattr(cli_main, "_lockbox_today", lambda: dt.date(2026, 9, 21))
    monkeypatch.setenv("FACTORLAB_LOCKBOX_ADMIN", "1")
    r = CliRunner().invoke(app, ["lockbox", "roll", "--quota-final", "99"])
    assert r.exit_code == 2, r.output
    assert "--quota-final" in r.output
    assert not db.exists(), "参数拒收不得创建/触碰台账"
