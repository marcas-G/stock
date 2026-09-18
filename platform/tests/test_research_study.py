"""R31 Task 6：study 一条链（plan `2026-09-18-research-api.md` Task 6）。

断言来源：
- spec `2026-09-18-research-api-design.md` §3 study：
  `flab study run <factor.yaml> [--strategy <yaml>] [--against reference] [--skip-admit]`
  = 因子 run →（admit 冗余检验）→ 策略回测 → 报告 URL；`flab study list` 历史记录。
- spec §4：stdout 单个 JSON；错误码→退出码（RUN_FAILED/STRATEGY_FAILED/BUSY/...）。
- spec §7：链序断言（mock 各组记录调用序 run→admit→strategy→report）；
  任一步失败→对应错误码且 artifacts 保留已完成步骤；禁止存根（调用序 + 参数）。
- plan Task 6 Step 1：`--skip-admit` 跳过；`study_list`。

禁止行为证明：链序与逐步骤参数由 mock 记录断言——硬编码/跳过步骤的存根必败；
失败传播测试要求 artifacts 携带已完成步骤的真实路径；study 记录落盘后 list 可读回。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factorlab.config import settings
from factorlab.research import envelope, registry
from factorlab.research import study as ST
from factorlab.research.envelope import EXIT_CODES
from factorlab.surfaces.cli.main import app as cli_app

runner = CliRunner()

STUDY_COMMANDS = {"study.run", "study.list"}


# ================================================================
# 工具：假组 handler（记录调用序/参数）+ 信封构造
# ================================================================

def _args(**kw) -> argparse.Namespace:
    base = dict(factor_yaml=None, strategy=None, against="reference",
                skip_admit=False, wait=False, pretty=False, json=True)
    base.update(kw)
    return argparse.Namespace(**base)


def _strict_json(env: envelope.Envelope) -> str:
    return json.dumps(env.to_doc(), ensure_ascii=False, allow_nan=False)


class _Chain:
    """伪造 factor/strategy/report 组 handler，记录调用序与参数。"""

    def __init__(self, monkeypatch, tmp_path: Path,
                 run_fail=None, admit_fail=None, strategy_fail=None,
                 report_fail=None):
        self.calls: list[tuple] = []
        self.tmp = tmp_path
        self.run_fail = run_fail
        self.admit_fail = admit_fail
        self.strategy_fail = strategy_fail
        self.report_fail = report_fail

        monkeypatch.setattr(ST, "factor_run", self._run)
        monkeypatch.setattr(ST, "factor_admit", self._admit)
        monkeypatch.setattr(ST, "strategy_run", self._strategy)
        monkeypatch.setattr(ST, "report_url", self._report)

    def _run(self, args):
        self.calls.append(("run", str(args.spec_path), bool(args.wait)))
        if self.run_fail is not None:
            return self.run_fail
        return envelope.ok(
            "factor.run", {"name": "cand"},
            artifacts={"run_dir": str(self.tmp / "runs" / "cand"),
                       "summary": str(self.tmp / "runs" / "cand" / "summary.json"),
                       "log": str(self.tmp / "runs" / "cand" / "run.log")})

    def _admit(self, args):
        self.calls.append(("admit", str(args.spec_path), args.scales))
        if self.admit_fail is not None:
            return self.admit_fail
        return envelope.ok("factor.admit", {"name": "cand", "verdict": "可加入",
                                            "corr_max": 0.1, "r2_lib": 0.1},
                           artifacts={"summary": str(self.tmp / "runs" / "cand"
                                                     / "summary.json")})

    def _strategy(self, args):
        self.calls.append(("strategy", str(args.doc_path), args.signal))
        if self.strategy_fail is not None:
            return self.strategy_fail
        return envelope.ok(
            "strategy.run", {"name": "strat", "signal": args.signal,
                             "decisions": 4, "execution_events": 4, "fills": 8,
                             "nav": {"events": 4, "last": 1.1}},
            artifacts={"strategy_dir": str(self.tmp / "runs" / "strategies" / "strat"),
                       "log": str(self.tmp / "runs" / "strategies" / "strat" / "run.log")})

    def _report(self, args):
        self.calls.append(("report", args.name))
        if self.report_fail is not None:
            return self.report_fail
        return envelope.ok("report.url", {"name": args.name,
                                          "url": f"http://127.0.0.1:8000/factor/{args.name}"},
                           artifacts={"summary": str(self.tmp / "runs" / args.name
                                                     / "summary.json")})

    def order(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture()
def results_dir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "runs" / "platform"
    monkeypatch.setattr(settings, "results_dir", root)
    return root


# ================================================================
# 链序：run → admit → strategy → report
# ================================================================

def test_study_run_chain_order_full(monkeypatch, tmp_path, results_dir):
    chain = _Chain(monkeypatch, tmp_path)
    spec = tmp_path / "cand.yaml"
    doc = tmp_path / "strat.yaml"

    e = ST.study_run(_args(factor_yaml=spec, strategy=doc))

    assert e.ok, e.error
    assert chain.order() == ["run", "admit", "strategy", "report"]
    assert chain.calls[0] == ("run", str(spec), False)
    assert chain.calls[1] == ("admit", str(spec), "daily")   # reference→daily 组
    assert chain.calls[2] == ("strategy", str(doc), "cand")  # signal 强制=本次因子
    assert chain.calls[3] == ("report", "cand")
    assert e.data["factor"] == "cand"
    assert e.data["verdict"] == "可加入"
    assert e.data["steps"] == ["run", "admit", "strategy", "report"]
    assert e.data["report"]["url"].endswith("/factor/cand")
    # artifacts 汇集已完成步骤（run_dir/summary/strategy_dir/report_url/log）
    assert Path(e.artifacts["run_dir"]).name == "cand"
    assert Path(e.artifacts["summary"]).name == "summary.json"
    assert Path(e.artifacts["strategy_dir"]).name == "strat"
    assert e.artifacts["report_url"].endswith("/factor/cand")
    assert Path(e.artifacts["log"]).exists()
    _strict_json(e)


def test_study_run_skip_admit(monkeypatch, tmp_path, results_dir):
    chain = _Chain(monkeypatch, tmp_path)
    e = ST.study_run(_args(factor_yaml=tmp_path / "cand.yaml",
                           strategy=tmp_path / "strat.yaml", skip_admit=True))
    assert e.ok, e.error
    assert chain.order() == ["run", "strategy", "report"]
    assert e.data["verdict"] is None
    assert e.data["steps"] == ["run", "strategy", "report"]


def test_study_run_without_strategy_still_reports(monkeypatch, tmp_path,
                                                  results_dir):
    chain = _Chain(monkeypatch, tmp_path)
    e = ST.study_run(_args(factor_yaml=tmp_path / "cand.yaml"))
    assert e.ok, e.error
    assert chain.order() == ["run", "admit", "report"]
    assert e.data["strategy"] is None
    assert "strategy_dir" not in e.artifacts


def test_study_run_redundant_verdict_is_warning_not_failure(monkeypatch,
                                                            tmp_path,
                                                            results_dir):
    chain = _Chain(monkeypatch, tmp_path)
    chain._admit = lambda args: envelope.ok(
        "factor.admit", {"name": "cand", "verdict": "冗余", "corr_max": 0.9,
                         "r2_lib": 0.85})
    monkeypatch.setattr(ST, "factor_admit", chain._admit)

    e = ST.study_run(_args(factor_yaml=tmp_path / "cand.yaml",
                           strategy=tmp_path / "strat.yaml"))
    assert e.ok, e.error
    assert e.data["verdict"] == "冗余"
    assert any("冗余" in w for w in e.warnings)
    _strict_json(e)


# ================================================================
# 失败传播：对应错误码 + artifacts 保留已完成步骤
# ================================================================

@pytest.mark.parametrize("code,exit_code", [("RUN_FAILED", 6), ("BUSY", 11)])
def test_study_run_factor_failure_propagates_code(monkeypatch, tmp_path,
                                                  results_dir, code, exit_code):
    chain = _Chain(monkeypatch, tmp_path,
                   run_fail=envelope.fail("factor.run", code, f"{code} 测试",
                                          hint="hint-run"))
    e = ST.study_run(_args(factor_yaml=tmp_path / "cand.yaml",
                           strategy=tmp_path / "strat.yaml"))
    assert e.ok is False
    assert e.error["code"] == code
    assert e.error["hint"] == "hint-run"
    assert envelope.exit_code(e) == EXIT_CODES[code] == exit_code
    assert chain.order() == ["run"]  # 后续步骤不得执行
    _strict_json(e)


def test_study_run_strategy_failure_keeps_prior_artifacts(monkeypatch, tmp_path,
                                                          results_dir):
    chain = _Chain(monkeypatch, tmp_path,
                   strategy_fail=envelope.fail("strategy.run", "STRATEGY_FAILED",
                                               "执行闸拦截", hint="hint-strategy"))
    e = ST.study_run(_args(factor_yaml=tmp_path / "cand.yaml",
                           strategy=tmp_path / "strat.yaml"))
    assert e.ok is False
    assert e.error["code"] == "STRATEGY_FAILED"
    assert envelope.exit_code(e) == EXIT_CODES["STRATEGY_FAILED"] == 7
    assert chain.order() == ["run", "admit", "strategy"]   # report 不执行
    # 已完成步骤产物保留（run_dir/summary 来自 step1）
    assert Path(e.artifacts["run_dir"]).name == "cand"
    assert Path(e.artifacts["summary"]).name == "summary.json"
    _strict_json(e)


def test_study_run_admit_failure_keeps_run_artifacts(monkeypatch, tmp_path,
                                                     results_dir):
    chain = _Chain(monkeypatch, tmp_path,
                   admit_fail=envelope.fail("factor.admit", "NOT_FOUND",
                                            "参考库缺失", hint="hint-admit"))
    e = ST.study_run(_args(factor_yaml=tmp_path / "cand.yaml"))
    assert e.ok is False
    assert e.error["code"] == "NOT_FOUND"
    assert chain.order() == ["run", "admit"]
    assert Path(e.artifacts["run_dir"]).name == "cand"
    _strict_json(e)


def test_study_run_report_failure_propagates(monkeypatch, tmp_path, results_dir):
    chain = _Chain(monkeypatch, tmp_path,
                   report_fail=envelope.fail("report.url", "NOT_FOUND", "报告缺失"))
    e = ST.study_run(_args(factor_yaml=tmp_path / "cand.yaml"))
    assert e.ok is False
    assert e.error["code"] == "NOT_FOUND"
    assert chain.order() == ["run", "admit", "report"]
    assert Path(e.artifacts["run_dir"]).name == "cand"
    _strict_json(e)


def test_study_run_unknown_against_is_usage(monkeypatch, tmp_path, results_dir):
    chain = _Chain(monkeypatch, tmp_path)
    e = ST.study_run(_args(factor_yaml=tmp_path / "cand.yaml", against="ghost"))
    assert e.ok is False
    assert e.error["code"] == "USAGE"
    assert chain.order() == []
    assert "reference" in (e.error["hint"] or "")
    _strict_json(e)


def test_study_run_minute_against_maps_to_minute_scales(monkeypatch, tmp_path,
                                                        results_dir):
    chain = _Chain(monkeypatch, tmp_path)
    e = ST.study_run(_args(factor_yaml=tmp_path / "cand.yaml", against="minute"))
    assert e.ok, e.error
    assert chain.calls[1] == ("admit", str(tmp_path / "cand.yaml"), "minute")


# ================================================================
# study list：历史记录（成功 + 失败）
# ================================================================

def test_study_list_records_and_orders(monkeypatch, tmp_path, results_dir):
    chain = _Chain(monkeypatch, tmp_path)
    spec, doc = tmp_path / "cand.yaml", tmp_path / "strat.yaml"
    ok_env = ST.study_run(_args(factor_yaml=spec, strategy=doc))
    assert ok_env.ok, ok_env.error

    monkeypatch.setattr(ST, "factor_run", lambda args: envelope.fail(
        "factor.run", "RUN_FAILED", "炸了"))
    bad_env = ST.study_run(_args(factor_yaml=spec))
    assert bad_env.ok is False

    e = ST.study_list(_args())
    assert e.ok, e.error
    assert e.data["n"] == 2
    newest, older = e.data["studies"]
    assert newest["ok"] is False and newest["error_code"] == "RUN_FAILED"
    assert older["ok"] is True and older["factor"] == "cand"
    assert older["verdict"] == "可加入"
    assert older["strategy_yaml"] == str(doc)
    assert older["report_url"].endswith("/factor/cand")
    assert Path(older["path"]).is_file()
    _strict_json(e)


def test_study_list_empty(monkeypatch, tmp_path, results_dir):
    e = ST.study_list(_args())
    assert e.ok, e.error
    assert e.data == {"n": 0, "studies": []}


# ================================================================
# registry / CLI 契约
# ================================================================

def test_study_commands_registered_with_schemas():
    assert STUDY_COMMANDS <= set(registry.COMMANDS)
    for name in sorted(STUDY_COMMANDS):
        doc = registry.COMMANDS[name].to_doc()
        assert doc["description"] and doc["examples"] and doc["output_schema"]
        assert doc["defaults"].get("json") is True


def test_study_run_parser_positional_and_flags():
    parser = registry.build_parser(registry.COMMANDS["study.run"])
    ns = parser.parse_args(["f.yaml", "--strategy", "s.yaml", "--against",
                            "minute", "--skip-admit", "--wait"])
    assert str(ns.factor_yaml) == "f.yaml"
    assert str(ns.strategy) == "s.yaml"
    assert ns.against == "minute" and ns.skip_admit is True and ns.wait is True


def test_cli_research_study_failure_single_json(monkeypatch, tmp_path,
                                                results_dir):
    monkeypatch.setattr(ST, "factor_run", lambda args: envelope.fail(
        "factor.run", "LINT", "spec 非法", hint="先 lint"))
    result = runner.invoke(cli_app, ["research", "study", "run",
                                     str(tmp_path / "x.yaml"), "--json"])
    assert result.exit_code == EXIT_CODES["LINT"] == 3
    assert len(result.stdout.strip().splitlines()) == 1
    doc = json.loads(result.stdout)
    assert doc["ok"] is False and doc["command"] == "study.run"
    assert doc["error"]["code"] == "LINT"


def test_cli_research_study_list_single_json(monkeypatch, tmp_path,
                                             results_dir):
    result = runner.invoke(cli_app, ["research", "study", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert len(result.stdout.strip().splitlines()) == 1
    doc = json.loads(result.stdout)
    assert doc["ok"] is True and doc["command"] == "study.list"
    assert doc["data"] == {"n": 0, "studies": []}
