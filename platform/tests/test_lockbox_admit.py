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
- **参考库准入**：|resIC t|≥3、corr_max<0.7、retention≥0.5 必须同时满足；
  手工 entry 参数不能绕过判决，也不能在备份前写入观察候选。

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

import numpy as np
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
    # 基座产物预检：参考成员必须有 `<b>_5y/panel.parquet`（否则最终测试零跑零登记）
    _write_panel(settings.results_dir / "seed_a_5y" / "panel.parquet")
    # heavy 闸（flock/nice/内存预检）与本任务无关：打桩保测试稳定
    monkeypatch.setattr(F, "guard_heavy", lambda argv, wait=False: ({}, "slot"))
    monkeypatch.setattr(F, "release_slots", lambda: None)
    return spec, db, ref


def _write_panel(path: Path) -> None:
    """最小可读 panel.parquet（仅占位；诊断被 fake，不读内容）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "date": [W.start], "code": ["000001"],
        "signal": [1.0], "forward_return_5d": [0.0],
    }).write_parquet(path)


def _write_variant(qr: Path, name: str, start: str, end: str) -> Path:
    """ref-sync 5y 变体：`$QR/experiments/r37_5y/<name>_5y.yaml`（规范候选解析优先）。"""
    d = qr / "experiments" / "r37_5y"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}_5y.yaml"
    p.write_text(_SPEC.format(name=name, start=start, end=end), encoding="utf-8")
    return p


def _write_final_products(out_dir: Path) -> None:
    """最终测试 `_5y` 产物占位（收尾重算路径的存在性判据）。"""
    _write_panel(out_dir / "panel.parquet")
    (out_dir / "summary.json").write_text("{}", encoding="utf-8")


def _fake_lane(tmp_path: Path, monkeypatch, db: Path, *,
               diag: tuple = (0.3, 0.4, 3.0, 0.5, 8),
               retention: float = 0.8) -> dict:
    """假执行车道：monkeypatch `cli_main.execute_run`（真 guard_run 登记 + 写 _5y 产物）。

    车道语义与流水线一致：调用方（gate）必须显式传 `final_mode=True` 且进程内
    设好 `FACTORLAB_PIPELINE=1`——fake 内直接调用真 `store.guard_run`，两者缺一
    guard 必然报错（测试即红，防存根蒙混）。审计理由按 CLI 语义消费
    `FACTORLAB_LOCKBOX_REASON`（车道来源前缀）。
    """
    calls: dict = {"run": [], "diag": [], "marker": [], "reason": [], "env": []}
    lane_env_keys = ("FACTORLAB_DATA_BACKEND", "FACTORLAB_ST_DEGRADE",
                     "FACTORLAB_MINUTE_UNCOVERED")

    def fake_execute_run(spec_path, **kw):
        calls["run"].append({"spec_path": Path(spec_path), **kw})
        calls["marker"].append(os.environ.get("FACTORLAB_PIPELINE"))
        lane_reason = os.environ.get("FACTORLAB_LOCKBOX_REASON")
        calls["reason"].append(lane_reason)
        calls["env"].append({k: os.environ.get(k) for k in lane_env_keys})
        assert kw.get("final_mode") is True, "入库车道必须显式 final_mode=True"
        spec_doc = load_spec(Path(spec_path)).model_dump(mode="json")
        store.guard_run(
            panel_start=W.start, panel_end=W.end, final_mode=True,
            reason=lane_reason or f"pipeline final test: {spec_doc['name']}",
            spec_doc=spec_doc, artifact=str(spec_path),
            command="factor run", tool="factorlab test",
            db_path=db, trading_days=list(DAYS), data_end=W.end)
        _write_final_products(Path(kw["output_dir"]))
        return {"name": spec_doc["name"]}

    monkeypatch.setattr(cli_main, "execute_run", fake_execute_run)

    def fake_diag(candidates, results_dir, base, fwd_col="forward_return_5d",
                  min_stocks=30, date_start=None, frequency="weekly"):
        calls["diag"].append({"candidates": list(candidates), "base": list(base),
                              "date_start": date_start,
                              "fwd_col": fwd_col, "frequency": frequency,
                              "results_dir": Path(results_dir)})
        corr_max, r2_lib, resic_t, resic_mean, n_weeks = diag
        return {"kind": "incremental", "base": list(base), "candidates": [{
            "name": candidates[0], "base": list(base),
            "corr_max": corr_max, "r2_lib": r2_lib, "resic_t": resic_t,
            "resic_mean": resic_mean, "n_weeks": n_weeks,
            "retention": retention}]}

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
    assert calls["reason"] == ["测试"]
    assert calls["run"][0]["final_mode"] is True
    assert os.environ.get("FACTORLAB_PIPELINE") is None  # 复原（不给宿主留车道身份）
    assert os.environ.get("FACTORLAB_LOCKBOX_REASON") is None
    assert gate["executed"] is True


def test_final_test_gate_restores_host_marker_value(tmp_path, monkeypatch):
    """宿主已有 `FACTORLAB_PIPELINE` 时：执行期覆盖为 1，退出复原宿主值。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORLAB_PIPELINE", "host")
    calls = _fake_lane(tmp_path, monkeypatch, db)

    F._final_test_gate(load_spec(spec).model_dump(mode="json"), spec,
                       reason="测试", command="factor admit")

    assert calls["marker"] == ["1"]
    assert os.environ.get("FACTORLAB_PIPELINE") == "host"


