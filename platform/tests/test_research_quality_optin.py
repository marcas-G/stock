"""N2（R32 终审修复）：research 门面读取门 opt-in 透传。

- `factor.run`：`--accept-quality`/`--override-reason` 解析校验（USAGE）→
  透传 `execute_run`；`DatasetQualityError` → DATA 信封（不裸传）。
- `strategy.run`：同参数面透传 `run_strategy`；usage/quality 错误各自映射。

断言来源：Plan DQ-M1 终审 N2 + 设计 §7。本文件用轻量替身隔离重链（真实 health
拒绝矩阵在 `test_cli_run.py` / `test_require_dataset.py`）。
"""
from __future__ import annotations

import argparse
import datetime as dt
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import polars as pl

from factorlab.adapters.read.health import DatasetQualityError
from factorlab.config import settings
from factorlab.research import factor as F
from factorlab.research import strategy as S
from factorlab.research.envelope import EXIT_CODES


def _factor_args(spec_path, **over) -> argparse.Namespace:
    base = dict(spec_path=spec_path, universe=None, max_memory="4GB", output_dir=None,
                no_backtest=False, groups=10, set=None, chunk_days=None,
                warmup_days=None, eval_frequency=None, wait=False,
                chunk_workers=None, no_float32=False, pretty=False,
                accept_quality=None, override_reason=None)
    base.update(over)
    return argparse.Namespace(**base)


def _strategy_args(doc_path, **over) -> argparse.Namespace:
    base = dict(doc_path=doc_path, signal=None, dry_run=False, out_dir=None,
                wait=False, pretty=False, json=True,
                accept_quality=None, override_reason=None)
    base.update(over)
    return argparse.Namespace(**base)


def _guard_ok(monkeypatch, module, tmp_path):
    monkeypatch.setattr(module, "guard_heavy",
                        lambda argv, wait=False: ({}, tmp_path / "heavy.lock"))
    monkeypatch.setattr(module, "release_slots", lambda: None)


# ================================================================
# factor.run
# ================================================================

def _factor_run_harness(tmp_path, monkeypatch):
    """替身面：guard + execute_run（surfaces 单点 import 处 patch）。"""
    import factorlab.surfaces.cli.main as cli_main
    _guard_ok(monkeypatch, F, tmp_path)
    captured: dict = {}
    run_dir = tmp_path / "results" / "demo"
    run_dir.mkdir(parents=True)
    outcome = SimpleNamespace(
        evaluation={"n_weeks": 1, "ic": {"mean": 0.0},
                    "decile_returns": {"spread": {"ret": 0.0}},
                    "frequency": "daily"},
        outputs=["signal"], notes=[], dataset_gate=None, frequency="daily")

    def fake_execute_run(spec_path_arg, **kw):
        captured.update(kw)
        return {"spec": object(), "variant": "demo",
                "ctx": SimpleNamespace(output_dir=run_dir),
                "result": SimpleNamespace(summary={}), "outcome": outcome}

    monkeypatch.setattr(cli_main, "execute_run", fake_execute_run)
    return captured


def test_factor_run_forwards_quality_opt_in(tmp_path, monkeypatch):
    captured = _factor_run_harness(tmp_path, monkeypatch)
    spec_path = tmp_path / "dummy.yaml"
    spec_path.write_text("name: dummy\n", encoding="utf-8")
    env = F.factor_run(_factor_args(spec_path, accept_quality="pass,degraded",
                                    override_reason="探索性研究"))
    assert env.ok, env.error
    assert captured["accept_quality"] == ("PASS", "DEGRADED")
    assert captured["override_reason"] == "探索性研究"


def test_factor_run_quality_default_is_pass_only(tmp_path, monkeypatch):
    captured = _factor_run_harness(tmp_path, monkeypatch)
    spec_path = tmp_path / "dummy.yaml"
    spec_path.write_text("name: dummy\n", encoding="utf-8")
    env = F.factor_run(_factor_args(spec_path))
    assert env.ok, env.error
    assert captured["accept_quality"] == ("PASS",)
    assert captured["override_reason"] is None


def test_factor_run_quality_missing_reason_is_usage(tmp_path, monkeypatch):
    captured = _factor_run_harness(tmp_path, monkeypatch)
    spec_path = tmp_path / "dummy.yaml"
    spec_path.write_text("name: dummy\n", encoding="utf-8")
    env = F.factor_run(_factor_args(spec_path, accept_quality="PASS,DEGRADED"))
    assert env.ok is False and env.error["code"] == "USAGE"
    assert "--override-reason" in env.error["message"]
    assert EXIT_CODES["USAGE"] == 2
    assert captured == {}, "用法错误不得进入执行（不该触发 execute_run）"


