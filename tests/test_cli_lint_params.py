"""lint 必须与引擎同序：先 ${param} 替换、再 AST 校验（2026-09-14 全库扫描暴露）。

背景：全库 152 个 spec 跑 `factorlab lint` → 15 个失败，**全部**是用了文档化
`params` + `${name}` 模板的 spec（如 vol_run_energy_*）。原因：lint 拿未替换的
文本（`${win}` 不是合法 Python）直接 `validate_formula` → 假报"语法错误"，而 run
链先 `_substitute_params` 再校验、正常跑通。写因子的第一条命令就误报 = 门失效。
"""
from __future__ import annotations

from typer.testing import CliRunner

from factorlab.surfaces.cli.main import app

runner = CliRunner()

_SPEC = """
name: lint_params_demo
category: custom
direction: 1
params: {{win: {win}}}
universe:
  codes: ["000001.SZ"]
date:
  start: "2024-01-02"
  end: "2024-01-12"
formula: |
  signal = ts_mean(close, ${{win}})
"""


def _write(tmp_path, text: str):
    path = tmp_path / "s.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_lint_accepts_params_template(tmp_path):
    result = runner.invoke(app, ["lint", str(_write(tmp_path, _SPEC.format(win=20)))])
    assert result.exit_code == 0, result.output
    assert "OK" in result.stdout


def test_lint_reports_undeclared_param(tmp_path):
    text = _SPEC.format(win=20).replace("params: {win: 20}", "params: {}")
    result = runner.invoke(app, ["lint", str(_write(tmp_path, text))])
    assert result.exit_code == 1, result.output
    assert "未知参数" in result.stdout


def test_lint_still_catches_real_syntax_error(tmp_path):
    """负向控制：真语法错误仍须报错（修复不得把门放宽成永远 OK）。"""
    text = _SPEC.format(win=20).replace("signal = ts_mean(close, ${win})",
                                        "signal = ts_mean(close,")
    result = runner.invoke(app, ["lint", str(_write(tmp_path, text))])
    assert result.exit_code == 1, result.output
    assert "语法错误" in result.stdout


def test_lint_validates_pool_formula(tmp_path):
    """池公式（universe.formula）纳入校验——此前完全没查。"""
    text = """
name: lint_pool_demo
category: custom
direction: 1
universe:
  formula: "close >"
formula: |
  signal = close
"""
    result = runner.invoke(app, ["lint", str(_write(tmp_path, text))])
    assert result.exit_code == 1, result.output
    assert "语法错误" in result.stdout


def test_lint_validates_operator_macro_body(tmp_path):
    """operators 宏体纳入校验（${} 的另一个载体）。"""
    text = """
name: lint_macro_demo
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
formula: |
  signal = my_op(close)
operators:
  my_op: {params: [x], formula: "x +"}
"""
    result = runner.invoke(app, ["lint", str(_write(tmp_path, text))])
    assert result.exit_code == 1, result.output
    assert "语法错误" in result.stdout