def test_execute_guard_consumes_lane_reason_env(tmp_path, monkeypatch):
    """`_lockbox_guard_for_execute` 消费 `FACTORLAB_LOCKBOX_REASON`（车道审计前缀）。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_main, "_lockbox_published_days", lambda: list(DAYS))
    monkeypatch.setattr(cli_main, "_lockbox_data_end", lambda: W.end)
    monkeypatch.setenv("FACTORLAB_LOCKBOX_REASON", "ref add final test: refcand")

    guard = cli_main._lockbox_guard_for_execute(load_spec(spec), spec, final_mode=True)

    assert guard.access_id
    assert _rows(db)[0]["reason"] == "ref add final test: refcand"


def test_final_test_lane_sets_mining_env_defaults(tmp_path, monkeypatch):
    """入库车道 = 生产车道：未设挖矿口径 env 时按 `_member_env` 同口径默认。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    for key in ("FACTORLAB_DATA_BACKEND", "FACTORLAB_ST_DEGRADE",
                "FACTORLAB_MINUTE_UNCOVERED"):
        monkeypatch.delenv(key, raising=False)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    F._final_test_gate(load_spec(spec).model_dump(mode="json"), spec,
                       reason="测试", command="factor admit")

    assert calls["env"] == [{"FACTORLAB_DATA_BACKEND": "ch",
                             "FACTORLAB_ST_DEGRADE": "allow",
                             "FACTORLAB_MINUTE_UNCOVERED": "drop"}]


def test_final_test_lane_keeps_explicit_mining_env(tmp_path, monkeypatch):
    """显式已设值优先（setdefault 语义）：`FACTORLAB_ST_DEGRADE=off` 不得被覆盖。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORLAB_ST_DEGRADE", "off")
    monkeypatch.delenv("FACTORLAB_DATA_BACKEND", raising=False)
    monkeypatch.delenv("FACTORLAB_MINUTE_UNCOVERED", raising=False)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    F._final_test_gate(load_spec(spec).model_dump(mode="json"), spec,
                       reason="测试", command="factor admit")

    assert calls["env"][0]["FACTORLAB_ST_DEGRADE"] == "off"
    assert calls["env"][0]["FACTORLAB_DATA_BACKEND"] == "ch"
    assert calls["env"][0]["FACTORLAB_MINUTE_UNCOVERED"] == "drop"


def test_final_test_lane_forces_backend_ch_over_explicit_env(tmp_path, monkeypatch):
    """I1：`FACTORLAB_DATA_BACKEND` **强制 ch**（pipeline `_member_env` 同口径）——
    显式 duckdb 也不得放行（否则最终测试在半口径下跑并占版本）；宿主值执行后复原。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORLAB_DATA_BACKEND", "duckdb")
    calls = _fake_lane(tmp_path, monkeypatch, db)

    F._final_test_gate(load_spec(spec).model_dump(mode="json"), spec,
                       reason="测试", command="factor admit")

    assert calls["env"][0]["FACTORLAB_DATA_BACKEND"] == "ch"
    assert os.environ.get("FACTORLAB_DATA_BACKEND") == "duckdb"  # 复原宿主值


