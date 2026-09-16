"""R05-I2：spec 严格解析——未知字段明确报错（含 op_meta 未实现指引）。

R05 实测 `bogus_field: 123` / `op_meta:` 被静默忽略，而引擎文案又引导补
op_meta（机制不存在）——本文件锁死：
- 未知顶层字段/嵌套字段 = 加载期 ValidationError 且点名字段；
- `op_meta` 非空 = 明确"暂未支持（Plan 2）"（不得静默吞掉）；
- `factorlab lint` 与引擎同走严格解析（load_spec）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from factorlab.core.spec import FactorSpec

_BASE = {
    "name": "strict_demo",
    "category": "custom",
    "direction": 1,
    "universe": {"codes": ["000001.SZ"]},
    "formula": "signal = close",
}


def test_valid_spec_still_loads():
    spec = FactorSpec.model_validate(_BASE)
    assert spec.name == "strict_demo"


def test_unknown_top_level_field_rejected_and_named():
    with pytest.raises(ValidationError, match="bogus_field"):
        FactorSpec.model_validate({**_BASE, "bogus_field": 123})


def test_op_meta_rejected_with_plan2_guidance():
    with pytest.raises(ValidationError, match="op_meta 暂未支持"):
        FactorSpec.model_validate(
            {**_BASE, "op_meta": {"my_fn": {"partition": "ts"}}})


def test_unknown_nested_universe_field_rejected():
    with pytest.raises(ValidationError, match="bogus_universe_field"):
        FactorSpec.model_validate(
            {**_BASE,
             "universe": {"codes": ["000001.SZ"], "bogus_universe_field": 1}})


def test_unknown_field_in_factors_item_rejected():
    data = {
        "name": "strict_multi",
        "category": "custom",
        "direction": 1,
        "universe": {"codes": ["000001.SZ"]},
        "factors": [{"name": "a", "formula": "a = close", "typo_field": 1}],
        "combine": {"method": "equal_weight"},
    }
    with pytest.raises(ValidationError, match="typo_field"):
        FactorSpec.model_validate(data)


def _lint(tmp_path, text: str):
    from typer.testing import CliRunner

    from factorlab.surfaces.cli.main import app

    p = tmp_path / "s.yaml"
    p.write_text(text, encoding="utf-8")
    return CliRunner().invoke(app, ["lint", str(p)])


def test_lint_rejects_unknown_spec_field(tmp_path):
    r = _lint(tmp_path, """
name: strict_cli
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
bogus_field: 123
formula: |
  signal = close
""")
    assert r.exit_code != 0, r.output
    assert "bogus_field" in r.output


def test_lint_rejects_op_meta_with_plan2_guidance(tmp_path):
    r = _lint(tmp_path, """
name: strict_opmeta
category: custom
direction: 1
universe:
  codes: ["000001.SZ"]
op_meta:
  my_fn: {partition: ts}
formula: |
  signal = close
""")
    assert r.exit_code != 0, r.output
    assert "op_meta 暂未支持" in r.output


def test_unknown_op_guidance_does_not_promise_op_meta():
    """semantics 未知算子文案：指引 def / factorlab op add，注明 op_meta 未实现。"""
    from factorlab.core.engine.semantics import SemanticError, infer
    from factorlab.core.ops.classification import default_catalog

    with pytest.raises(SemanticError) as exc:
        infer("signal = totally_new(close)", default_catalog())
    msg = str(exc.value)
    assert "def" in msg and "op add" in msg
    assert "op_meta 尚未实现" in msg
