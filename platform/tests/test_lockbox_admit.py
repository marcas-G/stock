"""R42 T4：`factor admit` / `factor ref add` 只看测试段冻结结果（沙箱真 SQLite）。

断言来源：设计 `2026-09-24-final-test-once-discipline-design.md` §3.3/§3.4/§5 +
T4 brief + 用户裁定：
- **IS-only 不可入库**：窗口全在训练段 → `LOCKBOX_TEST_ONLY_FINAL`（零登记零产物）；
- **无 final → 入库车道执行最终测试**：显式 `final_mode=True`、进程内自设
  `FACTORLAB_PIPELINE=1`（车道身份），guard_run 内部登记 1 行；随后算测试段诊断
  （`date_start=window_start`）→ 写 `<results>/<name>_5y/test_diagnostics.json`；
- **已有 final → 只读冻结件**：冻结件版本/窗口一致才放行；缺失但 `_5y` 产物在 →
  仅重算诊断（同一次测试收尾，不重跑因子）；缺失且产物没了/版本不符 → `LOCKBOX_FINAL_REQUIRED`；
- **判决吃冻结件数**：`factor_admit`/`factor_ref_add` 的 corr_max/r2_lib/resic_t
  来自冻结件（篡改冻结件即可改判决 → 证明不是现算全窗）；ref add 的 entry 字段同源。

T2 遗留 3 红（`test_admit_with_reason_registers_final_then_proceeds` /
`test_admit_second_call_reuses_same_access_id` /
`test_admit_existing_final_without_reason_reuses_and_proceeds`）在本轮按新语义转绿：
「门内执行最终测试并只登记一次 / 二次只读冻结件零新登记 / 已有登记收尾重算」。

禁止行为：拒跑后 `lockbox_access` 零行、参考库零写入、不执行最终测试。
沙箱只碰 tmp：假执行车道（monkeypatch `cli_main.execute_run`，内部走真
`guard_run` 登记）+ 假诊断（monkeypatch `incremental_diagnostics`）；不碰真 CH/台账。
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import polars as pl
import pytest
from _lockbox import DAYS, WINDOW as W, WINDOW_ID
from typer.testing import CliRunner

from factorlab.adapters import lockbox_store as store
from factorlab.adapters.lockbox_store import connect, roll
from factorlab.config import settings
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
    monkeypatch.setenv("FACTORLAB_PIPELINE", "1")


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
    # research 层日历单点（T8）：final gate 走 adapters.run_calendar，不复用 CLI 私有 helper
    monkeypatch.setattr(store, "run_calendar", lambda: (list(DAYS), W.end))
    monkeypatch.setenv("FACTORLAB_REFERENCE", str(ref))
    # heavy 闸（flock/nice/内存预检）与本任务无关：打桩保测试稳定
    monkeypatch.setattr(F, "guard_heavy", lambda argv, wait=False: ({}, "slot"))
    monkeypatch.setattr(F, "release_slots", lambda: None)
    return spec, db, ref


def _fake_lane(tmp_path: Path, monkeypatch, db: Path, *,
               diag: tuple = (0.3, 0.4, 3.0, 0.5, 8)) -> dict:
    """假执行车道：monkeypatch `cli_main.execute_run`（真 guard_run 登记 + 写 _5y 产物）。

    车道语义与流水线一致：调用方（gate）必须显式传 `final_mode=True` 且进程内
    设好 `FACTORLAB_PIPELINE=1`——fake 内直接调用真 `store.guard_run`，两者缺一
    guard 必然报错（测试即红，防存根蒙混）。
    """
    calls: dict = {"run": [], "diag": [], "marker": []}

    def fake_execute_run(spec_path, **kw):
        calls["run"].append({"spec_path": Path(spec_path), **kw})
        calls["marker"].append(os.environ.get("FACTORLAB_PIPELINE"))
        assert kw.get("final_mode") is True, "入库车道必须显式 final_mode=True"
        spec_doc = load_spec(Path(spec_path)).model_dump(mode="json")
        store.guard_run(
            panel_start=W.start, panel_end=W.end, final_mode=True,
            reason=f"pipeline final test: {spec_doc['name']}",
            spec_doc=spec_doc, artifact=str(spec_path),
            command="factor run", tool="factorlab test",
            db_path=db, trading_days=list(DAYS), data_end=W.end)
        out = Path(kw["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({
            "date": [W.start], "code": ["000001"],
            "signal": [1.0], "forward_return_5d": [0.0],
        }).write_parquet(out / "panel.parquet")
        (out / "summary.json").write_text("{}", encoding="utf-8")
        return {"name": spec_doc["name"]}

    monkeypatch.setattr(cli_main, "execute_run", fake_execute_run)

    def fake_diag(candidates, results_dir, base, fwd_col="forward_return_5d",
                  min_stocks=30, date_start=None):
        calls["diag"].append({"candidates": list(candidates), "base": list(base),
                              "date_start": date_start,
                              "results_dir": Path(results_dir)})
        corr_max, r2_lib, resic_t, resic_mean, n_weeks = diag
        return {"kind": "incremental", "base": list(base), "candidates": [{
            "name": candidates[0], "base": list(base),
            "corr_max": corr_max, "r2_lib": r2_lib, "resic_t": resic_t,
            "resic_mean": resic_mean, "n_weeks": n_weeks}]}

    monkeypatch.setattr(
        "factorlab.app.analysis.cross_section.incremental_diagnostics", fake_diag)
    return calls


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


def _frozen(tmp_path: Path, name: str = "refcand") -> Path:
    return settings.results_dir / f"{name}_5y" / "test_diagnostics.json"


def _read_frozen(tmp_path: Path, name: str = "refcand") -> dict:
    return json.loads(_frozen(tmp_path, name).read_text(encoding="utf-8"))


def _expected_fp(spec_path: Path) -> str:
    spec_doc = load_spec(spec_path).model_dump(mode="json")
    return store.final_version_fingerprint(spec_doc=spec_doc, window_id=WINDOW_ID)


def test_admit_and_ref_add_registry_have_no_lockbox_knobs():
    """T2 遗留清理：`factor.admit`/`factor.ref.add` 参数面不得再有 `--lockbox*`。"""
    from factorlab.research import registry
    for cmd in ("factor.admit", "factor.ref.add"):
        spec = registry.COMMANDS[cmd]
        assert not [p for p in spec.params if "lockbox" in p.name], cmd
        assert not [k for k in spec.defaults if "lockbox" in k], cmd
        parser = registry.build_parser(spec)
        argv = ([str(Path("x.yaml")), "--style", "s", "--reason", "r",
                 "--lockbox-reason", "终评"] if cmd == "factor.ref.add"
                else [str(Path("x.yaml")), "--lockbox-reason", "终评"])
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def test_final_test_gate_uses_adapter_run_calendar_not_cli_helpers(tmp_path, monkeypatch):
    """T8 单点：research 层锁箱日历走 `adapters.lockbox_store.run_calendar`。

    CLI 私有 helper 打桩为硬失败——门若仍复用 surfaces helper 必红。
    """
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    _fake_lane(tmp_path, monkeypatch, db)

    def _boom():
        raise AssertionError("research 层不得复用 surfaces/cli 私有日历 helper")

    monkeypatch.setattr(cli_main, "_lockbox_published_days", _boom)
    monkeypatch.setattr(cli_main, "_lockbox_data_end", _boom)

    gate = F._final_test_gate(load_spec(spec).model_dump(mode="json"), spec,
                              reason="终评", command="factor admit")

    rows = _rows(db)
    assert len(rows) == 1
    assert gate["access_id"] == rows[0]["access_id"]
    assert rows[0]["kind"] == "final"
    assert gate["executed"] is True


def test_final_test_gate_sets_marker_and_explicit_final_mode(tmp_path, monkeypatch):
    """无 final：车道执行期必须 `final_mode=True` + 进程内 marker，退出后复原。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    monkeypatch.delenv("FACTORLAB_PIPELINE", raising=False)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    gate = F._final_test_gate(load_spec(spec).model_dump(mode="json"), spec,
                              reason="测试", command="factor admit")

    assert calls["marker"] == ["1"]
    assert calls["run"][0]["final_mode"] is True
    assert os.environ.get("FACTORLAB_PIPELINE") is None  # 复原（不给宿主留车道身份）
    assert gate["executed"] is True


