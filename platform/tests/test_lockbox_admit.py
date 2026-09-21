"""R40 T7：`factor admit` / `factor ref add` 终评硬门（CliRunner + 沙箱 QR + 真 SQLite）。

断言来源：设计 §6/§7 + T7 控制器裁定：
- 碰箱缺终评且无 `--lockbox-reason` → `LOCKBOX_FINAL_REQUIRED`，零登记、零写入；
- 缺终评 + `--lockbox-reason` → 先补 final 登记（真台账行），随后 admit 内部重链
  因数据缺失报**非锁箱**错误（门先于重链；guard_run final 幂等复用同一登记）；
- 已登记候选再次调用 → 复用同一 access_id（登记数不变、无 DUPLICATE）；
- ref add 覆盖 spec 存在（spec.model_dump 指纹）与缺失（`ref:<name>` 回退指纹）两径；
- env `FACTORLAB_LOCKBOX=off` → 不读台账、不登记、台账文件不被创建。

禁止行为：拒跑后 `lockbox_access` 零行、参考库零写入；env off 后台账文件不存在。
墙钟鲁棒：窗口/日历全部从 `_lockbox.py` 动态派生（guard 走真墙钟）。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from _lockbox import DAYS, WINDOW as W, WINDOW_ID
from typer.testing import CliRunner

from factorlab.adapters import lockbox_store as store
from factorlab.adapters.lockbox_store import connect, roll
from factorlab.config import settings
from factorlab.core.lockbox import candidate_fingerprint, spec_fingerprint
from factorlab.core.spec import load_spec
from factorlab.research import factor as F
from factorlab.surfaces.cli import main as cli_main
from factorlab.surfaces.cli.main import app

_SPEC = ("name: {name}\ncategory: custom\ndirection: 1\n"
         "universe:\n  codes: [\"000001.SZ\"]\n"
         "formula: \"signal = close\"\n"
         "date:\n  start: '{start}'\n  end: '{end}'\n")

_REF_YAML = """\
# 沙箱参考库
scales:
  daily:
    - name: seed_a
      style: "风格A"
      reason: "种子"
      added: "2024-01-01"