def test_final_test_lane_restores_mining_env_after_run(tmp_path, monkeypatch):
    """执行返回后宿主 env 复原（三键回到调用前状态；默认注入不残留）。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    monkeypatch.delenv("FACTORLAB_DATA_BACKEND", raising=False)
    monkeypatch.setenv("FACTORLAB_ST_DEGRADE", "off")
    monkeypatch.setenv("FACTORLAB_MINUTE_UNCOVERED", "keep")
    calls = _fake_lane(tmp_path, monkeypatch, db)

    F._final_test_gate(load_spec(spec).model_dump(mode="json"), spec,
                       reason="测试", command="factor admit")

    assert calls["env"] == [{"FACTORLAB_DATA_BACKEND": "ch",
                             "FACTORLAB_ST_DEGRADE": "off",
                             "FACTORLAB_MINUTE_UNCOVERED": "keep"}]
    assert os.environ.get("FACTORLAB_DATA_BACKEND") is None
    assert os.environ.get("FACTORLAB_ST_DEGRADE") == "off"
    assert os.environ.get("FACTORLAB_MINUTE_UNCOVERED") == "keep"


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
    assert row["reason"] == "admit final test: refcand"   # 车道来源前缀进台账

    frozen = _read_frozen(tmp_path)
    assert frozen["version_fingerprint"] == row["fingerprint"]
    assert frozen["window_id"] == WINDOW_ID
    assert frozen["window_start"] == W.start.isoformat()
    assert frozen["date_start"] == W.start.isoformat()
    assert frozen["date_end"] == W.end.isoformat()
    assert frozen["diagnostics_schema"] == F._TEST_DIAGNOSTICS_SCHEMA
    assert frozen["frequency"] == "daily"
    assert frozen["fwd_col"] == "forward_return_1d"
    assert (frozen["corr_max"], frozen["r2_lib"], frozen["resic_t"]) == (0.3, 0.4, 3.0)
    assert frozen["resic_mean"] == 0.5 and frozen["n_weeks"] == 8
    assert frozen["created_at"]

    assert len(calls["run"]) == 1
    assert calls["run"][0]["spec_path"] == spec           # 无变体 → 回退用户 spec
    assert calls["run"][0]["output_dir"] == settings.results_dir / "refcand_5y"
    assert calls["reason"] == ["admit final test: refcand"]
    assert doc["data"]["spec_used"] == str(spec)
    assert doc["data"]["spec_note"] is None
    assert calls["diag"] == [{"candidates": ["refcand_5y"], "base": ["seed_a_5y"],
                              "date_start": W.start.isoformat(),
                              "fwd_col": "forward_return_1d",
                              "frequency": "daily",
                              "results_dir": Path(settings.results_dir)}]


def test_final_test_diagnostics_follow_weekly_spec_target(
        tmp_path, monkeypatch):
    """最终测试冻结件沿用 weekly spec.target，而不是默认 5d 标签。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    spec.write_text(
        _SPEC.format(name="refcand", start=W.start.isoformat(),
                     end=W.end.isoformat())
        + "\ntarget: forward_return_20d\n"
          "evaluation_frequency: weekly\n",
        encoding="utf-8",
    )
    calls = _fake_lane(tmp_path, monkeypatch, db)

    result = _invoke("factor", "admit", str(spec))
    doc = _doc(result)

    assert doc["ok"] is True, doc
    assert calls["diag"] == [{
        "candidates": ["refcand_5y"], "base": ["seed_a_5y"],
        "date_start": W.start.isoformat(),
        "fwd_col": "forward_return_20d",
        "frequency": "weekly",
        "results_dir": Path(settings.results_dir),
    }]
    frozen = _read_frozen(tmp_path)
    assert frozen["frequency"] == "weekly"
    assert frozen["fwd_col"] == "forward_return_20d"
    assert len(calls["run"]) == 1
    assert len(_rows(db)) == 1


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
    # 用户选择的操作门槛是 |t| >= 3；冻结 t=2.5 必须判为观察。
    frozen.update({"corr_max": 0.2, "r2_lib": 0.1, "resic_t": 2.5})
    _frozen(tmp_path).write_text(json.dumps(frozen), encoding="utf-8")
    second = _doc(_invoke("factor", "admit", str(spec)))

    assert second["ok"] is True
    assert second["data"]["corr_max"] == pytest.approx(0.2)
    assert second["data"]["verdict"] == "观察"
    assert len(calls["diag"]) == 1                    # 从未重算