# ================================================================
# admit：无 final → 执行最终测试 + 冻结件 + 1 行登记；二次只读
# ================================================================

def test_admit_without_final_executes_final_test_then_proceeds(tmp_path, monkeypatch):
    """T2 遗留红 ①（原 `test_admit_with_reason_registers_final_then_proceeds`）。

    新语义：无 `--lockbox-reason` 参数；门内执行最终测试（1 行登记）→ 测试段诊断
    （date_start=window_start）→ 冻结件；判决吃冻结件数。
    """
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    result = _invoke("factor", "admit", str(spec))
    doc = _doc(result)
    assert doc["ok"] is True, doc
    assert doc["data"]["ran"] is True
    assert doc["data"]["base"] == ["seed_a"]
    assert doc["data"]["verdict"] == "可加入"          # 0.3 / 0.4 / t=3.0
    assert doc["data"]["corr_max"] == pytest.approx(0.3)
    assert doc["data"]["resic"] == {"mean": 0.5, "t": 3.0, "n_weeks": 8}
    assert doc["data"]["test_diagnostics"] == str(_frozen(tmp_path))

    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert (row["kind"], row["window_id"], row["command"]) == (
        "final", WINDOW_ID, "factor run")
    assert row["artifact"] == str(spec)
    assert row["fingerprint"] == _expected_fp(spec)

    frozen = _read_frozen(tmp_path)
    assert frozen["version_fingerprint"] == row["fingerprint"]
    assert frozen["window_id"] == WINDOW_ID
    assert frozen["window_start"] == W.start.isoformat()
    assert frozen["date_start"] == W.start.isoformat()
    assert frozen["date_end"] == W.end.isoformat()
    assert (frozen["corr_max"], frozen["r2_lib"], frozen["resic_t"]) == (0.3, 0.4, 3.0)
    assert frozen["resic_mean"] == 0.5 and frozen["n_weeks"] == 8
    assert frozen["created_at"]

    assert len(calls["run"]) == 1
    assert calls["run"][0]["spec_path"] == spec
    assert calls["run"][0]["output_dir"] == settings.results_dir / "refcand_5y"
    assert calls["diag"] == [{"candidates": ["refcand_5y"], "base": ["seed_a_5y"],
                              "date_start": W.start.isoformat(),
                              "results_dir": Path(settings.results_dir)}]


