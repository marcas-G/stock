"""FactorArtifact to xscore panel acceptance tests (design §4.2)."""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import yaml

QR = Path(os.environ.get(
    "QUANTRESEARCH_ROOT", "/data/students/gaolei/quantresearch"
)).resolve()

from research_flows.artifacts import (  # noqa: E402
    ArtifactRef,
    content_sha256,
    publish_artifact,
)
from research_flows.xscore_flow import (  # noqa: E402
    _complete_prepared_replay,
    _lockbox_register,
    _publish_composite,
    _research_output_hashes,
    _validate_prepared_research,
    _validate_research_replay,
    assemble_factor_panel,
    parse_xscore_config,
    xscore_flow,
    xscore_version,
)


def _factor(
    out: Path,
    name: str,
    *,
    mode: str = "explore",
    sample_role: str = "is",
    window_id: str = "train-2026",
    access_ids: tuple[str, ...] = (),
) -> ArtifactRef:
    out.mkdir(parents=True)
    signal = pl.DataFrame(
        {
            "date": [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)],
            "code": ["000001.SZ", "000001.SZ", "000001.SZ"],
            "signal": [0.1, 0.2, 0.3],
        }
    )
    labels = pl.DataFrame(
        {
            "date": [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)],
            "code": ["000001.SZ", "000001.SZ", "000001.SZ"],
            "forward_return_1d": [0.01, 0.02, 0.03],
            "forward_return_5d": [0.01, 0.02, 0.03],
            "forward_return_20d": [0.01, 0.02, 0.03],
        }
    )
    from factorlab.adapters.parquet_artifacts import write_factor_artifacts
    from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta

    write_factor_artifacts(
        out,
        SignalArtifact(frame=signal, meta=SignalMeta(name=name)),
        LabelArtifact(frame=labels),
        signal.join(labels, on=["date", "code"]),
        {},
    )
    return publish_artifact(
        out,
        {
            "artifact_type": "factor_signal",
            "name": name,
            "version": "v1",
            "spec_sha256": "1" * 64,
            "config_sha256": "2" * 64,
            "data_version": "daily-test-1",
            "window_id": window_id,
            "sample_role": sample_role,
            "mode": mode,
            "status": "candidate",
            "platform_commit": "abc123",
            "access_ids": list(access_ids),
        },
        primary_file="signal.parquet",
    )


def _require_panel_adapter(monkeypatch) -> None:
    """面板适配器属于仓外 quantresearch；缺失时跳过集成用例。"""
    panel_path = QR / "lab/autoencoder42/panel.py"
    if not panel_path.is_file():
        pytest.skip(f"quantresearch 面板适配器不可用: {panel_path}")
    monkeypatch.syspath_prepend(str(QR))


def test_config_requires_inline_immutable_factor_refs_and_explicit_mode(tmp_path: Path):
    factor = _factor(tmp_path / "factor", "alpha")
    config = parse_xscore_config(
        {
            "artifact_root": str(tmp_path),
            "mode": "explore",
            "inputs": [factor.model_dump(mode="json")],
            "name": "blend",
            "groups": {"daily": ["alpha"]},
            "models": ["M0a"],
            "min_coverage": 0.9,
        }
    )
    assert config.inputs == (factor,)
    assert config.cache_dir == config.output_root / "blend" / "xscore" / ".cache"
    with pytest.raises(ValueError, match="mode"):
        parse_xscore_config(
            {
                "artifact_root": str(tmp_path),
                "inputs": [factor.model_dump(mode="json")],
                "name": "blend",
            }
        )
    with pytest.raises(ValueError, match="cache_dir"):
        parse_xscore_config(
            {
                "artifact_root": str(tmp_path),
                "output_root": str(QR / "results"),
                "cache_dir": str(tmp_path / "outside-cache"),
                "mode": "explore",
                "inputs": [factor.model_dump(mode="json")],
                "name": "blend",
                "groups": {"daily": ["alpha"]},
                "min_coverage": 0.9,
            }
        )
    with pytest.raises(ValueError, match="ArtifactRef|immutable|artifact"):
        parse_xscore_config(
            {
                "artifact_root": str(tmp_path),
                "mode": "explore",
                "inputs": [{"artifact": "factor_signal", "ref": factor.artifact_uri}],
                "name": "blend",
            }
        )