def test_admit_migrates_old_frozen_diagnostics_without_retesting(
        tmp_path, monkeypatch):
    """旧冻结件即使已有 retention，也要按新 cadence metadata 重算。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    first = _doc(_invoke("factor", "admit", str(spec)))
    assert first["ok"] is True
    frozen = _read_frozen(tmp_path)
    frozen.update({
        "diagnostics_schema": 1,
        "frequency": "weekly",
        "fwd_col": "forward_return_5d",
    })
    _frozen(tmp_path).write_text(json.dumps(frozen), encoding="utf-8")

    second = _doc(_invoke("factor", "admit", str(spec)))

    assert second["ok"] is True
    assert second["data"]["retention"] == pytest.approx(0.8)
    assert second["data"]["verdict"] == "可加入"
    assert _read_frozen(tmp_path)["frequency"] == "daily"
    assert _read_frozen(tmp_path)["fwd_col"] == "forward_return_1d"
    assert len(calls["run"]) == 1
    assert len(calls["diag"]) == 2       # 仅重算诊断，不重跑 final
    assert len(_rows(db)) == 1


@pytest.mark.parametrize(
    "corruption",
    [
        "boolean_retention",
        "string_retention",
        "nan_resic_t",
        "infinite_r2_lib",
        "missing_corr_max",
        "missing_r2_lib",
        "missing_retention",
        "missing_resic_t",
        "missing_resic_mean",
        "missing_n_weeks",
    ],
)
def test_admit_recomputes_malformed_frozen_diagnostics(
        tmp_path, monkeypatch, corruption):
    """坏缓存不能绕过数值准入，也不能让缺字段变成 KeyError。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    first = _doc(_invoke("factor", "admit", str(spec)))
    assert first["ok"] is True
    frozen = _read_frozen(tmp_path)
    if corruption == "boolean_retention":
        # bool 是 Python int 的子类，也是 JSON 合法值；不能当有限数值准入。
        frozen["retention"] = True
    elif corruption == "string_retention":
        frozen["retention"] = "0.8"
    elif corruption == "nan_resic_t":
        frozen["resic_t"] = float("nan")
    elif corruption == "infinite_r2_lib":
        frozen["r2_lib"] = float("inf")
    else:
        field = corruption.removeprefix("missing_")
        del frozen[field]
    _frozen(tmp_path).write_text(json.dumps(frozen), encoding="utf-8")

    second = _doc(_invoke("factor", "admit", str(spec)))

    assert second["ok"] is True
    assert second["data"]["verdict"] == "可加入"
    assert second["data"]["retention"] == pytest.approx(0.8)
    assert len(calls["run"]) == 1
    assert len(calls["diag"]) == 2  # 复用同次 final，只重算诊断
    assert len(_rows(db)) == 1
    repaired = _read_frozen(tmp_path)
    assert repaired["retention"] == pytest.approx(0.8)
    assert repaired["n_weeks"] == 8


def test_admit_damaged_frozen_json_returns_controlled_refusal(
        tmp_path, monkeypatch):
    """无法解析的冻结件受控拒绝，不泄漏 JSONDecodeError/不误判准入。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    assert _doc(_invoke("factor", "admit", str(spec)))["ok"] is True
    _frozen(tmp_path).write_text("{broken", encoding="utf-8")

    doc = _doc(_invoke("factor", "admit", str(spec)))

    assert doc["ok"] is False
    assert doc["error"]["code"] == "LOCKBOX_FINAL_REQUIRED"
    assert "冻结诊断文件损坏" in doc["error"]["message"]
    assert len(calls["run"]) == 1
    assert len(calls["diag"]) == 1
    assert len(_rows(db)) == 1


def test_admit_rejects_recomputed_nonfinite_diagnostics(
        tmp_path, monkeypatch):
    """诊断重算仍含 NaN/Inf 时受控拒绝，不能进入 JOIN。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(
        tmp_path, monkeypatch, db, diag=(0.3, 0.4, float("nan"), 0.5, 8))

    doc = _doc(_invoke("factor", "admit", str(spec)))

    assert doc["ok"] is False
    assert doc["error"]["code"] == "DATA"
    assert "非有限数值" in doc["error"]["message"]
    assert len(calls["run"]) == 1
    assert len(calls["diag"]) == 1
    assert len(_rows(db)) == 1
    assert not _frozen(tmp_path).exists()


