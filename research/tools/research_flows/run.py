"""Run one research flow locally from a YAML config.

Example:
    PYTHONPATH=research/tools research/.venv/bin/python -m research_flows.run \
        factor-mining/factor-mining /path/to/factor.yaml
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from research_flows.artifacts import ArtifactRef


_FLOW_NAMES = {
    "factor-mining/factor-mining",
    "xscore-pipeline/xscore",
    "strategy-execution/strategy-execution",
    "research-campaign/research-campaign",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, ArtifactRef):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _read_config(path: str | Path) -> Any:
    config_path = Path(path).expanduser().resolve()
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FileNotFoundError(f"Flow 配置不可读取: {config_path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Flow 配置根节点必须是 YAML mapping: {config_path}")
    return value


def run_flow(flow_name: str, config_path: str | Path) -> Any:
    """Load one config and call its Prefect flow entrypoint."""
    if flow_name not in _FLOW_NAMES:
        raise ValueError(
            f"未知 flow {flow_name!r}；可用值: {sorted(_FLOW_NAMES)}"
        )
    config = _read_config(config_path)
    if flow_name == "factor-mining/factor-mining":
        from research_flows.factor_mining import factor_mining_flow

        return factor_mining_flow(config)
    if flow_name == "xscore-pipeline/xscore":
        from research_flows.xscore_flow import xscore_flow

        return xscore_flow(Path(config_path).expanduser().resolve())
    if flow_name == "strategy-execution/strategy-execution":
        from research_flows.strategy_execution import strategy_execution_flow

        config = dict(config)
        raw_ref = config.pop("signal_ref", None)
        if raw_ref is None:
            raise ValueError(
                "strategy YAML 必须内嵌完整 signal_ref ArtifactRef"
            )
        return strategy_execution_flow(
            config,
            ArtifactRef.model_validate(raw_ref),
        )
    from research_flows.campaign import research_campaign_flow

    return research_campaign_flow(config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one Prefect research flow from a YAML configuration."
    )
    parser.add_argument("flow", choices=sorted(_FLOW_NAMES))
    parser.add_argument("config", help="YAML 配置文件路径")
    args = parser.parse_args(argv)
    result = run_flow(args.flow, args.config)
    print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - covered via main/unit tests.
    raise SystemExit(main())
