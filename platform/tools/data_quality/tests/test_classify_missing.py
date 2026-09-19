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


# ── 残余类 SPORADIC_GAP（F6）────────────────────────────────────────────
def test_terminal_tail_dates_picks_last_contiguous_run():
    """末端尾段=抵达最新日期的连续缺列段（交易日相邻，跳空 ≤5 天）；早期孤立单日不算。"""
    dates = [D(1991, 1, 29), D(1991, 2, 1), D(1991, 4, 1),
             D(2026, 7, 9), D(2026, 7, 10), D(2026, 7, 13)]
    tail = cm.terminal_tail_dates(dates, max_gap_days=5)
    assert tail == {D(2026, 7, 9), D(2026, 7, 10), D(2026, 7, 13)}
    assert D(1991, 1, 29) not in tail


def test_sporadic_gap_only_for_traded_rows_outside_tail():
    """SPORADIC_GAP：部分缺列码、volume>0、且不在退市尾段。"""
    tail = {D(2026, 6, 25)}
    assert cm.is_sporadic_gap(
        {"trade_date": D(1991, 1, 29), "volume": 1500.0},
        terminal_tail=tail, partial_code=True) is True
    assert cm.is_sporadic_gap(
        {"trade_date": D(2026, 6, 25), "volume": 100.0},
        terminal_tail=tail, partial_code=True) is False
    assert cm.is_sporadic_gap(
        {"trade_date": D(1991, 1, 29), "volume": 0.0},
        terminal_tail=tail, partial_code=True) is False
    assert cm.is_sporadic_gap(
        {"trade_date": D(1991, 1, 29), "volume": 100.0},
        terminal_tail=tail, partial_code=False) is False


def test_resolve_row_class_priority():
    """行级类=字段类的最高优先（TRUE_ERROR > LEGACY > SOURCE > EXPECTED）。"""
    assert cm.resolve_row_class(
        {"amount": cm.EXPECTED_MISSING, "adj_factor": cm.SOURCE_LIMITATION}
    ) == cm.SOURCE_LIMITATION
    assert cm.resolve_row_class(
        {"amount": cm.TRUE_ERROR, "adj_factor": cm.SOURCE_LIMITATION}
    ) == cm.TRUE_ERROR
    assert cm.resolve_row_class(
        {"amount": cm.LEGACY_SCHEMA, "adj_factor": cm.TRUE_ERROR}
    ) == cm.TRUE_ERROR
    assert cm.resolve_row_class({"amount": cm.EXPECTED_MISSING}) == cm.EXPECTED_MISSING


def test_iter_row_hits_yields_row_and_field_level():
    """逐行产出：行级类 + 字段类（命中展开由调用方按字段累计）。"""
    import polars as pl

    df = pl.DataFrame({
        "trade_date": [D(2026, 6, 25), D(2026, 6, 25)],
        "code": ["000004.SZ", "600519.SH"],
        "volume": [0.0, 1000.0],
        "close": [10.0, 10.0],
        "amount": [None, None],
        "float_shares": [None, None],
        "adj_factor": [None, None],
    })
    rows = list(cm.iter_row_hits(df, delisted={"000004.SZ"}, rules=_rules()))
    assert len(rows) == 2
    by_code = {r["code"]: r for r in rows}
    assert by_code["000004.SZ"]["klass"] == cm.EXPECTED_MISSING
    assert by_code["000004.SZ"]["classes"] == {
        "amount": cm.EXPECTED_MISSING, "float_shares": cm.EXPECTED_MISSING,
        "adj_factor": cm.EXPECTED_MISSING}
    assert by_code["600519.SH"]["klass"] == cm.TRUE_ERROR
    assert by_code["600519.SH"]["year"] == 2026
    assert by_code["600519.SH"]["market"] == "SH"


def test_module_has_no_parquet_writes():
    """只读红线：分群模块源码不得出现 parquet 写入。"""
    src = Path(cm.__file__).read_text(encoding="utf-8")
    assert "write_parquet" not in src
    assert "to_parquet" not in src
