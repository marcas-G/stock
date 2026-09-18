"""T4：聚合指标 + 系统性检测 + PRE-INGEST 分区门（Plan DQ-M1）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-4-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §2（聚合指标与 systemic 三规则）/ §3.1（判定定义，≤/边界语义）/ §3.2（policy 字段）。

判定顺序（§3.1 逐字）：
- PASS：fatal==0 且 error_rate <= pass.max_error_rate 且 coverage >= pass.min_coverage
        且 systematic_issue == false 且 unresolved_partition_error == false；
- FAIL：fatal>0 或 error_rate > fail.min_error_rate 或
        coverage < (1 - fail.max_missing_coverage) 或 systematic_issue 或 unresolved；
- 其余 = DEGRADED（含 fail 带内）；quarantine_count>0 不自动 DEGRADED
  （1/20000 孤立坏行 → PASS，判据只有 error_rate 与 pass 上界的 ≤ 比较）。

systemic 三规则阈值一律取 policy `systemic.*`；命中 → SystemicDetail（门判 FAIL）：
- SYSTEMATIC_FIELD_FAILURE：单一字段占全部 error > field_share 且 count > field_count；
- SYSTEMATIC_GROUP_FAILURE：group_rate > market_rate × group_ratio 且 group_count > group_count；
- SYSTEMATIC_TEMPORAL_FAILURE：单日占错误比例 > time_share 且 error_count > time_count。
"""
from __future__ import annotations

import dataclasses
import datetime as dt

import polars as pl
import pytest

from data_quality import aggregate, rules
from data_quality.rules import RuleResult

D = dt.date
DAY = D(2026, 9, 18)
POLICY = rules.load_policy()


def _r(rule_id, level, *keys, n=1, detail="test"):
    return [RuleResult(rule_id, level, k, detail) for k in keys for _ in range(n)]


def _sym_key(sym, day=DAY):
    return f"{sym}|{day.isoformat()}"


def _metrics(results, *, expected, actual=None):
    return aggregate.aggregate(results, expected, actual if actual is not None else expected)


def _rows(symbols, day=DAY):
    return pl.DataFrame({
        "symbol": [f"{s}.SH" if "." not in s else s for s in symbols],
        "trade_date": [day] * len(symbols),
    }, schema={"symbol": pl.String, "trade_date": pl.Date})


# ── §3.1 边界：error_rate / coverage 的 ≤ 语义 ───────────────────────────
def test_error_rate_at_pass_boundary_is_pass():
    """恰等于 pass.max_error_rate（0.0001）→ PASS（≤ 语义）。"""
    res = _r(rules.PRICE_NONPOSITIVE, rules.ERROR, _sym_key("600519.SH"))
    m = _metrics(res, expected=10000)
    assert m.error_rate == POLICY.partition_gate["pass"]["max_error_rate"] == 0.0001
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.PASS


def test_error_rate_just_above_pass_boundary_is_degraded():
    res = _r(rules.PRICE_NONPOSITIVE, rules.ERROR, _sym_key("600519.SH"))
    m = _metrics(res, expected=5000)          # 1/5000 = 0.0002
    assert m.error_rate > POLICY.partition_gate["pass"]["max_error_rate"]
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.DEGRADED


def test_error_rate_at_fail_boundary_is_degraded():
    """恰等于 fail.min_error_rate（0.01）→ DEGRADED（≤ 语义；FAIL 要严格大于）。"""
    res = _r(rules.PRICE_NONPOSITIVE, rules.ERROR, _sym_key("600519.SH"))
    m = _metrics(res, expected=100)
    assert m.error_rate == POLICY.partition_gate["fail"]["min_error_rate"] == 0.01
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.DEGRADED


def test_error_rate_just_above_fail_boundary_is_fail():
    res = _r(rules.PRICE_NONPOSITIVE, rules.ERROR, _sym_key("600519.SH"), n=2)
    m = _metrics(res, expected=100)           # 0.02 > 0.01
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.FAIL


def test_coverage_at_pass_boundary_is_pass():
    """coverage 恰等于 pass.min_coverage（0.999）→ PASS（≥ 语义）。"""
    m = _metrics([], expected=1000, actual=999)
    assert m.coverage == POLICY.partition_gate["pass"]["min_coverage"] == 0.999
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.PASS


def test_coverage_at_fail_boundary_is_degraded():
    """coverage 恰等于 1 - fail.max_missing_coverage（0.99）→ DEGRADED（未低于 fail 线）。"""
    m = _metrics([], expected=1000, actual=990)
    assert m.coverage == 1 - POLICY.partition_gate["fail"]["max_missing_coverage"] == 0.99
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.DEGRADED


