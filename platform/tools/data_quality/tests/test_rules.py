"""T1：dq_policy 加载 + 严重度 + 规则目录（Plan DQ-M1）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-1-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §1 严重度 / §3.2 policy 字段与初值 / §4 规则目录 / §6 health 字段命名参考。
"""
from __future__ import annotations

import dataclasses
import textwrap
from pathlib import Path

import pytest

from data_quality import rules

# spec §3.2 冻结初值（逐字）
FULL = """\
dq_policy_version: daily-v1
partition_gate:
  pass:
    max_error_rate: 0.0001
    min_coverage: 0.999
  fail:
    min_error_rate: 0.01
    max_missing_coverage: 0.01
systemic:
  field_share: 0.80
  field_count: 50
  group_ratio: 10.0
  group_count: 50
  time_share: 0.80
  time_count: 100
"""


def _write_policy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_load_policy_shipped_yaml_matches_spec_values():
    p = rules.load_policy()
    assert p.dq_policy_version == rules.POLICY_VERSION == "daily-v1"
    assert p.partition_gate == {
        "pass": {"max_error_rate": 0.0001, "min_coverage": 0.999},
        "fail": {"min_error_rate": 0.01, "max_missing_coverage": 0.01},
    }
    assert p.systemic == {
        "field_share": 0.80, "field_count": 50, "group_ratio": 10.0,
        "group_count": 50, "time_share": 0.80, "time_count": 100,
    }


def test_load_policy_reads_values_from_given_path(tmp_path):
    body = FULL.replace("max_error_rate: 0.0001", "max_error_rate: 0.002")
    p = rules.load_policy(_write_policy(tmp_path, body))
    assert p.partition_gate["pass"]["max_error_rate"] == 0.002


def test_missing_nested_field_raises_naming_it(tmp_path):
    body = FULL.replace("    max_missing_coverage: 0.01\n", "")
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "partition_gate.fail.max_missing_coverage" in str(ei.value)


def test_missing_version_raises_naming_it(tmp_path):
    body = FULL.replace("dq_policy_version: daily-v1\n", "")
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "dq_policy_version" in str(ei.value)


def test_unknown_version_rejected_not_mapped(tmp_path):
    body = FULL.replace("daily-v1", "daily-v0")
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    msg = str(ei.value)
    assert "daily-v0" in msg and "daily-v1" in msg


def test_non_numeric_threshold_rejected_naming_it(tmp_path):
    body = FULL.replace("max_error_rate: 0.0001", "max_error_rate: high")
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "partition_gate.pass.max_error_rate" in str(ei.value)


# §4 规则目录（daily 子集）→ §1 级别（逐条来自设计文档，不由实现推导）
CATALOG = {
    # ① 主键与重复
    "DUP_IDENTICAL": rules.INFO,
    "PK_CONFLICT": rules.ERROR,
    # ② 时间与交易日历
    "TIME_UNPARSEABLE": rules.ERROR,
    "TRADE_DATE_INVALID": rules.ERROR,
    "LIST_BEFORE": rules.ERROR,
    "LIST_AFTER_DELIST": rules.ERROR,
    "TIME_ORDER": rules.WARN,
    # ③ 基础数值合法性
    "PRICE_NONPOSITIVE": rules.ERROR,
    "VOLUME_NEGATIVE": rules.ERROR,
    "AMOUNT_NEGATIVE": rules.ERROR,
    "NONFINITE_VALUE": rules.ERROR,
    "MISSING_VALUE": rules.WARN,
    # ④ OHLC 与内部一致性
    "OHLC_INVALID": rules.ERROR,
    "VWAP_OUT_OF_RANGE": rules.ERROR,
    # ⑤ 收益跳变与复权/公司行为
    "ADJ_FACTOR_CA_MISMATCH": rules.WARN,
    "ADJ_NEGATIVE": rules.ERROR,
    # ⑥ 证券状态与市场规则
    "LIMIT_BREACH": rules.WARN,
    "LIMIT_FIRST_DAY_EXEMPT": rules.INFO,
}


def test_rule_catalog_ids_and_levels():
    for rid, level in CATALOG.items():
        assert getattr(rules, rid) == rid
        assert rules.RULE_LEVELS[rid] == level


def test_cross_source_and_health_referenced_rule_ids_exist():
    # §6 health 示例引用 UNIT_SUSPECT；§4⑦ 跨源/跨频（T6/M2 消费）
    for rid in ("UNIT_SUSPECT", "CROSS_SOURCE_DEVIATION", "MINUTE_DAILY_MISMATCH"):
        assert getattr(rules, rid) == rid
        assert rules.RULE_LEVELS[rid] in (rules.WARN, rules.INFO)


def test_rule_result_carries_interface_fields_and_is_frozen():
    r = rules.RuleResult("PK_CONFLICT", rules.ERROR, "600519.SH|2026-09-18", "payload 冲突")
    assert (r.rule_id, r.level, r.key, r.detail) == (
        "PK_CONFLICT", rules.ERROR, "600519.SH|2026-09-18", "payload 冲突")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.key = "x"


def test_sort_results_by_severity_then_rule_then_key_stable():
    rs = [
        rules.RuleResult(rules.ADJ_FACTOR_CA_MISMATCH, rules.WARN, "b|2", "w"),
        rules.RuleResult(rules.PK_CONFLICT, rules.ERROR, "b|1", "e"),
        rules.RuleResult(rules.DUP_IDENTICAL, rules.INFO, "a|1", "i"),
        rules.RuleResult(rules.PK_CONFLICT, rules.ERROR, "a|1", "e2"),
        rules.RuleResult(rules.PK_CONFLICT, rules.ERROR, "a|1", "e3"),
        rules.RuleResult("SCHEMA_MISSING_COLUMN", rules.FATAL, "close", "f"),
    ]
    got = rules.sort_results(rs)
    assert [(r.level, r.rule_id, r.key) for r in got] == [
        (rules.FATAL, "SCHEMA_MISSING_COLUMN", "close"),
        (rules.ERROR, rules.PK_CONFLICT, "a|1"),
        (rules.ERROR, rules.PK_CONFLICT, "a|1"),
        (rules.ERROR, rules.PK_CONFLICT, "b|1"),
        (rules.WARN, rules.ADJ_FACTOR_CA_MISMATCH, "b|2"),
        (rules.INFO, rules.DUP_IDENTICAL, "a|1"),
    ]
    # 同 (level, rule_id, key) 保持输入相对顺序（稳定）
    same = [r.detail for r in got
            if r.rule_id == rules.PK_CONFLICT and r.key == "a|1"]
    assert same == ["e2", "e3"]