def test_test_diagnostics_validation_accepts_numpy_numeric_scalars():
    """Real resIC calculations may yield NumPy floats; JSON-safe schema accepts them."""
    doc = F._jsonify({
        "version_fingerprint": "fingerprint",
        "window_id": WINDOW_ID,
        "window_start": W.start.isoformat(),
        "date_start": W.start.isoformat(),
        "date_end": W.end.isoformat(),
        "diagnostics_schema": F._TEST_DIAGNOSTICS_SCHEMA,
        "frequency": "daily",
        "fwd_col": "forward_return_1d",
        "corr_max": np.float64(0.3),
        "r2_lib": np.float64(0.4),
        "retention": np.float64(0.8),
        "resic_t": np.float64(3.2),
        "resic_mean": np.float64(0.1),
        "n_weeks": np.int64(8),
        "created_at": "2026-09-25T00:00:00+00:00",
    })

    assert F._test_diagnostics_valid(
        doc, fingerprint="fingerprint", window=W,
        frequency="daily", fwd_col="forward_return_1d")


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


def test_ref_add_subthreshold_frozen_diagnostics_reject_before_write(
        tmp_path, monkeypatch):
    """最终测试冻结件低于 |t|=3 门槛时，不得备份或改写参考库。"""
    spec, db, ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db,
                       diag=(0.3, 0.4, 2.999, 0.5, 8))
    before = ref.read_text(encoding="utf-8")

    result = _invoke("factor", "ref", "add", "refcand",
                     "--style", "量价", "--reason", "入选")
    doc = _doc(result)

    assert doc["ok"] is False
    assert doc["error"]["code"] == "DATA"
    assert "要求 ≥3" in doc["error"]["message"]
    assert len(calls["run"]) == 1 and len(_rows(db)) == 1
    assert ref.read_text(encoding="utf-8") == before
    assert not ref.with_name(ref.name + ".bak").exists()


@pytest.mark.parametrize(
    "diag, retention",
    [
        ((0.8, 0.1, 3.2, 0.5, 8), 0.8),
        ((0.3, 0.1, 3.2, 0.5, 8), 0.49),
    ],
)
def test_ref_add_observational_d10_verdict_rejects_before_write(
        tmp_path, monkeypatch, diag, retention):
    """t≥3 仍需满足 D10 独立性及 retention；观察候选不得进入库。"""
    _spec, db, ref = _sandbox(tmp_path, monkeypatch)
    _fake_lane(tmp_path, monkeypatch, db, diag=diag, retention=retention)
    before = ref.read_text(encoding="utf-8")

    doc = _doc(_invoke("factor", "ref", "add", "refcand",
                       "--style", "量价", "--reason", "入选"))

    assert doc["ok"] is False
    assert doc["error"]["code"] == "DATA"
    assert "verdict=观察" in doc["error"]["message"]
    assert ref.read_text(encoding="utf-8") == before
    assert not ref.with_name(ref.name + ".bak").exists()


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
    # M9：缺 spec 的指引必须是"补 spec"（而非 `make xpipe` 重建冻结件）
    assert "补 spec" in doc["error"]["hint"]
    assert "factor/**" in doc["error"]["hint"]
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
    _fake_lane(tmp_path, monkeypatch, db)
    db.unlink()
    result = _invoke("factor", "ref", "add", "refcand",
                     "--style", "量价", "--reason", "入选",
                     "--entry-corr-max", "0.31", "--entry-resic-t", "2.4")
    doc = _doc(result)
    assert doc["ok"] is True, doc
    assert doc["data"]["test_diagnostics"] is None
    assert doc["data"]["entry"]["entry_corr_max"] == pytest.approx(0.3)
    assert doc["data"]["entry"]["entry_resic_t"] == pytest.approx(3.0)
    assert not db.exists()
    assert "refcand" in ref.read_text(encoding="utf-8")


def test_ref_add_env_off_manual_entry_cannot_bypass_admission_floor(
        tmp_path, monkeypatch):
    _spec, db, ref = _sandbox(tmp_path, monkeypatch)
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "off")
    _fake_lane(tmp_path, monkeypatch, db,
               diag=(0.3, 0.4, 2.999, 0.5, 8))
    db.unlink()
    before = ref.read_text(encoding="utf-8")

    result = _invoke("factor", "ref", "add", "refcand",
                     "--style", "量价", "--reason", "入选",
                     "--entry-corr-max", "0.1", "--entry-resic-t", "99.0")
    doc = _doc(result)

    assert doc["ok"] is False
    assert doc["error"]["code"] == "DATA"
    assert "要求 ≥3" in doc["error"]["message"]
    assert ref.read_text(encoding="utf-8") == before
    assert not ref.with_name(ref.name + ".bak").exists()
    assert not db.exists()


