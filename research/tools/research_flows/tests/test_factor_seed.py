"""Seed FactorArtifact tests; all FactorLab outputs live under tmp_path."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml
from factorlab.adapters.parquet_artifacts import write_factor_artifacts
from factorlab.core.domain.frames import LabelArtifact, SignalArtifact, SignalMeta
from factorlab.core.spec import load_spec

TOOLS = Path(__file__).resolve().parents[2]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from research_flows.factor_seed import factor_seed_flow, validate_factor_seed_config


def _spec(tmp_path: Path, *, name: str = "small_cap") -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": name,
                "category": "custom",
                "direction": 1,
                "universe": {"codes": ["000001.SZ"]},
                "date": {"start": "2023-01-01", "end": "2026-07-31"},
                "formula": "signal = close",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _config(tmp_path: Path, spec_path: Path) -> dict[str, Any]:
    return {
        "name": "small_cap",
        "hypothesis": "小市值股票存在可交易的规模溢价。",
        "spec_path": str(spec_path),
        "mode": "explore",
        "data_version": "ashare-daily-2024-v1",
        "window": {
            "id": "train-2024",
            "start": "2023-01-01",
            "end": "2024-05-31",
            "sample_role": "is",
        },
        "output_root": str(tmp_path / "results" / "platform"),
        "validation": {"min_coverage": 0.8, "max_invalid_ratio": 0.2},
    }


def _write_native_bundle(
    output_dir: Path,
    spec_path: Path,
    *,
    sample_role: str = "is",
    data_version: str = "ashare-daily-2024-v1",
    invalid_rows: int = 0,
) -> None:
    spec = load_spec(spec_path)
    dates = []
    day = dt.date.fromisoformat(str(spec.date.start))
    end = dt.date.fromisoformat(str(spec.date.end))
    while day <= end and len(dates) < 20:
        if day.weekday() == 0:
            dates.append(day)
        day += dt.timedelta(days=1)
    signal = pl.DataFrame(
        [
            {
                "date": date,
                "code": f"{index:06d}.SZ",
                "signal": (
                    None
                    if day_index * 30 + index < invalid_rows
                    else float(index)
                ),
            }
            for day_index, date in enumerate(dates)
            for index in range(30)
        ],
        schema_overrides={"signal": pl.Float64},
    )
    labels = pl.DataFrame(
        [
            {
                "date": date,
                "code": f"{index:06d}.SZ",
                "forward_return_1d": float(index) / 1000,
                "forward_return_5d": float(index) / 500,
                "forward_return_20d": float(index) / 250,
            }
            for date in dates
            for index in range(30)
        ]
    )
    summary = {
        "name": spec.name,
        "date_start": signal["date"].min().isoformat(),
        "date_end": signal["date"].max().isoformat(),
        "signal_rows": signal.height,
        "signal_null_ratio": round(
            signal["signal"].is_null().sum() / signal.height, 4
        ),
        "spec_yaml": yaml.safe_dump(
            spec.model_dump(), allow_unicode=True, sort_keys=True
        ),
        "sample": {"role": sample_role, "window_id": None, "access_id": None},
        "data_quality": {
            "dataset_version": data_version,
            "quality_status": "PASS",
        },
        "evaluation": {
            "dead_signal": False,
            "ic": {"mean": 0.03, "t_nw": 2.0},
            "n_weeks": len(dates),
        },
    }
    panel = signal.join(labels, on=["date", "code"], how="left")
    write_factor_artifacts(
        output_dir,
        SignalArtifact(frame=signal, meta=SignalMeta(name=spec.name)),
        LabelArtifact(frame=labels),
        panel,
        summary,
    )


def _run_seed(
    config: dict[str, Any],
    *,
    run_calls: list[tuple[Path, Path, str]] | None = None,
    sample_role: str = "is",
    data_version: str = "ashare-daily-2024-v1",
    invalid_rows: int = 0,
):
    def runner(spec_path: Path, output_dir: Path, mode: str) -> None:
        if run_calls is not None:
            run_calls.append((spec_path, output_dir, mode))
        _write_native_bundle(
            output_dir,
            spec_path,
            sample_role=sample_role,
            data_version=data_version,
            invalid_rows=invalid_rows,
        )

    return factor_seed_flow(
        config,
        runner=runner,
        lint_runner=lambda _path: None,
    )


def test_seed_config_requires_is_explore_and_bounded_window(tmp_path: Path):
    spec = _spec(tmp_path)
    config = _config(tmp_path, spec)

    normalized = validate_factor_seed_config(config)

    assert normalized["window"]["sample_role"] == "is"
    assert normalized["window"]["end"] == "2024-05-31"
    config["mode"] = "final"
    with pytest.raises(ValueError, match="不接受 final"):
        validate_factor_seed_config(config)


def test_seed_flow_clips_existing_member_spec_and_publishes_factor_artifact(
    tmp_path: Path,
):
    spec = _spec(tmp_path)
    original_bytes = spec.read_bytes()
    config = _config(tmp_path, spec)
    calls: list[tuple[Path, Path, str]] = []

    ref = _run_seed(config, run_calls=calls)

    assert ref.artifact_type == "factor_signal"
    assert ref.name == "small_cap"
    assert ref.mode == "explore"
    assert ref.sample_role == "is"
    assert ref.status == "candidate"
    assert ref.window_id == "train-2024"
    assert ref.data_version == "ashare-daily-2024-v1"
    assert ref.artifact_uri.startswith(config["output_root"])
    assert len(calls) == 1
    effective_spec = yaml.safe_load(calls[0][0].read_text(encoding="utf-8"))
    assert effective_spec["date"] == {
        "start": "2023-01-01",
        "end": "2024-05-31",
    }
    assert calls[0][2] == "explore"
    assert spec.read_bytes() == original_bytes
    assert (Path(ref.artifact_uri) / "signal.parquet").is_file()
    assert (Path(ref.artifact_uri) / "summary.json").is_file()
    assert (Path(ref.artifact_uri) / "flow_manifest.json").is_file()
    assert ref.metadata["source_spec_sha256"] == hashlib.sha256(
        original_bytes
    ).hexdigest()
    assert ref.metadata["seed_only"] is True
    assert ref.metadata["evidence"]["redundancy_assessment"] == (
        "deferred_to_xscore_single_factor_selection"
    )
    from research_flows.xscore_flow import QR, parse_xscore_config

    xscore = parse_xscore_config({
        "mode": "explore",
        "name": "small-cap-singleton",
        "artifact_root": config["output_root"],
        "output_root": str(QR / "results"),
        "inputs": [ref.model_dump(mode="json")],
        "groups": {"daily": ["small_cap"]},
        "models": ["M0a"],
        "window_id": "train-2024",
        "min_coverage": 0.8,
    })
    assert xscore.inputs == (ref,)


def test_seed_retry_reuses_completed_immutable_artifact(tmp_path: Path):
    spec = _spec(tmp_path)
    config = _config(tmp_path, spec)
    calls: list[tuple[Path, Path, str]] = []

    first = _run_seed(config, run_calls=calls)
    second = _run_seed(config, run_calls=calls)

    assert second == first
    assert len(calls) == 1


def test_seed_accepts_factorlab_summary_ratio_rounded_to_four_decimals(
    tmp_path: Path,
):
    spec = _spec(tmp_path)
    config = _config(tmp_path, spec)

    ref = _run_seed(config, invalid_rows=1)

    assert ref.status == "candidate"
    summary = json.loads(
        (Path(ref.artifact_uri) / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["signal_null_ratio"] == 0.0017


@pytest.mark.parametrize(
    ("sample_role", "data_version", "message"),
    [
        ("lockbox", "ashare-daily-2024-v1", "FactorLab 样本模式"),
        ("is", "wrong-version", "dataset_version"),
    ],
)
def test_seed_rejects_non_is_or_mismatched_data_without_publishing(
    tmp_path: Path, sample_role: str, data_version: str, message: str
):
    spec = _spec(tmp_path)
    config = _config(tmp_path, spec)

    with pytest.raises(ValueError, match=message):
        _run_seed(config, sample_role=sample_role, data_version=data_version)

    assert not list(Path(config["output_root"]).rglob("flow_manifest.json"))
