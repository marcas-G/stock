import pytest
import yaml

from factorlab.core.spec import FactorSpec, load_spec


def make_spec(tmp_path, **overrides):
    data = {
        "name": "demo_factor",
        "category": "custom",
        "direction": 1,
        "universe": {"codes": ["000001.SZ", "600519.SH"]},
        "date": {"start": "2020-01-01", "end": "2021-01-01"},
        "formula": "signal = close / open - 1",
    }
    data.update(overrides)
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_load_valid_spec(tmp_path):
    spec = load_spec(make_spec(tmp_path))
    assert spec.name == "demo_factor"
    assert spec.universe.codes == ["000001.SZ", "600519.SH"]


def test_rejects_missing_direction(tmp_path):
    with pytest.raises(ValueError):
        load_spec(make_spec(tmp_path, direction=None))


def test_rejects_universe_both_codes_and_rules(tmp_path):
    with pytest.raises(ValueError):
        load_spec(make_spec(tmp_path, universe={"codes": ["000001.SZ"], "rules": {"exclude_st": True}}))


def test_rejects_formula_and_factors_together(tmp_path):
    with pytest.raises(ValueError):
        load_spec(make_spec(
            tmp_path,
            factors=[{"name": "a", "formula": "signal = close"}],
            combine={"method": "equal_weight"},
        ))


def test_load_valid_factors_and_combine(tmp_path):
    path = make_spec(
        tmp_path,
        formula=None,
        factors=[
            {"name": "a", "formula": "signal = close / open - 1"},
            {"name": "b", "formula": "signal = close - open"},
        ],
        combine={"method": "equal_weight"},
    )
    spec = load_spec(path)
    assert len(spec.factors) == 2
    assert spec.combine.method == "equal_weight"


def test_rejects_invalid_date_format(tmp_path):
    with pytest.raises(ValueError):
        load_spec(make_spec(tmp_path, date={"start": "2020/01/01"}))


def test_rejects_weight_sum_without_weights(tmp_path):
    with pytest.raises(ValueError):
        load_spec(make_spec(
            tmp_path,
            formula=None,
            factors=[{"name": "a", "formula": "signal = close"}],
            combine={"method": "weight_sum"},
        ))


def test_rejects_weight_sum_length_mismatch(tmp_path):
    with pytest.raises(ValueError):
        load_spec(make_spec(
            tmp_path,
            formula=None,
            factors=[
                {"name": "a", "formula": "signal = close"},
                {"name": "b", "formula": "signal = open"},
            ],
            combine={"method": "weight_sum", "weights": [0.5]},
        ))


def test_universe_accepts_string_reference(tmp_path):
    path = make_spec(tmp_path, universe="research_50")
    spec = load_spec(path)
    assert spec.universe.ref == "research_50"
    assert spec.universe.codes is None


def test_universe_rejects_multiple_sources(tmp_path):
    with pytest.raises(ValueError):
        load_spec(make_spec(tmp_path, universe={"ref": "a", "codes": ["000001.SZ"]}))


def test_universe_rejects_empty(tmp_path):
    with pytest.raises(ValueError):
        load_spec(make_spec(tmp_path, universe={}))


def test_universe_rejects_blank_reference(tmp_path):
    with pytest.raises(ValueError):
        load_spec(make_spec(tmp_path, universe="   "))


def test_spec_adjustment_default_qfq(tmp_path):
    spec = load_spec(make_spec(tmp_path))
    assert spec.adjustment == "qfq"


def test_spec_adjustment_explicit(tmp_path):
    spec = load_spec(make_spec(tmp_path, adjustment="raw"))
    assert spec.adjustment == "raw"


def test_spec_adjustment_invalid(tmp_path):
    with pytest.raises(ValueError):
        load_spec(make_spec(tmp_path, adjustment="bogus"))


def test_spec_params_default_empty(tmp_path):
    spec = load_spec(make_spec(tmp_path))
    assert spec.params == {}


def test_spec_params_parse(tmp_path):
    spec = load_spec(make_spec(tmp_path, params={"win": 200, "gain": 2.0, "name_x": "abc"}))
    assert spec.params == {"win": 200, "gain": 2.0, "name_x": "abc"}


# ── 调仓成本（R10：#15 spec 级接线）────────────────────────────────
def test_spec_cost_rate_default_zero(tmp_path):
    """缺省 0.0 = 与历史"零成本"结果逐值一致（不写该字段的 spec 行为不变）。"""
    assert load_spec(make_spec(tmp_path)).cost_rate == 0.0


def test_spec_cost_rate_explicit(tmp_path):
    assert load_spec(make_spec(tmp_path, cost_rate=0.0015)).cost_rate == 0.0015


@pytest.mark.parametrize("bad", [-0.1, 1.0, 2.5, "abc"])
def test_spec_cost_rate_rejects_out_of_range(tmp_path, bad):
    """费率必须是 [0, 1) 的数：负数 / ≥1 / 非数字串都拒绝（不静默接受后当成 0）。

    注：数字串 `"0.002"` 会被 pydantic 宽松模式转成 float 接受——这是既有口径
    （其它 float 字段同款），不是本字段的例外。"""
    with pytest.raises(Exception) as exc:
        load_spec(make_spec(tmp_path, cost_rate=bad))
    assert "cost_rate" in str(exc.value)


# ── R05-I2：spec 顶层未知字段严格拒绝 + op_meta 明确未实现 ─────────────


def test_rejects_unknown_top_level_field(tmp_path):
    """`bogus_field: 123` 不得静默忽略（extra=forbid，报错点名字段）。"""
    with pytest.raises(ValueError, match="bogus_field"):
        load_spec(make_spec(tmp_path, bogus_field=123))


def test_rejects_unknown_nested_universe_field(tmp_path):
    with pytest.raises(ValueError, match="bogus_nested"):
        load_spec(make_spec(tmp_path, universe={"codes": ["000001.SZ"],
                                                "bogus_nested": 1}))


def test_rejects_nonempty_op_meta_with_plan2_message(tmp_path):
    """op_meta 机制未实现（Plan 2）——非空时明确报错，不静默吞。"""
    with pytest.raises(ValueError, match="op_meta 暂未支持"):
        load_spec(make_spec(tmp_path, op_meta={"my_op": {"partition": "ts"}}))


def test_accepts_absent_or_empty_op_meta(tmp_path):
    """仅非空 op_meta 拒绝（指令口径）；缺省/null/空映射 = 无操作。"""
    assert load_spec(make_spec(tmp_path)).op_meta is None
    assert load_spec(make_spec(tmp_path, op_meta=None)).op_meta is None
    assert load_spec(make_spec(tmp_path, op_meta={})).op_meta == {}
