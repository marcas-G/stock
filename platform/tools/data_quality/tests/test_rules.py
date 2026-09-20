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

# spec §3.2 冻结初值（daily-v3：R37 范围收窄，2026-09-20 用户裁定）
FULL = """\
dq_policy_version: daily-v3
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
vwap:
  default_tol: 0.01
  pre_1995_tol: 0.02
  pre_1995_cutoff: "1995-01-01"
field_invalidity:
  null_on_nonpositive: ["adj_factor", "fq_factor"]
historic_unit_exceptions:
  - codes: ["000002.SZ", "000004.SZ"]
    before: "1994-01-01"
    factor: 5.0
    match_tol: 0.10
  - codes: ["600602.SH"]
    after: "1991-01-03"
    before: "1992-12-01"
    factor: 0.01
    match_tol: 0.10
scope:
  min_trade_date: "1996-01-01"
  exclude_code_suffixes: [".BJ"]
"""


def _write_policy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_load_policy_shipped_yaml_matches_spec_values():
    p = rules.load_policy()
    assert p.dq_policy_version == rules.POLICY_VERSION == "daily-v3"
    assert p.partition_gate == {
        "pass": {"max_error_rate": 0.0001, "min_coverage": 0.999},
        "fail": {"min_error_rate": 0.01, "max_missing_coverage": 0.01},
    }
    assert p.systemic == {
        "field_share": 0.80, "field_count": 50, "group_ratio": 10.0,
        "group_count": 50, "time_share": 0.80, "time_count": 100,
    }
    assert (p.vwap_default_tol, p.vwap_pre_1995_tol,
            p.vwap_pre_1995_cutoff) == (0.01, 0.02, "1995-01-01")
    assert p.field_invalidity_null == ("adj_factor", "fq_factor")
    assert p.historic_unit_exceptions[0] == rules.HistoricUnitException(
        codes=("000002.SZ", "000004.SZ"), before="1994-01-01", factor=5.0,
        match_tol=0.10)
    sh = next(r for r in p.historic_unit_exceptions
              if r.codes == ("600602.SH",))
    assert (sh.after, sh.before, sh.factor, sh.match_tol) == (
        "1991-01-03", "1992-12-01", 0.01, 0.10)


def test_shipped_registry_covers_t2b_unit_eras():
    """T2b：登记均为 code×era 单位约定（含 after 下界），样本与 dominance 见 RCA。"""
    p = rules.load_policy()
    regs = p.historic_unit_exceptions
    assert len(regs) == 15, "1（T2）+ 14（T2b 前 1996 单位 regime）"
    by_code = {}
    for r in regs:
        for c in r.codes:
            by_code.setdefault(c, []).append(r)
    # 每个 T2b 登记项都有 era 下界（after）与 unit factor（≠1，除既有 T2 条目）
    t2b = [r for r in regs if r.codes != ("000002.SZ", "000004.SZ")]
    assert all(r.after is not None for r in t2b), "T2b era 必须带 after 下界"
    assert all(r.factor != 1.0 for r in t2b), "登记只收单位因子（不得把 1.0 当例外）"
    assert {"600602.SH", "600654.SH", "600651.SH", "600601.SH", "000017.SZ"} \
        <= set(by_code)


def test_load_policy_reads_values_from_given_path(tmp_path):
    body = FULL.replace("  default_tol: 0.01", "  default_tol: 0.02")
    p = rules.load_policy(_write_policy(tmp_path, body))
    assert p.vwap_default_tol == 0.02


def test_missing_nested_field_raises_naming_it(tmp_path):
    body = FULL.replace("    max_missing_coverage: 0.01\n", "")
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "partition_gate.fail.max_missing_coverage" in str(ei.value)


def test_missing_vwap_field_raises_naming_it(tmp_path):
    body = FULL.replace("  default_tol: 0.01\n", "")
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "vwap.default_tol" in str(ei.value)


def test_vwap_cutoff_must_be_iso_date(tmp_path):
    body = FULL.replace('pre_1995_cutoff: "1995-01-01"',
                        'pre_1995_cutoff: "not-a-date"')
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "vwap.pre_1995_cutoff" in str(ei.value)


def test_missing_version_raises_naming_it(tmp_path):
    body = FULL.replace("dq_policy_version: daily-v3\n", "")
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "dq_policy_version" in str(ei.value)


def test_unknown_version_rejected_not_mapped(tmp_path):
    body = FULL.replace("daily-v3", "daily-v0")
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    msg = str(ei.value)
    assert "daily-v0" in msg and "daily-v3" in msg


def test_v1_policy_file_rejected_by_v2_loader():
    """v1 文件保留作历史；v2 加载器对旧版本明确拒绝（不兼容映射）。"""
    v1 = Path(rules.__file__).with_name("dq_policy.daily-v1.yaml")
    assert v1.is_file(), "daily-v1 文件必须保留（历史可追溯）"
    with pytest.raises(ValueError) as ei:
        rules.load_policy(v1)
    assert "daily-v1" in str(ei.value) and "daily-v3" in str(ei.value)


def test_non_numeric_threshold_rejected_naming_it(tmp_path):
    body = FULL.replace("max_error_rate: 0.0001", "max_error_rate: high")
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "partition_gate.pass.max_error_rate" in str(ei.value)


def test_registry_entry_missing_codes_rejected(tmp_path):
    body = FULL.replace('codes: ["000002.SZ", "000004.SZ"]', "codes: []")
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "historic_unit_exceptions[0].codes" in str(ei.value)


def test_registry_entry_bad_match_tol_rejected(tmp_path):
    body = FULL.replace("    match_tol: 0.10", "    match_tol: wide")
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "historic_unit_exceptions[0].match_tol" in str(ei.value)


def test_registry_entry_bad_after_rejected(tmp_path):
    body = FULL.replace('after: "1991-01-03"', 'after: "not-a-date"')
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "historic_unit_exceptions[1].after" in str(ei.value)


def test_registry_entry_after_on_or_after_before_rejected(tmp_path):
    body = FULL.replace('after: "1991-01-03"', 'after: "1993-01-01"')
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "historic_unit_exceptions[1].after" in str(ei.value)


def test_field_invalidity_requires_nonempty_string_list(tmp_path):
    body = FULL.replace('null_on_nonpositive: ["adj_factor", "fq_factor"]',
                        'null_on_nonpositive: []')
    with pytest.raises(ValueError) as ei:
        rules.load_policy(_write_policy(tmp_path, body))
    assert "field_invalidity.null_on_nonpositive" in str(ei.value)


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
    # ⑤ 收益跳变与复权/公司行为（v2：字段级 invalid → NULL + flag，行保留）
    "ADJ_FACTOR_CA_MISMATCH": rules.WARN,
    "ADJ_NULLED": rules.WARN,
    # v2：登记代码×年代的早期单位约定例外（VWAP 降级 WARN + flag）
    "HISTORIC_UNIT_EXCEPTION": rules.WARN,
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


def test_rule_fields_covers_every_rule_id():
    """T4 系统性检测按 rule_id 查 RULE_FIELDS，任何目录 rule_id 不得 KeyError。"""
    assert set(rules.RULE_FIELDS) == set(rules.RULE_LEVELS)
    for rid, field in rules.RULE_FIELDS.items():
        assert field is None or isinstance(field, str)
    assert rules.RULE_FIELDS[rules.SCHEMA_MISSING_COLUMN] is None
    assert rules.RULE_FIELDS[rules.SCHEMA_DATE_DTYPE] == "trade_date"


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