def test_admit_second_call_reuses_same_access_id(tmp_path, monkeypatch):
    """T2 遗留红 ②：二次 admit 只读冻结件——零新登记、零重跑、零重算。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    first = _doc(_invoke("factor", "admit", str(spec)))
    first_rows = _rows(db)
    frozen_before = _read_frozen(tmp_path)
    second = _doc(_invoke("factor", "admit", str(spec)))

    assert first["data"]["verdict"] == second["data"]["verdict"]
    assert second["data"]["ran"] is False                     # 未重跑
    assert second["data"]["test_diagnostics"] == str(_frozen(tmp_path))
    assert len(calls["run"]) == 1                             # 最终测试只执行一次
    assert len(calls["diag"]) == 1                            # 冻结件命中不重算
    second_rows = _rows(db)
    assert len(second_rows) == 1
    assert second_rows[0]["access_id"] == first_rows[0]["access_id"]  # 复用同一登记
    assert _read_frozen(tmp_path) == frozen_before


def test_admit_existing_final_missing_frozen_recomputes_and_proceeds(tmp_path, monkeypatch):
    """T2 遗留红 ③（原 `test_admit_existing_final_without_reason_reuses_and_proceeds`）。

    已有 final + 冻结件缺失 + `_5y` 产物在 → 仅重算诊断（收尾），不重跑因子、零新登记。
    """
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    assert _doc(_invoke("factor", "admit", str(spec)))["ok"] is True
    _frozen(tmp_path).unlink()
    doc = _doc(_invoke("factor", "admit", str(spec)))

    assert doc["ok"] is True, doc
    assert doc["data"]["ran"] is False
    assert len(calls["run"]) == 1            # 不重跑最终测试
    assert len(calls["diag"]) == 2           # 冻结件缺失 → 仅重算
    assert len(_rows(db)) == 1               # 零新登记
    assert _read_frozen(tmp_path)["corr_max"] == pytest.approx(0.3)


def test_admit_frozen_missing_and_products_missing_rejected(tmp_path, monkeypatch):
    """已有 final 但冻结件与 `_5y` 产物都没了 → `LOCKBOX_FINAL_REQUIRED`（提示重建）。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    assert _doc(_invoke("factor", "admit", str(spec)))["ok"] is True
    _frozen(tmp_path).unlink()
    (settings.results_dir / "refcand_5y" / "panel.parquet").unlink()
    doc = _doc(_invoke("factor", "admit", str(spec)))

    assert doc["ok"] is False
    assert doc["error"]["code"] == "LOCKBOX_FINAL_REQUIRED"
    assert "xpipe" in doc["error"]["hint"]
    assert len(calls["run"]) == 1            # 不得重跑（每版本一次）
    assert len(_rows(db)) == 1


