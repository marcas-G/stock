"""Platform-venv worker for xscore's Parquet and CompositeArtifact operations."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from research_flows.artifacts import ArtifactRef
from research_flows.xscore_flow import (
    _publish_composite,
    _validate_composite_artifact,
    assemble_factor_panel,
    parse_xscore_config,
    validate_factor_inputs,
)


def dispatch(action: str, payload: dict[str, Any]) -> Any:
    if action == "validate":
        refs = [ArtifactRef.model_validate(item) for item in payload["refs"]]
        checked = validate_factor_inputs(
            refs, allowed_root=payload.get("allowed_root")
        )
        return [ref.model_dump(mode="json") for ref in checked]
    if action == "assemble":
        refs = [ArtifactRef.model_validate(item) for item in payload["refs"]]
        return assemble_factor_panel(
            refs,
            payload["panel_path"],
            allowed_root=payload.get("allowed_root"),
            min_coverage=float(payload["min_coverage"]),
        )
    if action == "publish":
        refs = [ArtifactRef.model_validate(item) for item in payload["refs"]]
        config = parse_xscore_config(payload["config_path"])
        refs = validate_factor_inputs(refs, allowed_root=config.artifact_root)
        result = _publish_composite(
            refs=refs,
            config=config,
            version=payload["version"],
            code_sha=payload["code_sha"],
            score_dir=Path(payload["score_dir"]),
            panel_path=Path(payload["panel_path"]),
            access_ids=payload.get("access_ids", []),
        )
        return result.model_dump(mode="json")
    if action == "validate_composite":
        ref = _validate_composite_artifact(
            payload["artifact_dir"],
            allowed_root=payload["allowed_root"],
            mode=request_mode(payload),
        )
        return ref.model_dump(mode="json")
    raise ValueError(f"未知 xscore worker action: {action!r}")


def request_mode(payload: dict[str, Any]) -> str:
    mode = payload.get("mode")
    if mode not in {"explore", "final"}:
        raise ValueError("validate_composite worker 缺少有效 mode")
    return mode


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise SystemExit("usage: python -m research_flows.xscore_worker <request.json>")
    request_path = Path(args[0])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    result = dispatch(request["action"], request["payload"])
    response = Path(request["response"])
    response.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through heavy worker
    raise SystemExit(main())
