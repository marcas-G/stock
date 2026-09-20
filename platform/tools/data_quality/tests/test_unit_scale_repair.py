"""R37 T4：现代单位 bug 的逐行字段级修复（UNIT_SCALE_REPAIRED）。

规格：knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md §5
（scope 内残余 55 行：可确证的现代单位 bug 行按 bars_1m 复核后修复/登记，
其余保持隔离披露）；实施计划 T4。
证据：governance/evidence/verification/R37/residual-55/（19 行逐行对照表）。

语义（与 v2 ADJ_NULLED 同型：validator 产出 WARN + 字段级动作，repair 执行）：
- policy ``unit_scale_repairs`` 登记**逐行键**（``<code>|<YYYY-MM-DD>``）×字段×单位因子；
- validator 仅当该行 VWAP 越带（ERROR 候选）且 ``vwap/factor`` 落 [low,high]×(1±match_tol)
  时降级 WARN ``UNIT_SCALE_REPAIRED``（携带 field/factor 供 repair 换算）；
- repair 对命中行做字段换算（volume ×factor / amount ÷factor），行保留进 clean；
- 未命中/换算后仍越带 → 照旧 ERROR quarantine（不洗白）；stub 硬编码必败。
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from data_quality import repair, rules, validators

D = dt.date
DAY = D(2026, 9, 17)
SYM = "600519.SH"
KEY = f"{SYM}|2026-09-17"
REPO = Path(__file__).resolve().parents[4]
EVIDENCE_CSV = (REPO / "governance" / "evidence" / "verification" / "R37"
                / "residual-55" / "scoped-55-rows.csv")

_SCHEMA = {
    "symbol": pl.String, "trade_date": pl.Date,
    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Float64, "amount": pl.Float64,
}


def _row(symbol=SYM, trade_date=DAY, open=4.9, high=5.1, low=4.8, close=5.0,
         volume=1000.0, amount=500_000.0, **extra):
    r = {"symbol": symbol, "trade_date": trade_date, "open": open, "high": high,
         "low": low, "close": close, "volume": volume, "amount": amount}
    r.update(extra)
    return r


def _frame(rows, **dtypes):
    schema = dict(_SCHEMA)
    for k in rows[0]:
        schema.setdefault(k, pl.Float64)
    schema.update(dtypes)
    return pl.DataFrame(rows, schema=schema)


def _cal(*days):
    return pl.DataFrame({"trade_date": list(days or (DAY,))},
                        schema={"trade_date": pl.Date})


def _entry(field="volume", factor=100.0, keys=(KEY,), match_tol=0.10):
    return rules.UnitScaleRepair(field=field, factor=factor,
                                 match_tol=match_tol, keys=tuple(keys))


def _policy(repairs):
    return dataclasses.replace(rules.load_policy(),
                               unit_scale_repairs=tuple(repairs))


def _find(results, rule_id):
    return [r for r in results if r.rule_id == rule_id]


def _log(log, action):
    return [e for e in log if e["action"] == action]


# ── validator：登记行降级 WARN + 携带 field/factor ────────────────────────
def test_registered_volume_row_downgraded_to_warn_with_field_and_factor():
    df = _frame([_row()])       # vwap=500 = 100× close；登记 key 是 volume×100
    pol = _policy([_entry(field="volume", factor=100.0)])
    res = validators.validate_daily(df, _cal(), None, None, pol)

    assert _find(res, rules.VWAP_OUT_OF_RANGE) == [], (
        "登记行不得仍报 VWAP ERROR（否则 repair 会隔离它）")
    hit = _find(res, rules.UNIT_SCALE_REPAIRED)
    assert len(hit) == 1
    r = hit[0]
    assert (r.level, r.key, r.field, r.factor) == (rules.WARN, KEY, "volume", 100.0)
    assert "5.0000" in r.detail, "detail 应给换算后 VWAP（5.0）"


def test_repair_scales_registered_volume_by_factor_row_kept():
    df = _frame([_row()])
    pol = _policy([_entry()])
    res = validators.validate_daily(df, _cal(), None, None, pol)
    clean, q, log = repair.repair(df, res)

    assert q.height == 0, "可确证的单位 bug 修复后行保留，不得隔离"
    assert clean.height == 1
    assert clean["volume"].to_list() == [100_000.0], (
        "volume 必须精确 ×factor（不是近似/四舍五入）")
    assert clean["amount"].to_list() == [500_000.0], "amount 不动"
    assert (clean["open"][0], clean["high"][0], clean["low"][0],
            clean["close"][0]) == (4.9, 5.1, 4.8, 5.0), "OHLC 不得被改"
    e = _log(log, "scale_field")[0]
    assert (e["rule_id"], e["field"], e["factor"], e["count"],
            e["keys"]) == (rules.UNIT_SCALE_REPAIRED, "volume", 100.0, 1, [KEY])


def test_amount_field_repair_divides_by_factor():
    """field=amount（金额偏大 factor 倍）→ repair 除以 factor，volume 不动。"""
    df = _frame([_row(volume=1000.0, amount=500_000.0)])   # vwap=500；真值 5.0
    pol = _policy([_entry(field="amount", factor=100.0)])
    res = validators.validate_daily(df, _cal(), None, None, pol)
    assert [r.field for r in _find(res, rules.UNIT_SCALE_REPAIRED)] == ["amount"]

    clean, q, log = repair.repair(df, res)
    assert q.height == 0
    assert clean["amount"].to_list() == [5_000.0]
    assert clean["volume"].to_list() == [1000.0], "volume 不动"
    assert _log(log, "scale_field")[0]["field"] == "amount"


# ── 守卫：不误伤 / 不洗白 ────────────────────────────────────────────────
def test_unregistered_same_shape_row_still_error_and_quarantined():
    """同样形态（ratio≈100）但未登记的行 → 照旧 ERROR + quarantine（无连带）。"""
    other = _row(symbol="AAA.SH")
    df = _frame([other])
    pol = _policy([_entry(keys=(KEY,))])
    res = validators.validate_daily(df, _cal(), None, None, pol)
    assert _find(res, rules.UNIT_SCALE_REPAIRED) == []
    assert [r.key for r in _find(res, rules.VWAP_OUT_OF_RANGE)] == ["AAA.SH|2026-09-17"]

    clean, q, log = repair.repair(df, res)
    assert clean.height == 0 and q.height == 1
    assert _log(log, "scale_field") == []
    assert q["volume"].to_list() == [1000.0], "隔离行保持原始形态（证据）"


def test_registered_key_with_nonunit_deviation_stays_error():
    """登记键但偏离不是该单位因子（vwap/factor 不落带）→ ERROR（守卫，不洗白）。"""
    df = _frame([_row(volume=1000.0, amount=35_000.0)])    # vwap=35（7×close）
    pol = _policy([_entry(factor=100.0)])
    res = validators.validate_daily(df, _cal(), None, None, pol)
    assert _find(res, rules.UNIT_SCALE_REPAIRED) == []
    assert len(_find(res, rules.VWAP_OUT_OF_RANGE)) == 1

    clean, q, log = repair.repair(df, res)
    assert clean.height == 0 and q.height == 1
    assert _log(log, "scale_field") == []


def test_no_collateral_sibling_date_and_other_rows_untouched():
    """同码其它日期、其它码正常行：值与隔离状态都不受登记键影响。"""
    df = _frame([
        _row(),                                             # 登记坏行 → 修复
        _row(trade_date=D(2026, 9, 18), volume=1000.0, amount=5000.0),  # 同码正常日
        _row(symbol="AAA.SH", volume=1000.0, amount=5100.0),            # 其它码正常
    ])
    pol = _policy([_entry()])
    res = validators.validate_daily(df, _cal(DAY, D(2026, 9, 18)), None, None, pol)
    clean, q, log = repair.repair(df, res)

    assert q.height == 0 and clean.height == 3
    got = {(r["symbol"], r["trade_date"]): (r["volume"], r["amount"])
           for r in clean.iter_rows(named=True)}
    assert got[(SYM, D(2026, 9, 18))] == (1000.0, 5000.0), "同码其它日不得被换算"
    assert got[("AAA.SH", DAY)] == (1000.0, 5100.0), "其它码不得被换算"
    assert got[(SYM, DAY)] == (100_000.0, 500_000.0)
    assert _log(log, "scale_field")[0]["count"] == 1


def test_repair_idempotent_no_double_scale():
    """换算后行已落带：重复 validate+repair 不得再 ×100。"""
    df = _frame([_row()])
    pol = _policy([_entry()])
    res = validators.validate_daily(df, _cal(), None, None, pol)
    clean, _, _ = repair.repair(df, res)

    res2 = validators.validate_daily(clean, _cal(), None, None, pol)
    assert _find(res2, rules.UNIT_SCALE_REPAIRED) == []
    assert _find(res2, rules.VWAP_OUT_OF_RANGE) == []
    clean2, q2, log2 = repair.repair(clean, res2)
    assert clean2["volume"].to_list() == [100_000.0], "不得二次换算"
    assert q2.height == 0 and _log(log2, "scale_field") == []


# ── policy 加载：登记结构校验（fail fast，不许静默无效）──────────────────
_MIN_BODY = """\
dq_policy_version: daily-v3
partition_gate:
  pass: {max_error_rate: 0.0001, min_coverage: 0.999}
  fail: {min_error_rate: 0.01, max_missing_coverage: 0.01}
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
historic_unit_exceptions: []
# __REPAIRS__
scope:
  min_trade_date: "1996-01-01"
  exclude_code_suffixes: [".BJ"]