def test_panel_is_built_from_ref_files_and_preserves_ref_order(
    tmp_path: Path, monkeypatch
):
    _require_panel_adapter(monkeypatch)
    first = _factor(tmp_path / "a", "alpha")
    second = _factor(tmp_path / "b", "beta")
    output = tmp_path / "panel.npz"

    details = assemble_factor_panel(
        [first, second], output, allowed_root=tmp_path
    )

    assert output.is_file()
    assert details["members"] == ["alpha", "beta"]
    assert details["rows"] == 3
    assert details["panel_sha256"]
    from lab.autoencoder42.panel import load_panel

    panel = load_panel(output)
    assert panel.members == ("alpha", "beta")
    assert panel.raw.shape == (3, 1, 2)
    assert panel.meta["target"] == "forward_return_1d"


def test_panel_rejects_mixed_window_sample_and_duplicate_factor_names(tmp_path: Path):
    first = _factor(tmp_path / "a", "alpha")
    mixed_window = _factor(tmp_path / "b", "beta", window_id="other")
    wrong_role = _factor(
        tmp_path / "c", "alpha", sample_role="mixed", mode="final",
        access_ids=("LB-1",),
    )
    with pytest.raises(ValueError, match="window"):
        assemble_factor_panel([first, mixed_window], tmp_path / "bad.npz")
    with pytest.raises(ValueError, match="duplicate|重复|name"):
        assemble_factor_panel([first, wrong_role], tmp_path / "bad2.npz")


def test_final_mode_requires_matching_final_lockbox_refs(tmp_path: Path):
    explore_ref = _factor(tmp_path / "factor", "alpha")
    with pytest.raises(ValueError, match="final|锁箱"):
        parse_xscore_config(
            {
                "artifact_root": str(tmp_path),
                "mode": "final",
                "inputs": [explore_ref.model_dump(mode="json")],
                "name": "blend",
            }
        )


def test_xscore_version_binds_ref_identity_and_configuration(tmp_path: Path):
    ref = _factor(tmp_path / "factor", "alpha")
    common = {
        "name": "blend",
        "mode": "explore",
        "groups": {"daily": ["alpha"]},
        "models": ["M0a"],
        "walk_forward": {"train_days": 10, "test_days": 3, "step_days": 3},
    }
    first = xscore_version([ref], common, code_sha256="a" * 64)
    changed_config = xscore_version([ref], {**common, "models": ["M0b"]},
                                    code_sha256="a" * 64)
    changed_code = xscore_version([ref], common, code_sha256="b" * 64)
    assert first != changed_config
    assert first != changed_code


def test_composite_publication_round_trips_through_platform_reader(
    tmp_path: Path, monkeypatch
):
    _require_panel_adapter(monkeypatch)
    factor = _factor(tmp_path / "factor", "alpha")
    config = parse_xscore_config(
        {
            "artifact_root": str(tmp_path),
            "mode": "explore",
            "inputs": [factor.model_dump(mode="json")],
            "name": "blend",
            "groups": {"daily": ["alpha"]},
            "models": ["M0a"],
            "min_coverage": 0.9,
        }
    )
    panel_path = tmp_path / "panel.npz"
    assemble_factor_panel([factor], panel_path, allowed_root=tmp_path)
    from lab.autoencoder42.panel import load_panel

    panel = load_panel(panel_path)
    score_dir = tmp_path / "score"
    score_dir.mkdir()
    np.savez_compressed(
        score_dir / "signal.npz",
        signal=np.asarray([[0.2], [0.3], [0.4]], dtype=float),
        dates=np.asarray([str(day) for day in panel.dates]),
        codes=np.asarray([str(code) for code in panel.codes]),
    )

    ref = _publish_composite(
        refs=[factor],
        config=config,
        version="b" * 64,
        code_sha="c" * 64,
        score_dir=score_dir,
        panel_path=panel_path,
        access_ids=[],
    )

    artifact_dir = Path(ref.artifact_uri)
    assert (artifact_dir / "panel.parquet").is_file()
    assert (artifact_dir / "artifact.json").is_file()
    assert (artifact_dir / "provenance.json").is_file()
    assert ref.artifact_type == "composite_signal"
    assert ref.version == "b" * 64


