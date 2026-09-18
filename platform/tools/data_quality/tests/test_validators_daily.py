"""T2：daily 行级校验器（Plan DQ-M1；表驱动正反例）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-2-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §1 严重度 / §4 规则目录 ①-⑦（daily 子集）/ §5 确定性修复清单。

覆盖：每规则至少 1 个 PASS 样例 + 1 个命中样例；INFO 例外（首日/完全相同行）有样例；
反例含「禁止行为」断言（NaN 不得 fillna(0)、PK 冲突不得自动选一条、纯函数不改输入）。
"""
from __future__ import annotations

import datetime as dt
import math

import polars as pl
import pytest

from data_quality import rules, validators

D = dt.date
SYM = "600519.SH"
DAY1, DAY2 = D(2026, 9, 17), D(2026, 9, 18)

_BASE_SCHEMA = {
    "symbol": pl.String, "trade_date": pl.Date,
    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Float64, "amount": pl.Float64,
}


def _row(symbol=SYM, trade_date=DAY2, open=10.0, high=10.5, low=9.8, close=10.2,
         volume=1000.0, amount=10200.0, **extra):
    r = {"symbol": symbol, "trade_date": trade_date, "open": open, "high": high,
         "low": low, "close": close, "volume": volume, "amount": amount}
    r.update(extra)
    return r


def _frame(rows, **dtypes):
    schema = dict(_BASE_SCHEMA)
    for k in rows[0]:
        schema.setdefault(k, pl.Float64)
    schema.update(dtypes)
    return pl.DataFrame(rows, schema=schema)


def _cal(*days):
    return pl.DataFrame({"trade_date": list(days or (DAY1, DAY2))},
                        schema={"trade_date": pl.Date})


def _listing(rows=None):
    if rows is None:
        rows = [{"symbol": SYM, "list_date": D(2001, 8, 27), "delist_date": None},
                {"symbol": "AAA.SH", "list_date": D(2001, 8, 27), "delist_date": None},
                {"symbol": "BBB.SH", "list_date": D(2001, 8, 27), "delist_date": None}]
    return pl.DataFrame(rows, schema={"symbol": pl.String, "list_date": pl.Date,
                                      "delist_date": pl.Date})


def _limits(rows=None):
    if rows is None:
        rows = [{"symbol": s, "trade_date": d, "up_limit": 11.0, "down_limit": 9.0}
                for s in (SYM, "AAA.SH", "BBB.SH") for d in (DAY1, DAY2)]
    return pl.DataFrame(rows, schema={"symbol": pl.String, "trade_date": pl.Date,
                                      "up_limit": pl.Float64, "down_limit": pl.Float64})


def _run(df, *, calendar=None, listing=None, limits=None):
    return validators.validate_daily(
        df,
        calendar if calendar is not None else _cal(),
        listing if listing is not None else _listing(),
        limits if limits is not None else _limits(),
    )


def _hits(results, rule_id):
    return [r for r in results if r.rule_id == rule_id]


def _assert_hits(results, rule_id, level, n=None):
    hits = _hits(results, rule_id)
    assert hits, f"未命中 {rule_id}；实际: {results}"
    assert all(r.level == level for r in hits), f"{rule_id} 级别≠{level}: {hits}"
    if n is not None:
        assert len(hits) == n, f"{rule_id} 命中 {len(hits)} ≠ {n}: {hits}"
    return hits


def _assert_absent(results, rule_id):
    assert not _hits(results, rule_id), f"不应命中 {rule_id}: {_hits(results, rule_id)}"


# ── ① 主键与重复 ─────────────────────────────────────────────────────────
def test_identical_duplicate_is_info_dedup_marker():
    """完全相同行 → INFO（确定性可修），不得误判为 PK_CONFLICT。"""
    df = _frame([_row(), _row()])
    hits = _assert_hits(_run(df), rules.DUP_IDENTICAL, rules.INFO, n=1)
    assert hits[0].key == f"{SYM}|2026-09-18"
    assert "2" in hits[0].detail
    _assert_absent(_run(df), rules.PK_CONFLICT)


