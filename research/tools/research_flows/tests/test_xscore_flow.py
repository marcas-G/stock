"""FactorArtifact to xscore panel acceptance tests (design §4.2)."""
from __future__ import annotations

import json
import inspect
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
import research_flows.xscore_flow as xscore_module  # noqa: E402
from research_flows.xscore_flow import (  # noqa: E402
    _complete_prepared_replay,
    _ensure_final_attempt_marker,
    _lockbox_register,
    _publish_composite,
    _render_xscore_report,
    _resume_prepared_publication,
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


def test_xscore_shared_helpers_do_not_shadow_platform_tools_lib():
    import subprocess

    stock_root = Path(__file__).resolve().parents[4]
    research_tools = stock_root / "research" / "tools"
    platform_tools = stock_root / "platform" / "tools"
    platform_src = stock_root / "platform" / "src"
    env = os.environ.copy()
    # Load and cache the platform's generic `lib` before importing the flow.
    env["PYTHONPATH"] = os.pathsep.join(
        map(str, (platform_tools, research_tools, platform_src))
    )
    pipeline_dir = stock_root / "research" / "tools" / "xscore" / "pipeline"
    loaders = (
        "from research_flows import xscore_flow; "
        "module = xscore_flow.xscore_lockbox",
        f"import sys; sys.path.insert(0, {str(pipeline_dir)!r}); "
        "import xlib; import sys; "
        "module = sys.modules['research_xscore_lockbox']",
    )
    for loader in loaders:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import lib.moneyflow; "
                f"{loader}; "
                "from pathlib import Path; "
                "assert Path(module.__file__).name == 'research_xscore_lockbox.py'",
            ],
            cwd=stock_root,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr


def test_default_artifact_root_is_platform_results(tmp_path: Path, monkeypatch):
    factor = _factor(tmp_path / "factor", "alpha")
    monkeypatch.setattr(
        xscore_module,
        "_validate_factor_refs_basic",
        lambda refs, allowed_root: tuple(refs),
    )

    config = parse_xscore_config(
        {
            "mode": "explore",
            "inputs": [factor.model_dump(mode="json")],
            "name": "blend",
            "groups": {"daily": ["alpha"]},
            "min_coverage": 0.9,
        }
    )

    assert config.artifact_root == (QR / "results" / "platform").resolve()


@pytest.mark.parametrize(
    ("mode", "expected_pipeline"),
    [("explore", None), ("final", "1")],
)
def test_platform_worker_propagates_mode_environment(
    tmp_path: Path, monkeypatch, mode: str, expected_pipeline: str | None
):
    captured: dict[str, str | None] = {}

    def fake_run_heavy(argv, *, cwd, env):
        del cwd
        captured["pipeline"] = env.get("FACTORLAB_PIPELINE")
        request = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
        Path(request["response"]).write_text(
            json.dumps({"ok": True}), encoding="utf-8"
        )
        return ""

    monkeypatch.setattr(xscore_module, "_run_heavy", fake_run_heavy)
    monkeypatch.setenv("FACTORLAB_PIPELINE", "parent")

    assert xscore_module._run_platform_worker(
        "validate", {}, mode=mode
    ) == {"ok": True}
    assert captured["pipeline"] == expected_pipeline
    assert os.environ["FACTORLAB_PIPELINE"] == "parent"


def test_panel_is_built_from_ref_files_and_preserves_ref_order(
    tmp_path: Path, monkeypatch
):
    _require_panel_adapter(monkeypatch)
    first = _factor(tmp_path / "a", "alpha")
    second = _factor(tmp_path / "b", "beta")
    output = tmp_path / "panel.npz"
    from factorlab.adapters import parquet_artifacts

    loaded: list[tuple[Path, Path]] = []
    load_signal_artifact = parquet_artifacts.load_signal_artifact

    def tracked_loader(path):
        caller = Path(inspect.currentframe().f_back.f_code.co_filename).resolve()
        loaded.append((Path(path), caller))
        return load_signal_artifact(path)

    monkeypatch.setattr(
        parquet_artifacts, "load_signal_artifact", tracked_loader
    )

    details = assemble_factor_panel(
        [first, second], output, allowed_root=tmp_path
    )

    assert output.is_file()
    assert [
        item for item in loaded if item[1] == Path(xscore_module.__file__).resolve()
    ] == [
        (Path(first.artifact_uri), Path(xscore_module.__file__).resolve()),
        (Path(second.artifact_uri), Path(xscore_module.__file__).resolve()),
    ]
    assert details["members"] == ["alpha", "beta"]
    assert details["rows"] == 3
    assert details["coverage"] == 1.0
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


