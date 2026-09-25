"""Acceptance tests for parent-flow orchestration and Prefect deployments."""
from __future__ import annotations

import json
import os
import re
import sys
import types
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _artifact_ref(tmp_path, name, artifact_type="factor_signal"):
    from research_flows.artifacts import publish_artifact

    artifact_dir = tmp_path / name
    artifact_dir.mkdir()
    primary = "signal.parquet" if artifact_type == "factor_signal" else "panel.parquet"
    (artifact_dir / primary).write_bytes(f"{name}-payload".encode())
    return publish_artifact(
        artifact_dir,
        {
            "artifact_type": artifact_type,
            "name": name,
            "version": f"{name}-v1",
            "config_sha256": "a" * 64,
            "spec_sha256": "b" * 64 if artifact_type == "factor_signal" else None,
            "data_version": "daily-v1",
            "window_id": "train-2026",
            "sample_role": "is",
            "mode": "explore",
            "status": (
                "completed" if artifact_type == "strategy_backtest" else "accepted"
            ),
            "source_artifacts": [],
            "platform_commit": "test-commit",
            "access_ids": [],
        },
        primary_file=primary,
    )


def _identity_prefect(monkeypatch):
    prefect = types.ModuleType("prefect")
    prefect.flow = lambda *args, **kwargs: (
        args[0] if args and callable(args[0]) and len(args) == 1 and not kwargs
        else lambda fn: fn
    )
    prefect.task = prefect.flow
    monkeypatch.setitem(sys.modules, "prefect", prefect)