def test_factor_run_quality_fail_flag_is_usage(tmp_path, monkeypatch):
    captured = _factor_run_harness(tmp_path, monkeypatch)
    spec_path = tmp_path / "dummy.yaml"
    spec_path.write_text("name: dummy\n", encoding="utf-8")
    env = F.factor_run(_factor_args(spec_path, accept_quality="PASS,FAIL",
                                    override_reason="x"))
    assert env.ok is False and env.error["code"] == "USAGE"
    assert "FAIL" in env.error["message"]
    assert captured == {}


def test_factor_run_quality_reject_maps_to_data_envelope(tmp_path, monkeypatch):
    import factorlab.surfaces.cli.main as cli_main
    _guard_ok(monkeypatch, F, tmp_path)

    def boom(*_a, **_k):
        raise DatasetQualityError(
            "读取门拒绝：dataset=ashare_daily partition=2024-01-12 status=DEGRADED"
            "——不在 accept_quality=('PASS',)（默认 fail-closed）",
            dataset="ashare_daily", partition="2024-01-12", status="DEGRADED",
            freshness={"latest_trade_date": "2024-01-12"})

    monkeypatch.setattr(cli_main, "execute_run", boom)
    spec_path = tmp_path / "dummy.yaml"
    spec_path.write_text("name: dummy\n", encoding="utf-8")
    env = F.factor_run(_factor_args(spec_path, accept_quality="PASS,DEGRADED",
                                    override_reason="探索"))
    assert env.ok is False and env.error["code"] == "DATA"
    assert "读取门拒绝" in env.error["message"]
    assert "--accept-quality" in env.error["hint"]


# ================================================================
# strategy.run
# ================================================================

@contextmanager
def _fake_handle():
    yield object()


def _strategy_run_harness(tmp_path, monkeypatch):
    import test_run_strategy as trs
    _guard_ok(monkeypatch, S, tmp_path)
    monkeypatch.setattr(S, "_read_handle", _fake_handle)
    monkeypatch.setattr("factorlab.app.memory.apply_address_space_limit",
                        lambda b, **kw: None)
    results = tmp_path / "results"
    (results / trs._SIGNAL_NAME).mkdir(parents=True)
    (results / trs._SIGNAL_NAME / "summary.json").write_text(
        "{}", encoding="utf-8")
    monkeypatch.setattr(settings, "results_dir", results)
    doc_path = tmp_path / "doc.yaml"
    doc_path.write_text(trs._doc_yaml(), encoding="utf-8")
    captured: dict = {}
    nav = pl.DataFrame({"execution_date": [dt.date(2024, 1, 3)], "nav": [1.0]})
    fake_res = SimpleNamespace(
        out_dir=tmp_path / "out",
        signal_name=trs._SIGNAL_NAME, decision_count=1,
        backtest=SimpleNamespace(
            artifacts=[SimpleNamespace(fills=SimpleNamespace(
                frame=pl.DataFrame({"x": [1]})))]),
        nav_series=SimpleNamespace(frame=nav))

    def fake_run_strategy(doc, rd, **kw):
        captured.update(kw)
        return fake_res

    monkeypatch.setattr(S, "run_strategy", fake_run_strategy)
    return doc_path, captured


def test_strategy_run_forwards_quality_opt_in(tmp_path, monkeypatch):
    doc_path, captured = _strategy_run_harness(tmp_path, monkeypatch)
    env = S.strategy_run(_strategy_args(doc_path, accept_quality="PASS,DEGRADED",
                                        override_reason="探索性研究"))
    assert env.ok, env.error
    assert captured["accept_quality"] == ("PASS", "DEGRADED")
    assert captured["override_reason"] == "探索性研究"


def test_strategy_run_quality_missing_reason_is_usage(tmp_path, monkeypatch):
    doc_path, captured = _strategy_run_harness(tmp_path, monkeypatch)
    env = S.strategy_run(_strategy_args(doc_path, accept_quality="PASS,DEGRADED"))
    assert env.ok is False and env.error["code"] == "USAGE"
    assert "--override-reason" in env.error["message"]
    assert captured == {}, "用法错误不得进入策略链"


def test_strategy_run_quality_reject_maps_to_data_envelope(tmp_path, monkeypatch):
    doc_path, _captured = _strategy_run_harness(tmp_path, monkeypatch)

    def boom(*_a, **_k):
        raise DatasetQualityError(
            "读取门拒绝：dataset=ashare_daily partition=2024-01-12 status=FAIL",
            dataset="ashare_daily", partition="2024-01-12", status="FAIL")

    monkeypatch.setattr(S, "run_strategy", boom)
    env = S.strategy_run(_strategy_args(doc_path, accept_quality="PASS,DEGRADED",
                                        override_reason="探索"))
    assert env.ok is False and env.error["code"] == "DATA"
    assert "--accept-quality" in env.error["hint"]
