"""Acceptance tests for the Prefect research-flow artifact boundary.

The required behavior is defined in
knowledge/design/research/specs/2026-09-25-prefect-research-flows-design.md.
These tests use temporary artifacts only; they never touch CH, quantresearch
results, or the production lockbox ledger.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from factorlab.adapters.parquet_artifacts import write_factor_artifacts
from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta
from research_flows.artifacts import (
    ArtifactRef,
    content_sha256,
    load_artifact_ref,
    publish_artifact,
    validate_artifact_ref,
    version_fingerprint,
)
from research_flows.contracts import validate_mode_sample
from research_flows.xscore_flow import validate_factor_inputs
from research_flows.strategy_execution import validate_signal_input
from research_flows.campaign import run_campaign_steps
from research_flows.deployments import DEPLOYMENT_NAMES


def _publish(
    root: Path,
    *,
    artifact_type: str = "factor_signal",
    name: str = "alpha",
    mode: str = "explore",
    sample_role: str = "is",
    status: str = "candidate",
    source_artifacts: tuple[str, ...] = (),
    access_ids: tuple[str, ...] = (),
    metadata: dict | None = None,
) -> ArtifactRef:
    root.mkdir(parents=True, exist_ok=True)
    if artifact_type == "factor_signal":
        signal = pl.DataFrame({
            "date": pl.Series(
                ["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"],
                dtype=pl.Date,
            ),
            "code": ["000001.SZ", "000002.SZ", "000001.SZ", "000002.SZ"],
            "signal": [0.1, 0.2, 0.3, 0.4],
        })
        labels = pl.DataFrame({
            "date": signal["date"],
            "code": signal["code"],
            "forward_return_1d": [0.01, 0.02, 0.03, 0.04],
            "forward_return_5d": [0.02, 0.03, 0.04, 0.05],
            "forward_return_20d": [0.03, 0.04, 0.05, 0.06],
        })
        write_factor_artifacts(
            root,
            SignalArtifact(frame=signal, meta=SignalMeta(name=name)),
            LabelArtifact(frame=labels),
            signal.join(labels, on=["date", "code"], how="left"),
            {
                "name": name,
                "sample": {
                    "role": sample_role,
                    "window_id": "2026Q3",
                    "access_id": "LB-1" if access_ids else None,
                },
            },
        )
    else:
        (root / "signal.parquet").write_bytes(b"signal-v1")
    return publish_artifact(
        root,
        {
            "artifact_type": artifact_type,
            "name": name,
            "version": "v1",
            "config_sha256": "a" * 64,
            "data_version": "daily-2026-09-24",
            "window_id": "2026Q3",
            "sample_role": sample_role,
            "mode": mode,
            "status": status,
            "source_artifacts": list(source_artifacts),
            "platform_commit": "deadbeef",
            "access_ids": list(access_ids),
            "metadata": metadata or {},
        },
        primary_file="signal.parquet",
    )


def test_artifact_ref_binds_primary_bytes_and_manifest(tmp_path: Path):
    ref = _publish(tmp_path / "factor")

    loaded = load_artifact_ref(tmp_path / "factor")

    assert loaded == ref
    assert loaded.artifact_sha256 == content_sha256(tmp_path / "factor/signal.parquet")
    assert len(loaded.manifest_sha256) == 64
    assert loaded.artifact_type == "factor_signal"
    with pytest.raises((AttributeError, TypeError, ValueError)):
        loaded.version = "changed"


def test_artifact_ref_metadata_round_trips_in_manifest(tmp_path: Path):
    metadata = {
        "hypothesis": "高换手股票存在短期反转",
        "fingerprint_identity": {
            "spec_sha256": "a" * 64,
            "data_version": "daily-2026-09-24",
        },
        "evidence": {"evaluation": {"ic_mean": 0.03}},
    }
    ref = _publish(tmp_path / "factor", metadata=metadata)

    assert ref.metadata == metadata
    assert load_artifact_ref(tmp_path / "factor").metadata == metadata
    with pytest.raises((AttributeError, TypeError)):
        ref.metadata["hypothesis"] = "changed"
    with pytest.raises((AttributeError, TypeError)):
        ref.metadata["evidence"]["evaluation"]["ic_mean"] = 0.9


def test_artifact_ref_rejects_non_json_metadata(tmp_path: Path):
    out = tmp_path / "factor"
    out.mkdir()
    (out / "signal.parquet").write_bytes(b"signal-v1")

    with pytest.raises(ValueError, match="metadata|JSON|json"):
        publish_artifact(
            out,
            {
                "artifact_type": "factor_signal",
                "name": "alpha",
                "version": "v1",
                "config_sha256": "a" * 64,
                "data_version": "daily-2026-09-24",
                "window_id": "2026Q3",
                "sample_role": "is",
                "mode": "explore",
                "status": "candidate",
                "platform_commit": "deadbeef",
                "metadata": {"not_json": object()},
            },
            primary_file="signal.parquet",
        )


def test_artifact_ref_rejects_tampered_primary_file(tmp_path: Path):
    ref = _publish(tmp_path / "factor")
    (tmp_path / "factor/signal.parquet").write_bytes(b"replaced")

    with pytest.raises(ValueError, match="sha256"):
        validate_artifact_ref(ref, expected_type="factor_signal")


def test_published_version_cannot_be_overwritten(tmp_path: Path):
    out = tmp_path / "factor"
    first = _publish(out)
    with pytest.raises(FileExistsError, match="禁止覆盖"):
        publish_artifact(
            out,
            first.model_dump(mode="json"),
            primary_file="signal.parquet",
        )


def test_failed_artifact_cannot_be_published(tmp_path: Path):
    out = tmp_path / "failed"
    out.mkdir()
    (out / "signal.parquet").write_bytes(b"partial")
    with pytest.raises(ValueError, match="failed|失败"):
        publish_artifact(
            out,
            {
                "artifact_type": "factor_signal",
                "name": "alpha",
                "version": "v1",
                "config_sha256": "a" * 64,
                "data_version": "daily-2026-09-24",
                "window_id": "2026Q3",
                "sample_role": "is",
                "mode": "explore",
                "status": "failed",
                "platform_commit": "deadbeef",
            },
            primary_file="signal.parquet",
        )
    assert not (out / "flow_manifest.json").exists()


def test_reference_must_stay_under_configured_root(tmp_path: Path):
    root = tmp_path / "allowed"
    ref = _publish(tmp_path / "outside/factor")

    with pytest.raises(ValueError, match="逃逸|root|根目录"):
        validate_artifact_ref(ref, allowed_root=root)


@pytest.mark.parametrize(
    "changed",
    [
        {"spec_sha256": "1" * 64},
        {"code_sha256": "2" * 64},
        {"data_version": "daily-next"},
        {"window_id": "2026Q4"},
        {"config_sha256": "3" * 64},
    ],
)
def test_version_fingerprint_changes_when_any_input_version_changes(changed):
    base = {
        "spec_sha256": "a" * 64,
        "code_sha256": "b" * 64,
        "data_version": "daily-2026-09-24",
        "window_id": "2026Q3",
        "config_sha256": "c" * 64,
    }

    v1 = version_fingerprint("factor_signal", "alpha", base)
    v2 = version_fingerprint("factor_signal", "alpha", {**base, **changed})

    assert v1 != v2
    assert v1 == version_fingerprint("factor_signal", "alpha", base)


def test_xscore_accepts_only_complete_factor_refs(tmp_path: Path):
    factor = _publish(tmp_path / "factor")

    assert validate_factor_inputs([factor]) == [factor]
    with pytest.raises((TypeError, ValueError), match="ArtifactRef|artifact"):
        validate_factor_inputs([str(tmp_path / "factor")])

    rejected = _publish(tmp_path / "rejected", status="rejected")
    with pytest.raises(ValueError, match="status|rejected"):
        validate_factor_inputs([rejected])


def test_strategy_accepts_only_factor_or_composite_signal_refs(tmp_path: Path):
    factor = _publish(tmp_path / "factor")
    composite = _publish(
        tmp_path / "composite",
        artifact_type="composite_signal",
        name="blend",
        source_artifacts=(factor.artifact_uri,),
    )

    assert validate_signal_input(factor) == factor
    assert validate_signal_input(composite) == composite
    strategy_result = _publish(
        tmp_path / "strategy",
        artifact_type="strategy_backtest",
        name="demo",
        status="completed",
    )
    with pytest.raises(ValueError, match="artifact_type|signal"):
        validate_signal_input(strategy_result)


def test_explore_and_final_sample_modes_are_explicit():
    validate_mode_sample("explore", "is", access_ids=[])
    with pytest.raises(ValueError, match="explore|训练|IS"):
        validate_mode_sample("explore", "mixed", access_ids=[])
    with pytest.raises(ValueError, match="access|final|锁箱"):
        validate_mode_sample("final", "lockbox", access_ids=[])
    validate_mode_sample("final", "lockbox", access_ids=["LB-1"])


def test_campaign_runs_in_order_and_stops_after_failure():
    seen: list[str] = []

    def factor(config):
        seen.append("factor")
        return "factor-ref"

    def xscore(config, factor_ref):
        seen.append(f"xscore:{factor_ref}")
        return "composite-ref"

    def strategy(config, composite_ref):
        seen.append(f"strategy:{composite_ref}")
        return "strategy-ref"

    result = run_campaign_steps(
        {},
        factor_step=factor,
        xscore_step=xscore,
        strategy_step=strategy,
    )

    assert seen == ["factor", "xscore:factor-ref", "strategy:composite-ref"]
    assert result == {
        "factor_ref": "factor-ref",
        "composite_ref": "composite-ref",
        "strategy_ref": "strategy-ref",
    }

    seen.clear()

    def fail_xscore(config, factor_ref):
        seen.append(f"xscore:{factor_ref}")
        raise RuntimeError("xscore failed")

    with pytest.raises(RuntimeError, match="xscore failed"):
        run_campaign_steps(
            {},
            factor_step=factor,
            xscore_step=fail_xscore,
            strategy_step=strategy,
        )
    assert seen == ["factor", "xscore:factor-ref"]


def test_deployment_names_match_the_three_flows_and_parent():
    assert DEPLOYMENT_NAMES == {
        "factor-mining/factor-mining",
        "xscore-pipeline/xscore",
        "strategy-execution/strategy-execution",
        "research-campaign/research-campaign",
    }