"""


@pytest.fixture(autouse=True)
def _lockbox_enabled(monkeypatch):
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "1")


def _sandbox(tmp_path: Path, monkeypatch):
    """沙箱 QR：真 spec + 真参考库 + 真 SQLite 台账（已 roll 到当前季窗）。"""
    qr = tmp_path / "qr"
    (qr / "factor" / "demo").mkdir(parents=True)
    ref = qr / "factor" / "_reference.yaml"
    ref.write_text(_REF_YAML, encoding="utf-8")
    spec = qr / "factor" / "demo" / "refcand.yaml"
    spec.write_text(_SPEC.format(name="refcand", start=W.start.isoformat(),
                                 end=W.end.isoformat()), encoding="utf-8")
    db = tmp_path / "ledger.sqlite"
    conn = connect(db)
    roll(conn, window=W)
    conn.close()
    monkeypatch.setattr(settings, "lockbox_db", db)
    monkeypatch.setattr(settings, "research_root", qr)
    monkeypatch.setattr(settings, "results_dir", tmp_path / "results")
    monkeypatch.setattr(cli_main, "_lockbox_published_days", lambda: list(DAYS))
    monkeypatch.setattr(cli_main, "_lockbox_data_end", lambda: W.end)
    # research 层日历单点（T8）：final gate 走 adapters.run_calendar，不复用 CLI 私有 helper
    monkeypatch.setattr(store, "run_calendar", lambda: (list(DAYS), W.end))
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    # heavy 闸（flock/nice/内存预检）与本任务无关：打桩保测试稳定
    monkeypatch.setattr(F, "guard_heavy", lambda argv, wait=False: ({}, "slot"))
    monkeypatch.setattr(F, "release_slots", lambda: None)
    return spec, db, ref


def _invoke(*argv: str):
    return CliRunner().invoke(app, ["research", *argv])


def _doc(result) -> dict:
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    return json.loads(lines[-1])


def _rows(db: Path) -> list[dict]:
    conn = connect(db)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM lockbox_access ORDER BY ts_utc, access_id")]
    finally:
        conn.close()


def _count(db: Path) -> int:
    return len(_rows(db))


def _expected_fp(doc: dict) -> str:
    return candidate_fingerprint(artifact_sha256=spec_fingerprint(doc),
                                 params={"intent": "final"},
                                 window_id=WINDOW_ID, kind="final")


def test_final_gate_uses_adapter_run_calendar_not_cli_helpers(tmp_path, monkeypatch):
    """T8 单点：research 层锁箱日历走 `adapters.lockbox_store.run_calendar`。

    CLI 私有 helper 打桩为硬失败——门若仍复用 surfaces helper 必红。
    """
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)

    def _boom():
        raise AssertionError("research 层不得复用 surfaces/cli 私有日历 helper")

    monkeypatch.setattr(cli_main, "_lockbox_published_days", _boom)
    monkeypatch.setattr(cli_main, "_lockbox_data_end", _boom)

    access_id = F._lockbox_final_gate(spec=load_spec(spec), artifact=str(spec),
                                      reason="终评", command="factor admit",
                                      tool="factorlab test")

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["access_id"] == access_id
    assert rows[0]["kind"] == "final"


# ================================================================
# admit：缺终评拒 / 补登记放行 / 复用同一登记
# ================================================================

def test_admit_locked_without_final_and_reason_is_refused(tmp_path, monkeypatch):
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    result = _invoke("factor", "admit", str(spec))
    doc = _doc(result)
    assert result.exit_code != 0
    assert doc["error"]["code"] == "LOCKBOX_FINAL_REQUIRED"
    assert "factorlab lockbox status" in doc["error"]["hint"]
    assert "flab lockbox" not in doc["error"]["hint"]
    assert _count(db) == 0
    assert not (settings.results_dir / "refcand" / "panel.parquet").exists()


def test_admit_with_reason_registers_final_then_proceeds(tmp_path, monkeypatch):
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    result = _invoke("factor", "admit", str(spec),
                     "--lockbox-reason", "首轮终评")
    doc = _doc(result)
    assert doc["ok"] is False
    assert doc["error"]["code"] != "LOCKBOX_FINAL_REQUIRED"
    assert not str(doc["error"]["code"]).startswith("LOCKBOX_")
    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert (row["kind"], row["window_id"], row["command"]) == (
        "final", WINDOW_ID, "factor admit")
    assert row["artifact"] == str(spec)
    assert row["reason"] == "首轮终评"
    assert row["fingerprint"] == _expected_fp(load_spec(spec).model_dump(mode="json"))
    assert not (settings.results_dir / "refcand" / "panel.parquet").exists()


def test_admit_second_call_reuses_same_access_id(tmp_path, monkeypatch):
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    argv = ("factor", "admit", str(spec), "--lockbox-reason", "首轮终评")
    _invoke(*argv)
    first = _rows(db)
    assert len(first) == 1
    result = _invoke(*argv)
    doc = _doc(result)
    assert not str(doc["error"]["code"]).startswith("LOCKBOX_")
    assert "DUPLICATE" not in result.stdout
    second = _rows(db)
    assert len(second) == 1
    assert second[0]["access_id"] == first[0]["access_id"]


def test_admit_existing_final_without_reason_reuses_and_proceeds(tmp_path, monkeypatch):
    """已有终评 + 二次 admit 不带 reason + 缺产物（内链重跑）→ 复用放行不索要理由。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    _invoke("factor", "admit", str(spec), "--lockbox-reason", "首轮终评")
    first = _rows(db)
    assert len(first) == 1
    result = _invoke("factor", "admit", str(spec))
    doc = _doc(result)
    assert doc["ok"] is False
    assert not str(doc["error"]["code"]).startswith("LOCKBOX_")
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["access_id"] == first[0]["access_id"]


def test_admit_is_window_passes_without_final(tmp_path, monkeypatch):
    """门层 IS：整段早于 window.start → 无 final 无 reason 照常进入重链，零登记。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    is_spec = spec.parent / "iscand.yaml"
    is_spec.write_text(_SPEC.format(
        name="iscand", start=(W.start - dt.timedelta(days=400)).isoformat(),
        end=(W.start - dt.timedelta(days=1)).isoformat()), encoding="utf-8")
    result = _invoke("factor", "admit", str(is_spec))
    doc = _doc(result)
    assert doc["ok"] is False
    assert not str(doc["error"]["code"]).startswith("LOCKBOX_")
    assert _count(db) == 0


def test_admit_locked_without_state_is_no_state(tmp_path, monkeypatch):
    """碰箱 + 未初始化（无 state）→ LOCKBOX_NO_STATE（不是 FINAL_REQUIRED）。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    db.unlink()
    result = _invoke("factor", "admit", str(spec))
    doc = _doc(result)
    assert doc["error"]["code"] == "LOCKBOX_NO_STATE"
    assert _count(db) == 0


# ================================================================
# ref add：spec 存在（spec 指纹）/ 缺失（ref:<name> 回退指纹）两径
# ================================================================

