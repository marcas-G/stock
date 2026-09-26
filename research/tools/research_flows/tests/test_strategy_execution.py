"""TDD acceptance tests for the independent strategy-execution flow.

The tests use a Prefect shim and injected platform I/O. They exercise version
binding, call order, immutable output layout, and publication gates without CH.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

_TOOLS = Path(__file__).resolve().parents[2]
_PLATFORM_SRC = _TOOLS.parents[1] / "platform" / "src"
for _path in (_TOOLS, _PLATFORM_SRC):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def _prefect_shim(monkeypatch):
    prefect = types.ModuleType("prefect")

    def decorate(*dargs, **dkwargs):
        if dargs and callable(dargs[0]) and len(dargs) == 1 and not dkwargs:
            return dargs[0]

        def apply(fn):
            return fn

        return apply

    prefect.flow = decorate
    prefect.task = decorate
    monkeypatch.setitem(sys.modules, "prefect", prefect)


def _reload_flow(monkeypatch):
    _prefect_shim(monkeypatch)
    sys.modules.pop("research_flows.strategy_execution", None)
    import research_flows.strategy_execution as module
    return module


def _publish_signal(root: Path, *, artifact_type="factor_signal",
                    mode="explore", sample_role="is", access_ids=()):
    from research_flows.artifacts import publish_artifact

    root.mkdir(parents=True)
    primary = "signal.parquet" if artifact_type == "factor_signal" else "panel.parquet"
    (root / primary).write_bytes(b"immutable-signal-payload")
    doc = {
        "artifact_type": artifact_type,
        "name": "alpha",
        "version": "factor-v1",
        "config_sha256": "a" * 64,
        "spec_sha256": "b" * 64,
        "data_version": "daily-2026-09-24",
        "window_id": "2026Q3",
        "sample_role": sample_role,
        "mode": mode,
        "status": "accepted",
        "source_artifacts": [],
        "platform_commit": "platform-deadbeef",
        "access_ids": list(access_ids),
    }
    return publish_artifact(root, doc, primary_file=primary)


_SPEC = """\
name: strategy_alpha
signal: alpha
direction: 1
portfolio:
  top_k: 2
  weighting: equal_weight
  rebalance_frequency: daily
execution:
  timing: NEXT_OPEN
  initial_cash: 1000000.0
  cost_model:
    commission_rate: 0.00025
    minimum_commission: 5.0
    stamp_tax_sell_rate: 0.0005
    transfer_fee_rate: 0.00001
    slippage_bps: 5.0