# ================================================================
# 修复轮1：基座预检 / 规范候选 spec 解析 / 异常路径
# ================================================================

def test_resolve_candidate_spec_prefers_variant_then_source(tmp_path, monkeypatch):
    """规范解析单点：变体存在优先；否则回退 `factor/**`；都没有 → None。"""
    spec, _db, _ref = _sandbox(tmp_path, monkeypatch)
    assert F.resolve_candidate_spec("refcand") == spec
    variant = _write_variant(settings.research_root, "refcand",
                             W.start.isoformat(), W.end.isoformat())
    assert F.resolve_candidate_spec("refcand") == variant
    assert F.resolve_candidate_spec("ghost_missing") is None


def test_admit_uses_canonical_variant_and_matches_ref_sync_fp(tmp_path, monkeypatch):
    """规范 5y 变体存在 → 指纹/最终测试身份用变体，与 ref-sync guard 登记同 fp。

    源 spec 故意 IS-only（直接用它必 TEST_ONLY_FINAL）。ref-sync 已登记 final 且
    `_5y` 产物在 → admit 仅收尾重算：零重跑、零新登记、输出注明所用 spec。
    """
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    spec.write_text(_SPEC.format(
        name="refcand", start=(W.start - dt.timedelta(days=400)).isoformat(),
        end=(W.start - dt.timedelta(days=1)).isoformat()), encoding="utf-8")
    variant = _write_variant(settings.research_root, "refcand",
                             W.start.isoformat(), W.end.isoformat())
    store.guard_run(  # 模拟 pipeline ref-sync：变体 spec 登记 final + 产物
        panel_start=W.start, panel_end=W.end, final_mode=True,
        reason="pipeline final test: refcand",
        spec_doc=load_spec(variant).model_dump(mode="json"),
        artifact=str(variant), command="factor run", tool="factorlab test",
        db_path=db, trading_days=list(DAYS), data_end=W.end)
    _write_final_products(settings.results_dir / "refcand_5y")
    calls = _fake_lane(tmp_path, monkeypatch, db)

    doc = _doc(_invoke("factor", "admit", str(spec)))

    assert doc["ok"] is True, doc
    assert doc["data"]["ran"] is False                    # ref-sync 已测 → 只收尾
    assert calls["run"] == []                             # 不触发第二次最终测试
    assert calls["diag"][0]["date_start"] == W.start.isoformat()
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["fingerprint"] == _expected_fp(variant)  # 同一 helper 复算
    assert _read_frozen(tmp_path)["version_fingerprint"] == rows[0]["fingerprint"]
    assert doc["data"]["spec_used"] == str(variant)
    assert "r37_5y" in (doc["data"]["spec_note"] or "")
    # M1 负向：name/date 口径差异不算漂移（内容一致 → 无警告）
    assert "内容不一致" not in (doc["data"]["spec_note"] or "")