def test_admit_frozen_version_mismatch_rejected(tmp_path, monkeypatch):
    """冻结件版本指纹与登记不符（文件陈旧/被改）→ 拒（不得静默重算覆盖）。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    assert _doc(_invoke("factor", "admit", str(spec)))["ok"] is True
    frozen = _read_frozen(tmp_path)
    frozen["version_fingerprint"] = "0" * 64
    _frozen(tmp_path).write_text(json.dumps(frozen), encoding="utf-8")
    doc = _doc(_invoke("factor", "admit", str(spec)))

    assert doc["ok"] is False
    assert doc["error"]["code"] == "LOCKBOX_FINAL_REQUIRED"
    assert len(calls["run"]) == 1
    assert len(_rows(db)) == 1


def test_admit_verdict_follows_frozen_diagnostics(tmp_path, monkeypatch):
    """判决吃冻结件数（不是现算全窗）：篡改冻结件 → 二次 admit 判决随之改变。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db, diag=(0.97, 0.9, 0.1, 0.1, 8))

    first = _doc(_invoke("factor", "admit", str(spec)))
    assert first["data"]["verdict"] == "重复"         # corr_max 0.97 ≥ 0.95
    frozen = _read_frozen(tmp_path)
    frozen.update({"corr_max": 0.2, "r2_lib": 0.1, "resic_t": 2.5})
    _frozen(tmp_path).write_text(json.dumps(frozen), encoding="utf-8")
    second = _doc(_invoke("factor", "admit", str(spec)))

    assert second["ok"] is True
    assert second["data"]["corr_max"] == pytest.approx(0.2)
    assert second["data"]["verdict"] == "可加入"
    assert len(calls["diag"]) == 1                    # 从未重算


def test_admit_is_only_window_rejected(tmp_path, monkeypatch):
    """IS-only（窗口全在训练段）→ `LOCKBOX_TEST_ONLY_FINAL`（入库必须有测试段结果）。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)
    is_spec = spec.parent / "iscand.yaml"
    is_spec.write_text(_SPEC.format(
        name="iscand", start=(W.start - dt.timedelta(days=400)).isoformat(),
        end=(W.start - dt.timedelta(days=1)).isoformat()), encoding="utf-8")

    result = _invoke("factor", "admit", str(is_spec))
    doc = _doc(result)
    assert result.exit_code != 0
    assert doc["error"]["code"] == "LOCKBOX_TEST_ONLY_FINAL"
    assert "测试段" in doc["error"]["hint"]
    assert calls["run"] == [] and _rows(db) == []
    assert not (settings.results_dir / "iscand_5y").exists()


def test_admit_locked_without_state_is_no_state(tmp_path, monkeypatch):
    """碰箱 + 未初始化（无 state）→ LOCKBOX_NO_STATE（不是 FINAL_REQUIRED）。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    db.unlink()
    result = _invoke("factor", "admit", str(spec))
    doc = _doc(result)
    assert doc["error"]["code"] == "LOCKBOX_NO_STATE"