def test_xscore_replay_requires_hash_verified_research_outputs(
    tmp_path: Path, monkeypatch
):
    factor = _factor(tmp_path / "factor", "alpha")
    composite_dir = tmp_path / "composite"
    composite_dir.mkdir()
    (composite_dir / "panel.parquet").write_bytes(b"composite")
    ref = publish_artifact(
        composite_dir,
        {
            "artifact_type": "composite_signal",
            "name": "blend",
            "version": "v1",
            "config_sha256": "2" * 64,
            "data_version": factor.data_version,
            "window_id": factor.window_id,
            "sample_role": factor.sample_role,
            "mode": factor.mode,
            "status": "candidate",
            "source_artifacts": [
                f"{factor.artifact_uri}#version={factor.version}"
            ],
            "platform_commit": factor.platform_commit,
            "access_ids": [],
        },
        primary_file="panel.parquet",
    )
    result_root = tmp_path / "results" / "campaign" / "xscore" / "v1"
    score_dir = result_root / "scores" / "daily_M0a"
    score_dir.mkdir(parents=True)
    panel = result_root / "panel.npz"
    panel.write_bytes(b"panel")
    metrics = result_root / "metrics.json"
    metrics.write_text("{}", encoding="utf-8")
    portfolio = result_root / "portfolio_research.json"
    portfolio.write_text("[]", encoding="utf-8")
    report = result_root / "report.md"
    report.write_text("# report\n", encoding="utf-8")
    for filename in ("signal.npz", "metrics.json", "manifest.json"):
        (score_dir / filename).write_bytes(filename.encode())
    portfolio_score = score_dir / "portfolio_open_all.json"
    portfolio_score.write_text("{}", encoding="utf-8")
    research_results = [
        {"score": "daily_M0a", "exec_mode": "open", "domain": "all"}
    ]
    scores = [{"score_dir": str(score_dir)}]
    hashes = _research_output_hashes(
        result_root,
        panel_path=panel,
        score_results=scores,
        research_results=research_results,
    )
    prepared_doc = {
        "status": "prepared",
        "version": "v1",
        "config_sha256": "2" * 64,
        "mode": "explore",
        "inputs": [factor.model_dump(mode="json")],
        "panel": {
            "path": "panel.npz",
            "panel_sha256": content_sha256(panel),
        },
        "score_results": [
            {
                "group": "daily",
                "model": "M0a",
                "score_dir": str(score_dir),
                "metrics": {},
            }
        ],
        "portfolio_results": research_results,
        "research_output_sha256": hashes,
        "lockbox": {
            "window_id": factor.window_id,
            "sample_role": factor.sample_role,
            "access_id": None,
        },
        "group_models": ["daily_M0a"],
    }
    (result_root / "prepared_manifest.json").write_text(
        json.dumps(prepared_doc), encoding="utf-8"
    )
    assert _validate_prepared_research(
        result_root,
        version="v1",
        config_sha256="2" * 64,
        mode="explore",
        refs=[factor],
    )["status"] == "prepared"
    report.write_text("# changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希"):
        _validate_prepared_research(
            result_root,
            version="v1",
            config_sha256="2" * 64,
            mode="explore",
            refs=[factor],
    )
    report.write_text("# report\n", encoding="utf-8")
    monkeypatch.setattr("research_flows.xscore_flow.QR", tmp_path)
    config = parse_xscore_config(
        {
            "artifact_root": str(tmp_path),
            "output_root": str(tmp_path / "results"),
            "campaign": "campaign",
            "mode": "explore",
            "inputs": [factor.model_dump(mode="json")],
            "name": "blend",
            "groups": {"daily": ["alpha"]},
            "models": ["M0a"],
            "min_coverage": 0.9,
        }
    )
    monkeypatch.setattr(
        "research_flows.xscore_flow._write_lockbox_manifests",
        lambda **_kwargs: None,
    )

    assert _complete_prepared_replay(
        config,
        refs=[factor],
        version="v1",
        composite_ref=ref,
        result_root=result_root,
        prepared=prepared_doc,
    ) == ref
    assert _validate_research_replay(
        result_root, version="v1", composite_ref=ref
    )["status"] == "completed"
    report.write_text("# changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希不符"):
        _validate_research_replay(result_root, version="v1", composite_ref=ref)


