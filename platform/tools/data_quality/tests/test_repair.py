"""T3：确定性修复 + 行级隔离（Plan DQ-M1）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-3-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §1（ERROR/FATAL → 行级 quarantine）/ §5（确定性修复清单穷尽）/ §6（索引字段）。

红线：只用确定性修复（完全相同行 dedup / 时间解析时区规范化 / schema 类型强制）；
同 PK 不同 payload → 全部行 quarantine（禁止选一条）；WARN/INFO 保留 + flag。
"""
from __future__ import annotations

import datetime as dt
import json
import math
from datetime import datetime, timedelta, timezone

import polars as pl
from polars.testing import assert_frame_equal

from data_quality import repair, rules, validators
from data_quality.rules import RuleResult

D = dt.date
SYM = "600519.SH"
DAY1, DAY2 = D(2026, 9, 17), D(2026, 9, 18)

_SCHEMA = {
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
    schema = dict(_SCHEMA)
    for k in rows[0]:
        schema.setdefault(k, pl.Float64)
    schema.update(dtypes)
    return pl.DataFrame(rows, schema=schema)


def _cal(*days):
    return pl.DataFrame({"trade_date": list(days or (DAY1, DAY2))},
                        schema={"trade_date": pl.Date})


def _finds(log, action):
    return [e for e in log if e["action"] == action]


# ── 确定性修复 ───────────────────────────────────────────────────────────
def test_exact_duplicate_rows_deduped_keep_first_order():
    df = _frame([_row(close=10.2), _row(close=10.2), _row(trade_date=DAY1)])
    clean, q, log = repair.repair(df, [])
    assert clean.height == 2
    assert clean["trade_date"].to_list() == [DAY2, DAY1]
    assert q.height == 0
    e = _finds(log, "dedup_identical")[0]
    assert e["count"] == 1 and e["keys"] == [f"{SYM}|2026-09-18"]


def test_same_pk_different_payload_not_deduped_without_results():
    """全列一致才删：同 PK 不同 payload 在无 results 时也必须保留两行。"""
    df = _frame([_row(close=10.2), _row(close=10.3)])
    clean, q, log = repair.repair(df, [])
    assert clean.height == 2
    assert q.height == 0
    assert _finds(log, "dedup_identical") == []


def test_time_string_parse_and_idempotent():
    df = _frame([_row(trade_date="2026-09-18"),
                 _row(symbol="AAA.SH", trade_date="20260917")],
                trade_date=pl.String)
    clean, _, log = repair.repair(df, [])
    assert clean.schema["trade_date"] == pl.Date
    assert clean["trade_date"].to_list() == [DAY2, DAY1]
    e = _finds(log, "normalize_time")[0]
    assert e["count"] == 2

    clean2, _, log2 = repair.repair(clean, [])
    assert_frame_equal(clean2, clean)
    assert _finds(log2, "normalize_time") == []


def test_datetime_tz_normalized_to_exchange_date():
    tz8 = timezone(timedelta(hours=8))
    df = pl.DataFrame(
        {"symbol": [SYM], "trade_date": [datetime(2026, 9, 18, 0, 30, tzinfo=tz8)],
         "open": [10.0], "high": [10.5], "low": [9.8], "close": [10.2],
         "volume": [1000.0], "amount": [10200.0]},
        schema={**_SCHEMA, "trade_date": pl.Datetime("us", "Asia/Shanghai")})
    clean, _, log = repair.repair(df, [])
    assert clean.schema["trade_date"] == pl.Date
    # 交易所本地日（Asia/Shanghai）= 09-18；按 UTC 会错成 09-17
    assert clean["trade_date"].to_list() == [DAY2]
    assert _finds(log, "normalize_time")[0]["count"] == 1


def test_schema_coercion_and_idempotent():
    df = _frame([_row(close="10.5"), _row(close="oops")], close=pl.String)
    clean, _, log = repair.repair(df, [])
    assert clean.schema["close"] == pl.Float64
    vals = clean["close"].to_list()
    assert vals[0] == 10.5
    assert vals[1] is None, "非法数值只能归 NULL，绝不 fillna(0)"
    assert 0.0 not in vals
    e = _finds(log, "coerce_dtypes")[0]
    assert "close" in e["columns"]

    clean2, _, log2 = repair.repair(clean, [])
    assert_frame_equal(clean2, clean)
    assert _finds(log2, "coerce_dtypes") == []


# ── 行级隔离 ─────────────────────────────────────────────────────────────
def test_pk_conflict_both_rows_quarantined_clean_has_no_key():
    """真实 validator 产出 → repair：冲突组全部隔离，clean 不得残留该键。"""
    df = _frame([_row(close=10.2), _row(close=10.3), _row(symbol="AAA.SH")])
    res = validators.validate_daily(df, _cal(), None, None)
    assert any(r.rule_id == rules.PK_CONFLICT for r in res)

    clean, q, log = repair.repair(df, res)
    assert q.height == 2
    assert set(q["close"].to_list()) == {10.2, 10.3}
    assert clean.height == 1 and clean["symbol"][0] == "AAA.SH"
    assert clean.filter((pl.col("symbol") == SYM)
                        & (pl.col("trade_date") == DAY2)).height == 0
    e = _finds(log, "quarantine")[0]
    assert e["rule_id"] == rules.PK_CONFLICT
    assert e["count"] == 2
    assert e["keys"] == [f"{SYM}|2026-09-18"]


def test_error_quarantined_while_warn_and_info_rows_kept():
    df = _frame([
        _row(symbol="AAA.SH", close=-1.0, low=-1.0),          # ERROR → quarantine
        _row(symbol="BBB.SH", close=math.nan),                # WARN → 保留
        _row(symbol="CCC.SH", trade_date=DAY1),               # INFO 重复 → dedup 后保留 1
        _row(symbol="CCC.SH", trade_date=DAY1),
    ])
    res = validators.validate_daily(df, _cal(), None, None)
    clean, q, log = repair.repair(df, res)

    assert q.height == 1 and q["symbol"][0] == "AAA.SH"
    assert clean.height == 2
    assert set(clean["symbol"].to_list()) == {"BBB.SH", "CCC.SH"}
    qlog = _finds(log, "quarantine")
    assert [(e["rule_id"], e["count"]) for e in qlog] == [(rules.PRICE_NONPOSITIVE, 1)]


def test_frame_level_fatal_does_not_isolate_rows():
    """schema FATAL 的 key 是字段名（不可定位到行）→ 交分区门，不误隔离行。"""
    df = _frame([_row()])
    res = [RuleResult(rules.SCHEMA_MISSING_COLUMN, rules.FATAL, "close", "缺列")]
    clean, q, log = repair.repair(df, res)
    assert clean.height == 1
    assert q.height == 0
    assert _finds(log, "quarantine") == []


def test_quarantined_keeps_raw_form_clean_normalized():
    """隔离行是证据，保持原始形态；规范化只作用于 clean。"""
    df = _frame([_row(trade_date="2026-09-18", close=-1.0, low=-1.0)],
                trade_date=pl.String)
    res = validators.validate_daily(df, _cal(), None, None)
    clean, q, _ = repair.repair(df, res)
    assert q.schema["trade_date"] == pl.String
    assert q["trade_date"].to_list() == ["2026-09-18"]
    assert clean.height == 0


# ── quarantine 落盘 ──────────────────────────────────────────────────────
def test_write_quarantine_layout_index_and_rewrite(tmp_path):
    rows = _frame([_row(close=10.2), _row(close=10.3)])
    index = [{"rule_id": "PK_CONFLICT", "reason": "payload 冲突", "count": 2}]
    d = repair.write_quarantine("ashare_daily", "2026-09-18", rows, index,
                                root=tmp_path)
    assert d == tmp_path / "quarantine" / "ashare_daily" / "2026-09-18"
    assert d.joinpath("_SUCCESS").exists()
    assert_frame_equal(pl.read_parquet(d / "rows.parquet"), rows)
    doc = json.loads((d / "index.json").read_text(encoding="utf-8"))
    assert doc == {
        "dataset": "ashare_daily", "partition": "2026-09-18", "row_count": 2,
        "entries": [{"rule_id": "PK_CONFLICT", "reason": "payload 冲突", "count": 2}],
    }

    # 幂等重写（同分区覆盖）不残留旧行
    d2 = repair.write_quarantine("ashare_daily", "2026-09-18", rows.head(1), index,
                                 root=tmp_path)
    assert d2 == d
    assert pl.read_parquet(d / "rows.parquet").height == 1
    assert json.loads((d / "index.json").read_text(encoding="utf-8"))["row_count"] == 1