def test_coverage_below_fail_boundary_is_fail():
    m = _metrics([], expected=1000, actual=989)
    assert m.coverage < 1 - POLICY.partition_gate["fail"]["max_missing_coverage"]
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.FAIL


def test_fatal_count_positive_is_fail_even_with_clean_rates():
    res = _r(rules.SCHEMA_DATE_DTYPE, rules.FATAL, "trade_date")
    m = _metrics(res, expected=10000)
    assert m.fatal_count == 1 and m.error_count == 0
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.FAIL


def test_isolated_error_one_in_20000_is_pass_not_degraded():
    """spec §3.1 说明：1/20000 孤立坏行 → PASS（quarantine_count>0 不自动 DEGRADED）。"""
    res = _r(rules.VWAP_OUT_OF_RANGE, rules.ERROR, _sym_key("000001.SZ"))
    m = _metrics(res, expected=20000, actual=19999)
    assert m.quarantine_count == 1
    assert m.error_rate == 1 / 20000
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.PASS


# ── §2 聚合：WARN/INFO 全量消费（health 侧 warning/rules 计数）───────────
def test_aggregate_consumes_warn_info_and_rule_counts():
    res = (
        _r(rules.MISSING_VALUE, rules.WARN, _sym_key("600519.SH"), n=2)
        + _r(rules.DUP_IDENTICAL, rules.INFO, _sym_key("000001.SZ"), n=1)
        + _r(rules.VOLUME_NEGATIVE, rules.ERROR, _sym_key("000002.SZ"), n=1)
    )
    m = aggregate.aggregate(res, 10000, 10000)
    assert (m.warning_count, m.info_count, m.error_count) == (2, 1, 1)
    assert m.rules == {
        rules.MISSING_VALUE: 2, rules.DUP_IDENTICAL: 1, rules.VOLUME_NEGATIVE: 1,
    }
    # WARN/INFO 不抬高 error_rate（分子只数 ERROR）
    assert m.error_rate == 1 / 10000
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.PASS


def test_unresolved_partition_error_true_for_frame_level_fatal():
    """schema FATAL 的 key 是字段名（不可行级隔离）→ unresolved_partition_error。"""
    res = _r(rules.SCHEMA_MISSING_COLUMN, rules.FATAL, "close")
    m = aggregate.aggregate(res, 1000, 1000)
    assert m.unresolved_partition_error is True
    assert m.quarantine_count == 0, "帧级 FATAL 不是行级隔离"
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.FAIL


def test_quarantine_count_distinct_row_keys():
    res = (
        _r(rules.PK_CONFLICT, rules.ERROR, _sym_key("600519.SH"), n=2)
        + _r(rules.PRICE_NONPOSITIVE, rules.ERROR, _sym_key("000001.SZ"), n=1)
    )
    m = aggregate.aggregate(res, 10000, 10000)
    assert m.quarantine_count == 2, "同一键的多行证据只算一次隔离行组"
    assert m.error_count == 3