@pytest.mark.parametrize(
    ("field", "expanded"),
    [
        ("groups", {"daily": ["alpha"], "minute": ["alpha"]}),
        ("models", ["M0a", "M0b"]),
        ("portfolio.exec", ["open", "close"]),
        ("portfolio.domains", ["all", "Q1Q3"]),
    ],
)
def test_final_config_rejects_multiple_candidates(
    tmp_path: Path, monkeypatch, field: str, expanded
):
    monkeypatch.setattr(xscore_module, "QR", tmp_path)
    factor = _factor(
        tmp_path / "factor",
        "alpha",
        mode="final",
        sample_role="lockbox",
        access_ids=("LB-1",),
    )
    config = {
        "artifact_root": str(tmp_path),
        "output_root": str(tmp_path / "results"),
        "mode": "final",
        "inputs": [factor.model_dump(mode="json")],
        "name": "blend",
        "groups": {"daily": ["alpha"]},
        "models": ["M0a"],
        "composite": {"group": "daily", "model": "M0a"},
        "min_coverage": 0.9,
        "portfolio": {"exec": ["open"], "domains": ["all"]},
    }
    if field.startswith("portfolio."):
        config["portfolio"][field.split(".", 1)[1]] = expanded
    else:
        config[field] = expanded

    with pytest.raises(ValueError, match="final 模式只允许一个"):
        parse_xscore_config(config)


