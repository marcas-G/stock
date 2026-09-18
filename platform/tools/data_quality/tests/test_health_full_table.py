"""F5：health full_table 口径（Plan DQ-M1 修复轮 1）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1（修复轮 1 F5）
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §6（health 字段；scope=full_table 注记）/ §3.1（coverage/error_rate 腿）。

口径（F5）：
- ``quality.*`` 与 ``completeness.*`` 统一 **full_table**：
  分母 ``expected_count`` = raw 全表行数，``actual`` = clean 行数；
  ``coverage = actual/expected``；``error_rate = error_count/expected``；
  ``fatal/warning/quarantine`` 同为全表清洗范围计数；
- ``completeness.status = COMPLETE`` 当且仅当差额被清洗账完全解释
  （actual + quarantined + deduped == raw）；未解释差额 → INCOMPLETE → FINAL FAIL。
"""
from __future__ import annotations

import datetime as dt
import json

import polars as pl
import pytest

from data_quality import audit, health, rules

D = dt.date
PART = "2026-09-18"
TAG = "20260918"
DATASET = "ashare_daily"

SCHEMA = {"code": pl.String, "trade_date": pl.Date, "open": pl.Float64,
          "high": pl.Float64, "low": pl.Float64, "close": pl.Float64}


def _rows(n, day=PART, close=10.0):
    d = D.fromisoformat(day)
    return [{"code": f"00000{i:02d}.SZ", "trade_date": d, "open": close,
             "high": close * 1.02, "low": close * 0.98, "close": close}
            for i in range(n)]


def _frame(rows):
    return pl.DataFrame(rows, schema=SCHEMA)


def test_completeness_explained_drops_full_table():
    """F5：full_table 口径下拉 raw→clean 的差额须由清洗账（quarantine+dedup）解释。"""
    c = audit.check_completeness(100, 90, explained_drops=10)
    assert c.status == audit.COMPLETE and c.coverage == pytest.approx(0.9)
    assert (c.expected_count, c.actual_count) == (100, 90)

    partial = audit.check_completeness(100, 90, explained_drops=5)
    assert partial.status == audit.INCOMPLETE, "未解释的差额 = INCOMPLETE（fail-closed）"

    over = audit.check_completeness(100, 91, explained_drops=10)
    assert over.status == audit.INCOMPLETE, "超量同样不完整（actual+explained>expected）"


def _cli_fixture(root, *, raw_rows, clean_rows, quarantined, deduped=0,
                 partition=PART, decision="DEGRADED", error_count=60):
    """伪 raw + clean staging（全表口径 fixture）；返回 raw parquet 路径。"""
    raw_path = root / "raw_daily_fact.parquet"
    _frame(_rows(raw_rows)).write_parquet(raw_path)
    d = root / "staging" / DATASET / TAG
    d.mkdir(parents=True, exist_ok=True)
    _frame(_rows(clean_rows)).write_parquet(d / "daily_fact.parquet")
    summary = {
        "dataset": DATASET, "run_tag": TAG, "scope": "full_table",
        "partition": partition, "dq_policy_version": rules.POLICY_VERSION,
        "decision": decision, "clean_rows": clean_rows,
        "quarantined_rows": quarantined, "deduped_rows": deduped,
        "quality": {"fatal_count": 0, "error_count": error_count,
                    "warning_count": 3, "quarantine_count": quarantined,
                    "error_rate": error_count / raw_rows,
                    "systematic_issue": False, "systemic_detail": None,
                    "unresolved_partition_error": False},
        "rules": {}, "dry_run": False,
    }
    (d / "summary.json").write_text(json.dumps(summary, ensure_ascii=False),
                                    encoding="utf-8")
    return raw_path


def test_health_cli_full_table_scope(tmp_path, monkeypatch):
    """F5：health quality/completeness 统一 full_table 口径（raw 分母 / clean 实际）。"""
    raw_path = _cli_fixture(tmp_path, raw_rows=10000, clean_rows=9995,
                            quarantined=5)
    canonical = _frame(_rows(9995))
    monkeypatch.setattr(health, "_read_canonical",
                        lambda partition, dataset=DATASET: (canonical, 9995))
    rc = health.main(["publish", "--root", str(tmp_path), "--run-tag", TAG,
                      "--partition", PART, "--no-sample", "--raw", str(raw_path)])
    assert rc == 0, "DEGRADED 非 FAIL 必须成功发布（exit 0）"
    doc = json.loads((tmp_path / "health" / DATASET / f"{PART}.json")
                     .read_text(encoding="utf-8"))
    assert doc["health_status"] == "DEGRADED"
    assert doc["completeness"] == {
        "status": "COMPLETE", "expected_count": 10000, "actual_count": 9995,
        "coverage": pytest.approx(0.9995)}
    assert doc["quality"]["error_count"] == 60
    assert doc["quality"]["quarantine_count"] == 5
    assert doc["quality"]["error_rate"] == pytest.approx(60 / 10000), (
        "full_table 口径：error_rate 分母 = raw 全表行数（F5）")


def test_health_cli_unexplained_deficit_is_fail(tmp_path, monkeypatch):
    """F5：raw→clean 差额超出清洗账解释范围 → INCOMPLETE → FINAL FAIL。"""
    raw_path = _cli_fixture(tmp_path, raw_rows=100, clean_rows=90,
                            quarantined=2)
    canonical = _frame(_rows(90))
    monkeypatch.setattr(health, "_read_canonical",
                        lambda partition, dataset=DATASET: (canonical, 90))
    rc = health.main(["publish", "--root", str(tmp_path), "--run-tag", TAG,
                      "--partition", PART, "--no-sample", "--raw", str(raw_path)])
    assert rc == 1, "INCOMPLETE → FAIL 必须非零退出"
    doc = json.loads((tmp_path / "health" / DATASET / f"{PART}.json")
                     .read_text(encoding="utf-8"))
    assert doc["completeness"]["status"] == "INCOMPLETE"
    assert doc["health_status"] == "FAIL"