def test_metrics_is_frozen_dataclass():
    m = _metrics([], expected=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.error_count = 9


# ── §2 systemic：字段集中（阈值取 policy.systemic.*）────────────────────
def test_field_concentration_over_share_and_count_is_systemic():
    res = (
        _r(rules.VOLUME_NEGATIVE, rules.ERROR,
           *[_sym_key(f"6005{i:02d}.SH") for i in range(81)])
        + _r(rules.OHLC_INVALID, rules.ERROR,
             *[_sym_key(f"0000{i:02d}.SZ") for i in range(19)])
    )
    m = aggregate.aggregate(res, 100000, 100000)
    assert (m.error_count, m.error_rate) == (100, 0.001)
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.DEGRADED, (
        "先确认：无 systemic 时这是 fail 带内的 DEGRADED")

    sd = aggregate.detect_systemic(m, None, POLICY)
    assert sd is not None and sd.rule == aggregate.SYSTEMATIC_FIELD_FAILURE
    assert sd.subject == "volume" and sd.count == 81
    assert aggregate.decide_pre_ingest(m, sd, POLICY) == aggregate.FAIL


def test_field_concentration_at_threshold_not_systemic():
    """严格大于：share==0.80 或 error_count==field_count 均不命中。"""
    at_share = (
        _r(rules.VOLUME_NEGATIVE, rules.ERROR,
           *[_sym_key(f"6005{i:02d}.SH") for i in range(80)])
        + _r(rules.OHLC_INVALID, rules.ERROR,
             *[_sym_key(f"0000{i:02d}.SZ") for i in range(20)])
    )
    m = aggregate.aggregate(at_share, 100000, 100000)
    assert m.error_rate <= POLICY.partition_gate["fail"]["min_error_rate"]
    assert aggregate.detect_systemic(m, None, POLICY) is None

    at_count = _r(rules.VOLUME_NEGATIVE, rules.ERROR,
                  *[_sym_key(f"6005{i:02d}.SH") for i in range(50)])
    m2 = aggregate.aggregate(at_count, 100000, 100000)
    assert aggregate.detect_systemic(m2, None, POLICY) is None


def test_field_boundary_compares_total_error_count_not_field_subset():
    """I2（spec §2 字面）：`C_field > 0.80 且 error_count > N_field`。

    偏差带用例：59 errors、单字段 50 → share 50/59≈0.847>0.80 且**总数** 59>50
    → 命中（旧「字段自身计数 50>50 不成立」的语义会漏报）。
    """
    keys = [_sym_key(f"6005{i:02d}.SH") for i in range(59)]
    res = (_r(rules.VOLUME_NEGATIVE, rules.ERROR, *keys[:50])
           + _r(rules.OHLC_INVALID, rules.ERROR, *keys[50:]))
    m = aggregate.aggregate(res, 100000, 100000)
    assert (m.error_count, m.errors_by_field) == (59, {"volume": 50})
    sd = aggregate.detect_systemic(m, None, POLICY)
    assert sd is not None and sd.rule == aggregate.SYSTEMATIC_FIELD_FAILURE
    assert sd.subject == "volume" and sd.count == 50


def test_policy_field_count_not_hardcoded():
    """M2：非默认 policy 必须改变判定（field_count 50→10 才命中）。"""
    keys = [_sym_key(f"6005{i:02d}.SH") for i in range(20)]
    res = (_r(rules.VOLUME_NEGATIVE, rules.ERROR, *keys[:17])
           + _r(rules.OHLC_INVALID, rules.ERROR, *keys[17:]))
    m = aggregate.aggregate(res, 100000, 100000)       # 20 errors / volume 17 (85%)
    assert aggregate.detect_systemic(m, None, POLICY) is None, "默认 20 不 > 50"
    tight = dataclasses.replace(
        POLICY, systemic={**POLICY.systemic, "field_count": 10})
    sd = aggregate.detect_systemic(m, None, tight)
    assert sd is not None and sd.rule == aggregate.SYSTEMATIC_FIELD_FAILURE


def test_warn_level_does_not_feed_field_concentration():
    """字段集中只看 ERROR：WARN 命中的字段再多也不得触发 systemic。"""
    res = _r(rules.MISSING_VALUE, rules.WARN,
             *[_sym_key(f"6005{i:02d}.SH") for i in range(200)])
    m = aggregate.aggregate(res, 100000, 100000)
    assert m.error_count == 0
    assert aggregate.detect_systemic(m, None, POLICY) is None


# ── §2 systemic：分组集中 ────────────────────────────────────────────────
def test_group_concentration_ratio_and_count_is_systemic():
    """100 只 SH + 9900 只 SZ；60 个错全在 SH → 0.6 vs 0.006×10 → 命中。"""
    rows = _rows([f"6005{i:02d}.SH" for i in range(100)]
                 + [f"0000{i:02d}.SZ" for i in range(9900)])
    res = _r(rules.OHLC_INVALID, rules.ERROR,
             *[_sym_key(f"6005{i:02d}.SH") for i in range(60)])
    m = aggregate.aggregate(res, 10000, 10000)
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.DEGRADED
    sd = aggregate.detect_systemic(m, rows, POLICY)
    assert sd is not None and sd.rule == aggregate.SYSTEMATIC_GROUP_FAILURE
    assert sd.subject == "SH" and sd.count == 60
    assert aggregate.decide_pre_ingest(m, sd, POLICY) == aggregate.FAIL


def test_group_count_at_threshold_not_systemic():
    """I2 钉语义：`group_count` = **组内 error 数**，不是分区总 error_count。

    SH 组 50 错（== group_count，不满足严格大于）+ SZ 组 10 错 → 总 60>50 也不命中；
    若实现拿总数 60 与阈值比会误报。
    """
    rows = _rows([f"6005{i:02d}.SH" for i in range(100)]
                 + [f"0000{i:02d}.SZ" for i in range(9900)])
    res = (_r(rules.OHLC_INVALID, rules.ERROR,
              *[_sym_key(f"6005{i:02d}.SH") for i in range(50)])
           + _r(rules.OHLC_INVALID, rules.ERROR,
                *[_sym_key(f"0000{i:02d}.SZ") for i in range(10)]))
    m = aggregate.aggregate(res, 10000, 10000)
    assert m.error_count == 60
    assert aggregate.detect_systemic(m, rows, POLICY) is None


def test_group_rule_needs_rows_denominator():
    """无 rows（无分组分母）→ 不判分组集中（不 fail-open 也不误报）。"""
    res = _r(rules.OHLC_INVALID, rules.ERROR,
             *[_sym_key(f"6005{i:02d}.SH") for i in range(60)])
    m = aggregate.aggregate(res, 2000, 2000)
    assert aggregate.detect_systemic(m, None, POLICY) is None


# ── §2 systemic：时间集中 ────────────────────────────────────────────────
def test_temporal_concentration_over_time_count_is_systemic():
    """101 个错同一天（单组占比 1.0 > 0.80）且 error_count > 100 → 命中。"""
    syms = [f"830{i:03d}.BJ" for i in range(101)]
    res = _r(rules.OHLC_INVALID, rules.ERROR, *[_sym_key(s) for s in syms])
    rows = _rows(syms)
    m = aggregate.aggregate(res, 101, 101)
    sd = aggregate.detect_systemic(m, rows, POLICY)
    assert sd is not None and sd.rule == aggregate.SYSTEMATIC_TEMPORAL_FAILURE
    assert sd.subject == DAY.isoformat() and sd.count == 101
    assert aggregate.decide_pre_ingest(m, sd, POLICY) == aggregate.FAIL


def test_temporal_count_at_threshold_not_systemic():
    syms = [f"830{i:03d}.BJ" for i in range(100)]
    res = _r(rules.OHLC_INVALID, rules.ERROR, *[_sym_key(s) for s in syms])
    m = aggregate.aggregate(res, 100, 100)
    assert aggregate.detect_systemic(m, _rows(syms), POLICY) is None


def test_temporal_boundary_compares_total_error_count_not_day_subset():
    """I2（spec §2 字面）：`C_time > 0.80 且 error_count > N_time`。

    偏差带用例：120 errors、单日 100 → share 100/120≈0.833>0.80 且**总数**
    120>100 → 命中（旧「当日自身计数 100>100 不成立」的语义会漏报）。
    """
    day_keys = [f"6005{i:02d}.SH|{DAY.isoformat()}" for i in range(100)]
    other_keys = [f"0000{i:02d}.SZ|2026-09-17" for i in range(20)]
    res = _r(rules.OHLC_INVALID, rules.ERROR, *(day_keys + other_keys))
    m = aggregate.aggregate(res, 100000, 100000)
    assert (m.error_count, m.errors_by_date) == (120, {DAY.isoformat(): 100,
                                                       "2026-09-17": 20})
    sd = aggregate.detect_systemic(m, None, POLICY)
    assert sd is not None and sd.rule == aggregate.SYSTEMATIC_TEMPORAL_FAILURE
    assert sd.subject == DAY.isoformat() and sd.count == 100


def test_time_spread_across_dates_not_systemic():
    """I3：时间集中按范围内 trade_date 分布——错误均匀跨日即便总数 > N_time 也不命中。"""
    keys = []
    for day in ("2026-09-15", "2026-09-16", "2026-09-17"):
        keys += [f"6005{j:02d}.SH|{day}" for j in range(40)]
    res = _r(rules.OHLC_INVALID, rules.ERROR, *keys)
    m = aggregate.aggregate(res, 100000, 100000)
    assert m.error_count == 120
    assert max(m.errors_by_date.values()) == 40
    assert aggregate.detect_systemic(m, None, POLICY) is None


def test_policy_time_count_not_hardcoded():
    """M2：非默认 policy 必须改变判定（time_count 100→200 改变命中）。"""
    keys = [f"6005{i:03d}.SH|{DAY.isoformat()}" for i in range(150)]
    res = _r(rules.OHLC_INVALID, rules.ERROR, *keys)
    m = aggregate.aggregate(res, 100000, 100000)       # 150 同日 errors
    sd = aggregate.detect_systemic(m, None, POLICY)
    assert sd is not None and sd.rule == aggregate.SYSTEMATIC_TEMPORAL_FAILURE
    loose = dataclasses.replace(
        POLICY, systemic={**POLICY.systemic, "time_count": 200})
    assert aggregate.detect_systemic(m, None, loose) is None


def test_systemic_none_when_no_rule_hit():
    res = _r(rules.VOLUME_NEGATIVE, rules.ERROR, _sym_key("600519.SH"))
    m = aggregate.aggregate(res, 10000, 10000)
    assert aggregate.detect_systemic(m, _rows(["600519"]), POLICY) is None
    assert aggregate.decide_pre_ingest(m, None, POLICY) == aggregate.PASS