def test_campaign_injects_only_published_refs_and_passes_them_in_order(
    tmp_path, monkeypatch
):
    _identity_prefect(monkeypatch)
    factor_refs = [_artifact_ref(tmp_path, "alpha"), _artifact_ref(tmp_path, "beta")]
    composite_ref = _artifact_ref(tmp_path, "composite", "composite_signal")
    strategy_ref = _artifact_ref(tmp_path, "strategy", "strategy_backtest")
    calls = []

    factor_module = types.ModuleType("research_flows.factor_mining")

    def factor_flow(config):
        calls.append(("factor", config))
        return factor_refs[len([call for call in calls if call[0] == "factor"]) - 1]

    factor_module.factor_mining_flow = factor_flow
    xscore_module = types.ModuleType("research_flows.xscore_flow")

    def xscore_flow(config_path):
        import yaml

        config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        calls.append(("xscore", config))
        return composite_ref

    xscore_module.xscore_flow = xscore_flow
    strategy_module = types.ModuleType("research_flows.strategy_execution")

    def strategy_execution_flow(config, ref):
        calls.append(("strategy", config, ref))
        return strategy_ref

    strategy_module.strategy_execution_flow = strategy_execution_flow
    monkeypatch.setitem(sys.modules, "research_flows.factor_mining", factor_module)
    monkeypatch.setitem(sys.modules, "research_flows.xscore_flow", xscore_module)
    monkeypatch.setitem(
        sys.modules, "research_flows.strategy_execution", strategy_module
    )
    sys.modules.pop("research_flows.campaign", None)
    from research_flows.campaign import research_campaign_flow

    result = research_campaign_flow(
        {
            "name": "campaign-demo",
            "output_root": str(tmp_path / "results"),
            "factor": [{"name": "alpha"}, {"name": "beta"}],
            "xscore": {
                "mode": "explore",
                "name": "campaign-composite",
                "groups": {"daily": ["alpha", "beta"]},
                "models": ["M0a"],
            },
            "strategy": {"strategy_spec": "/tmp/strategy.yaml"},
        }
    )

    assert [call[0] for call in calls] == ["factor", "factor", "xscore", "strategy"]
    assert calls[2][1]["inputs"] == [
        ref.model_dump(mode="json") for ref in factor_refs
    ]
    assert calls[2][1]["groups"] == {"daily": ["alpha", "beta"]}
    assert calls[3] == ("strategy", {"strategy_spec": "/tmp/strategy.yaml"}, composite_ref)
    assert result["factor_refs"] == factor_refs
    assert result["composite_ref"] == composite_ref
    assert result["strategy_ref"] == strategy_ref
    manifest_path = Path(result["campaign_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["factor_artifacts"] == [
        ref.model_dump(mode="json") for ref in factor_refs
    ]
    assert manifest["composite_artifact"] == composite_ref.model_dump(mode="json")
    assert manifest["strategy_artifact"] == strategy_ref.model_dump(mode="json")


def test_campaign_stops_after_a_failed_child_flow(monkeypatch):
    _identity_prefect(monkeypatch)
    calls = []
    factor_module = types.ModuleType("research_flows.factor_mining")
    factor_module.factor_mining_flow = lambda _config: (
        calls.append("factor") or (_ for _ in ()).throw(RuntimeError("factor failed"))
    )
    monkeypatch.setitem(sys.modules, "research_flows.factor_mining", factor_module)
    sys.modules.pop("research_flows.campaign", None)
    from research_flows.campaign import research_campaign_flow

    with pytest.raises(RuntimeError, match="factor failed"):
        research_campaign_flow({
            "factor": {"name": "alpha"},
            "xscore": {"name": "x"},
            "strategy": {},
        })
    assert calls == ["factor"]


def test_deployment_registration_uses_expected_names_and_supported_arguments(
    monkeypatch,
):
    registrations = []
    prefect = types.ModuleType("prefect")

    class FakeFlow:
        def __init__(self, name, fn):
            self.name = name
            self.fn = fn

        def __call__(self, *args, **kwargs):
            return self.fn(*args, **kwargs)

        def to_deployment(self, **kwargs):
            registrations.append((self.name, kwargs))
            return {"flow": self.name, **kwargs}

    def flow(*args, name=None, **kwargs):
        del kwargs
        if args and callable(args[0]):
            return FakeFlow(name or args[0].__name__, args[0])
        return lambda fn: FakeFlow(name or fn.__name__, fn)

    prefect.flow = flow
    prefect.task = flow
    prefect.serve = lambda *deployments: deployments
    monkeypatch.setitem(sys.modules, "prefect", prefect)

    def fake_flow_module(module_name, flow_name, entrypoint):
        module = types.ModuleType(module_name)
        module.__dict__[entrypoint] = flow(name=flow_name)(lambda *_a, **_k: None)
        monkeypatch.setitem(sys.modules, module_name, module)

    fake_flow_module(
        "research_flows.factor_mining", "factor-mining", "factor_mining_flow"
    )
    fake_flow_module("research_flows.xscore_flow", "xscore", "xscore_flow")
    fake_flow_module(
        "research_flows.strategy_execution",
        "strategy-execution",
        "strategy_execution_flow",
    )
    sys.modules.pop("research_flows.campaign", None)
    sys.modules.pop("research_flows.deployments", None)
    from research_flows.deployments import create_deployments

    deployments = create_deployments()

    assert [d["name"] for d in deployments] == [
        "factor-mining",
        "xscore",
        "strategy-execution",
        "research-campaign",
    ]
    assert [name for name, _ in registrations] == [
        "factor-mining",
        "xscore-pipeline",
        "strategy-execution",
        "research-campaign",
    ]
    assert all(
        set(kwargs) <= {"name", "description", "tags", "parameters"}
        for _, kwargs in registrations
    )
    assert all(kwargs["name"] in {
        "factor-mining", "xscore", "strategy-execution", "research-campaign"
    } for _, kwargs in registrations)


def test_runner_installer_starts_the_new_research_deployments():
    root = Path(__file__).resolve().parents[4]
    installer = (root / "governance/ops/install_prefect_runner.sh").read_text(
        encoding="utf-8"
    )

    assert "research_flows.deployments" in installer
    assert "xscore/pipeline/serve.py" not in installer
    assert "factor-mining/factor-mining" in installer
    assert "xscore-pipeline/xscore" in installer
    assert "strategy-execution/strategy-execution" in installer


def test_local_runner_loads_yaml_and_dispatches_one_flow(tmp_path, monkeypatch):
    config_path = tmp_path / "factor.yaml"
    config_path.write_text("name: alpha\nmode: explore\n", encoding="utf-8")
    received = []

    module = types.ModuleType("research_flows.factor_mining")
    module.factor_mining_flow = lambda config: received.append(config) or "done"
    monkeypatch.setitem(sys.modules, "research_flows.factor_mining", module)

    from research_flows.run import run_flow

    assert run_flow("factor-mining/factor-mining", config_path) == "done"
    assert received == [{"name": "alpha", "mode": "explore"}]


def test_runbook_yaml_examples_are_parseable_and_cover_four_flows():
    import yaml

    repo_root = Path(__file__).resolve().parents[4]
    pointer = (repo_root / "research/tools/research_flows/README.md").read_text(
        encoding="utf-8"
    )
    assert "$QUANTRESEARCH_ROOT/knowledge/pipeline-usage.md" in pointer
    assert len(pointer.splitlines()) <= 12

    qr_root = Path(os.environ.get(
        "QUANTRESEARCH_ROOT", "/data/students/gaolei/quantresearch"
    ))
    runbook_path = qr_root / "knowledge/pipeline-usage.md"
    if not runbook_path.is_file():
        pytest.skip(f"quantresearch 手册不存在：{runbook_path}")
    runbook = runbook_path.read_text(encoding="utf-8")
    examples = re.findall(r"```yaml\s+(.*?)```", runbook, flags=re.DOTALL)

    assert len(examples) == 4
    configs = [yaml.safe_load(example) for example in examples]
    assert all(isinstance(config, dict) for config in configs)
    assert configs[0]["mode"] == "explore"
    assert "inputs" in configs[1]
    assert "signal_ref" in configs[2]
    assert {"factor", "xscore", "strategy"} <= set(configs[3])
