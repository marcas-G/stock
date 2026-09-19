"""Plan CX-C1 T1（C1-01）：CompositeSpec 契约 + definition_hash 顺序敏感。

断言来源：knowledge/design/platform/specs/2026-09-19-composite-alpha-aggregation-design.md
- §3 成员声明顺序 = 矩阵列顺序；Loader 禁止 sort/dedup 重排；
- §4 spec V1 结构（members/implementation.entrypoint/params/alignment/output，不表达数学）；
- §5 C1 冻结 alignment = intersection + reject；
- §9/§17：顺序变化必须改变 definition_hash；成员 hash 并入（T1 仅 spec 级）。

「禁止行为」保证：把 definition_hash 存根为常量 → 顺序敏感性断言必红。
"""

from __future__ import annotations

import copy
import re

import pytest
import yaml
from pydantic import ValidationError

from factorlab.core.composite import (CompositeSpec, definition_hash,
                                      load_composite_spec, parse_member_ref)

BASE: dict = {
    "name": "composite_001",
    "members": ["factor_A", "factor_B", "factor_C"],
    "implementation": {"entrypoint": "research.composites.impl_001:compute"},
    "params": {"w1": 0.4, "w2": 0.3, "w3": -0.3},
    "alignment": {"join": "intersection", "missing_policy": "reject"},
    "output": {"name": "signal"},
}


def _spec(**overrides) -> CompositeSpec:
    data = copy.deepcopy(BASE)
    data.update(overrides)
    return CompositeSpec.model_validate(data)


# ================================================================
# 解析与默认值
# ================================================================

def test_spec_parses_full_contract():
    spec = _spec()
    assert spec.name == "composite_001"
    assert spec.members == ["factor_A", "factor_B", "factor_C"]
    assert spec.implementation.entrypoint == "research.composites.impl_001:compute"
    assert spec.params == {"w1": 0.4, "w2": 0.3, "w3": -0.3}
    assert spec.alignment.join == "intersection"
    assert spec.alignment.missing_policy == "reject"
    assert spec.output.name == "signal"


def test_spec_defaults_and_order_preserved():
    """缺省对齐=冻结值；members 声明顺序原样保留（A 不在首位也绝不重排）。"""
    spec = CompositeSpec.model_validate({
        "name": "comp",
        "members": ["zeta", "alpha", "mike"],
        "implementation": {"entrypoint": "pkg.mod:compute"},
    })
    assert spec.members == ["zeta", "alpha", "mike"]
    assert spec.params == {}
    assert spec.alignment.join == "intersection"
    assert spec.alignment.missing_policy == "reject"
    assert spec.output.name == "signal"


# ================================================================
# extra=forbid（顶层 + 嵌套）
# ================================================================

def test_unknown_top_level_field_rejected():
    with pytest.raises(ValidationError) as ei:
        _spec(bogus_field=123)
    msg = str(ei.value)
    assert "bogus_field" in msg and "extra" in msg.lower()


def test_unknown_nested_field_rejected():
    with pytest.raises(ValidationError) as ei:
        _spec(implementation={"entrypoint": "pkg.mod:compute", "bogus": 1})
    assert "bogus" in str(ei.value)
    with pytest.raises(ValidationError) as ei:
        _spec(alignment={"join": "intersection", "missing_policy": "reject",
                         "extra_key": True})
    assert "extra_key" in str(ei.value)
    with pytest.raises(ValidationError) as ei:
        _spec(output={"name": "signal", "postprocess": "zscore"})
    assert "postprocess" in str(ei.value)


# ================================================================
# members 契约：非空 / 重复 / 前缀 / 名字
# ================================================================

def test_empty_members_rejected():
    with pytest.raises(ValidationError) as ei:
        _spec(members=[])
    msg = str(ei.value)
    assert "members" in msg and "空" in msg


def test_duplicate_members_rejected_names_positions():
    with pytest.raises(ValidationError) as ei:
        _spec(members=["factor_A", "factor_B", "factor_A"])
    msg = str(ei.value)
    assert "factor_A" in msg
    assert "1" in msg and "3" in msg          # 两个冲突位置都点名
    assert "重复" in msg


def test_duplicate_across_prefix_forms_rejected():
    """`factor_A` 与 `factors/factor_A` 指向同一 artifact——必须视为重复。"""
    with pytest.raises(ValidationError) as ei:
        _spec(members=["factor_A", "factors/factor_A"])
    assert "factor_A" in str(ei.value)


