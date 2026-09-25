"""Optional parent flow that hands immutable artifacts between research flows."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from research_flows.artifacts import ArtifactRef, validate_artifact_ref

try:
    from prefect import flow
except ImportError:  # Platform-venv unit tests do not require Prefect.
    def flow(fn=None, **_options):
        if fn is None:
            return lambda wrapped: wrapped
        return fn


def run_campaign_steps(
    config: Any,
    *,
    factor_step: Callable[[Any], Any],
    xscore_step: Callable[[Any, Any], Any],
    strategy_step: Callable[[Any, Any], Any],
) -> dict[str, Any]:
    """Run three injected child steps in order, stopping on the first failure."""
    factor_ref = factor_step(config)
    composite_ref = xscore_step(config, factor_ref)
    strategy_ref = strategy_step(config, composite_ref)
    return {
        "factor_ref": factor_ref,
        "composite_ref": composite_ref,
        "strategy_ref": strategy_ref,
    }


def _factor_configs(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        configs = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        configs = list(value)
    else:
        raise ValueError("campaign.factor 必须是因子配置对象或非空对象列表")
    if not configs or not all(isinstance(item, Mapping) for item in configs):
        raise ValueError("campaign.factor 必须包含至少一个因子配置对象")
    return [dict(item) for item in configs]


def _load_xscore_config(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        raw = dict(value)
    elif isinstance(value, (str, Path)) and str(value):
        path = Path(value).expanduser().resolve()
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise FileNotFoundError(f"xscore 配置不可读取: {path}") from exc
    else:
        raise ValueError("campaign.xscore 必须是配置对象或 YAML 路径")
    if not isinstance(raw, dict):
        raise ValueError("xscore 配置根结构必须是对象")
    return raw


def _as_ref(value: Any, *, expected_type: str, stage: str) -> ArtifactRef:
    try:
        ref = value if isinstance(value, ArtifactRef) else ArtifactRef.model_validate(value)
    except Exception as exc:
        raise ValueError(f"{stage} 必须返回不可变 ArtifactRef: {exc}") from exc
    if ref.artifact_type != expected_type:
        raise ValueError(
            f"{stage} 返回 {ref.artifact_type!r}，预期 {expected_type!r}"
        )
    statuses = ("completed",) if expected_type == "strategy_backtest" else (
        "candidate",
        "accepted",
    )
    return validate_artifact_ref(
        ref,
        expected_type=expected_type,
        allowed_statuses=statuses,
    )


def _publish_campaign_manifest(
    config: Mapping[str, Any],
    *,
    factor_refs: Sequence[ArtifactRef],
    composite_ref: ArtifactRef,
    strategy_ref: ArtifactRef,
) -> Path:
    name = config.get("name")
    if not isinstance(name, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name
    ):
        raise ValueError("research-campaign config 必须有安全的非空 name")
    root = Path(
        config.get("output_root")
        or Path(os.environ.get(
            "QUANTRESEARCH_ROOT", "/data/students/gaolei/quantresearch"
        )) / "results"
    ).expanduser().resolve()
    data_root = Path(os.environ.get(
        "QUANTRESEARCH_ROOT", "/data/students/gaolei/quantresearch"
    )).expanduser().resolve() / "data"
    if root == data_root or data_root in root.parents:
        raise ValueError("research-campaign 输出不能写入 quantresearch/data/")

    sources = {
        "factor_artifacts": [
            ref.model_dump(mode="json") for ref in factor_refs
        ],
        "composite_artifact": composite_ref.model_dump(mode="json"),
        "strategy_artifact": strategy_ref.model_dump(mode="json"),
    }
    version_payload = {
        "schema_version": 1,
        "name": name,
        "artifacts": sources,
    }
    version = hashlib.sha256(
        json.dumps(
            version_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    campaign_dir = root / name / version
    manifest_path = campaign_dir / "campaign_manifest.json"
    manifest = {
        "schema_version": 1,
        "artifact_type": "research_campaign",
        "name": name,
        "version": version,
        "status": "completed",
        **sources,
    }
    encoded = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2
    ) + "\n"
    campaign_dir.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        if manifest_path.read_text(encoding="utf-8") != encoded:
            raise FileExistsError(
                f"research-campaign immutable manifest 不一致: {manifest_path}"
            )
        return manifest_path
    temp_path = campaign_dir / f".campaign_manifest.{os.getpid()}.tmp"
    try:
        temp_path.write_text(encoded, encoding="utf-8")
        os.replace(temp_path, manifest_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return manifest_path


@flow(name="research-campaign", log_prints=True)
def research_campaign_flow(config: Mapping[str, Any]) -> dict[str, Any]:
    """Run factor mining → xscore → M7/M8, forwarding only published refs.

    ``factor`` accepts one config or a list of factor configs. ``xscore`` may be
    a YAML path or mapping; its ``inputs`` are always replaced with the refs
    returned by factor-mining. ``strategy`` is passed together with the
    CompositeArtifact returned by xscore.
    """
    if not isinstance(config, Mapping):
        raise ValueError("research-campaign config 必须为对象")
    missing = {"factor", "xscore", "strategy"} - set(config)
    if missing:
        raise ValueError(f"research-campaign config 缺少必填项: {sorted(missing)}")
    strategy_config = config["strategy"]
    if not isinstance(strategy_config, Mapping):
        raise ValueError("campaign.strategy 必须是配置对象")

    # Import child entrypoints only when the parent flow runs. This keeps the
    # helpers importable in tooling that does not install Prefect or platform
    # runtime dependencies, and avoids reimplementing child-flow logic here.
    from research_flows.factor_mining import factor_mining_flow
    from research_flows.xscore_flow import xscore_flow
    from research_flows.strategy_execution import strategy_execution_flow

    factor_refs: list[ArtifactRef] = []
    for factor_config in _factor_configs(config["factor"]):
        factor_refs.append(_as_ref(
            factor_mining_flow(factor_config),
            expected_type="factor_signal",
            stage="factor-mining",
        ))

    xscore_config = _load_xscore_config(config["xscore"])
    xscore_config["inputs"] = [
        ref.model_dump(mode="json") for ref in factor_refs
    ]
    with tempfile.TemporaryDirectory(prefix="research-campaign-xscore-") as temp:
        config_path = Path(temp) / "xscore.yaml"
        config_path.write_text(
            yaml.safe_dump(xscore_config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        composite_ref = _as_ref(
            xscore_flow(config_path),
            expected_type="composite_signal",
            stage="xscore",
        )

    strategy_ref = _as_ref(
        strategy_execution_flow(dict(strategy_config), composite_ref),
        expected_type="strategy_backtest",
        stage="strategy-execution",
    )
    campaign_manifest = _publish_campaign_manifest(
        config,
        factor_refs=factor_refs,
        composite_ref=composite_ref,
        strategy_ref=strategy_ref,
    )
    return {
        "factor_refs": factor_refs,
        "composite_ref": composite_ref,
        "strategy_ref": strategy_ref,
        "campaign_manifest": str(campaign_manifest),
    }


__all__ = ["research_campaign_flow", "run_campaign_steps"]
