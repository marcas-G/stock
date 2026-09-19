"""Plan CX-C1 T4b：`factorlab compose` CLI 薄壳（错误映射 + 友好文案）。

断言来源：design §14（命令 `factorlab compose <yaml>`，薄；流程含 eval/persist）
与 §17 验收④/⑥（缺 member / output NaN → 非零退出且文案可读）。
"""

from __future__ import annotations

from typer.testing import CliRunner

import test_composite_runner as fx
from factorlab.surfaces.cli.main import app

runner = CliRunner()


def test_compose_cli_end_to_end_products(tmp_path):
    runs = tmp_path / "runs"
    fx.write_factor(runs, "factor_A", 0.0, scale=1.0)
    fx.write_factor(runs, "factor_B", 5.0, scale=2.0)
    entry, _ = fx.write_impl(tmp_path, fx._COMPUTE_BODY)
    spec = fx.write_spec(tmp_path, "cx_demo", ["factor_A", "factor_B"], entry)

    result = runner.invoke(app, ["compose", str(spec), "--results-dir", str(runs)])

    assert result.exit_code == 0, result.output
    assert "cx_demo" in result.output and "cached=False" in result.output
    out = runs / "composites" / "cx_demo"
    assert (out / "artifact.json").is_file()
    assert (out / "summary.json").is_file()

    again = runner.invoke(app, ["compose", str(spec), "--results-dir", str(runs)])
    assert again.exit_code == 0, again.output
    assert "cached=True" in again.output


def test_compose_cli_missing_member_exit1_with_guidance(tmp_path):
    runs = tmp_path / "runs"
    fx.write_factor(runs, "factor_A", 0.0)
    entry, _ = fx.write_impl(tmp_path, fx._COMPUTE_BODY)
    spec = fx.write_spec(tmp_path, "cx_demo", ["factor_A", "factor_MISSING"], entry)

    result = runner.invoke(app, ["compose", str(spec), "--results-dir", str(runs)])

    assert result.exit_code == 1
    assert "factor_MISSING" in result.output
    assert "解析目录" in result.output        # 指引：含解析目录（rich 折行会断路径）


def test_compose_cli_nan_output_exit1(tmp_path):
    runs = tmp_path / "runs"
    fx.write_factor(runs, "factor_A", 0.0)
    fx.write_factor(runs, "factor_B", 5.0)
    entry, _ = fx.write_impl(tmp_path, fx._NAN_BODY)
    spec = fx.write_spec(tmp_path, "cx_demo", ["factor_A", "factor_B"], entry)

    result = runner.invoke(app, ["compose", str(spec), "--results-dir", str(runs)])

    assert result.exit_code == 1
    assert "NaN" in result.output
    assert not (runs / "composites" / "cx_demo" / "artifact.json").exists()
