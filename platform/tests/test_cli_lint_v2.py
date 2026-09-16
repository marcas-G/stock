"""Task 8：lint 接入完整静态管线（分类表归一化 + 未来门 + 输出名检查；不触 DB）。

与引擎同源：`prepare_static` = prepare_formula_pipeline（参数替换/宏/def 内联/
薄封装）→ stable_rank 与 vendor alias 改写 → normalize_calls（分类表解析）→
check_causality → 输出名检查。
"""

from __future__ import annotations

from typer.testing import CliRunner

from factorlab.surfaces.cli.main import app

runner = CliRunner()


def _spec(tmp_path, formula: str, *, universe: str = 'codes: ["000001.SZ"]',
          extra: str = "") -> object:
    body = "\n".join("  " + line for line in formula.splitlines())
    path = tmp_path / "s.yaml"
    path.write_text(f"""
name: lint_v2_demo
category: custom
direction: 1
universe:
  {universe}
date:
  start: "2024-01-02"
  end: "2024-01-12"
{extra}formula: |
{body}
""", encoding="utf-8")
    return path


def test_lint_rejects_negative_shift(tmp_path):
    p = _spec(tmp_path, "signal = ts_delay(close, -1)")
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code != 0 and "负位移" in r.output


def test_lint_accepts_open_library_op(tmp_path):
    # 开放面：此前报"未知算子"的库函数（ts_arg_max 不在 55 注册清单）现在 lint 通过
    p = _spec(tmp_path, "signal = ts_arg_max(volume, 120)")
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code == 0, r.output
    assert "OK" in r.output


def test_lint_rejects_struct_return_op(tmp_path):
    # R05-I1：BBANDS 返回 Struct——直接作输出/进 process 均不支持，lint 即静态拒绝
    p = _spec(tmp_path, "signal = BBANDS(volume, 20)")
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code != 0, r.output
    assert "BBANDS" in r.output and "Struct" in r.output
    assert "process" in r.output


def test_lint_rejects_unknown_operator_with_guidance(tmp_path):
    # R05-I2：指引为公式内 def / op add 插件，并注明 op_meta 机制尚未实现
    p = _spec(tmp_path, "signal = totally_new(volume)")
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code != 0
    assert "未知算子" in r.output and "op add" in r.output
    assert "op_meta" in r.output and "尚未实现" in r.output


def test_lint_rejects_platform_macro_import_with_guidance(tmp_path):
    # R03-M1：误 import 平台宏 → lint 清晰报错（非深层裸 traceback），文案"请裸用"
    p = _spec(tmp_path, "from polars_ta.prefix.wq import returns\nsignal = returns(close)")
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code == 1
    assert "平台宏 returns 请裸用" in r.output
    assert "Traceback" not in r.output


def test_lint_rejects_outputs_mismatch(tmp_path):
    p = _spec(tmp_path, "signal = close", extra='outputs: ["signal", "alpha"]\n')
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code != 0
    assert "未产出声明输出列" in r.output


def test_lint_rejects_future_in_pool_formula(tmp_path):
    p = _spec(tmp_path, "signal = close", universe='formula: "close[-1] > 10"')
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code != 0 and "负位移" in r.output


def test_lint_accepts_params_template_open_op(tmp_path):
    text = """
name: lint_v2_params
category: custom
direction: 1
params: {win: 60}
universe:
  codes: ["000001.SZ"]
formula: |
  signal = ts_arg_max(volume, ${win})
"""
    p = tmp_path / "p.yaml"
    p.write_text(text, encoding="utf-8")
    r = runner.invoke(app, ["lint", str(p)])
    assert r.exit_code == 0, r.output
