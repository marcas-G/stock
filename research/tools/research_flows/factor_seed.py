"""Create one IS-only FactorArtifact from an existing library Factor Spec.

This is a bootstrap entrypoint: it runs one existing factor through FactorLab
inside a Prefect flow, trims its date range to the declared IS window, and
publishes the result without claiming redundancy or portfolio-selection
evidence. XScore owns the subsequent single-factor comparison.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from research_flows.artifacts import (
    ArtifactRef,
    load_artifact_ref,
    publish_artifact,
    validate_artifact_ref,
    version_fingerprint,
)
from research_flows.factor_mining import (
    _ATTEMPT_MARKER,
    _RUN_OPTIONS,
    _attempt_lock,
    _canonical_json,
    _factor_spec_model_dump,
    _implementation_fingerprint,
    _lint_spec,
    _platform_commit,
    _run_factorlab,
    _sha256_bytes,
    _sha256_json,
    _validate_native_factor,
    _write_attempt_marker,
)

try:
    from prefect import flow, task
except ImportError:  # Lightweight platform-venv import for unit tests.
    def task(fn=None, **_options):
        if fn is None:
            return lambda wrapped: wrapped
        return fn

    def flow(fn=None, **_options):
        if fn is None:
            return lambda wrapped: wrapped
        return fn


_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SEED_SPEC = ".seed_factor_spec.yaml"


def _effective_spec(source_path: Path, window: Mapping[str, str]) -> tuple[str, str]:
    try:
        document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"种子 Factor Spec 不可读取: {source_path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("种子 Factor Spec 根节点必须是 mapping")
    date_doc = document.get("date")
    if not isinstance(date_doc, dict):
        raise ValueError("种子 Factor Spec 必须声明 date.start/end")
    # Keep the library source byte-for-byte intact. The immutable seed run gets
    # a private Spec snapshot bounded to its IS window.
    effective = dict(document)
    effective["date"] = {
        **date_doc,
        "start": window["start"],
        "end": window["end"],
    }
    try:
        text = yaml.safe_dump(
            effective,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    except yaml.YAMLError as exc:
        raise ValueError(f"无法生成 IS 窗口 Factor Spec: {exc}") from exc
    return text, _sha256_bytes(text.encode("utf-8"))


def validate_factor_seed_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the one-factor bootstrap request before creating any files."""
    if not isinstance(config, Mapping):
        raise ValueError("factor-seed config 必须为 mapping")
    required = (
        "name",
        "hypothesis",
        "spec_path",
        "mode",
        "data_version",
        "window",
        "output_root",
    )
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"factor-seed config 缺少必填项: {missing}")
    extra = sorted(
        set(config) - set(required) - {"run_options", "validation"}
    )
    if extra:
        raise ValueError(f"factor-seed config 包含未知字段: {extra}")

    normalized = dict(config)
    name = config["name"]
    if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name):
        raise ValueError("name 必须为安全的 Factor 名称")
    hypothesis = config["hypothesis"]
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise ValueError("hypothesis 必须为非空经济假设")
    if config["mode"] != "explore":
        raise ValueError("factor-seed 只支持 mode: explore，不接受 final")
    data_version = config["data_version"]
    if not isinstance(data_version, str) or not data_version.strip():
        raise ValueError("data_version 必须为非空且冻结的数据版本约束")

    window = config["window"]
    if not isinstance(window, Mapping):
        raise ValueError("window 必须声明 id、start、end 和 sample_role")
    window_id = window.get("id")
    if not isinstance(window_id, str) or not window_id.strip():
        raise ValueError("window.id 必须为非空窗口标识")
    start = window.get("start")
    end = window.get("end")
    for field, value in (("start", start), ("end", end)):
        if not isinstance(value, str):
            raise ValueError(f"window.{field} 必须是 YYYY-MM-DD 日期")
        try:
            parsed = dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"window.{field} 必须是 YYYY-MM-DD 日期: {value!r}"
            ) from exc
        if parsed.isoformat() != value:
            raise ValueError(
                f"window.{field} 必须是规范 YYYY-MM-DD 日期: {value!r}"
            )
    if start > end:
        raise ValueError("window.start 不得晚于 window.end")
    if window.get("sample_role") != "is":
        raise ValueError("factor-seed 只能声明 sample_role: is")
    normalized["window"] = {
        "id": window_id,
        "start": start,
        "end": end,
        "sample_role": "is",
    }

    source_path = Path(str(config["spec_path"])).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Factor Spec 不存在: {source_path}")
    source_spec = _factor_spec_model_dump(source_path)
    if source_spec.get("name") != name:
        raise ValueError(
            f"config.name {name!r} 与 Factor Spec name "
            f"{source_spec.get('name')!r} 不一致"
        )
    source_spec_sha = _sha256_bytes(source_path.read_bytes())
    effective_spec_yaml, effective_spec_sha = _effective_spec(
        source_path,
        normalized["window"],
    )
    effective_spec_doc = yaml.safe_load(effective_spec_yaml)
    if effective_spec_doc.get("name") != name:
        raise ValueError("IS 窗口 Factor Spec 改变了因子名称")
    normalized["spec_path"] = str(source_path)
    normalized["_source_spec_sha256"] = source_spec_sha
    normalized["_effective_spec_yaml"] = effective_spec_yaml
    normalized["_effective_spec_sha256"] = effective_spec_sha

    output_root = Path(str(config["output_root"])).expanduser()
    if not output_root.is_absolute():
        raise ValueError("output_root 必须是绝对路径")
    normalized["output_root"] = str(output_root.resolve())

    run_options = config.get("run_options", {})
    if not isinstance(run_options, Mapping):
        raise ValueError("run_options 必须为 mapping")
    unknown = sorted(
        set(run_options) - (set(_RUN_OPTIONS) | {"profile", "no_read_cache"})
    )
    if unknown:
        raise ValueError(f"run_options 不支持: {unknown}")
    for key in ("profile", "no_read_cache"):
        if key in run_options and not isinstance(run_options[key], bool):
            raise ValueError(f"run_options.{key} 必须为 bool")
    for key, (_flag, cast) in _RUN_OPTIONS.items():
        if key not in run_options:
            continue
        value = run_options[key]
        if cast is int:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"run_options.{key} 必须为整数")
        elif not isinstance(value, str) or not value:
            raise ValueError(f"run_options.{key} 必须为非空字符串")
    normalized["run_options"] = dict(run_options)

    validation = config.get("validation", {})
    if not isinstance(validation, Mapping):
        raise ValueError("validation 必须为 mapping")
    extra_validation = sorted(
        set(validation) - {"min_coverage", "max_invalid_ratio"}
    )
    if extra_validation:
        raise ValueError(
            f"factor-seed validation 包含未知字段: {extra_validation}"
        )
    min_coverage = validation.get("min_coverage", 0.8)
    max_invalid_ratio = validation.get("max_invalid_ratio", 0.2)
    for key, value in (
        ("min_coverage", min_coverage),
        ("max_invalid_ratio", max_invalid_ratio),
    ):
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError(f"validation.{key} 必须在 [0, 1]")
    normalized["validation"] = {
        "baseline_refs": [],
        "diagnostics_window": {
            "start": start,
            "end": end,
        },
        "oos_window": {
            "start": start,
            "end": end,
        },
        "thresholds": {
            "min_coverage": float(min_coverage),
            "max_invalid_ratio": float(max_invalid_ratio),
        },
    }
    _canonical_json({
        key: value
        for key, value in normalized.items()
        if not key.startswith("_")
    })
    return normalized