# ================================================================
# ref add：entry 字段与测试段一致；无 spec / IS-only 拒
# ================================================================

def test_ref_add_entry_fields_come_from_frozen_segment(tmp_path, monkeypatch):
    """ref add 与 admit 共用门：entry_corr_max/entry_resic_t = 测试段冻结件数。

    显式传 `--entry-*` 也不得覆盖冻结值（语义必须与测试段一致）。
    """
    spec, db, ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    result = _invoke("factor", "ref", "add", "refcand",
                     "--style", "量价", "--reason", "入选",
                     "--entry-corr-max", "0.9", "--entry-resic-t", "9.9")
    doc = _doc(result)
    assert doc["ok"] is True, doc
    assert doc["data"]["entry"]["entry_corr_max"] == pytest.approx(0.3)
    assert doc["data"]["entry"]["entry_resic_t"] == pytest.approx(3.0)
    assert doc["data"]["test_diagnostics"] == str(_frozen(tmp_path))
    assert len(_rows(db)) == 1
    assert len(calls["run"]) == 1

    from factorlab.app.analysis.reference import load_reference
    entry = load_reference(ref)["daily"][-1]
    assert entry.name == "refcand"
    assert entry.entry_corr_max == pytest.approx(0.3)
    assert entry.entry_resic_t == pytest.approx(3.0)


def test_ref_add_missing_spec_rejected(tmp_path, monkeypatch):
    """无 spec 的幽灵成员无法执行最终测试 → `LOCKBOX_FINAL_REQUIRED`，零写入。"""
    _spec, db, ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)
    before = ref.read_text(encoding="utf-8")

    result = _invoke("factor", "ref", "add", "ghost_factor",
                     "--style", "量价", "--reason", "入选")
    doc = _doc(result)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "LOCKBOX_FINAL_REQUIRED"
    assert "spec" in doc["error"]["message"]
    assert calls["run"] == [] and _rows(db) == []
    assert ref.read_text(encoding="utf-8") == before


def test_ref_add_is_only_rejected(tmp_path, monkeypatch):
    """ref add 的 IS-only 因子同样不可入库（零登记零写入）。"""
    spec, db, ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)
    is_spec = spec.parent / "iscand.yaml"
    is_spec.write_text(_SPEC.format(
        name="iscand", start=(W.start - dt.timedelta(days=400)).isoformat(),
        end=(W.start - dt.timedelta(days=1)).isoformat()), encoding="utf-8")
    before = ref.read_text(encoding="utf-8")

    result = _invoke("factor", "ref", "add", "iscand",
                     "--style", "量价", "--reason", "入选")
    doc = _doc(result)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "LOCKBOX_TEST_ONLY_FINAL"
    assert calls["run"] == [] and _rows(db) == []
    assert ref.read_text(encoding="utf-8") == before


# ================================================================
# env off：直接跳过（不读台账、不登记、不创建台账文件）
# ================================================================

def test_ref_add_env_off_skips_lockbox_branch(tmp_path, monkeypatch):
    _spec, db, ref = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "off")
    db.unlink()
    result = _invoke("factor", "ref", "add", "refcand",
                     "--style", "量价", "--reason", "入选",
                     "--entry-corr-max", "0.31", "--entry-resic-t", "2.4")
    doc = _doc(result)
    assert doc["ok"] is True, doc
    assert doc["data"]["test_diagnostics"] is None
    assert doc["data"]["entry"]["entry_corr_max"] == pytest.approx(0.31)
    assert not db.exists()
    assert "refcand" in ref.read_text(encoding="utf-8")