def test_admit_warns_when_source_spec_drifted_from_variant(tmp_path, monkeypatch):
    """M1：变体生成后源 spec 实质更新（name/date 之外）→ `spec_note` 追加警告，不阻断。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    variant = _write_variant(settings.research_root, "refcand",
                             W.start.isoformat(), W.end.isoformat())
    store.guard_run(  # ref-sync 已登记 + 产物：admit 只读冻结件
        panel_start=W.start, panel_end=W.end, final_mode=True,
        reason="pipeline final test: refcand",
        spec_doc=load_spec(variant).model_dump(mode="json"),
        artifact=str(variant), command="factor run", tool="factorlab test",
        db_path=db, trading_days=list(DAYS), data_end=W.end)
    _write_final_products(settings.results_dir / "refcand_5y")
    calls = _fake_lane(tmp_path, monkeypatch, db)
    spec.write_text(_SPEC.format(name="refcand", start=W.start.isoformat(),
                                 end=W.end.isoformat())
                    .replace("signal = close", "signal = close * -1"),
                    encoding="utf-8")  # 源 spec 在变体生成后更新（公式变化）

    doc = _doc(_invoke("factor", "admit", str(spec)))

    assert doc["ok"] is True, doc
    assert calls["run"] == []            # 警告不阻断、不重跑
    assert len(_rows(db)) == 1
    assert "内容不一致" in (doc["data"]["spec_note"] or "")


def test_ref_add_canonical_variant_no_second_final(tmp_path, monkeypatch):
    """ref add（变体在 + ref-sync 已登记）→ 不触发第二次最终测试，登记行数不变。"""
    spec, db, ref = _sandbox(tmp_path, monkeypatch)
    spec.write_text(_SPEC.format(
        name="refcand", start=(W.start - dt.timedelta(days=400)).isoformat(),
        end=(W.start - dt.timedelta(days=1)).isoformat()), encoding="utf-8")
    variant = _write_variant(settings.research_root, "refcand",
                             W.start.isoformat(), W.end.isoformat())
    store.guard_run(
        panel_start=W.start, panel_end=W.end, final_mode=True,
        reason="pipeline final test: refcand",
        spec_doc=load_spec(variant).model_dump(mode="json"),
        artifact=str(variant), command="factor run", tool="factorlab test",
        db_path=db, trading_days=list(DAYS), data_end=W.end)
    _write_final_products(settings.results_dir / "refcand_5y")
    calls = _fake_lane(tmp_path, monkeypatch, db)

    doc = _doc(_invoke("factor", "ref", "add", "refcand",
                       "--style", "量价", "--reason", "入选"))

    assert doc["ok"] is True, doc
    assert calls["run"] == []                     # 零重跑
    assert len(_rows(db)) == 1                    # 零新登记
    assert doc["data"]["spec"] == str(variant)    # 规范解析结果注明
    assert doc["data"]["entry"]["entry_corr_max"] == pytest.approx(0.3)
    assert _read_frozen(tmp_path)["version_fingerprint"] == _rows(db)[0]["fingerprint"]
    assert "refcand" in ref.read_text(encoding="utf-8")


def test_final_test_gate_preflights_base_products(tmp_path, monkeypatch):
    """执行最终测试前预检基座 `_5y` 产物：缺 → 零跑零登记零冻结（issue #35）。"""
    spec, db, ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)
    assert not (settings.results_dir / "ghost_base_5y").exists()
    ref.write_text(_REF_YAML.rstrip("\n")
                   + "\n    - name: ghost_base\n"
                     "      style: \"风格G\"\n"
                     "      reason: \"基座产物缺失\"\n"
                     "      added: \"2024-01-01\"\n", encoding="utf-8")

    result = _invoke("factor", "admit", str(spec))
    doc = _doc(result)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "DATA"
    assert "ghost_base" in doc["error"]["message"]
    assert "xpipe-data" in doc["error"]["hint"]
    assert calls["run"] == [] and calls["diag"] == []
    assert _rows(db) == []
    assert not _frozen(tmp_path).exists()


def test_final_test_gate_empty_base_rejected(tmp_path, monkeypatch):
    """空 base（无可对照成员）→ DATA，零跑零登记零冻结。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    calls = _fake_lane(tmp_path, monkeypatch, db)

    with pytest.raises(F.FinalTestError) as e:
        F._final_test_gate(load_spec(spec).model_dump(mode="json"), spec,
                           reason="测试", command="factor admit", base=[])

    assert e.value.code == "DATA" and "为空" in e.value.message
    assert calls["run"] == [] and _rows(db) == []
    assert not _frozen(tmp_path).exists()


def test_admit_final_run_failure_is_run_failed_without_freeze(tmp_path, monkeypatch):
    """执行最终测试抛 ValueError → `RUN_FAILED`；无冻结件、无登记。"""
    spec, db, _ref = _sandbox(tmp_path, monkeypatch)
    _fake_lane(tmp_path, monkeypatch, db)
    attempts: list = []

    def boom_execute_run(spec_path, **kw):
        attempts.append(Path(spec_path))
        raise ValueError("synthetic run failure")

    monkeypatch.setattr(cli_main, "execute_run", boom_execute_run)
    result = _invoke("factor", "admit", str(spec))
    doc = _doc(result)

    assert doc["ok"] is False
    assert doc["error"]["code"] == "RUN_FAILED"
    assert "synthetic run failure" in doc["error"]["message"]
    assert attempts == [spec]
    assert _rows(db) == []
    assert not _frozen(tmp_path).exists()