def test_ref_add_locked_without_final_and_reason_is_refused(tmp_path, monkeypatch):
    _spec, db, ref = _sandbox(tmp_path, monkeypatch)
    before = ref.read_text(encoding="utf-8")
    result = _invoke("factor", "ref", "add", "refcand",
                     "--style", "量价", "--reason", "入选")
    doc = _doc(result)
    assert result.exit_code != 0
    assert doc["error"]["code"] == "LOCKBOX_FINAL_REQUIRED"
    assert _count(db) == 0
    assert ref.read_text(encoding="utf-8") == before


def test_ref_add_with_reason_registers_spec_path_fingerprint(tmp_path, monkeypatch):
    spec, db, ref = _sandbox(tmp_path, monkeypatch)
    result = _invoke("factor", "ref", "add", "refcand",
                     "--style", "量价", "--reason", "入选",
                     "--lockbox-reason", "首轮终评")
    doc = _doc(result)
    assert doc["ok"] is True, doc
    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert (row["kind"], row["window_id"], row["command"]) == (
        "final", WINDOW_ID, "factor ref add")
    assert row["artifact"] == str(spec)
    assert row["reason"] == "首轮终评"
    assert row["fingerprint"] == _expected_fp(load_spec(spec).model_dump(mode="json"))
    assert "refcand" in ref.read_text(encoding="utf-8")


def test_ref_add_missing_spec_locked_without_reason_is_refused(tmp_path, monkeypatch):
    _spec, db, ref = _sandbox(tmp_path, monkeypatch)
    before = ref.read_text(encoding="utf-8")
    result = _invoke("factor", "ref", "add", "ghost_factor",
                     "--style", "量价", "--reason", "入选")
    doc = _doc(result)
    assert doc["error"]["code"] == "LOCKBOX_FINAL_REQUIRED"
    assert _count(db) == 0
    assert ref.read_text(encoding="utf-8") == before


def test_ref_add_corrupt_spec_uses_ref_fallback(tmp_path, monkeypatch):
    _spec, db, ref = _sandbox(tmp_path, monkeypatch)
    broken = settings.research_root / "factor" / "demo" / "broken.yaml"
    broken.write_text("name: [unclosed\n", encoding="utf-8")
    result = _invoke("factor", "ref", "add", "broken",
                     "--style", "量价", "--reason", "入选",
                     "--lockbox-reason", "补终评")
    doc = _doc(result)
    assert doc["ok"] is True, doc
    row = _rows(db)[0]
    assert row["artifact"] == "ref:broken"
    assert row["fingerprint"] == _expected_fp(
        {"ref_name": "broken", "scales": "daily"})


def test_ref_add_missing_spec_with_reason_registers_ref_fingerprint(tmp_path, monkeypatch):
    _spec, db, ref = _sandbox(tmp_path, monkeypatch)
    result = _invoke("factor", "ref", "add", "ghost_factor",
                     "--style", "量价", "--reason", "入选",
                     "--lockbox-reason", "补终评")
    doc = _doc(result)
    assert doc["ok"] is True, doc
    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["artifact"] == "ref:ghost_factor"
    assert row["fingerprint"] == _expected_fp(
        {"ref_name": "ghost_factor", "scales": "daily"})
    assert "ghost_factor" in ref.read_text(encoding="utf-8")


def test_ref_add_is_window_passes_without_final(tmp_path, monkeypatch):
    """门层 IS：ref add 整段早于 window.start → 无 final 无 reason 直接入库，零登记。"""
    spec, db, ref = _sandbox(tmp_path, monkeypatch)
    is_spec = spec.parent / "iscand.yaml"
    is_spec.write_text(_SPEC.format(
        name="iscand", start=(W.start - dt.timedelta(days=400)).isoformat(),
        end=(W.start - dt.timedelta(days=1)).isoformat()), encoding="utf-8")
    result = _invoke("factor", "ref", "add", "iscand",
                     "--style", "量价", "--reason", "入选")
    doc = _doc(result)
    assert doc["ok"] is True, doc
    assert _count(db) == 0
    assert "iscand" in ref.read_text(encoding="utf-8")


# ================================================================
# env off：直接跳过（不读台账、不登记、不创建台账文件）
# ================================================================

def test_ref_add_env_off_skips_lockbox_branch(tmp_path, monkeypatch):
    _spec, db, ref = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "off")
    db.unlink()
    result = _invoke("factor", "ref", "add", "refcand",
                     "--style", "量价", "--reason", "入选")
    doc = _doc(result)
    assert doc["ok"] is True, doc
    assert not db.exists()
    assert "refcand" in ref.read_text(encoding="utf-8")