"""


def _load(tmp_path, block):
    p = tmp_path / "policy.yaml"
    p.write_text(_MIN_BODY.replace("# __REPAIRS__", block), encoding="utf-8")
    return rules.load_policy(p)


def test_policy_block_absent_defaults_empty(tmp_path):
    pol = _load(tmp_path, "")
    assert pol.unit_scale_repairs == ()


def test_policy_entry_parsed(tmp_path):
    pol = _load(tmp_path, """\
unit_scale_repairs:
  - field: volume
    factor: 100.0
    match_tol: 0.10
    keys: ["600519.SH|2015-05-28", "000725.SZ|2026-07-02"]
""")
    assert pol.unit_scale_repairs == (
        rules.UnitScaleRepair(field="volume", factor=100.0, match_tol=0.10,
                              keys=("600519.SH|2015-05-28",
                                    "000725.SZ|2026-07-02")),)


@pytest.mark.parametrize("field_val", ["'close'", "''"])
def test_policy_entry_bad_field_rejected(tmp_path, field_val):
    block = ("unit_scale_repairs:\n"
             f"  - field: {field_val}\n"
             "    factor: 100.0\n    match_tol: 0.10\n    keys: [\"600519.SH|2015-05-28\"]\n")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, block)
    assert "unit_scale_repairs[0].field" in str(ei.value)


@pytest.mark.parametrize("factor_val", ["1.0", "0.0", "-100.0", "hundred"])
def test_policy_entry_bad_factor_rejected(tmp_path, factor_val):
    block = ("unit_scale_repairs:\n"
             "  - field: volume\n"
             f"    factor: {factor_val}\n"
             "    match_tol: 0.10\n    keys: [\"600519.SH|2015-05-28\"]\n")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, block)
    assert "unit_scale_repairs[0].factor" in str(ei.value)


def test_policy_entry_empty_keys_rejected(tmp_path):
    block = ("unit_scale_repairs:\n  - field: volume\n    factor: 100.0\n"
             "    match_tol: 0.10\n    keys: []\n")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, block)
    assert "unit_scale_repairs[0].keys" in str(ei.value)


@pytest.mark.parametrize("bad_key", ['"600519.SH"', '"600519.SH|not-a-date"',
                                     '"|2015-05-28"'])
def test_policy_entry_malformed_key_rejected(tmp_path, bad_key):
    block = ("unit_scale_repairs:\n  - field: volume\n    factor: 100.0\n"
             f"    match_tol: 0.10\n    keys: [{bad_key}]\n")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, block)
    assert "unit_scale_repairs[0].keys" in str(ei.value)


@pytest.mark.parametrize("bad_key", ['"000725.SZ|1995-12-29"', '"430047.BJ|2020-07-01"'])
def test_policy_entry_out_of_scope_key_rejected(tmp_path, bad_key):
    """逐行登记只允许 scope 内行（范围外免修；登记即 scope 内处置声明）。"""
    block = ("unit_scale_repairs:\n  - field: volume\n    factor: 100.0\n"
             f"    match_tol: 0.10\n    keys: [{bad_key}]\n")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, block)
    assert "unit_scale_repairs[0].keys" in str(ei.value)


def test_policy_duplicate_key_across_entries_rejected(tmp_path):
    block = ("unit_scale_repairs:\n"
             "  - field: volume\n    factor: 100.0\n    match_tol: 0.10\n"
             "    keys: [\"600519.SH|2015-05-28\"]\n"
             "  - field: volume\n    factor: 100.0\n    match_tol: 0.10\n"
             "    keys: [\"600519.SH|2015-05-28\"]\n")
    with pytest.raises(ValueError) as ei:
        _load(tmp_path, block)
    assert "unit_scale_repairs[1].keys" in str(ei.value)


# ── shipped policy × RCA 证据：19 行登记内容锁定 ──────────────────────────
def test_shipped_policy_registers_exactly_evidence_unit_rows():
    """shipped v3 的登记键必须与 R37 证据 CSV 的 unit_like 19 行逐键一致。

    这是「不是存根」的内容锁：改实现/删证据/漏行都会失败。
    """
    assert EVIDENCE_CSV.is_file(), f"RCA 证据缺失：{EVIDENCE_CSV}"
    ev = (pl.read_csv(EVIDENCE_CSV, try_parse_dates=True)
          .filter(pl.col("class") == "unit_like"))
    expected = {f"{r['code']}|{r['trade_date']}" for r in ev.iter_rows(named=True)}
    assert len(expected) == 19, "RCA 证据 unit_like 必须 19 行"

    pol = rules.load_policy()
    assert len(pol.unit_scale_repairs) == 1
    rep = pol.unit_scale_repairs[0]
    assert (rep.field, rep.factor) == ("volume", 100.0)
    assert set(rep.keys) == expected, "登记键必须与逐行证据完全一致"
