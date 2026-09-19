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


_UNSET = object()


def _run(df, *, calendar=_UNSET, listing=_UNSET, limits=_UNSET):
    """缺省走 2026 日历/上市/涨跌停 fixture；显式 ``None`` = 关闭该项检查。"""
    return validators.validate_daily(
        df,
        _cal() if calendar is _UNSET else calendar,
        _listing() if listing is _UNSET else listing,
        _limits() if limits is _UNSET else limits,
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


def test_datetime_utc_cross_day_recognized_as_exchange_day():
    """UTC 16:30Z = 沪市 09-18 00:30；日历仅含 09-18 → 恒等取 Date 会误报非交易日。"""
    df = pl.DataFrame(
        {"symbol": [SYM],
         "trade_date": [dt.datetime(2026, 9, 17, 16, 30, tzinfo=dt.timezone.utc)],
         "open": [10.0], "high": [10.5], "low": [9.8], "close": [10.2],
         "volume": [1000.0], "amount": [10200.0]},
        schema={**_BASE_SCHEMA, "trade_date": pl.Datetime("us", "UTC")})
    res = _run(df, calendar=_cal(DAY2))
    assert _hits(res, rules.TIME_UNPARSEABLE) == []
    assert _hits(res, rules.TRADE_DATE_INVALID) == []


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


def test_adj_nonpositive_warn_field_flag_not_error():
    """v2 字段级语义：adj/fq <= 0 → ADJ_NULLED（WARN + field），不再是整行 ERROR。"""
    hits = _assert_hits(_run(_frame([_row(adj_factor=-1.0)])),
                        rules.ADJ_NULLED, rules.WARN, n=1)
    assert hits[0].field == "adj_factor"
    assert "adj_factor" in hits[0].detail

    zero = _assert_hits(_run(_frame([_row(adj_factor=0.0)])),
                        rules.ADJ_NULLED, rules.WARN, n=1)
    assert zero[0].field == "adj_factor"

    fq = _assert_hits(_run(_frame([_row(fq_factor=-1.0)])),
                      rules.ADJ_NULLED, rules.WARN, n=1)
    assert fq[0].field == "fq_factor"
    _assert_absent(_run(_frame([_row(adj_factor=-1.0)])), rules.VWAP_OUT_OF_RANGE)


# ── v2：早市 VWAP 容差（<1995-01-01 用 2%）──────────────────────────────
PRE_1995 = D(1994, 1, 5)
POST_1995 = D(1995, 1, 5)


def _vwap_row(day, vwap, close=10.0):
    """构造 low=high=close 的窄行：vwap=amount/volume 由入参决定。"""
    return _row(trade_date=day, open=close, high=close, low=close, close=close,
                volume=100.0, amount=vwap * 100.0)


def test_vwap_pre1995_tolerance_two_percent_boundary():
    """<1995：2% 带（含带边）通过；超带即 ERROR。>=1995 保持 1%（2% 越界）。"""
    res = _run(_frame([_vwap_row(PRE_1995, 10.2)]), calendar=None)
    _assert_absent(res, rules.VWAP_OUT_OF_RANGE)

    res = _run(_frame([_vwap_row(PRE_1995, 10.2001)]), calendar=None)
    _assert_hits(res, rules.VWAP_OUT_OF_RANGE, rules.ERROR, n=1)

    res = _run(_frame([_vwap_row(POST_1995, 10.2)]), calendar=None)
    _assert_hits(res, rules.VWAP_OUT_OF_RANGE, rules.ERROR, n=1)

    _assert_absent(_run(_frame([_vwap_row(POST_1995, 10.1)]), calendar=None),
                   rules.VWAP_OUT_OF_RANGE)


def test_vwap_cutoff_and_tol_come_from_policy():
    """容差/截止日取 policy（不硬编码）：改 policy 即改行为。"""
    import dataclasses
    pol = dataclasses.replace(rules.default_policy(),
                              vwap_pre_1995_cutoff="1996-01-01")
    res = validators.validate_daily(
        _frame([_vwap_row(POST_1995, 10.2)]), None, None, None, pol)
    _assert_absent(res, rules.VWAP_OUT_OF_RANGE)


# ── v2：历史制度例外 registry（code 集合 × era，factor≈5）────────────────
def test_historic_unit_exception_registered_code_and_era_downgraded():
    """登记代码×年代的 factor 5 行 → HISTORIC_UNIT_EXCEPTION（WARN），无 ERROR。"""
    row = _row(symbol="000002.SZ", trade_date=D(1991, 2, 2), open=14.59,
               high=14.59, low=14.59, close=14.59, volume=1300.0,
               amount=95000.0)
    res = _run(_frame([row]), calendar=None)
    hits = _assert_hits(res, rules.HISTORIC_UNIT_EXCEPTION, rules.WARN, n=1)
    assert "factor" in hits[0].detail and "5" in hits[0].detail
    _assert_absent(res, rules.VWAP_OUT_OF_RANGE)
    assert all(r.level == rules.WARN for r in res), "例外行不得触发任何 ERROR"


def test_historic_unit_exception_covers_integer_rounded_amount_row():
    """金额按整数价取整（k=5 略越 2% 带但落在代码约定 ±10%）→ 仍为登记例外。"""
    row = _row(symbol="000004.SZ", trade_date=D(1991, 3, 16), open=13.19,
               high=13.19, low=13.19, close=13.19, volume=100.0,
               amount=6000.0)
    _assert_hits(_run(_frame([row]), calendar=None),
                 rules.HISTORIC_UNIT_EXCEPTION, rules.WARN, n=1)


def test_historic_unit_exception_not_generalized_unregistered_code():
    """未登记代码同样数值 → 不得降级，仍是 VWAP ERROR。"""
    row = _row(symbol="000003.SZ", trade_date=D(1991, 2, 2), open=14.59,
               high=14.59, low=14.59, close=14.59, volume=1300.0,
               amount=95000.0)
    res = _run(_frame([row]), calendar=None)
    _assert_hits(res, rules.VWAP_OUT_OF_RANGE, rules.ERROR, n=1)
    _assert_absent(res, rules.HISTORIC_UNIT_EXCEPTION)


def test_historic_unit_exception_not_generalized_out_of_era():
    """登记代码但年代之外 → 不得降级（era 开区间）。"""
    row = _row(symbol="000002.SZ", trade_date=D(1994, 1, 3), open=15.0,
               high=15.0, low=15.0, close=15.0, volume=1000.0,
               amount=75000.0)
    res = _run(_frame([row]), calendar=None)
    _assert_hits(res, rules.VWAP_OUT_OF_RANGE, rules.ERROR, n=1)
    _assert_absent(res, rules.HISTORIC_UNIT_EXCEPTION)


def test_historic_unit_exception_rejects_corrupt_row_of_registered_code():
    """登记代码内的真坏行（ratio 背离约定 >10%）→ 保持 VWAP ERROR（不得洗白）。"""
    row = _row(symbol="000002.SZ", trade_date=D(1991, 4, 13), open=12.49,
               high=12.49, low=12.49, close=12.49, volume=1000.0,
               amount=6000.0)
    res = _run(_frame([row]), calendar=None)
    _assert_hits(res, rules.VWAP_OUT_OF_RANGE, rules.ERROR, n=1)
    _assert_absent(res, rules.HISTORIC_UNIT_EXCEPTION)

    bad = _row(symbol="000004.SZ", trade_date=D(1991, 11, 17), open=17.10,
               high=17.20, low=16.95, close=17.10, volume=17600.0,
               amount=622000.0)
    res = _run(_frame([bad]), calendar=None)
    _assert_hits(res, rules.VWAP_OUT_OF_RANGE, rules.ERROR, n=1)
    _assert_absent(res, rules.HISTORIC_UNIT_EXCEPTION)


# ── T2b：前 1996 单位 regime（after..before 双边界 era）───────────────────
def _sh_row(day, close=10.0, amount=100.0):
    """600602.SH 0.01 单位约定行：vwap = close/100。"""
    return _row(symbol="600602.SH", trade_date=day, open=close, high=close,
                low=close, close=close, volume=1000.0, amount=amount)


def test_historic_unit_exception_era_after_boundary():
    """era 有下界：after 之前 / before 之后的行不得降级（不得泛化到未登记年代）。"""
    inside = _sh_row(D(1992, 6, 1))
    res = _run(_frame([inside]), calendar=None)
    _assert_hits(res, rules.HISTORIC_UNIT_EXCEPTION, rules.WARN, n=1)
    _assert_absent(res, rules.VWAP_OUT_OF_RANGE)

    before_era = _sh_row(D(1990, 12, 19))
    res = _run(_frame([before_era]), calendar=None)
    _assert_hits(res, rules.VWAP_OUT_OF_RANGE, rules.ERROR, n=1)
    _assert_absent(res, rules.HISTORIC_UNIT_EXCEPTION)

    after_era = _sh_row(D(1993, 1, 4))
    res = _run(_frame([after_era]), calendar=None)
    _assert_hits(res, rules.VWAP_OUT_OF_RANGE, rules.ERROR, n=1)
    _assert_absent(res, rules.HISTORIC_UNIT_EXCEPTION)


def test_historic_unit_exception_era_guard_rejects_deviation():
    """era 内但背离单位约定 >10%（ratio≈0.12）→ 仍 VWAP ERROR。"""
    bad = _sh_row(D(1992, 6, 1), amount=120.0)       # vwap=0.12 → /0.01=12
    res = _run(_frame([bad]), calendar=None)
    _assert_hits(res, rules.VWAP_OUT_OF_RANGE, rules.ERROR, n=1)
    _assert_absent(res, rules.HISTORIC_UNIT_EXCEPTION)

    edge = _sh_row(D(1992, 6, 1), amount=110.0)      # /0.01=11 = high*1.1 带边
    res = _run(_frame([edge]), calendar=None)
    _assert_absent(res, rules.VWAP_OUT_OF_RANGE)


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