def _seed_identity(config: Mapping[str, Any]) -> dict[str, Any]:
    implementation_sha = _implementation_fingerprint()
    semantic_config = {
        key: value
        for key, value in config.items()
        if key not in {
            "spec_path",
            "output_root",
            "_source_spec_sha256",
            "_effective_spec_yaml",
            "_effective_spec_sha256",
        }
    }
    return {
        "spec_sha256": config["_effective_spec_sha256"],
        "source_spec_sha256": config["_source_spec_sha256"],
        "implementation_sha256": implementation_sha,
        "config_sha256": _sha256_json(semantic_config),
        "data_version": config["data_version"],
        "window": dict(config["window"]),
        "mode": "explore",
        "source_artifacts": [],
    }


def _materialize_effective_spec(target: Path, config: Mapping[str, Any]) -> Path:
    spec_path = target / _SEED_SPEC
    body = str(config["_effective_spec_yaml"]).encode("utf-8")
    expected = config["_effective_spec_sha256"]
    if spec_path.exists():
        if _sha256_bytes(spec_path.read_bytes()) != expected:
            raise ValueError("attempt 目录中的冻结 IS Spec 与当前版本身份不一致")
        return spec_path
    temp = spec_path.with_name(f".{spec_path.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(body)
        os.replace(temp, spec_path)
    finally:
        temp.unlink(missing_ok=True)
    return spec_path


@task(name="factor-seed-register", retries=0)
def _register_seed_task(
    target: Path,
    identity: Mapping[str, Any],
    hypothesis: str,
) -> None:
    _write_attempt_marker(target, identity, hypothesis=hypothesis)


@task(name="factor-seed-lint", retries=0)
def _lint_seed_task(config: Mapping[str, Any], lint_runner=None) -> None:
    spec_path = Path(config["spec_path"])
    (lint_runner or _lint_spec)(spec_path)


@task(name="factor-seed-compute", retries=0)
def _compute_seed_task(config: Mapping[str, Any], output_dir: Path, runner=None) -> None:
    if runner is None:
        _run_factorlab(config, output_dir)
    else:
        runner(Path(config["spec_path"]), output_dir, config["mode"])


def _existing_seed_publication(
    config: Mapping[str, Any],
    target: Path,
    identity: Mapping[str, Any],
    version: str,
) -> ArtifactRef | None:
    manifest_path = target / "flow_manifest.json"
    if not manifest_path.is_file():
        return None
    ref = validate_artifact_ref(
        load_artifact_ref(target),
        expected_type="factor_signal",
        allowed_statuses=("candidate",),
        expected_mode="explore",
        expected_window_id=config["window"]["id"],
        allowed_root=config["output_root"],
    )
    if ref.name != config["name"] or ref.version != version:
        raise ValueError("已发布 seed FactorArtifact 名称或版本不一致")
    if (
        ref.spec_sha256 != identity["spec_sha256"]
        or ref.config_sha256 != identity["config_sha256"]
        or ref.data_version != config["data_version"]
        or ref.metadata.get("fingerprint_identity") != dict(identity)
    ):
        raise ValueError("已发布 seed FactorArtifact identity 与当前请求不一致")
    native_config = dict(config)
    native_config["spec_path"] = str(target / _SEED_SPEC)
    _validate_native_factor(
        native_config,
        target,
        expected_identity=identity,
    )
    return ref


@task(name="factor-seed-publish", retries=0)
def _publish_seed_task(
    config: Mapping[str, Any],
    target: Path,
    version: str,
    identity: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> ArtifactRef:
    manifest = {
        "artifact_type": "factor_signal",
        "name": config["name"],
        "version": version,
        "spec_sha256": identity["spec_sha256"],
        "config_sha256": identity["config_sha256"],
        "data_version": config["data_version"],
        "window_id": config["window"]["id"],
        "sample_role": "is",
        "mode": "explore",
        "status": "candidate",
        "platform_commit": _platform_commit(),
        "source_artifacts": [],
        "access_ids": [],
        "metadata": {
            "hypothesis": config["hypothesis"].strip(),
            "seed_only": True,
            "source_spec_sha256": identity["source_spec_sha256"],
            "effective_spec_sha256": identity["spec_sha256"],
            "identity_sha256": _sha256_json(identity),
            "fingerprint_identity": dict(identity),
            "evidence": {
                "checks_completed": [
                    "library_factor_spec_loaded",
                    "is_window_spec_frozen",
                    "factor_spec_lint",
                    "factorlab_single_factor_evaluation",
                    "native_artifact_validation",
                    "sample_role_and_data_version_validation",
                ],
                "redundancy_assessment": (
                    "deferred_to_xscore_single_factor_selection"
                ),
                "signal_date_start": summary.get("date_start"),
                "signal_date_end": summary.get("date_end"),
                "signal_rows": summary.get("signal_rows"),
                "single_factor_ic_mean": summary["evaluation"]["ic"]["mean"],
            },
            "signal_sha256": _sha256_bytes(
                (target / "signal.parquet").read_bytes()
            ),
            "labels_sha256": _sha256_bytes(
                (target / "labels.parquet").read_bytes()
            ),
            "factorlab_summary_sha256": _sha256_bytes(
                (target / "summary.json").read_bytes()
            ),
            "date_window": [
                config["window"]["start"],
                config["window"]["end"],
            ],
        },
    }
    ref = publish_artifact(target, manifest, primary_file="signal.parquet")
    validate_artifact_ref(
        ref,
        expected_type="factor_signal",
        allowed_statuses=("candidate",),
        expected_mode="explore",
        expected_window_id=config["window"]["id"],
        allowed_root=config["output_root"],
    )
    return ref


@flow(name="factor-seed")
def factor_seed_flow(
    config: Mapping[str, Any],
    *,
    runner: Callable[[Path, Path, str], None] | None = None,
    lint_runner: Callable[[Path], None] | None = None,
) -> ArtifactRef:
    """Run one existing library Spec as an IS-only XScore seed."""
    normalized = validate_factor_seed_config(config)
    identity = _seed_identity(normalized)
    version = version_fingerprint("factor_signal", normalized["name"], identity)
    output_root = Path(normalized["output_root"])
    target = output_root / normalized["name"] / version
    resolved_target = target.resolve()
    if output_root.resolve() not in resolved_target.parents:
        raise ValueError("seed artifact target 不得逃逸 output_root")

    with _attempt_lock(target):
        published = _existing_seed_publication(
            normalized,
            target,
            identity,
            version,
        )
        if published is not None:
            return published
        if target.exists():
            marker = target / _ATTEMPT_MARKER
            if not marker.is_file():
                entries = list(target.iterdir())
                if entries:
                    raise ValueError(
                        f"seed 版本目录已存在但无 attempt marker，拒绝覆盖: {target}"
                    )
                _write_attempt_marker(
                    target,
                    identity,
                    hypothesis=normalized["hypothesis"],
                )
            marker_doc = json.loads(marker.read_text(encoding="utf-8"))
            if (
                marker_doc.get("fingerprint_identity") != identity
                or marker_doc.get("identity_sha256") != _sha256_json(identity)
                or marker_doc.get("hypothesis")
                != normalized["hypothesis"].strip()
            ):
                raise ValueError("seed attempt marker 与当前请求不一致")
        else:
            _register_seed_task(
                target,
                identity,
                normalized["hypothesis"],
            )

        effective_spec = _materialize_effective_spec(target, normalized)
        compute_config = dict(normalized)
        compute_config["spec_path"] = str(effective_spec)
        _lint_seed_task(compute_config, lint_runner)

        summary_path = target / "summary.json"
        if not summary_path.is_file():
            _compute_seed_task(compute_config, target, runner)
        report, summary, access_ids = _validate_native_factor(
            compute_config,
            target,
            expected_identity=identity,
        )
        if access_ids:
            raise ValueError("IS seed Artifact 不得包含任何锁箱 access_id")

        evidence_summary = {
            "date_start": report.date_start,
            "date_end": report.date_end,
            "signal_rows": report.rows,
            "evaluation": summary["evaluation"],
        }
        ref = _publish_seed_task(
            normalized,
            target,
            version,
            identity,
            evidence_summary,
        )
        return ref


__all__ = ["factor_seed_flow", "validate_factor_seed_config"]
