"""T3：1.25M 命中 root-cause 分群纯函数（Plan DQ-M1.5）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m15/task-3-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §8 只审不改/三状态；§4 ③ 核心字段 NaN 登记原因不填充。

四分类（规则可配置）：EXPECTED_MISSING / SOURCE_LIMITATION / TRUE_ERROR / LEGACY_SCHEMA。
断言来自 brief 与 R32 impact-analysis 事实（退市股全史缺 amount/adj/float_shares），
不从实现推导：
- 停牌（volume=0）缺额 → 语义预期缺失；
- 退市代码有成交（volume>0）缺列 → 源限制；
- 非退市代码缺列 → 真实错误（无已知源解释）；
- 早于字段可用日 → LEGACY_SCHEMA（优先级最高）。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from data_quality import classify_missing as cm

D = dt.date


def _rules(**kw):
    base = dict(expected_when_volume_zero=True, legacy_field_first_dates={})
    base.update(kw)
    return cm.ClassRules(**base)


# ── 单行分类（四类 + 优先级）─────────────────────────────────────────────
def test_expected_missing_for_suspension_row():
    """退市码、volume=0（停牌 carry 行）→ EXPECTED_MISSING（无成交语义）。"""
    klass, reason = cm.classify_missing_row(
        field="amount", trade_date=D(1996, 5, 31), volume=0.0,
        delisted=True, rules=_rules())
    assert klass == cm.EXPECTED_MISSING
    assert "停牌" in reason or "无成交" in reason


def test_source_limitation_for_traded_delisted_row():
    """退市码、volume>0 但 amount/adj/float_shares 全缺 → SOURCE_LIMITATION。"""
    klass, reason = cm.classify_missing_row(
        field="amount", trade_date=D(2006, 5, 10), volume=12800.0,
        delisted=True, rules=_rules())
    assert klass == cm.SOURCE_LIMITATION
    assert "退市" in reason


def test_true_error_for_active_code_missing():
    """非退市代码缺核心字段 → TRUE_ERROR（不得默认归因为源限制）。"""
    klass, reason = cm.classify_missing_row(
        field="amount", trade_date=D(2020, 1, 2), volume=1000.0,
        delisted=False, rules=_rules())
    assert klass == cm.TRUE_ERROR


def test_legacy_schema_has_highest_precedence():
    """早于字段首次可用日 → LEGACY_SCHEMA（即使 code 退市且无成交）。"""
    rules = _rules(legacy_field_first_dates={"amount": D(1999, 1, 1)})
    klass, _ = cm.classify_missing_row(
        field="amount", trade_date=D(1991, 1, 16), volume=0.0,
        delisted=True, rules=rules)
    assert klass == cm.LEGACY_SCHEMA


def test_zero_volume_rule_is_configurable():
    """关掉 volume==0 预期规则 → 停牌行回落到 SOURCE_LIMITATION。"""
    klass, _ = cm.classify_missing_row(
        field="float_shares", trade_date=D(1996, 5, 31), volume=0.0,
        delisted=True, rules=_rules(expected_when_volume_zero=False))
    assert klass == cm.SOURCE_LIMITATION


def test_classification_depends_on_inputs_not_constants():
    """同字段同日期，volume/delisted 任一变化即换类（存根必败）。"""
    kw = dict(field="adj_factor", trade_date=D(2006, 5, 10), rules=_rules())
    assert cm.classify_missing_row(volume=0.0, delisted=True, **kw)[0] == cm.EXPECTED_MISSING
    assert cm.classify_missing_row(volume=100.0, delisted=True, **kw)[0] == cm.SOURCE_LIMITATION
    assert cm.classify_missing_row(volume=100.0, delisted=False, **kw)[0] == cm.TRUE_ERROR


# ── 聚合 ──────────────────────────────────────────────────────────────────
def test_market_of_code_sh_sz_bj():
    assert cm.market_of_code("600519.SH") == "SH"
    assert cm.market_of_code("000001.SZ") == "SZ"
    assert cm.market_of_code("920305.BJ") == "BJ"
    assert cm.market_of_code("430047.BJ") == "BJ"


def _hits():
    return [
        {"field": "amount", "code": "000004.SZ", "year": 1991, "delisted": True,
         "volume": 0.0, "klass": cm.EXPECTED_MISSING},
        {"field": "amount", "code": "000002.SZ", "year": 1991, "delisted": True,
         "volume": 1300.0, "klass": cm.SOURCE_LIMITATION},
        {"field": "float_shares", "code": "000002.SZ", "year": 1992,
         "delisted": True, "volume": 100.0, "klass": cm.SOURCE_LIMITATION},
        {"field": "adj_factor", "code": "600000.SH", "year": 2020,
         "delisted": False, "volume": 1000.0, "klass": cm.TRUE_ERROR},
    ]


def test_class_counts_total_and_per_class():
    got = cm.class_counts(_hits())
    assert got == {cm.EXPECTED_MISSING: 1, cm.SOURCE_LIMITATION: 2,
                   cm.TRUE_ERROR: 1}
    assert sum(got.values()) == 4


def test_aggregate_by_dimension():
    got = cm.aggregate_by(_hits(), by="field")
    assert got["amount"][cm.SOURCE_LIMITATION] == 1
    assert got["amount"][cm.EXPECTED_MISSING] == 1
    assert got["float_shares"][cm.SOURCE_LIMITATION] == 1
    assert got["adj_factor"][cm.TRUE_ERROR] == 1


def test_full_code_gap_vs_partial_split():
    """按代码全史缺失（n_miss==n_total）与部分缺失分流计数。"""
    per_code = {"000002.SZ": (764, 764), "000004.SZ": (803, 430)}
    got = cm.split_full_partial(per_code)
    assert got == {"full": 1, "partial": 1}


def test_module_has_no_parquet_writes():
    """只读红线：分群模块源码不得出现 parquet 写入。"""
    src = Path(cm.__file__).read_text(encoding="utf-8")
    assert "write_parquet" not in src
    assert "to_parquet" not in src