date: {start: "2025-01-02", end: "2025-01-03"}
"""


class _FakeRead:
    def close(self):
        pass


def _input_signal(name="alpha"):
    from factorlab.core.domain.frames import SignalArtifact, SignalMeta

    return SignalArtifact(
        frame=pl.DataFrame({
            "date": [__import__("datetime").date(2025, 1, 2),
                     __import__("datetime").date(2025, 1, 3)],
            "code": ["000001.SZ", "000001.SZ"],
            "signal": [1.0, 2.0],
        }),
        meta=SignalMeta(name=name),
    )


def _fake_execution_dependencies(tmp_path, *, sample=None, calls=None):
    calls = calls if calls is not None else []

    @contextlib.contextmanager
    def read_handle():
        calls.append("open_read")
        yield _FakeRead()
        calls.append("close_read")

    def run_strategy(doc, rd, *, results_dir, out_dir, final_mode, doc_path):
        calls.append(("run_strategy", final_mode, doc.strategy.signal_name))
        calls.append(("pipeline_env", os.environ.get("FACTORLAB_PIPELINE")))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "nav").mkdir()
        (out_dir / "nav" / "nav_series.parquet").write_bytes(b"nav-payload")
        (out_dir / "target_portfolio.parquet").write_bytes(b"target-payload")
        (out_dir / "rebalance_schedule.parquet").write_bytes(b"schedule-payload")
        (out_dir / "strategy_manifest.json").write_text(json.dumps({
            "sample": sample or {"role": "is", "window_id": "2026Q3",
                                 "access_id": None},
        }), encoding="utf-8")
        (out_dir / "manifest.json").write_text(json.dumps({
            "artifact_type": "backtest_result",
            "schema_version": "1",
            "execution_timing": "next_open",
        }), encoding="utf-8")
        return SimpleNamespace(out_dir=out_dir)

    def load_strategy_artifacts(path):
        calls.append("load_strategy_artifacts")
        return SimpleNamespace(
            spec=SimpleNamespace(signal_name="alpha", name="strategy_alpha"),
            target=SimpleNamespace(decision_dates=(
                __import__("datetime").date(2025, 1, 2),
                __import__("datetime").date(2025, 1, 3))),
        )

    def load_backtest_result(path):
        calls.append("load_backtest_result")
        return SimpleNamespace(
            nav_series=SimpleNamespace(frame=pl.DataFrame({
                "execution_date": [__import__("datetime").date(2025, 1, 3)],
                "cash": [900.0], "market_value": [100.0], "nav": [1000.0],
            })),
            artifacts=(),
        )

    def report_builder(bundle, backtest, *, mode, sample, signal_ref):
        calls.append(("report", mode, sample["role"]))
        return "# Strategy report\n"

    @contextlib.contextmanager
    def heavy_guard(config):
        calls.append("heavy_guard")
        yield

    return {
        "load_signal_artifact": lambda path: (calls.append(("factor_load", path))
                                               or _input_signal()),
        "read_composite_artifact": lambda path: (
            calls.append(("composite_load", path)),
            (pl.DataFrame({"date": [__import__("datetime").date(2025, 1, 2)],
                           "code": ["000001.SZ"], "signal": [1.0]}),
             {"name": "alpha", "frequency": "1d"}, {}),
        )[1],
        "open_read": read_handle,
        "run_strategy": run_strategy,
        "load_strategy_artifacts": load_strategy_artifacts,
        "load_backtest_result": load_backtest_result,
        "report_builder": report_builder,
        "heavy_guard": heavy_guard,
        "platform_commit": lambda: "platform-deadbeef",
        "calls": calls,
    }


def test_signal_reference_is_validated_before_strategy_spec(tmp_path, monkeypatch):
    module = _reload_flow(monkeypatch)
    ref = _publish_signal(tmp_path / "factor")
    calls = []

    def validate(ref, **kwargs):
        calls.append("validate_ref")
        return ref

    deps = _fake_execution_dependencies(tmp_path, calls=calls)
    deps["validate_signal_input"] = validate
    with pytest.raises(FileNotFoundError):
        module.run_strategy_execution(
            {"mode": "explore", "strategy_spec": str(tmp_path / "missing.yaml"),
             "output_root": str(tmp_path / "out")},
            ref,
            dependencies=deps,
        )

    assert calls[0] == "validate_ref"
    assert any(isinstance(call, tuple) and call[0] == "factor_load"
               for call in calls)
    assert not any(isinstance(x, tuple) and x[0] == "run_strategy"
                   for x in calls)


def test_final_strategy_worker_cannot_force_a_second_lockbox_test(
    tmp_path, monkeypatch
):
    module = _reload_flow(monkeypatch)
    ref = _publish_signal(
        tmp_path / "factor",
        mode="final",
        sample_role="lockbox",
        access_ids=("LB-1",),
    )
    captured = {}

    def fake_run(argv, *, check, capture_output, text, env, cwd):
        del check, capture_output, text, cwd
        captured["env"] = dict(env)
        request = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
        Path(request["response_path"]).write_text(
            json.dumps(request["ref"]), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setenv("FACTORLAB_RE_FINAL", "1")

    assert module._run_in_heavy_worker({"mode": "final"}, ref) == ref
    assert "FACTORLAB_RE_FINAL" not in captured["env"]
    assert os.environ["FACTORLAB_RE_FINAL"] == "1"


def test_strategy_flow_executes_resolved_composite_and_publishes_last(
        tmp_path, monkeypatch):
    module = _reload_flow(monkeypatch)
    ref = _publish_signal(tmp_path / "composite",
                          artifact_type="composite_signal")
    spec_path = tmp_path / "strategy.yaml"
    spec_path.write_text(_SPEC, encoding="utf-8")
    calls = []
    deps = _fake_execution_dependencies(tmp_path, calls=calls)
    real_validate = module.validate_signal_input

    def validate_then_record(ref, **kwargs):
        calls.append("validate_ref")
        return real_validate(ref, **kwargs)

    deps["validate_signal_input"] = validate_then_record
    original_publish = deps.get("publish_artifact")

    def publish(*args, **kwargs):
        calls.append("publish")
        if original_publish is None:
            from research_flows.artifacts import publish_artifact
            return publish_artifact(*args, **kwargs)
        return original_publish(*args, **kwargs)

    deps["publish_artifact"] = publish
    result = module.run_strategy_execution(
        {"mode": "explore", "strategy_spec": str(spec_path),
         "output_root": str(tmp_path / "out")},
        ref,
        dependencies=deps,
    )

    assert result.artifact_type == "strategy_backtest"
    assert result.status == "completed"
    assert result.sample_role == "is"
    assert result.source_artifacts == (ref.artifact_uri,)
    out = Path(result.artifact_uri)
    assert out.parent == tmp_path / "out" / "strategies" / "strategy_alpha"
    assert (out / "target_portfolio.parquet").is_file()
    assert (out / "strategy_manifest.json").is_file()
    assert (out / "manifest.json").is_file()
    assert (out / "report.md").read_text(encoding="utf-8") == "# Strategy report\n"
    assert (out / "flow_manifest.json").is_file()
    assert calls.index("validate_ref") < next(
        i for i, call in enumerate(calls)
        if isinstance(call, tuple) and call[0] == "composite_load")
    assert calls.index("load_backtest_result") < calls.index(("report", "explore", "is"))
    assert calls.index(("report", "explore", "is")) < calls.index("publish")


def test_strategy_flow_rejects_unsupported_execution_before_open_read(
        tmp_path, monkeypatch):
    module = _reload_flow(monkeypatch)
    ref = _publish_signal(tmp_path / "factor")
    spec_path = tmp_path / "unsupported.yaml"
    spec_path.write_text(_SPEC.replace("NEXT_OPEN", "NEXT_CLOSE"), encoding="utf-8")
    calls = []
    deps = _fake_execution_dependencies(tmp_path, calls=calls)

    with pytest.raises((ValueError, NotImplementedError), match="NEXT_CLOSE|timing"):
        module.run_strategy_execution(
            {"mode": "explore", "strategy_spec": str(spec_path),
             "output_root": str(tmp_path / "out")},
            ref,
            dependencies=deps,
        )

    assert "open_read" not in calls
    assert not any(isinstance(x, tuple) and x[0] == "run_strategy"
                   for x in calls)


def test_final_flow_requires_lockbox_evidence_before_publication(
        tmp_path, monkeypatch):
    module = _reload_flow(monkeypatch)
    pipeline_before = os.environ.get("FACTORLAB_PIPELINE")
    ref = _publish_signal(
        tmp_path / "factor", mode="final", sample_role="lockbox",
        access_ids=("UPSTREAM-LB-1",))
    spec_path = tmp_path / "strategy.yaml"
    spec_path.write_text(_SPEC, encoding="utf-8")
    calls = []
    deps = _fake_execution_dependencies(
        tmp_path, sample={"role": "lockbox", "window_id": "2026Q3",
                          "access_id": None}, calls=calls)

    with pytest.raises(ValueError, match="access|锁箱"):
        module.run_strategy_execution(
            {"mode": "final", "strategy_spec": str(spec_path),
             "output_root": str(tmp_path / "out")},
            ref,
            dependencies=deps,
        )

    assert ("run_strategy", True, "alpha") in calls
    assert ("pipeline_env", "1") in calls
    assert os.environ.get("FACTORLAB_PIPELINE") == pipeline_before
    out_dirs = list((tmp_path / "out" / "strategies" / "strategy_alpha").glob("*"))
    assert out_dirs
    assert all(not (path / "flow_manifest.json").exists() for path in out_dirs)


def test_completed_strategy_artifact_replay_does_not_run_again(
        tmp_path, monkeypatch):
    module = _reload_flow(monkeypatch)
    ref = _publish_signal(tmp_path / "factor")
    spec_path = tmp_path / "strategy.yaml"
    spec_path.write_text(_SPEC, encoding="utf-8")
    calls = []
    deps = _fake_execution_dependencies(tmp_path, calls=calls)
    config = {"mode": "explore", "strategy_spec": str(spec_path),
              "output_root": str(tmp_path / "out")}

    first = module.run_strategy_execution(config, ref, dependencies=deps)
    calls.clear()
    replay = module.run_strategy_execution(config, ref, dependencies=deps)

    assert replay == first
    assert not any(isinstance(x, tuple) and x[0] == "run_strategy"
                   for x in calls)
    assert "open_read" not in calls
    assert "heavy_guard" not in calls


def test_strategy_spec_change_creates_new_immutable_version(
        tmp_path, monkeypatch):
    module = _reload_flow(monkeypatch)
    ref = _publish_signal(tmp_path / "factor")
    spec_path = tmp_path / "strategy.yaml"
    spec_path.write_text(_SPEC, encoding="utf-8")
    calls = []
    deps = _fake_execution_dependencies(tmp_path, calls=calls)
    config = {"mode": "explore", "strategy_spec": str(spec_path),
              "output_root": str(tmp_path / "out")}

    first = module.run_strategy_execution(config, ref, dependencies=deps)
    spec_path.write_text(_SPEC + "\n# reviewed strategy revision\n",
                         encoding="utf-8")
    second = module.run_strategy_execution(config, ref, dependencies=deps)

    assert first.version != second.version
    assert Path(first.artifact_uri) != Path(second.artifact_uri)
    assert len([call for call in calls
                if isinstance(call, tuple) and call[0] == "run_strategy"]) == 2


def test_replay_rejects_tampered_report_instead_of_trusting_nav_hash(
        tmp_path, monkeypatch):
    module = _reload_flow(monkeypatch)
    ref = _publish_signal(tmp_path / "factor")
    spec_path = tmp_path / "strategy.yaml"
    spec_path.write_text(_SPEC, encoding="utf-8")
    calls = []
    deps = _fake_execution_dependencies(tmp_path, calls=calls)
    config = {"mode": "explore", "strategy_spec": str(spec_path),
              "output_root": str(tmp_path / "out")}

    result = module.run_strategy_execution(config, ref, dependencies=deps)
    (Path(result.artifact_uri) / "report.md").write_text("tampered\n",
                                                         encoding="utf-8")

    with pytest.raises(ValueError, match="report_sha256"):
        module.run_strategy_execution(config, ref, dependencies=deps)
