"""Design-driven tests for the versioned factor-mining Prefect flow.

All runs use temporary FactorLab-native parquet artifacts and injected task
adapters. They do not connect to ClickHouse or the production lockbox ledger.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml
from factorlab.adapters.parquet_artifacts import write_factor_artifacts
from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta
from research_flows.artifacts import ArtifactRef, publish_artifact
from research_flows.factor_mining import (
    factor_mining_flow,
    factor_mining_version,
)


def _dates(start: str = "2024-01-01", end: str = "2024-05-31") -> list[dt.date]:
    first = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    dates = []
    while first <= last:
        if first.weekday() < 5:
            dates.append(first)
        first += dt.timedelta(days=7)
    return dates


def _write_native_factor(
    output: Path,
    name: str,
    *,
    spec_path: Path,
    sample_role: str = "is",
    window_id: str | None = None,
    access_id: str | None = None,
    data_version: str = "daily-v1",
    outputs: list[str] | None = None,
    invalid_ratio: float = 0.0,
) -> None:
    spec_doc = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    dates = _dates(spec_doc["date"]["start"], spec_doc["date"]["end"])
    codes = [f"{i:06d}.SZ" for i in range(40)]
    rows: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    index = 0
    for day_index, day in enumerate(dates):
        for code_index, code in enumerate(codes):
            value = float(day_index * 100 + code_index)
            signal = None if index < int(len(dates) * len(codes) * invalid_ratio) else value
            rows.append({"date": day, "code": code, "signal": signal})
            label_rows.append({
                "date": day,
                "code": code,
                "forward_return_1d": float(code_index + day_index) / 1000,
                "forward_return_5d": float(code_index - day_index) / 1000,
                "forward_return_20d": float(code_index + 1) / 500,
            })
            index += 1
    signal_frame = pl.DataFrame(
        rows,
        schema_overrides={"signal": pl.Float64},
        infer_schema_length=None,
    )
    labels = pl.DataFrame(label_rows)
    panel = signal_frame.join(labels, on=["date", "code"], how="left")
    from factorlab.core.spec import load_spec

    summary: dict[str, Any] = {
        "name": name,
        "date_start": str(signal_frame["date"].min()),
        "date_end": str(signal_frame["date"].max()),
        "signal_rows": signal_frame.height,
        "signal_null_ratio": invalid_ratio,
        "spec_yaml": yaml.safe_dump(
            load_spec(spec_path).model_dump(), allow_unicode=True, sort_keys=True
        ),
        "sample": {
            "role": sample_role,
            "window_id": window_id,
            "access_id": access_id,
        },
        "data_quality": {
            "dataset_version": data_version,
            "quality_status": "PASS",
        },
        "evaluation": {
            "dead_signal": False,
            "ic": {"mean": 0.03, "t_nw": 2.1},
            "coverage": {"mean": 1.0 - invalid_ratio},
        },
    }
    if outputs is not None:
        summary["outputs"] = outputs
    write_factor_artifacts(
        output,
        SignalArtifact(frame=signal_frame, meta=SignalMeta(name=name)),
        LabelArtifact(frame=labels),
        panel,
        summary,
    )


def _spec(tmp_path: Path, *, name: str = "alpha") -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": name,
                "category": "custom",
                "direction": 1,
                "universe": {"codes": ["000001.SZ"]},
                "date": {"start": "2024-01-01", "end": "2024-05-31"},
                "formula": "signal = close",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _publish_baseline(
    tmp_path: Path,
    spec_path: Path,
    *,
    data_version: str = "daily-v1",
    mode: str = "explore",
    sample_role: str = "is",
    window_id: str | None = "is-2024",
    access_id: str | None = None,
) -> ArtifactRef:
    root = tmp_path / f"baseline-{data_version}-{mode}"
    baseline_spec = tmp_path / f"baseline-spec-{data_version}-{mode}.yaml"
    baseline_doc = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    baseline_doc["name"] = "baseline"
    baseline_spec.write_text(
        yaml.safe_dump(baseline_doc, sort_keys=False), encoding="utf-8"
    )
    _write_native_factor(
        root,
        "baseline",
        spec_path=baseline_spec,
        sample_role=sample_role,
        window_id=window_id,
        access_id=access_id,
        data_version=data_version,
    )
    return publish_artifact(
        root,
        {
            "artifact_type": "factor_signal",
            "name": "baseline",
            "version": "baseline-v1",
            "config_sha256": "a" * 64,
            "data_version": data_version,
            "window_id": window_id,
            "sample_role": sample_role,
            "mode": mode,
            "status": "candidate",
            "platform_commit": "deadbeef",
            "access_ids": [access_id] if access_id else [],
        },
        primary_file="signal.parquet",
    )


def _config(tmp_path: Path, spec_path: Path, baseline: ArtifactRef) -> dict[str, Any]:
    return {
        "name": "alpha",
        "hypothesis": "高换手股票存在可交易的短期反转。",
        "spec_path": str(spec_path),
        "mode": "explore",
        "data_version": "daily-v1",
        "window": {
            "id": "is-2024",
            "start": "2024-01-01",
            "end": "2024-05-31",
            "sample_role": "is",
        },
        "output_root": str(tmp_path / "results" / "platform"),
        "validation": {
            "baseline_refs": [baseline.model_dump(mode="json")],
            "diagnostics_window": {"start": "2024-01-01", "end": "2024-02-29"},
            "oos_window": {"start": "2024-03-01", "end": "2024-05-31"},
            "thresholds": {
                "max_abs_corr": 0.95,
                "max_r2_lib": 0.8,
                "min_abs_resic_t": 2.0,
                "min_signed_oos_ic_t": 0.0,
                "min_diagnostic_weeks": 2,
                "min_oos_weeks": 2,
                "min_coverage": 0.8,
                "max_invalid_ratio": 0.2,
            },
        },
    }


def _successful_assessment(_candidate: Path, _config: dict[str, Any]) -> dict[str, Any]:
    return {
        "diagnostics": {
            "corr_max": 0.25,
            "r2_lib": 0.2,
            "resic_t": 2.4,
            "n_weeks": 4,
        },
        "oos": {"ic_t": 2.2, "ic_mean": 0.03, "n_weeks": 4},
    }


def _run(
    config: dict[str, Any],
    *,
    sample_role: str = "is",
    window_id: str | None = None,
    access_id: str | None = None,
    outputs: list[str] | None = None,
    invalid_ratio: float = 0.0,
    runner_calls: list[tuple[str, Path, str]] | None = None,
    assessment=_successful_assessment,
) -> ArtifactRef:
    def runner(spec_path: Path, output_dir: Path, mode: str) -> None:
        if runner_calls is not None:
            runner_calls.append((str(spec_path), output_dir, mode))
        _write_native_factor(
            output_dir,
            "alpha",
            spec_path=spec_path,
            sample_role=sample_role,
            window_id=window_id,
            access_id=access_id,
            data_version=config["data_version"],
            outputs=outputs,
            invalid_ratio=invalid_ratio,
        )

    return factor_mining_flow(
        config,
        runner=runner,
        assessment_runner=assessment,
        lint_runner=lambda _path: None,
        finalize_lockbox_runner=lambda _ids, _sha, _result: None,
    )


def test_factor_flow_requires_hypothesis_explicit_mode_window_and_data_version(
    tmp_path: Path,
):
    config = {"name": "alpha", "spec_path": "missing.yaml"}

    with pytest.raises(ValueError, match="hypothesis|mode|window|data_version"):
        factor_mining_flow(config)


def test_factor_flow_runs_and_publishes_immutable_factor_artifact(tmp_path: Path):
    spec = _spec(tmp_path)
    baseline = _publish_baseline(tmp_path, spec)
    config = _config(tmp_path, spec, baseline)
    calls: list[tuple[str, Path, str]] = []

    ref = _run(config, runner_calls=calls)

    assert ref.artifact_type == "factor_signal"
    assert ref.name == "alpha"
    assert ref.status == "candidate"
    assert ref.sample_role == "is"
    assert ref.mode == "explore"
    assert ref.version == factor_mining_version(config)
    assert calls == [(str(spec), Path(ref.artifact_uri), "explore")]
    assert (Path(ref.artifact_uri) / "summary.json").is_file()
    assert (Path(ref.artifact_uri) / "flow_manifest.json").is_file()
    assert ref.metadata["hypothesis"] == config["hypothesis"]
    assert ref.metadata["fingerprint_identity"]["spec_sha256"] == hashlib.sha256(
        spec.read_bytes()
    ).hexdigest()
    assert set(ref.metadata["evidence"]) >= {
        "single_factor_evaluation",
        "diagnostics",
        "oos",
    }
    marker = Path(ref.artifact_uri) / ".factor_mining_attempt.json"
    assert yaml.safe_load(marker.read_text(encoding="utf-8"))["hypothesis"] == (
        config["hypothesis"]
    )


def test_factor_flow_content_changes_get_new_versioned_run(tmp_path: Path):
    spec = _spec(tmp_path)
    baseline = _publish_baseline(tmp_path, spec)
    config = _config(tmp_path, spec, baseline)
    calls: list[tuple[str, Path, str]] = []

    first = _run(config, runner_calls=calls)
    config["data_version"] = "daily-v2"
    baseline_v2 = _publish_baseline(
        tmp_path,
        spec,
        data_version="daily-v2",
    )
    config["validation"]["baseline_refs"] = [
        baseline_v2.model_dump(mode="json")
    ]
    second = _run(config, runner_calls=calls)

    assert first.version != second.version
    assert Path(first.artifact_uri) != Path(second.artifact_uri)
    assert [call[2] for call in calls] == ["explore", "explore"]

    spec.write_text(spec.read_text(encoding="utf-8") + "\n# content change\n",
                    encoding="utf-8")
    third = _run(config, runner_calls=calls)
    assert third.version != second.version
    assert len(calls) == 3


def test_complete_factor_flow_replay_skips_runner_and_rechecks_identity(
    tmp_path: Path,
):
    spec = _spec(tmp_path)
    baseline = _publish_baseline(tmp_path, spec)
    config = _config(tmp_path, spec, baseline)
    calls: list[tuple[str, Path, str]] = []

    first = _run(config, runner_calls=calls)
    replay = _run(config, runner_calls=calls)

    assert replay == first
    assert len(calls) == 1

    changed = first.model_dump(mode="json")["metadata"]
    changed["fingerprint_identity"] = {"wrong": "identity"}
    # Replacing the published metadata must invalidate the immutable reference.
    manifest = Path(first.artifact_uri) / "flow_manifest.json"
    document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    document["metadata"] = changed
    manifest.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")
    with pytest.raises(ValueError, match="sha256|manifest|fingerprint|identity"):
        _run(config, runner_calls=calls)
    assert len(calls) == 1


def test_half_finished_factor_run_is_never_recomputed_or_replayed(tmp_path: Path):
    spec = _spec(tmp_path)
    baseline = _publish_baseline(tmp_path, spec)
    config = _config(tmp_path, spec, baseline)
    version = factor_mining_version(config)
    target = Path(config["output_root"]) / "alpha" / version
    target.mkdir(parents=True)
    (target / "signal.parquet").write_bytes(b"partial")
    calls: list[tuple[str, Path, str]] = []

    with pytest.raises(ValueError, match="partial|incomplete|半成品|attempt"):
        _run(config, runner_calls=calls)
    assert calls == []


def test_tampered_factor_signal_is_rejected_without_recompute(tmp_path: Path):
    spec = _spec(tmp_path)
    baseline = _publish_baseline(tmp_path, spec)
    config = _config(tmp_path, spec, baseline)
    calls: list[tuple[str, Path, str]] = []

    ref = _run(config, runner_calls=calls)
    (Path(ref.artifact_uri) / "signal.parquet").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="sha256|hash|不一致"):
        _run(config, runner_calls=calls)
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("mode", "configured_role", "run_role", "window_id", "access_id"),
    [
        ("explore", "is", "lockbox", "lock-2024Q1", "LB-1"),
        ("explore", "is", "is", None, "LB-1"),
        ("final", "lockbox", "lockbox", "lock-2024Q1", None),
        ("final", "lockbox", "is", None, None),
    ],
)
def test_factor_flow_enforces_native_mode_sample_and_access_evidence(
    tmp_path: Path,
    mode: str,
    configured_role: str,
    run_role: str,
    window_id: str | None,
    access_id: str | None,
):
    spec = _spec(tmp_path)
    baseline = _publish_baseline(tmp_path, spec)
    config = _config(tmp_path, spec, baseline)
    config["mode"] = mode
    config["window"]["sample_role"] = configured_role
    if mode == "final":
        baseline = _publish_baseline(
            tmp_path,
            spec,
            mode="final",
            sample_role="lockbox",
            window_id="lock-2024Q1",
            access_id="LB-baseline-1",
        )
        config["validation"]["baseline_refs"] = [baseline.model_dump(mode="json")]
        config["window"]["id"] = "lock-2024Q1"
        config["window"]["start"] = "2024-03-01"
        config["window"]["end"] = "2024-05-31"
        config["validation"]["diagnostics_window"] = {
            "start": "2024-03-01",
            "end": "2024-05-31",
        }
        config["validation"]["oos_window"] = {
            "start": "2024-03-01",
            "end": "2024-05-31",
        }

    with pytest.raises(ValueError, match="sample|explore|final|access|window|锁箱"):
        _run(
            config,
            sample_role=run_role,
            window_id=window_id,
            access_id=access_id,
        )
    assert not list((Path(config["output_root"]) / "alpha").glob("*/flow_manifest.json"))


def test_final_flow_passes_final_mode_to_factor_task_and_requires_lockbox_ref(
    tmp_path: Path,
):
    spec = _spec(tmp_path)
    baseline = _publish_baseline(tmp_path, spec)
    config = _config(tmp_path, spec, baseline)
    spec_doc = yaml.safe_load(spec.read_text(encoding="utf-8"))
    spec_doc["date"] = {"start": "2024-03-01", "end": "2024-05-31"}
    spec.write_text(yaml.safe_dump(spec_doc, sort_keys=False), encoding="utf-8")
    baseline = _publish_baseline(
        tmp_path,
        spec,
        mode="final",
        sample_role="lockbox",
        window_id="lock-2024Q1",
        access_id="LB-baseline-1",
    )
    config["validation"]["baseline_refs"] = [baseline.model_dump(mode="json")]
    config["mode"] = "final"
    config["window"].update(
        id="lock-2024Q1",
        start="2024-03-01",
        end="2024-05-31",
        sample_role="lockbox",
    )
    config["validation"]["diagnostics_window"] = {
        "start": "2024-03-01",
        "end": "2024-05-31",
    }
    config["validation"]["oos_window"] = {
        "start": "2024-03-01",
        "end": "2024-05-31",
    }
    calls: list[tuple[str, Path, str]] = []

    ref = _run(
        config,
        sample_role="lockbox",
        window_id="lock-2024Q1",
        access_id="LB-2024Q1-0001",
        runner_calls=calls,
    )

    assert ref.mode == "final"
    assert ref.sample_role == "lockbox"
    assert ref.access_ids == ("LB-2024Q1-0001",)
    assert calls[0][2] == "final"
    replay = _run(
        config,
        sample_role="lockbox",
        window_id="lock-2024Q1",
        access_id="LB-2024Q1-0001",
        runner_calls=calls,
    )
    assert replay == ref
    assert len(calls) == 1


def test_final_factor_resumes_incomplete_native_run_with_same_attempt_marker(
    tmp_path: Path,
):
    spec = _spec(tmp_path)
    spec_doc = yaml.safe_load(spec.read_text(encoding="utf-8"))
    spec_doc["date"] = {"start": "2024-03-01", "end": "2024-05-31"}
    spec.write_text(yaml.safe_dump(spec_doc, sort_keys=False), encoding="utf-8")
    baseline = _publish_baseline(
        tmp_path,
        spec,
        mode="final",
        sample_role="lockbox",
        window_id="lock-2024Q1",
        access_id="LB-baseline-1",
    )
    config = _config(tmp_path, spec, baseline)
    config["mode"] = "final"
    config["window"].update(
        id="lock-2024Q1",
        start="2024-03-01",
        end="2024-05-31",
        sample_role="lockbox",
    )
    config["validation"]["diagnostics_window"] = {
        "start": "2024-03-01",
        "end": "2024-05-31",
    }
    config["validation"]["oos_window"] = {
        "start": "2024-03-01",
        "end": "2024-05-31",
    }
    calls: list[Path] = []

    def interrupted_runner(spec_path: Path, output: Path, mode: str) -> None:
        assert mode == "final"
        calls.append(output)
        if len(calls) == 1:
            (output / "signal.parquet").write_bytes(b"interrupted")
            raise RuntimeError("simulated worker interruption")
        _write_native_factor(
            output,
            "alpha",
            spec_path=spec_path,
            sample_role="lockbox",
            window_id="lock-2024Q1",
            access_id="LB-final-1",
            data_version=config["data_version"],
        )

    with pytest.raises(RuntimeError, match="interruption"):
        factor_mining_flow(
            config,
            runner=interrupted_runner,
            assessment_runner=_successful_assessment,
            lint_runner=lambda _path: None,
            finalize_lockbox_runner=lambda _ids, _sha, _result: None,
        )

    target = calls[0]
    attempt = json.loads(
        (target / ".factor_mining_attempt.json").read_text(encoding="utf-8")
    )
    # The failed run left a partial signal. The second invocation must
    # recompute through the same immutable attempt, then validate and publish.
    result = factor_mining_flow(
        config,
        runner=interrupted_runner,
        assessment_runner=_successful_assessment,
        lint_runner=lambda _path: None,
        finalize_lockbox_runner=lambda _ids, _sha, _result: None,
    )
    assert calls == [target, target]
    assert attempt["identity_sha256"]
    assert result.access_ids == ("LB-final-1",)
    assert (target / "flow_manifest.json").is_file()


def test_final_factor_replay_backfills_lockbox_after_publish_interruption(
    tmp_path: Path,
):
    spec = _spec(tmp_path)
    spec_doc = yaml.safe_load(spec.read_text(encoding="utf-8"))
    spec_doc["date"] = {"start": "2024-03-01", "end": "2024-05-31"}
    spec.write_text(yaml.safe_dump(spec_doc, sort_keys=False), encoding="utf-8")
    baseline = _publish_baseline(
        tmp_path,
        spec,
        mode="final",
        sample_role="lockbox",
        window_id="lock-2024Q1",
        access_id="LB-baseline-1",
    )
    config = _config(tmp_path, spec, baseline)
    config["mode"] = "final"
    config["window"].update(
        id="lock-2024Q1",
        start="2024-03-01",
        end="2024-05-31",
        sample_role="lockbox",
    )
    config["validation"]["diagnostics_window"] = {
        "start": "2024-03-01",
        "end": "2024-05-31",
    }
    config["validation"]["oos_window"] = {
        "start": "2024-03-01",
        "end": "2024-05-31",
    }
    calls: list[Path] = []
    finalized: list[tuple[list[str], str, Path]] = []

    def runner(spec_path: Path, output: Path, mode: str) -> None:
        assert mode == "final"
        calls.append(output)
        _write_native_factor(
            output,
            "alpha",
            spec_path=spec_path,
            sample_role="lockbox",
            window_id="lock-2024Q1",
            access_id="LB-final-1",
            data_version=config["data_version"],
        )

    def interrupt_first_finalize(
        access_ids: list[str], attempt_sha256: str, result_ref: Path
    ) -> None:
        finalized.append((access_ids, attempt_sha256, result_ref))
        if len(finalized) == 1:
            raise RuntimeError("simulated lockbox backfill interruption")

    with pytest.raises(RuntimeError, match="backfill interruption"):
        factor_mining_flow(
            config,
            runner=runner,
            assessment_runner=_successful_assessment,
            lint_runner=lambda _path: None,
            finalize_lockbox_runner=interrupt_first_finalize,
        )

    result = factor_mining_flow(
        config,
        runner=runner,
        assessment_runner=_successful_assessment,
        lint_runner=lambda _path: None,
        finalize_lockbox_runner=interrupt_first_finalize,
    )

    assert len(calls) == 1
    assert result.access_ids == ("LB-final-1",)
    assert len(finalized) == 2
    assert finalized[0] == finalized[1]


def test_factor_final_worker_cannot_force_a_second_lockbox_test(
    tmp_path: Path, monkeypatch
):
    from research_flows import factor_mining

    factorlab = tmp_path / "factorlab"
    heavy = tmp_path / "heavy.sh"
    factorlab.write_text("", encoding="utf-8")
    heavy.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        factor_mining,
        "_platform_paths",
        lambda: (factorlab, heavy),
    )
    captured = {}
    monkeypatch.setattr(
        factor_mining,
        "_run_cli",
        lambda argv, *, env: captured.update(argv=argv, env=dict(env)),
    )
    monkeypatch.setenv("FACTORLAB_RE_FINAL", "1")
    spec = _spec(tmp_path)
    factor_mining._run_factorlab(
        {
            "spec_path": str(spec),
            "run_options": {},
            "output_root": str(tmp_path / "results"),
            "mode": "final",
            "name": "alpha",
            "window": {"id": "lockbox-2024"},
        },
        tmp_path / "output",
    )

    assert captured["env"]["FACTORLAB_PIPELINE"] == "1"
    assert "FACTORLAB_RE_FINAL" not in captured["env"]


def test_factor_final_worker_sets_replay_only_for_matching_attempt(
    tmp_path: Path, monkeypatch
):
    from research_flows import factor_mining

    factorlab = tmp_path / "factorlab"
    heavy = tmp_path / "heavy.sh"
    factorlab.write_text("", encoding="utf-8")
    heavy.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        factor_mining,
        "_platform_paths",
        lambda: (factorlab, heavy),
    )
    captured = []
    monkeypatch.setattr(
        factor_mining,
        "_run_cli",
        lambda argv, *, env: captured.append(dict(env)),
    )
    monkeypatch.setenv("FACTORLAB_LOCKBOX_REPLAY", "parent")
    monkeypatch.setenv("FACTORLAB_LOCKBOX_ATTEMPT_SHA256", "parent")
    base = {
        "spec_path": str(_spec(tmp_path)),
        "run_options": {},
        "output_root": str(tmp_path / "results"),
        "mode": "final",
        "name": "alpha",
        "window": {"id": "lockbox-2024"},
        "_lockbox_attempt_sha256": "a" * 64,
    }

    factor_mining._run_factorlab(base, tmp_path / "new")
    factor_mining._run_factorlab(
        {**base, "_lockbox_resume_attempt": True}, tmp_path / "retry"
    )

    assert captured[0]["FACTORLAB_LOCKBOX_ATTEMPT_SHA256"] == "a" * 64
    assert "FACTORLAB_LOCKBOX_REPLAY" not in captured[0]
    assert captured[1]["FACTORLAB_LOCKBOX_ATTEMPT_SHA256"] == "a" * 64
    assert captured[1]["FACTORLAB_LOCKBOX_REPLAY"] == "1"


def test_multi_output_factor_artifact_is_rejected(tmp_path: Path):
    spec = _spec(tmp_path)
    baseline = _publish_baseline(tmp_path, spec)
    config = _config(tmp_path, spec, baseline)

    with pytest.raises(ValueError, match="multi|多输出|single|单输出"):
        _run(config, outputs=["signal", "other"])


def test_missing_diagnostics_and_oos_evidence_fail_closed(tmp_path: Path):
    spec = _spec(tmp_path)
    baseline = _publish_baseline(tmp_path, spec)
    config = _config(tmp_path, spec, baseline)
    calls: list[tuple[str, Path, str]] = []

    with pytest.raises(ValueError, match="evidence|diagnostic|assessment|评估|证据"):
        _run(config, runner_calls=calls, assessment=lambda *_: {})
    assert len(calls) == 1
    assert not list((Path(config["output_root"]) / "alpha").glob("*/flow_manifest.json"))


def test_redundant_factor_is_published_rejected_not_accepted(tmp_path: Path):
    spec = _spec(tmp_path)
    baseline = _publish_baseline(tmp_path, spec)
    config = _config(tmp_path, spec, baseline)
    ref = _run(
        config,
        assessment=lambda *_: {
            "diagnostics": {
                "corr_max": 0.99,
                "r2_lib": 0.95,
                "resic_t": 0.1,
                "n_weeks": 4,
            },
            "oos": {"ic_t": 2.2, "ic_mean": 0.03, "n_weeks": 4},
        },
    )

    assert ref.status == "rejected"
    assert ref.metadata["evidence"]["decision"]["redundant"] is True
    assert ref.status != "accepted"


def test_invalid_signal_coverage_cannot_be_published(tmp_path: Path):
    spec = _spec(tmp_path)
    baseline = _publish_baseline(tmp_path, spec)
    config = _config(tmp_path, spec, baseline)

    with pytest.raises(ValueError, match="coverage|invalid|覆盖|缺失"):
        _run(config, invalid_ratio=0.5)
    assert not list((Path(config["output_root"]) / "alpha").glob("*/flow_manifest.json"))


def test_flow_version_identity_changes_with_engine_code_fingerprint(tmp_path: Path):
    spec = _spec(tmp_path)
    baseline = _publish_baseline(tmp_path, spec)
    config = _config(tmp_path, spec, baseline)

    from research_flows import factor_mining

    original = factor_mining._implementation_fingerprint
    try:
        factor_mining._implementation_fingerprint = lambda: "1" * 64
        first = factor_mining.factor_mining_version(config)
        factor_mining._implementation_fingerprint = lambda: "2" * 64
        second = factor_mining.factor_mining_version(config)
    finally:
        factor_mining._implementation_fingerprint = original

    assert first != second
