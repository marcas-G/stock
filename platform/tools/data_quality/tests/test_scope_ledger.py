"""R37 T2：DQ 账本 scope 化（scope 内口径）。

规格：knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md §2
     （validators 前先过滤 scope；expected/actual/quarantine/dedup/quality_backlog
      全部按 scope 计算；范围外行不进 quarantine 证据、不进 staging）。
计划：knowledge/design/platform/plans/2026-09-20-dq-scope-cut.md Task 2。

断言来源 = 规格（不是实现）：
- 范围外行（trade_date < 1996-01-01 或 code 以 .BJ 结尾）不进
  expected/actual/quarantine/rules/账本 by_date；
- 范围内坏行照旧隔离（quarantine 证据保留）；
- 日更链装载 raw 的入口（pipeline.py clean CLI）先过 scope 再清洗；
- scope 过滤后为空 → 拒绝（防空表覆盖 canonical）。
"""
from __future__ import annotations

import datetime as dt
import json

import polars as pl

from data_quality import pipeline, rules

D = dt.date
PRE = D(1995, 12, 29)          # 前 1996（范围外）
DAY1 = D(2026, 9, 17)
DAY2 = D(2026, 9, 18)
BJ_DAY = D(2026, 9, 18)
DATASET = "ashare_daily"
TAG = "scopetest"

SCHEMA = {"code": pl.String, "trade_date": pl.Date, "open": pl.Float64,
          "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
          "volume": pl.Float64, "amount": pl.Float64}


def _raw(rows) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SCHEMA)


def _good(code="600519.SH", day=DAY2, **over):
    r = {"code": code, "trade_date": day, "open": 10.0, "high": 10.5,
         "low": 9.8, "close": 10.2, "volume": 1000.0, "amount": 10200.0}
    r.update(over)
    return r


def _bad(code="999999.SZ", day=DAY2):
    """恰好触发单条 PRICE_NONPOSITIVE（全价格 -1、零量额）。"""
    return _good(code=code, day=day, open=-1.0, high=-1.0, low=-1.0, close=-1.0,
                 volume=0.0, amount=0.0)


# ── scoped 全范围账本（publish-history 公共 ledger）──────────────────────
def test_build_scoped_ledger_excludes_out_of_scope_rows_from_every_count():
    raw = _raw([
        _good(code="600519.SH", day=DAY1),
        _good(code="000001.SZ", day=PRE),      # 前 1996 好行 → 范围外
        _bad(code="830001.BJ", day=BJ_DAY),    # BJ 坏行 → 范围外
        _bad(code="999999.SZ", day=DAY2),      # 范围内坏行 → 照旧隔离
        _good(code="000002.SZ", day=DAY2),
    ])
    ledger = pipeline.build_scoped_ledger(raw)

    assert ledger.expected_count == 3, "expected = scoped raw 行数（不含 BJ/前 1996）"
    assert ledger.clean_count == 2
    assert ledger.quarantine_count == 1, "范围外坏行不得进 quarantine 计数"
    assert ledger.error_count == 1
    assert ledger.rules.get(rules.PRICE_NONPOSITIVE) == 1, (
        "范围外坏行不得进 rules 计数（2 条坏行只应剩 scope 内 1 条）")

    assert set(ledger.by_date) == {DAY1.isoformat(), DAY2.isoformat()}, (
        "by_date 只含 scope 内日期（1995-12-29 无条目）")
    p2 = ledger.by_date[DAY2.isoformat()]
    assert p2.expected == 2 and p2.clean == 1
    assert (p2.quarantined_rows, p2.quarantined_keys) == (1, 1)
    assert p2.deduped_rows == 0 and p2.error_count == 1
    p1 = ledger.by_date[DAY1.isoformat()]
    assert p1.expected == 1 and p1.clean == 1 and p1.quarantined_keys == 0


def test_build_scoped_ledger_clean_frame_is_good_only(tmp_path):
    """范围外行不进 staging：全表入口过滤后，clean staging 只含 scope 内行。"""
    raw = _raw([_good(code="600519.SH", day=DAY1),
                _good(code="000001.SZ", day=PRE),
                _good(code="830001.BJ", day=BJ_DAY),
                _bad(code="999999.SZ", day=DAY2)])
    p = tmp_path / "daily_fact.parquet"
    raw.write_parquet(p)
    root = tmp_path / "data"
    rc = pipeline.main(["clean", "--raw", str(p), "--root", str(root),
                        "--run-tag", TAG])

    assert rc == 1, "scope 内 1/2 错误 > fail 线 → PRE-INGEST FAIL"
    stage = root / "staging" / DATASET / TAG
    staged = pl.read_parquet(stage / "daily_fact.parquet")
    assert staged["code"].to_list() == ["600519.SH"], (
        "staging 必须只含 scope 内 clean 行（BJ/前 1996 行不得入）")
    assert not (stage / "_SUCCESS").exists(), "FAIL 不得落完成标记"

    summary = json.loads((stage / "summary.json").read_text(encoding="utf-8"))
    assert summary["completeness"]["expected_count"] == 2, (
        "账本分母 = scoped raw（不是含 BJ/前 1996 的 4）")
    assert summary["quarantined_rows"] == 1
    qrows = pl.read_parquet(root / "quarantine" / DATASET / TAG / "rows.parquet")
    assert qrows["code"].to_list() == ["999999.SZ"], (
        "quarantine 证据只含 scope 内坏行")


def test_clean_cli_rejects_raw_with_no_scoped_rows(tmp_path):
    """scope 过滤后为空 → 拒绝（防空 staging 覆盖 canonical）。"""
    raw = _raw([_good(code="830001.BJ", day=BJ_DAY), _good(code="000001.SZ", day=PRE)])
    p = tmp_path / "daily_fact.parquet"
    raw.write_parquet(p)
    root = tmp_path / "data"
    rc = pipeline.main(["clean", "--raw", str(p), "--root", str(root),
                        "--run-tag", TAG])
    assert rc == 2
    assert not (root / "staging").exists()
