"""R31 Task 1 契约层测试：envelope / 错误码 / registry / describe / CLI 单 JSON。

断言来源：knowledge/design/platform/specs/2026-09-18-research-api-design.md
§2（门面包 registry+envelope+cli）、§4（JSON 契约 + 错误码→退出码表）、
§6（describe 自描述：命令/参数/默认值/schema/示例/错误码表；registry 单点）。
"""

from __future__ import annotations

import json
from argparse import Namespace

import pytest
from typer.testing import CliRunner

from factorlab import __version__
from factorlab.research import envelope, registry
from factorlab.research.envelope import EXIT_CODES, emit, fail, ok
from factorlab.surfaces.cli.main import app

runner = CliRunner()

SPEC_EXIT_CODES = {
    "USAGE": 2,
    "LINT": 3,
    "MEMORY_GUARD": 4,
    "DEAD_SIGNAL": 5,
    "RUN_FAILED": 6,
    "STRATEGY_FAILED": 7,
    "DATA": 8,
    "NOT_FOUND": 9,
    "INTERNAL": 10,
    "BUSY": 11,
}


@pytest.fixture()
def isolated_registry(monkeypatch):
    """注册表隔离：测试命令注册不污染全局（浅拷贝 COMMANDS/_HANDLERS）。"""
    monkeypatch.setattr(registry, "COMMANDS", dict(registry.COMMANDS))
    monkeypatch.setattr(registry, "_HANDLERS", dict(registry._HANDLERS))
    return registry


@pytest.fixture()
def probe_command(isolated_registry):
    """注册一个带真实计算 handler 的探针命令：n+1（存根返回固定值必被抓住）。"""
    calls: list[Namespace] = []

    def handler(args: Namespace) -> envelope.Envelope:
        calls.append(args)
        return ok("probe", {"n": args.n + 1, "double": args.n * 2})

    spec = registry.CommandSpec(
        name="probe",
        params=(
            registry.ParamSpec("n", kind="int", required=True, help="整数入参"),
            registry.ParamSpec("pretty", kind="bool", help="缩进 JSON"),
        ),
        defaults={"n": 0, "pretty": False},
        description="契约探针",
        examples=("factorlab research probe --n 2",),
        output_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
    )
    isolated_registry.register(spec, handler)
    return isolated_registry, spec, calls


# ================================================================
# envelope（spec §4：单 JSON 信封 + 错误码→退出码）
# ================================================================

def test_exit_codes_match_spec_table():
    assert EXIT_CODES == SPEC_EXIT_CODES


def test_ok_envelope_is_single_json(capsys):
    code = emit(ok("version", {"version": "0.1.0"}))
    assert code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(captured.out.strip().splitlines()) == 1, captured.out
    assert json.loads(captured.out) == {
        "ok": True,
        "schema_version": 1,
        "command": "version",
        "data": {"version": "0.1.0"},
        "artifacts": {},
        "warnings": [],
        "error": None,
    }


def test_fail_envelope_exit_code_and_error_shape(capsys):
    env = fail("factor.run", "BUSY", "重任务闸已满", hint="稍后重试或 --wait")
    assert emit(env) == EXIT_CODES["BUSY"] == 11
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False
    assert doc["schema_version"] == 1
    assert doc["command"] == "factor.run"
    assert doc["data"] is None
    assert doc["artifacts"] == {}
    assert doc["warnings"] == []
    assert doc["error"] == {
        "code": "BUSY",
        "message": "重任务闸已满",
        "hint": "稍后重试或 --wait",
        "log": None,
    }


def test_unknown_error_code_falls_back_to_internal(capsys):
    code = emit(fail("x.y", "NO_SUCH_CODE", "boom"))
    assert code == EXIT_CODES["INTERNAL"] == 10
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "NO_SUCH_CODE"
    assert doc["error"]["message"] == "boom"


def test_pretty_is_still_single_json(capsys):
    assert emit(ok("version", {"version": "0.1.0"}), pretty=True) == 0
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) > 1
    assert json.loads(out)["data"]["version"] == "0.1.0"


def test_python_face_dataclass_fields():
    from factorlab.research import Envelope

    env = ok("version", {"version": "0.1.0"}, artifacts={"log": "/tmp/x.log"},
             warnings=["w"])
    assert isinstance(env, Envelope)
    assert (env.ok, env.schema_version, env.command) == (True, 1, "version")
    assert env.data == {"version": "0.1.0"}
    assert env.artifacts == {"log": "/tmp/x.log"}
    assert list(env.warnings) == ["w"]
    assert env.error is None
    bad = fail("factor.run", "LINT", "spec 非法", hint="看 lint 输出")
    assert bad.ok is False
    assert bad.error["code"] == "LINT"
    assert bad.error["hint"] == "看 lint 输出"


# ================================================================
# registry + dispatch（spec §2：注册表单点；handler 异常→INTERNAL）
# ================================================================

def test_register_exposes_spec_and_handler(isolated_registry):
    sentinel = object()
    spec = registry.CommandSpec(name="unit_probe", description="x")
    isolated_registry.register(spec, lambda args: sentinel)
    assert isolated_registry.COMMANDS["unit_probe"] is spec
    assert isolated_registry.get_handler("unit_probe")(Namespace()) is sentinel