def test_pk_conflict_is_error_for_every_row_and_never_picks():
    """同 PK 不同 payload：每行都 ERROR；不得只标一行（禁止自动选一条）。"""
    df = _frame([_row(close=10.2), _row(close=10.3)])
    hits = _assert_hits(_run(df), rules.PK_CONFLICT, rules.ERROR, n=2)
    assert {h.key for h in hits} == {f"{SYM}|2026-09-18"}
    _assert_absent(_run(df), rules.DUP_IDENTICAL)


def test_mixed_group_conflict_not_deduped():
    """3 行同 PK：2 行相同 + 1 行不同 → 整组 CONFLICT，不做部分 dedup。"""
    df = _frame([_row(close=10.2), _row(close=10.2), _row(close=10.4)])
    _assert_hits(_run(df), rules.PK_CONFLICT, rules.ERROR, n=3)
    _assert_absent(_run(df), rules.DUP_IDENTICAL)


# ── ② 时间与交易日历 ─────────────────────────────────────────────────────
def test_trade_date_not_in_calendar_error():
    """合法日期但不在交易日历（如周末）→ ERROR；日历内 → 无。"""
    df = _frame([_row(trade_date=D(2026, 9, 19))])
    _assert_hits(_run(df), rules.TRADE_DATE_INVALID, rules.ERROR, n=1)
    _assert_absent(_run(_frame([_row()])), rules.TRADE_DATE_INVALID)


def test_trade_date_string_forms_parse_and_bad_one_errors():
    """String 日期 %Y%m%d / %Y-%m-%d 可解析（PASS）；不可解析 → TIME_UNPARSEABLE。"""
    df = _frame([_row(trade_date="20260918"), _row(symbol="AAA.SH", trade_date="2026-09-18")],
                trade_date=pl.String)
    res = _run(df)
    _assert_absent(res, rules.TIME_UNPARSEABLE)
    _assert_absent(res, rules.TRADE_DATE_INVALID)

    bad = _frame([_row(trade_date="20261340")], trade_date=pl.String)
    hits = _assert_hits(_run(bad), rules.TIME_UNPARSEABLE, rules.ERROR, n=1)
    assert hits[0].key == f"{SYM}|20261340"


def test_list_before_and_after_delist_error():
    """上市前/退市后（is_listed = t < delist_date）→ ERROR；窗口内 → 无。"""
    listing = _listing([{"symbol": SYM, "list_date": DAY2, "delist_date": None}])
    _assert_hits(_run(_frame([_row(trade_date=DAY1)]), listing=listing),
                 rules.LIST_BEFORE, rules.ERROR, n=1)

    listing = _listing([{"symbol": SYM, "list_date": D(2001, 8, 27), "delist_date": DAY2}])
    _assert_hits(_run(_frame([_row(trade_date=DAY2)]), listing=listing),
                 rules.LIST_AFTER_DELIST, rules.ERROR, n=1)
    _assert_absent(_run(_frame([_row(trade_date=DAY1)]), listing=listing),
                   rules.LIST_AFTER_DELIST)


def test_time_order_descending_is_warn_flag_not_quarantine():
    """组内时间倒序 → WARN（保留 + flag），非 ERROR。"""
    df = _frame([_row(trade_date=DAY2), _row(trade_date=DAY1)])
    hits = _assert_hits(_run(df), rules.TIME_ORDER, rules.WARN, n=1)
    assert hits[0].key == f"{SYM}|2026-09-17"
    # 正序 → 无
    _assert_absent(_run(_frame([_row(trade_date=DAY1), _row(trade_date=DAY2)])),
                   rules.TIME_ORDER)


# ── ③ 基础数值合法性 ─────────────────────────────────────────────────────
def test_price_nonpositive_error():
    _assert_hits(_run(_frame([_row(close=-1.0, low=-1.0)])),
                 rules.PRICE_NONPOSITIVE, rules.ERROR, n=1)
    zero = _assert_hits(_run(_frame([_row(open=0.0)])),
                        rules.PRICE_NONPOSITIVE, rules.ERROR, n=1)
    assert "open" in zero[0].detail
    _assert_absent(_run(_frame([_row()])), rules.PRICE_NONPOSITIVE)


