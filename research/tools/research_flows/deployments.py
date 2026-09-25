"""Register the independent research flows as local Prefect deployments.

Run with ``python -m research_flows.deployments`` from ``research/tools``.
Prefect's ``serve`` process keeps the deployments registered and polls for runs.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

DEPLOYMENT_NAMES = frozenset({
    "factor-mining/factor-mining",
    "xscore-pipeline/xscore",
    "strategy-execution/strategy-execution",
    "research-campaign/research-campaign",
})


def _deployment_flows():
    """Build thin lazy-import wrappers for importable research-venv deployments."""
    from prefect import flow

    @flow(name="factor-mining")
    def factor_mining_deployment(config: dict[str, Any]):
        from research_flows.factor_mining import factor_mining_flow

        return factor_mining_flow(config)

    @flow(name="xscore-pipeline")
    def xscore_pipeline(config_path: str):
        from research_flows.xscore_flow import xscore_flow

        return xscore_flow(config_path)

    @flow(name="strategy-execution")
    def strategy_deployment(config: dict[str, Any], ref: dict[str, Any]):
        from research_flows.artifacts import ArtifactRef
        from research_flows.strategy_execution import strategy_execution_flow

        return strategy_execution_flow(config, ArtifactRef.model_validate(ref))

    from research_flows.campaign import research_campaign_flow

    return (
        factor_mining_deployment,
        xscore_pipeline,
        strategy_deployment,
        research_campaign_flow,
    )


def create_deployments(*, include_campaign: bool = True) -> list[Any]:
    """Build the three core deployments and optionally the parent campaign.

    Each child flow keeps its own public parameter schema. No made-up default
    parameters are installed: runs can supply the full research configuration
    through the Prefect UI or CLI.
    """
    flows = _deployment_flows()
    definitions = [
        (flows[0], "factor-mining", "FactorLab factor run and FactorArtifact publication",
         ["research", "factor-mining"]),
        (flows[1], "xscore", "FactorArtifact scoring and CompositeArtifact publication",
         ["research", "xscore"]),
        (flows[2], "strategy-execution", "M7/M8 strategy backtest over an immutable signal",
         ["research", "strategy-execution"]),
    ]
    if include_campaign:
        definitions.append((
            flows[3],
            "research-campaign",
            "Run factor-mining, xscore, and strategy-execution in sequence",
            ["research", "campaign"],
        ))
    return [
        flow_obj.to_deployment(
            name=deployment_name,
            description=description,
            tags=tags,
        )
        for flow_obj, deployment_name, description, tags in definitions
    ]


def serve_deployments(*, include_campaign: bool = True) -> Sequence[Any]:
    """Register and serve deployments using Prefect's supported ``serve`` API."""
    from prefect import serve

    return serve(*create_deployments(include_campaign=include_campaign))


def main() -> int:
    os.environ.setdefault("PREFECT_API_URL", "http://127.0.0.1:4200/api")
    serve_deployments()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by installed runner.
    raise SystemExit(main())