def test_unknown_member_prefix_rejected():
    with pytest.raises(ValidationError) as ei:
        _spec(members=["pools/factor_A"])
    msg = str(ei.value)
    assert "pools/factor_A" in msg
    assert "composites" in msg              # 修法指引：合法前缀点名


def test_invalid_member_name_rejected():
    with pytest.raises(ValidationError) as ei:
        _spec(members=["not valid!"])
    msg = str(ei.value)
    assert "not valid!" in msg


# ================================================================
# alignment / output / entrypoint 冻结值（非法值点名报错）
# ================================================================

def test_alignment_join_only_intersection():
    with pytest.raises(ValidationError) as ei:
        _spec(alignment={"join": "union", "missing_policy": "reject"})
    msg = str(ei.value)
    assert "union" in msg
    assert "intersection" in msg
    assert "join" in msg


def test_alignment_missing_policy_only_reject():
    with pytest.raises(ValidationError) as ei:
        _spec(alignment={"join": "intersection", "missing_policy": "fill"})
    msg = str(ei.value)
    assert "fill" in msg
    assert "reject" in msg
    assert "missing_policy" in msg


def test_output_name_frozen_to_signal():
    with pytest.raises(ValidationError) as ei:
        _spec(output={"name": "alpha"})
    msg = str(ei.value)
    assert "alpha" in msg
    assert "signal" in msg


def test_entrypoint_must_be_module_attr():
    for bad in ("", "no_colon", ":compute", "pkg.mod:", "pkg..mod:compute",
                "pkg.mod:not valid"):
        with pytest.raises(ValidationError) as ei:
            _spec(implementation={"entrypoint": bad})
        assert "entrypoint" in str(ei.value), bad
        assert bad in str(ei.value), bad


def test_parse_member_ref_forms():
    assert parse_member_ref("factor_A") == ("factor", "factor_A")
    assert parse_member_ref("factors/factor_A") == ("factor", "factor_A")
    assert parse_member_ref("composites/comp_1") == ("composite", "comp_1")
    for bad in ("pools/x", "factors/", "composites/", "bad name"):
        with pytest.raises(ValueError) as ei:
            parse_member_ref(bad)
        assert bad in str(ei.value), bad


# ================================================================
# definition_hash：规范化 + 顺序敏感
# ================================================================

def test_definition_hash_stable_and_hex():
    h1 = definition_hash(_spec())
    h2 = definition_hash(_spec())
    assert h1 == h2
    assert re.fullmatch(r"[0-9a-f]{64}", h1)


def test_definition_hash_member_order_sensitive():
    h1 = definition_hash(_spec())
    h2 = definition_hash(_spec(members=["factor_B", "factor_A", "factor_C"]))
    assert h1 != h2


def test_definition_hash_params_key_order_insensitive():
    p1 = {"w1": 0.4, "w2": 0.3, "w3": -0.3}
    p2 = {"w3": -0.3, "w1": 0.4, "w2": 0.3}
    assert list(p1) != list(p2)                 # 插入顺序确实不同
    assert definition_hash(_spec(params=p1)) == definition_hash(_spec(params=p2))


def test_definition_hash_changes_with_semantics():
    base = definition_hash(_spec())
    assert definition_hash(_spec(name="composite_002")) != base
    assert definition_hash(
        _spec(implementation={"entrypoint": "other.mod:compute"})) != base
    assert definition_hash(_spec(params={"w1": 0.5, "w2": 0.3, "w3": -0.3})) != base


def test_definition_hash_member_hashes_order_sensitive():
    a, b = "a" * 64, "b" * 64
    spec_only = definition_hash(_spec())
    ab = definition_hash(_spec(), [a, b])
    ba = definition_hash(_spec(), [b, a])
    assert ab != ba                        # 成员 hash 顺序 = 列顺序
    assert ab != spec_only                 # 并入成员 hash 后与 spec-only 不同


# ================================================================
# YAML 加载
# ================================================================

def test_load_composite_spec_yaml(tmp_path):
    path = tmp_path / "composite_001.yaml"
    path.write_text(yaml.safe_dump(BASE, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    spec = load_composite_spec(path)
    assert spec.name == "composite_001"
    assert spec.members == ["factor_A", "factor_B", "factor_C"]


def test_load_composite_spec_rejects_unknown_field(tmp_path):
    data = copy.deepcopy(BASE)
    data["weighting"] = "market_cap"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValidationError) as ei:
        load_composite_spec(path)
    assert "weighting" in str(ei.value)
