"""Cross-flow validation rules that do not depend on Prefect or data services."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from research_flows.artifacts import ArtifactRef


def validate_mode_sample(
    mode: str,
    sample_role: str,
    *,
    access_ids: Sequence[str],
) -> None:
    """Require exploration to stay IS-only and final to carry lockbox evidence."""
    if mode not in {"explore", "final"}:
        raise ValueError(f"mode 必须显式为 explore 或 final，收到 {mode!r}")
    if sample_role not in {"is", "mixed", "lockbox", "unknown"}:
        raise ValueError(f"未知 sample_role: {sample_role!r}")
    if mode == "explore":
        if sample_role != "is":
            raise ValueError(
                f"explore 只允许训练段 IS，不能交接 sample_role={sample_role!r}"
            )
        if access_ids:
            raise ValueError("explore 不得产生锁箱 access_id")
        return
    if sample_role not in {"mixed", "lockbox"}:
        raise ValueError(
            f"final 必须声明 mixed/lockbox 样本，不能使用 {sample_role!r}"
        )
    if not access_ids:
        raise ValueError("final 必须带锁箱 access_ids 证据")


def validate_factor_inputs(
    refs: Sequence[ArtifactRef],
    *,
    allowed_root: str | Path | None = None,
) -> list[ArtifactRef]:
    if not refs:
        raise ValueError("xscore 至少需要一个 FactorArtifact 引用")
    validated: list[ArtifactRef] = []
    seen: set[tuple[str, str]] = set()
    from research_flows.artifacts import validate_artifact_ref

    for ref in refs:
        current = validate_artifact_ref(
            ref,
            expected_type="factor_signal",
            allowed_statuses=("candidate", "accepted"),
            allowed_root=allowed_root,
        )
        key = (current.name, current.version)
        if key in seen:
            raise ValueError(f"xscore 输入重复 FactorArtifact: {key}")
        seen.add(key)
        validated.append(current)
    return validated


def validate_signal_input(
    ref: ArtifactRef,
    *,
    allowed_root: str | Path | None = None,
) -> ArtifactRef:
    from research_flows.artifacts import validate_artifact_ref

    return validate_artifact_ref(
        ref,
        expected_type=("factor_signal", "composite_signal"),
        allowed_statuses=("candidate", "accepted"),
        allowed_root=allowed_root,
    )