def test_xscore_flow_validates_research_replay_before_returning_composite(
    tmp_path: Path, monkeypatch
):
    import research_flows.xscore_flow as xscore_module

    factor = _factor(tmp_path / "factor", "alpha")
    monkeypatch.setattr(xscore_module, "QR", tmp_path)
    monkeypatch.setattr(xscore_module, "_code_sha256", lambda: "c" * 64)
    config_doc = {
        "artifact_root": str(tmp_path),
        "output_root": str(tmp_path / "results"),
        "cache_dir": str(tmp_path / "results" / "cache"),
        "campaign": "campaign",
        "mode": "explore",
        "inputs": [factor.model_dump(mode="json")],
        "name": "blend",
        "groups": {"daily": ["alpha"]},
        "models": ["M0a"],
        "min_coverage": 0.9,
    }
    config_path = tmp_path / "xscore.yaml"
    config_path.write_text(
        yaml.safe_dump(config_doc), encoding="utf-8"
    )
    config = parse_xscore_config(config_path)
    identity_config = {
        "name": config.name,
        "mode": config.mode,
        "config_sha256": config.config_sha256,
        "groups": {key: list(value) for key, value in config.groups.items()},
        "models": list(config.models),
        "walk_forward": config.walk_forward,
        "direction": config.direction,
        "composite": {
            "group": config.composite_group,
            "model": config.composite_model,
        },
    }
    version = xscore_version(
        config.inputs, identity_config, code_sha256="c" * 64
    )
    result_root = config.output_root / config.campaign / "xscore" / version
    result_root.mkdir(parents=True)
    (result_root / "flow_manifest.json").write_text(
        "{}", encoding="utf-8"
    )
    final_dir = config.artifact_root / "composites" / config.name / version
    final_dir.mkdir(parents=True)
    (final_dir / "panel.parquet").write_bytes(b"published composite")
    composite = publish_artifact(
        final_dir,
        {
            "artifact_type": "composite_signal",
            "name": "blend",
            "version": version,
            "config_sha256": config.config_sha256,
            "data_version": factor.data_version,
            "window_id": factor.window_id,
            "sample_role": "is",
            "mode": "explore",
            "status": "candidate",
            "source_artifacts": [
                f"{factor.artifact_uri}#version={factor.version}"
            ],
            "platform_commit": factor.platform_commit,
            "access_ids": [],
        },
        primary_file="panel.parquet",
    )
    monkeypatch.setattr(xscore_module, "_validate_task", lambda refs, *_a: refs)
    monkeypatch.setattr(
        xscore_module, "_validate_composite_task", lambda *_a: composite
    )
    validated = []
    monkeypatch.setattr(
        xscore_module,
        "_validate_research_replay",
        lambda *args, **kwargs: validated.append((args, kwargs)),
    )

    result = xscore_flow(config_path)

    assert result == composite
    assert validated


def test_lockbox_flow_ignores_re_final_escape_and_restores_parent_environment(
    tmp_path: Path, monkeypatch
):
    import os
    import types
    import research_flows.xscore_flow as xscore_module

    observed = []
    xlib = types.ModuleType("xlib")

    def register(**kwargs):
        observed.append((os.environ.get("FACTORLAB_RE_FINAL"), kwargs))
        return {"access_id": "LB-1"}

    xlib.lockbox_register = register
    monkeypatch.setitem(sys.modules, "xlib", xlib)
    monkeypatch.setenv("FACTORLAB_RE_FINAL", "1")

    result = _lockbox_register(
        panel_path=tmp_path / "panel.npz",
        panel_sig="panel-sha",
        config_path=tmp_path / "xscore.yaml",
        replay_ok=True,
    )

    assert result == {"access_id": "LB-1"}
    assert observed[0][0] is None
    assert observed[0][1]["replay_ok"] is True
    assert os.environ["FACTORLAB_RE_FINAL"] == "1"