def test_zero_volume_amount_ok_negative_is_error():
    """vol/amount=0 合法（停牌语义）；<0 → ERROR。"""
    res = _run(_frame([_row(volume=0.0, amount=0.0)]))
    _assert_absent(res, rules.VOLUME_NEGATIVE)
    _assert_absent(res, rules.AMOUNT_NEGATIVE)

    _assert_hits(_run(_frame([_row(volume=-1.0)])),
                 rules.VOLUME_NEGATIVE, rules.ERROR, n=1)
    _assert_hits(_run(_frame([_row(amount=-0.5)])),
                 rules.AMOUNT_NEGATIVE, rules.ERROR, n=1)


def test_inf_is_nonfinite_error_nan_is_missing():
    """inf/-inf → ERROR；NaN → MISSING_VALUE（登记原因，不 fillna）。"""
    _assert_hits(_run(_frame([_row(close=math.inf)])),
                 rules.NONFINITE_VALUE, rules.ERROR, n=1)
    _assert_hits(_run(_frame([_row(amount=-math.inf)])),
                 rules.NONFINITE_VALUE, rules.ERROR, n=1)

    df = _frame([_row(close=math.nan)])
    res = _run(df)
    hits = _assert_hits(res, rules.MISSING_VALUE, rules.WARN, n=1)
    assert "close" in hits[0].detail
    _assert_absent(res, rules.NONFINITE_VALUE)
    # 禁止行为：NaN 只登记，绝不 fillna(0)、不改输入帧
    assert math.isnan(df["close"][0])


# ── ④ OHLC 与内部一致性 ──────────────────────────────────────────────────
def test_ohlc_invalid_error():
    _assert_hits(_run(_frame([_row(high=10.0, close=10.2)])),
                 rules.OHLC_INVALID, rules.ERROR, n=1)
    _assert_hits(_run(_frame([_row(low=10.4)])),
                 rules.OHLC_INVALID, rules.ERROR, n=1)
    _assert_absent(_run(_frame([_row()])), rules.OHLC_INVALID)


def test_vwap_out_of_range_error_skips_zero_volume():
    """volume>0 且 amount>0 才校验 VWAP ∈ [low,high]（容差）；0 量不校验。"""
    _assert_hits(_run(_frame([_row(volume=1000.0, amount=20000.0)])),
                 rules.VWAP_OUT_OF_RANGE, rules.ERROR, n=1)
    _assert_absent(_run(_frame([_row(volume=1000.0, amount=10200.0)])),
                   rules.VWAP_OUT_OF_RANGE)
    _assert_absent(_run(_frame([_row(volume=0.0, amount=0.0)])),
                   rules.VWAP_OUT_OF_RANGE)


# ── ⑤ 复权/公司行为 ──────────────────────────────────────────────────────
def test_adj_factor_ca_mismatch_warn_both_directions():
    """因子变化日无 CA（以及 CA 日因子未变）→ WARN；变化与 CA 同日 → 无。"""
    rows = [
        _row(trade_date=DAY1, fq_factor=1.0),
        _row(trade_date=DAY2, fq_factor=1.2),
    ]
    hits = _assert_hits(_run(_frame(rows)), rules.ADJ_FACTOR_CA_MISMATCH,
                        rules.WARN, n=1)
    assert hits[0].key == f"{SYM}|2026-09-18"

    event_day = [
        _row(trade_date=DAY1, fq_factor=1.0, div_cash=0.0),
        _row(trade_date=DAY2, fq_factor=1.2, div_cash=0.5),
    ]
    _assert_absent(_run(_frame(event_day)), rules.ADJ_FACTOR_CA_MISMATCH)

    event_no_change = [
        _row(trade_date=DAY1, fq_factor=1.0, div_cash=0.0),
        _row(trade_date=DAY2, fq_factor=1.0, div_cash=0.5),
    ]
    _assert_hits(_run(_frame(event_no_change)), rules.ADJ_FACTOR_CA_MISMATCH,
                 rules.WARN, n=1)


