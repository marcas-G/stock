"""lint 必须与引擎同序：先 ${param} 替换、再 AST 校验（2026-09-14 全库扫描暴露）。

背景：全库 152 个 spec 跑 `factorlab lint` → 15 个失败，**全部**是用了文档化
`params` + `${name}` 模板的 spec（如 vol_run_energy_*）。原因：lint 拿未替换的
文本（`${win}` 不是合法 Python）直接 `validate_formula` → 假报"语法错误"，而 run
链先 `_substitute_params` 再校验、正常跑通。写因子的第一条命令就误报 = 门失效。
"""
from __future__ import annotations

from pathlib import Path

import pytest
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


# ---------- R01-ENG C1/C2/I5：lint 必须跑引擎同序语义门 ----------

_EVIDENCE = (Path(__file__).resolve().parents[2]
             / "docs/reviews/r01-2026-09-15-strict-review/evidence/engine-dsl")


def _formula_spec(tmp_path, formula: str, universe: str = 'codes: ["000001.SZ"]'):
    body = "\n".join("  " + line for line in formula.splitlines())
    path = tmp_path / "s_sem.yaml"
    path.write_text(f"""
name: lint_sem_demo
category: custom
direction: 1
universe:
  {universe}
formula: |
{body}
""", encoding="utf-8")
    return path


def test_lint_rejects_future_subscript(tmp_path):
    result = runner.invoke(app, ["lint", str(_formula_spec(tmp_path, "signal = close[-1]"))])
    assert result.exit_code == 1, result.output
    assert "负位移" in result.stdout


def test_lint_rejects_negative_shift_via_named_const(tmp_path):
    result = runner.invoke(app, [
        "lint", str(_formula_spec(tmp_path, "_n = 3\nsignal = ts_delay(close, -_n)"))])
    assert result.exit_code == 1, result.output
    assert "负位移" in result.stdout


def test_lint_rejects_unknown_operator(tmp_path):
    result = runner.invoke(app, ["lint", str(_formula_spec(tmp_path, "signal = no_such_op(close)"))])
    assert result.exit_code == 1, result.output
    assert "未知算子" in result.stdout


def test_lint_rejects_future_shift_in_pool_formula(tmp_path):
    result = runner.invoke(app, [
        "lint", str(_formula_spec(tmp_path, "signal = close", universe='formula: "close[-1] > 10"'))])
    assert result.exit_code == 1, result.output
    assert "负位移" in result.stdout


def test_lint_accepts_positive_subscript(tmp_path):
    result = runner.invoke(app, ["lint", str(_formula_spec(tmp_path, "signal = close[1]"))])
    assert result.exit_code == 0, result.output
    assert "OK" in result.stdout


@pytest.mark.parametrize("fname", [
    "future_subscript.yaml", "future_const.yaml", "neg_shift_lint.yaml",
])
def test_lint_rejects_review_probe_specs(fname):
    """I5 验收：三类 R01 review probe spec 必须 exit≠0（此前全部 exit 0）。"""
    result = runner.invoke(app, ["lint", str(_EVIDENCE / fname)])
    assert result.exit_code == 1, f"{fname} 过 lint（语义门缺失）: {result.output}"