def test_final_config_accepts_one_preselected_candidate_and_report_is_explicit(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(xscore_module, "QR", tmp_path)
    factor = _factor(
        tmp_path / "factor",
        "alpha",
        mode="final",
        sample_role="lockbox",
        access_ids=("LB-1",),
    )
    config = parse_xscore_config(
        {
            "artifact_root": str(tmp_path),
            "output_root": str(tmp_path / "results"),
            "mode": "final",
            "inputs": [factor.model_dump(mode="json")],
            "name": "blend",
            "groups": {"daily": ["alpha"]},
            "models": ["M0a"],
            "composite": {"group": "daily", "model": "M0a"},
            "min_coverage": 0.9,
            "portfolio": {"exec": ["open"], "domains": ["all"]},
        }
    )
    report = _render_xscore_report(
        config,
        factor_count=1,
        panel_sha256="a" * 64,
        version="b" * 64,
        score_results=[{
            "group": "daily",
            "model": "M0a",
            "metrics": {"ic": {"mean": 0.02, "t_nw": 2.1}},
        }],
    )
    assert "- final 候选: `daily/M0a`" in report
    assert "- final 组合口径: `open/all`" in report
    assert "IC t(NW) 为描述统计" in report
    assert "| daily_M0a | 0.020000 | 2.100 |" in report


def test_final_config_requires_explicit_composite_candidate(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(xscore_module, "QR", tmp_path)
    factor = _factor(
        tmp_path / "factor",
        "alpha",
        mode="final",
        sample_role="lockbox",
        access_ids=("LB-1",),
    )
    with pytest.raises(ValueError, match="final 模式必须显式声明"):
        parse_xscore_config(
            {
                "artifact_root": str(tmp_path),
                "output_root": str(tmp_path / "results"),
                "mode": "final",
                "inputs": [factor.model_dump(mode="json")],
                "name": "blend",
                "groups": {"daily": ["alpha"]},
                "models": ["M0a"],
                "min_coverage": 0.9,
                "portfolio": {"exec": ["open"], "domains": ["all"]},
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


def test_composite_publication_recovers_native_artifact_missing_flow_manifest(
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
    args = {
        "refs": [factor],
        "config": config,
        "version": "b" * 64,
        "code_sha": "c" * 64,
        "score_dir": score_dir,
        "panel_path": panel_path,
        "access_ids": [],
    }

    first = _publish_composite(**args)
    artifact_dir = Path(first.artifact_uri)
    (artifact_dir / "flow_manifest.json").unlink()

    recovered = _publish_composite(**args)

    assert recovered == first
    assert (artifact_dir / "flow_manifest.json").is_file()


def test_final_attempt_marker_fails_closed_on_unknown_nonempty_directory(
    tmp_path: Path,
):
    result_root = tmp_path / "attempt"
    result_root.mkdir()
    (result_root / "unexpected.txt").write_text("do not overwrite")
    expected = {
        "artifact_type": "xscore_final_attempt",
        "flow_attempt_sha256": "a" * 64,
    }

    with pytest.raises(ValueError, match="marker 缺失"):
        _ensure_final_attempt_marker(result_root, expected)


def test_final_attempt_marker_cleans_only_atomic_write_orphan(tmp_path: Path):
    result_root = tmp_path / "attempt"
    result_root.mkdir()
    orphan = result_root / ".final_attempt.json.123.tmp"
    orphan.write_text("{")
    expected = {
        "artifact_type": "xscore_final_attempt",
        "flow_attempt_sha256": "a" * 64,
    }

    assert _ensure_final_attempt_marker(result_root, expected) == "a" * 64
    assert not orphan.exists()
    assert json.loads((result_root / "final_attempt.json").read_text()) == expected


def test_unprepared_final_retry_clears_only_its_shared_auxiliary_caches(
    tmp_path: Path,
):
    version = "version-123"
    result_root = tmp_path / "campaign" / "xscore" / version
    result_root.mkdir(parents=True)
    (result_root / "final_attempt.json").write_text("{}", encoding="utf-8")
    shared_cache = tmp_path / "campaign" / "xscore" / ".cache"
    shared_cache.mkdir()
    partial_names = [
        f"{prefix}_{version}.npz"
        for prefix in ("open_adj", "mv", "limits", "amount")
    ]
    for name in partial_names:
        (shared_cache / name).write_bytes(b"partial npz")
    other_version_cache = shared_cache / "mv_other-version.npz"
    unrelated_cache = shared_cache / "operator-owned.txt"
    other_version_cache.write_bytes(b"valid other version")
    unrelated_cache.write_text("keep", encoding="utf-8")

    xscore_module._reset_unprepared_final_outputs(
        result_root, version=version, cache_dir=shared_cache
    )

    assert not any((shared_cache / name).exists() for name in partial_names)
    assert other_version_cache.read_bytes() == b"valid other version"
    assert unrelated_cache.read_text(encoding="utf-8") == "keep"


def test_prepared_final_recovery_checks_lockbox_before_publishing(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(xscore_module, "QR", tmp_path)
    factor = _factor(
        tmp_path / "factor",
        "alpha",
        mode="final",
        sample_role="lockbox",
        window_id="lockbox-2026",
        access_ids=("LB-factor",),
    )
    config_path = tmp_path / "xscore.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "artifact_root": str(tmp_path),
                "output_root": str(tmp_path / "results"),
                "campaign": "campaign",
                "mode": "final",
                "window_id": "lockbox-2026",
                "inputs": [factor.model_dump(mode="json")],
                "name": "blend",
                "groups": {"daily": ["alpha"]},
                "models": ["M0a"],
                "composite": {"group": "daily", "model": "M0a"},
                "min_coverage": 0.9,
            }
        ),
        encoding="utf-8",
    )
    config = parse_xscore_config(config_path)
    result_root = tmp_path / "prepared"
    result_root.mkdir()
    panel_path = result_root / "panel.npz"
    panel_path.write_bytes(b"prepared panel")
    prepared = {
        "panel": {
            "path": panel_path.name,
            "panel_sha256": content_sha256(panel_path),
        },
        "lockbox": {
            "window_id": "lockbox-2026",
            "sample_role": "lockbox",
            "access_id": "LB-original",
        },
        "score_results": [{
            "group": "daily",
            "model": "M0a",
            "score_dir": str(result_root / "scores" / "daily_M0a"),
        }],
    }
    monkeypatch.setattr(
        xscore_module,
        "_lockbox_register",
        lambda **_kwargs: {
            "window_id": "lockbox-2026",
            "sample_role": "lockbox",
            "access_id": "LB-different",
        },
    )
    monkeypatch.setattr(
        xscore_module,
        "_publish_composite_task",
        lambda *_args: pytest.fail("must validate attempt before publish"),
    )

    with pytest.raises(ValueError, match="prepared attempt"):
        _resume_prepared_publication(
            config,
            refs=[factor],
            version="v" * 64,
            code_sha="c" * 64,
            result_root=result_root,
            prepared=prepared,
            flow_attempt_sha256="a" * 64,
            artifact_already_published=False,
        )


def test_final_xscore_resumes_same_attempt_after_registration_before_prepared(
    tmp_path: Path, monkeypatch
):
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
        "composite": {"group": "daily", "model": "M0a"},
        "min_coverage": 0.9,
    }
    config_path = tmp_path / "xscore-final.yaml"
    config_path.write_text(yaml.safe_dump(config_doc), encoding="utf-8")
    config = parse_xscore_config(config_path)
    _, version = xscore_module._xscore_identity(config, "c" * 64)
    result_root = config.output_root / config.campaign / "xscore" / version
    marker = xscore_module._final_attempt_marker(
        config, version=version, code_sha="c" * 64
    )
    attempt_sha = xscore_module._ensure_final_attempt_marker(result_root, marker)
    panel_path = result_root / f"panel_{version}.npz"

    monkeypatch.setattr(xscore_module, "_validate_task", lambda refs, *_a: refs)

    def assemble(_refs, panel_file, *_args):
        Path(panel_file).write_bytes(b"stable panel")
        return {
            "panel_sha256": content_sha256(panel_file),
            "members": ["alpha"],
            "rows": 3,
            "coverage": 1.0,
        }

    monkeypatch.setattr(xscore_module, "_assemble_task", assemble)
    registration_calls = []

    def register(**kwargs):
        registration_calls.append(kwargs)
        return {
            "window_id": "lockbox-2026",
            "sample_role": "lockbox",
            "access_id": "LB-xscore",
        }

    monkeypatch.setattr(xscore_module, "_lockbox_register", register)
    score_calls = 0

    def score(_panel, score_dir, *_args):
        nonlocal score_calls
        score_calls += 1
        output = Path(score_dir)
        output.mkdir(parents=True, exist_ok=True)
        if score_calls == 1:
            (output / "signal.npz").write_bytes(b"partial")
            raise RuntimeError("simulated worker interruption")
        (output / "signal.npz").write_bytes(b"signal")
        (output / "metrics.json").write_text("{}", encoding="utf-8")
        (output / "manifest.json").write_text("{}", encoding="utf-8")
        return {
            "group": "daily",
            "model": "M0a",
            "score_dir": str(output),
            "metrics": {},
        }

    monkeypatch.setattr(xscore_module, "_score_task", score)
    monkeypatch.setattr(
        xscore_module,
        "_prepare_portfolio_data_task",
        lambda *_args: {"panel": str(panel_path)},
    )

    def portfolio(score_dir, _aux, _portfolio, exec_mode, domain, _mode):
        output = Path(score_dir) / f"portfolio_{exec_mode}_{domain}.json"
        output.write_text("{}", encoding="utf-8")
        return {
            "score": Path(score_dir).name,
            "exec_mode": exec_mode,
            "domain": domain,
            "result": {},
        }

    monkeypatch.setattr(xscore_module, "_portfolio_task", portfolio)
    composite_dir = tmp_path / "mock-composite-ref"
    composite_dir.mkdir(parents=True)
    (composite_dir / "panel.parquet").write_bytes(b"composite")
    composite = publish_artifact(
        composite_dir,
        {
            "artifact_type": "composite_signal",
            "name": config.name,
            "version": version,
            "config_sha256": config.config_sha256,
            "data_version": factor.data_version,
            "window_id": factor.window_id,
            "sample_role": factor.sample_role,
            "mode": "final",
            "status": "candidate",
            "source_artifacts": [
                f"{factor.artifact_uri}#version={factor.version}"
            ],
            "platform_commit": factor.platform_commit,
            "access_ids": ["LB-factor", "LB-xscore"],
        },
        primary_file="panel.parquet",
    )
    publish_calls = 0

    def publish(*_args):
        nonlocal publish_calls
        publish_calls += 1
        if publish_calls == 1:
            raise RuntimeError("simulated publisher interruption")
        return composite

    monkeypatch.setattr(xscore_module, "_publish_composite_task", publish)
    monkeypatch.setattr(
        xscore_module, "_write_lockbox_manifests", lambda **_kwargs: None
    )

    impl = lambda: xscore_module._xscore_flow_impl(
        config_path,
        expected_version=version,
        flow_attempt_sha256=attempt_sha,
    )
    with pytest.raises(RuntimeError, match="simulated worker interruption"):
        impl()
    assert not (result_root / "prepared_manifest.json").exists()

    with pytest.raises(RuntimeError, match="simulated publisher interruption"):
        impl()
    assert (result_root / "prepared_manifest.json").is_file()
    assert impl() == composite
    assert score_calls == 2, "prepared recovery must not score again"
    assert publish_calls == 2
    assert len(registration_calls) == 3
    assert all(call["flow_attempt_sha256"] == attempt_sha for call in registration_calls)
    assert all(call["resume_pending"] is True for call in registration_calls)
    assert all(call["replay_ok"] is False for call in registration_calls)
    completed = json.loads((result_root / "flow_manifest.json").read_text())
    assert completed["status"] == "completed"
    assert json.loads((result_root / "prepared_manifest.json").read_text())[
        "lockbox"
    ]["access_id"] == "LB-xscore"


def test_composite_publication_preserves_access_ids_from_every_factor(
    tmp_path: Path, monkeypatch
):
    """Composite provenance must retain every input FactorArtifact access id."""
    _require_panel_adapter(monkeypatch)
    first = _factor(
        tmp_path / "alpha",
        "alpha",
        mode="final",
        sample_role="lockbox",
        access_ids=("LB-alpha",),
    )
    second = _factor(
        tmp_path / "beta",
        "beta",
        mode="final",
        sample_role="lockbox",
        access_ids=("LB-beta",),
    )
    config = parse_xscore_config(
        {
            "artifact_root": str(tmp_path),
            "mode": "final",
            "inputs": [
                first.model_dump(mode="json"),
                second.model_dump(mode="json"),
            ],
            "name": "blend",
            "groups": {"daily": ["alpha", "beta"]},
            "models": ["M0a"],
            "composite": {"group": "daily", "model": "M0a"},
            "min_coverage": 0.9,
        }
    )
    panel_path = tmp_path / "panel.npz"
    assemble_factor_panel([first, second], panel_path, allowed_root=tmp_path)
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
        refs=[first, second],
        config=config,
        version="d" * 64,
        code_sha="e" * 64,
        score_dir=score_dir,
        panel_path=panel_path,
        access_ids=["LB-alpha", "LB-xscore"],
    )

    assert ref.access_ids == ("LB-alpha", "LB-beta", "LB-xscore")
    provenance = json.loads(
        (Path(ref.artifact_uri) / "provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["access_ids"] == ["LB-alpha", "LB-beta", "LB-xscore"]


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
        "composite": {"group": "daily", "model": "M0a"},
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
    import research_flows.xscore_flow as xscore_module
    import research_xscore_lockbox as xscore_lockbox

    observed = []

    def register(**kwargs):
        observed.append((os.environ.get("FACTORLAB_RE_FINAL"), kwargs))
        return {"access_id": "LB-1"}

    monkeypatch.setattr(xscore_lockbox, "lockbox_register", register)
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


def _lockbox_store_fixture(tmp_path: Path, monkeypatch):
    from factorlab.adapters import lockbox_store as store
    from factorlab.core.lockbox import LockboxWindow

    db = tmp_path / "ledger.sqlite"
    window = LockboxWindow(
        "2026Q2", date(2025, 7, 1), date(2026, 7, 3)
    )
    conn = store.connect(db)
    store.roll(conn, window=window)
    conn.close()
    monkeypatch.setattr(xscore_module.xscore_lockbox, "_ensure_platform_src", lambda: None)

    def open_ledger(**_kwargs):
        conn = store.connect(db)
        return conn, store.load_state(conn), window, "lockbox"

    monkeypatch.setattr(xscore_module.xscore_lockbox, "_lockbox_open", open_ledger)
    monkeypatch.setenv("FACTORLAB_LOCKBOX", "1")
    monkeypatch.delenv("FACTORLAB_RE_FINAL", raising=False)
    panel = tmp_path / "panel.npz"
    panel.write_bytes(b"frozen panel")
    config = tmp_path / "xscore.yaml"
    config.write_text("mode: final\n", encoding="utf-8")
    return store, db, panel, config


def test_xscore_lockbox_resumes_only_latest_matching_pending_attempt(
    tmp_path: Path, monkeypatch
):
    from factorlab.core.lockbox import LockboxError

    store, db, panel, config = _lockbox_store_fixture(tmp_path, monkeypatch)
    register = xscore_module.xscore_lockbox.lockbox_register
    panel_sig = "panel-fingerprint"
    first_sha = "a" * 64
    second_sha = "b" * 64
    first = register(
        panel=panel,
        panel_sig=panel_sig,
        config_path=str(config),
        flow_attempt_sha256=first_sha,
    )
    resumed = register(
        panel=panel,
        panel_sig=panel_sig,
        config_path=str(config),
        flow_attempt_sha256=first_sha,
        resume_pending=True,
    )
    assert resumed["access_id"] == first["access_id"]

    monkeypatch.setenv("FACTORLAB_RE_FINAL", "1")
    newer = register(
        panel=panel,
        panel_sig=panel_sig,
        config_path=str(config),
        flow_attempt_sha256=second_sha,
    )
    monkeypatch.delenv("FACTORLAB_RE_FINAL")
    with pytest.raises(LockboxError, match="LOCKBOX_FINAL_DUPLICATE"):
        register(
            panel=panel,
            panel_sig=panel_sig,
            config_path=str(config),
            flow_attempt_sha256=first_sha,
            resume_pending=True,
        )
    latest = register(
        panel=panel,
        panel_sig=panel_sig,
        config_path=str(config),
        flow_attempt_sha256=second_sha,
        resume_pending=True,
    )
    assert latest["access_id"] == newer["access_id"]

    conn = store.connect(db)
    try:
        row = conn.execute(
            "SELECT params, result_ref FROM lockbox_access WHERE access_id = ?",
            (newer["access_id"],),
        ).fetchone()
        assert json.loads(row["params"])["flow_attempt_sha256"] == second_sha
        assert row["result_ref"] is None
        store.update_result_ref(conn, newer["access_id"], "/results/composite")
    finally:
        conn.close()
    with pytest.raises(LockboxError, match="LOCKBOX_FINAL_DUPLICATE"):
        register(
            panel=panel,
            panel_sig=panel_sig,
            config_path=str(config),
            flow_attempt_sha256=second_sha,
            resume_pending=True,
        )


def _acquire_xscore_lock(lock_path: str, acquired) -> None:
    with xscore_module.xscore_lockbox.single_writer_lock(Path(lock_path)):
        acquired.set()


def test_xscore_attempt_lock_serializes_across_processes(tmp_path: Path):
    import multiprocessing

    ctx = multiprocessing.get_context("fork")
    acquired = ctx.Event()
    lock_path = tmp_path / "attempt.lock"
    with xscore_module.xscore_lockbox.single_writer_lock(lock_path):
        child = ctx.Process(
            target=_acquire_xscore_lock,
            args=(str(lock_path), acquired),
        )
        child.start()
        assert not acquired.wait(0.15), "第二个进程应阻塞到首个 attempt 释放锁"
    assert acquired.wait(3), "首个进程释放后，第二个 attempt 应能继续"
    child.join(3)
    assert child.exitcode == 0


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
        "composite": {"group": "daily", "model": "M0a"},
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
    xscore_module._ensure_final_attempt_marker(
        result_root,
        xscore_module._final_attempt_marker(
            config, version=version, code_sha="c" * 64
        ),
    )
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