def test_dispatch_invokes_handler_with_parsed_args(probe_command, capsys):
    reg, _spec, calls = probe_command
    code = reg.dispatch(["probe", "--n", "2"])
    assert code == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["command"] == "probe"
    assert doc["data"] == {"n": 3, "double": 4}
    assert calls and calls[0].n == 2


def test_dispatch_unknown_command_is_usage(capsys, isolated_registry):
    assert isolated_registry.dispatch(["nosuch"]) == EXIT_CODES["USAGE"]
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "USAGE"
    assert "nosuch" in doc["error"]["message"]


def test_dispatch_bad_args_is_usage_single_json(capsys, probe_command):
    reg, _spec, calls = probe_command
    assert reg.dispatch(["probe", "--n", "not-an-int"]) == EXIT_CODES["USAGE"]
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "USAGE"
    assert calls == []


def test_dispatch_handler_exception_internal_with_stderr_traceback(
        capsys, isolated_registry):
    def boom(args: Namespace) -> envelope.Envelope:
        raise RuntimeError("炸了")

    isolated_registry.register(
        registry.CommandSpec(name="boom", description="x"), boom)
    assert isolated_registry.dispatch(["boom"]) == EXIT_CODES["INTERNAL"]
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "INTERNAL"
    assert "RuntimeError" in doc["error"]["message"]
    assert "RuntimeError" in captured.err  # traceback 走 stderr


def test_dispatch_coded_exception_maps_to_its_exit_code(capsys, isolated_registry):
    class Coded(Exception):
        code = "NOT_FOUND"
        message = "因子不存在"
        hint = "先跑 factor run"

    def missing(args: Namespace) -> envelope.Envelope:
        raise Coded()

    isolated_registry.register(
        registry.CommandSpec(name="missing", description="x"), missing)
    assert isolated_registry.dispatch(["missing"]) == EXIT_CODES["NOT_FOUND"]
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "NOT_FOUND"
    assert doc["error"]["message"] == "因子不存在"
    assert doc["error"]["hint"] == "先跑 factor run"


def test_dispatch_real_handler_returning_non_envelope_is_internal(
        capsys, isolated_registry):
    isolated_registry.register(
        registry.CommandSpec(name="badret", description="x"), lambda args: {"n": 1})
    assert isolated_registry.dispatch(["badret"]) == EXIT_CODES["INTERNAL"]
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INTERNAL"


def test_command_spec_doc_contains_params_defaults_examples_schema(probe_command):
    _reg, spec, _calls = probe_command
    doc = spec.to_doc()
    assert doc["name"] == "probe"
    assert doc["description"] == "契约探针"
    assert json.loads(json.dumps(doc["defaults"])) == {"n": 0, "pretty": False}
    assert doc["examples"] == ["factorlab research probe --n 2"]
    assert doc["output_schema"]["properties"]["n"]["type"] == "integer"
    names = [p["name"] for p in doc["params"]]
    assert names == ["n", "pretty"]
    assert [p["kind"] for p in doc["params"]] == ["int", "bool"]


# ================================================================
# CLI：`factorlab research describe --json`（spec §3/§6）
# ================================================================

def test_cli_research_describe_single_json():
    result = runner.invoke(app, ["research", "describe", "--json"])
    assert result.exit_code == 0
    assert len(result.stdout.strip().splitlines()) == 1
    doc = json.loads(result.stdout)
    assert doc["ok"] is True
    assert doc["command"] == "describe"
    data = doc["data"]
    assert data["exit_codes"] == SPEC_EXIT_CODES
    assert {"describe", "version"} <= set(data["commands"])
    desc = data["commands"]["describe"]
    assert desc["params"] and isinstance(desc["defaults"], dict)
    assert desc["examples"] and desc["output_schema"]


def test_cli_research_version_accepts_json_flag():
    result = runner.invoke(app, ["research", "version", "--json"])
    assert result.exit_code == 0
    assert len(result.stdout.strip().splitlines()) == 1
    doc = json.loads(result.stdout)
    assert doc["ok"] is True
    assert doc["command"] == "version"
    assert doc["data"]["version"]


def test_cli_research_describe_single_command():
    result = runner.invoke(
        app, ["research", "describe", "--json", "--command", "describe"])
    assert result.exit_code == 0
    doc = json.loads(result.stdout)
    assert doc["data"]["name"] == "describe"
    assert doc["data"]["description"]


def test_cli_research_describe_unknown_command_not_found():
    result = runner.invoke(
        app, ["research", "describe", "--json", "--command", "nope.nope"])
    assert result.exit_code == EXIT_CODES["NOT_FOUND"]
    doc = json.loads(result.stdout)
    assert doc["ok"] is False
    assert doc["error"]["code"] == "NOT_FOUND"


def test_cli_research_describe_pretty_multiline_json():
    result = runner.invoke(app, ["research", "describe", "--json", "--pretty"])
    assert result.exit_code == 0
    assert len(result.stdout.strip().splitlines()) > 1
    assert json.loads(result.stdout)["ok"] is True


def test_cli_research_version_single_json():
    result = runner.invoke(app, ["research", "version"])
    assert result.exit_code == 0
    doc = json.loads(result.stdout)
    assert doc["ok"] is True
    assert doc["data"]["version"] == __version__


def test_cli_research_without_subcommand_is_usage_json():
    result = runner.invoke(app, ["research"])
    assert result.exit_code == EXIT_CODES["USAGE"]
    doc = json.loads(result.stdout)
    assert doc["error"]["code"] == "USAGE"