def test_final_xscore_replay_finishes_prepared_artifact_without_rescoring(
    tmp_path: Path, monkeypatch
):
    import research_flows.xscore_flow as xscore_module

    factor = _factor(
        tmp_path / "factor",
        "alpha",
        mode="final",
        sample_role="lockbox",
        window_id="lockbox-2026",
        access_ids=("LB-factor",),
    )
    monkeypatch.setattr(xscore_module, "QR", tmp_path)
    monkeypatch.setattr(xscore_module, "_code_sha256", lambda: "c" * 64)
    config_doc = {
        "artifact_root": str(tmp_path),
        "output_root": str(tmp_path / "results"),
        "campaign": "campaign",
        "mode": "final",
        "window_id": "lockbox-2026",
        "inputs": [factor.model_dump(mode="json")],
        "name": "blend",
        "groups": {"daily": ["alpha"]},
        "models": ["M0a"],
        "min_coverage": 0.9,
    }
    config_path = tmp_path / "xscore-final.yaml"
    config_path.write_text(yaml.safe_dump(config_doc), encoding="utf-8")
    config = parse_xscore_config(config_path)
    identity_config = {
        "name": config.name,
        "mode": config.mode,
        "config_sha256": config.config_sha256,
        "groups": {key: list(value) for key, value in config.groups.items()},
        "models": list(config.models),
        "walk_forward": config.walk_forward,
        "direction": config.direction,
        "composite": {
            "group": config.composite_group,
            "model": config.composite_model,
        },
    }
    version = xscore_version(
        config.inputs, identity_config, code_sha256="c" * 64
    )
    result_root = config.output_root / config.campaign / "xscore" / version
    score_dir = result_root / "scores" / "daily_M0a"
    score_dir.mkdir(parents=True)
    panel_path = result_root / f"panel_{version}.npz"
    panel_path.write_bytes(b"final panel")
    for filename, content in (
        ("signal.npz", b"signal"),
        ("metrics.json", b"{}"),
        ("manifest.json", b"{}"),
        ("portfolio_open_all.json", b"{}"),
    ):
        (score_dir / filename).write_bytes(content)
    (result_root / "metrics.json").write_text("{}", encoding="utf-8")
    (result_root / "portfolio_research.json").write_text("[]", encoding="utf-8")
    (result_root / "report.md").write_text("# final report\n", encoding="utf-8")
    score_results = [{
        "group": "daily",
        "model": "M0a",
        "score_dir": str(score_dir),
        "metrics": {},
    }]
    portfolio_results = [{
        "score": "daily_M0a",
        "exec_mode": "open",
        "domain": "all",
        "result": {},
    }]
    panel_manifest = {
        "path": panel_path.name,
        "panel_sha256": content_sha256(panel_path),
        "members": ["alpha"],
        "rows": 3,
        "coverage": 1.0,
    }
    prepared = {
        "status": "prepared",
        "name": "blend",
        "version": version,
        "config_sha256": config.config_sha256,
        "mode": "final",
        "inputs": [factor.model_dump(mode="json")],
        "panel": panel_manifest,
        "group_models": ["daily_M0a"],
        "score_results": score_results,
        "portfolio_results": portfolio_results,
        "lockbox": {
            "window_id": "lockbox-2026",
            "sample_role": "lockbox",
            "access_id": "LB-xscore",
        },
        "research_output_sha256": _research_output_hashes(
            result_root,
            panel_path=panel_path,
            score_results=score_results,
            research_results=portfolio_results,
            include_lockbox_manifest=False,
        ),
    }
    _atomic_candidate = result_root / "prepared_manifest.json"
    _atomic_candidate.write_text(json.dumps(prepared), encoding="utf-8")

    source_ref = f"{factor.artifact_uri}#version={factor.version}"
    composite_dir = tmp_path / "composites" / "blend" / version
    composite_dir.mkdir(parents=True)
    (composite_dir / "panel.parquet").write_bytes(b"composite panel")
    composite = publish_artifact(
        composite_dir,
        {
            "artifact_type": "composite_signal",
            "name": "blend",
            "version": version,
            "config_sha256": config.config_sha256,
            "data_version": factor.data_version,
            "window_id": factor.window_id,
            "sample_role": "lockbox",
            "mode": "final",
            "status": "candidate",
            "source_artifacts": [source_ref],
            "platform_commit": factor.platform_commit,
            "access_ids": ["LB-factor", "LB-xscore"],
        },
        primary_file="panel.parquet",
    )

    lockbox_calls = []
    monkeypatch.setattr(xscore_module, "_validate_task", lambda refs, *_a: refs)
    monkeypatch.setattr(
        xscore_module, "_validate_composite_task", lambda *_a: composite
    )

    def replay_lockbox(**kwargs):
        lockbox_calls.append(kwargs)
        return {
            "window_id": "lockbox-2026",
            "sample_role": "lockbox",
            "access_id": "LB-xscore",
        }

    monkeypatch.setattr(xscore_module, "_lockbox_register", replay_lockbox)
    monkeypatch.setattr(
        xscore_module, "_write_lockbox_manifests", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        xscore_module,
        "_score_task",
        lambda *_a, **_kw: pytest.fail("final replay must not score again"),
    )

    assert xscore_module.xscore_flow(config_path) == composite
    assert len(lockbox_calls) == 1 and lockbox_calls[0]["replay_ok"] is True
    completed = json.loads((result_root / "flow_manifest.json").read_text())
    assert completed["status"] == "completed"
    assert completed["artifact_sha256"] == composite.artifact_sha256