def test_adj_negative_error():
    hits = _assert_hits(_run(_frame([_row(fq_factor=-1.0)])),
                        rules.ADJ_NEGATIVE, rules.ERROR, n=1)
    assert "fq_factor" in hits[0].detail


# ── ⑥ 证券状态与市场规则 ─────────────────────────────────────────────────
def test_limit_breach_warn_and_first_day_exempt_info():
    """越界 → WARN；上市首日例外 → INFO（正常特殊状态，不隔离）。"""
    limits = _limits([{"symbol": SYM, "trade_date": DAY2, "up_limit": 9.5,
                       "down_limit": 9.0}])
    _assert_hits(_run(_frame([_row()]), limits=limits),
                 rules.LIMIT_BREACH, rules.WARN, n=1)
    _assert_absent(_run(_frame([_row()]), limits=_limits()), rules.LIMIT_BREACH)

    # 首日：listing.list_date == trade_date 且存在 limit 行
    listing = _listing([{"symbol": SYM, "list_date": DAY2, "delist_date": None}])
    res = _run(_frame([_row()]), listing=listing, limits=limits)
    hits = _assert_hits(res, rules.LIMIT_FIRST_DAY_EXEMPT, rules.INFO, n=1)
    assert hits[0].key == f"{SYM}|2026-09-18"
    _assert_absent(res, rules.LIMIT_BREACH)


# ── schema / 别名 / 排序 / 空帧 ──────────────────────────────────────────
def test_missing_required_column_fatal_and_early_return():
    df = pl.DataFrame({"symbol": [SYM], "trade_date": [DAY2],
                       "open": [10.0], "high": [10.5], "low": [9.8],
                       "volume": [1.0], "amount": [10.0]},
                      schema={"symbol": pl.String, "trade_date": pl.Date,
                              **{c: pl.Float64 for c in ("open", "high", "low", "volume", "amount")}})
    res = _run(df)
    hits = _assert_hits(res, rules.SCHEMA_MISSING_COLUMN, rules.FATAL)
    assert hits[0].key == "close"
    assert all(r.level == rules.FATAL for r in res), "schema FATAL 后不得继续行级检查"

    df2 = df.drop("symbol")
    _assert_hits(_run(df2), rules.SCHEMA_MISSING_COLUMN, rules.FATAL)


def test_code_alias_and_vol_alias_accepted():
    df = pl.DataFrame({"code": [SYM], "trade_date": [DAY2], "open": [10.0],
                       "high": [10.5], "low": [9.8], "close": [10.2],
                       "vol": [1000.0], "amount": [10200.0]},
                      schema={"code": pl.String, "trade_date": pl.Date,
                              **{c: pl.Float64 for c in
                                 ("open", "high", "low", "close", "vol", "amount")}})
    assert _run(df) == []


def test_results_sorted_by_severity_rule_key():
    """输出稳定排序：level → rule_id → key（分区聚合前序契约）。"""
    df = _frame([
        _row(close=math.nan), _row(close=10.2),          # PK_CONFLICT(2) + MISSING(1)
        _row(symbol="AAA.SH", close=math.nan),           # MISSING(1)
        _row(symbol="BBB.SH"), _row(symbol="BBB.SH"),    # DUP_IDENTICAL(1)
    ])
    got = [(r.level, r.rule_id, r.key) for r in _run(df)]
    assert got == [
        (rules.ERROR, rules.PK_CONFLICT, f"{SYM}|2026-09-18"),
        (rules.ERROR, rules.PK_CONFLICT, f"{SYM}|2026-09-18"),
        (rules.WARN, rules.MISSING_VALUE, f"{SYM}|2026-09-18"),
        (rules.WARN, rules.MISSING_VALUE, "AAA.SH|2026-09-18"),
        (rules.INFO, rules.DUP_IDENTICAL, "BBB.SH|2026-09-18"),
    ]


def test_empty_frame_returns_no_results():
    schema = {k: v for k, v in _BASE_SCHEMA.items()}
    df = pl.DataFrame(schema=schema)
    assert _run(df) == []


def test_clean_row_no_results():
    """基线：合法行不得触发任何规则（避免假阳性）。"""
    assert _run(_frame([_row()])) == []
