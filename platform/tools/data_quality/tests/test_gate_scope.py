"""Plan DQ-M1.5c：门控作用域 = 更新增量（delta）+ health `quality_backlog` 披露。

权威：设计 §3.3（2026-09-19 冻结）——
- 门（PRE-INGEST + FINAL）判 `delta = trade_date > 上次成功发布 freshness.latest_trade_date`
  的行；无发布历史 → 全量；
- delta 上算 error_rate/coverage/completeness/systemic → PASS/DEGRADED/FAIL
  （阈值与检测器不变；delta 内系统性仍 FAIL）；
- 全表口径 → health 顶层 `quality_backlog`（不参与判定）；
- delta 空 → PASS + `note: no_new_data`。

本文件覆盖：watermark 选择、delta 过滤、delta 门结果、不弱化反例、backlog 披露与
「backlog 不影响 status」、无新数据路径、FINAL 门 delta 完整性腿。
"""
from __future__ import annotations

import datetime as dt
import json

import polars as pl
import pytest

from data_quality import health, pipeline, rules

D = dt.date
DATASET = "ashare_daily"
PART = D(2026, 9, 17)
NEW1, NEW2 = D(2026, 9, 18), D(2026, 9, 19)
TAG = "20260919d"

_SCHEMA = {"code": pl.String, "trade_date": pl.Date, "open": pl.Float64,
           "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
           "volume": pl.Float64, "amount": pl.Float64}


def _row(code, day, open=10.0, high=10.2, low=9.8, close=10.0,
         volume=1000.0, amount=10200.0):
    return {"code": code, "trade_date": day, "open": open, "high": high,
            "low": low, "close": close, "volume": volume, "amount": amount}


def _good(code, day):
    return _row(code, day)


def _bad(code, day):
    """PRICE_NONPOSITIVE（低量额避开 OHLC/VWAP）——历史残余型坏行。"""
    return _row(code, day, open=-1.0, high=-1.0, low=-1.0, close=-1.0,
                volume=0.0, amount=0.0)


def _vwap_bad(code, day):
    """VWAP 越带（amount/volume=20 vs close=10）——系统性 field=amount 类。"""
    return _row(code, day, open=10.0, high=10.0, low=10.0, close=10.0,
                volume=1000.0, amount=20000.0)


def _raw(rows) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=_SCHEMA)


def _prev_publish(root, *, partition=PART, status="DEGRADED",
                  verification_state="VERIFIED"):
    return health.publish_health(
        root=root, dataset_id=DATASET, partition=partition.isoformat(),
        data_version="vprev_01", dq_policy_version=rules.POLICY_VERSION,
        health_status=status, verification_state=verification_state,
        completeness={"status": "COMPLETE", "expected_count": 10,
                      "actual_count": 10, "coverage": 1.0},
        quality={"fatal_count": 0, "error_count": 0, "warning_count": 0,
                 "quarantine_count": 0, "error_rate": 0.0,
                 "systematic_issue": False, "systemic_detail": None},
        rules_counts={}, latest_trade_date=partition.isoformat())


# ── watermark 选择（health.last_published_trade_date）────────────────────
def test_last_published_trade_date_requires_chain_published_artifact(tmp_path):
    """只认链发布的 VERIFIED 档案；无档案/仅 LEGACY 打标 → None（首跑=全量）。"""
    assert health.last_published_trade_date(tmp_path, DATASET) is None

    legacy_dir = tmp_path / "health" / DATASET
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "2026-09-16.json").write_text(json.dumps({
        "partition": "2026-09-16", "verification_state": "LEGACY_UNVERIFIED",
        "health_status": "UNKNOWN",
        "freshness": {"latest_trade_date": "2026-09-16"},
    }), encoding="utf-8")
    assert health.last_published_trade_date(tmp_path, DATASET) is None


def test_last_published_trade_date_takes_max_freshness_of_verified(tmp_path):
    _prev_publish(tmp_path, partition=D(2026, 9, 15))
    _prev_publish(tmp_path, partition=PART)
    assert health.last_published_trade_date(tmp_path, DATASET) == "2026-09-17"

    # FAIL 档案也是"已发布水位"（否则失败后重跑会死锁在历史残余上）
    _prev_publish(tmp_path, partition=NEW2, status="FAIL")
    assert health.last_published_trade_date(tmp_path, DATASET) == "2026-09-19"


def test_last_published_trade_date_skips_broken_files(tmp_path):
    d = tmp_path / "health" / DATASET
    d.mkdir(parents=True)
    (d / "2026-09-30.json").write_text("{ not json", encoding="utf-8")
    _prev_publish(tmp_path, partition=PART)
    assert health.last_published_trade_date(tmp_path, DATASET) == "2026-09-17"


# ── PRE-INGEST 门：delta 口径 ────────────────────────────────────────────
def test_clean_gate_scope_delta_excludes_historical_backlog(tmp_path):
    """全表 150 错（时间集中，旧口径必 FAIL）但 delta（新增 2 日）干净 → PASS。"""
    _prev_publish(tmp_path)
    rows = [_bad(f"9{i:05d}.SZ", PART) for i in range(150)]
    rows += [_good(f"8{i:05d}.SZ", NEW1) for i in range(3000)]
    rows += [_good(f"8{i:05d}.SZ", NEW2) for i in range(3000)]

    res = pipeline.run_clean_stage(_raw(rows), run_tag=TAG, root=tmp_path)
    assert res.watermark == "2026-09-17"
    assert res.delta_rows == 6000
    assert res.decision == pipeline.PASS, "旧口径残余不得阻断新增量"
    assert res.metrics.error_count == 0, "门看 delta"
    summary = json.loads((tmp_path / "staging" / DATASET / TAG
                          / "summary.json").read_text(encoding="utf-8"))
    assert summary["gate_scope"] == "delta"
    assert summary["quality"]["error_count"] == 150, "全表口径照常披露（backlog 源）"
    assert summary["delta"]["quality"]["error_count"] == 0
    assert summary["delta"]["rules"].get(rules.PRICE_NONPOSITIVE, 0) == 0


def test_clean_gate_without_history_is_full_table_and_fails(tmp_path):
    """无发布历史 → delta=全量：同一批数据系统性照旧 FAIL（不弱化 + 首跑等价）。"""
    rows = [_bad(f"9{i:05d}.SZ", PART) for i in range(150)]
    rows += [_good(f"8{i:05d}.SZ", NEW1) for i in range(3000)]
    res = pipeline.run_clean_stage(_raw(rows), run_tag=TAG, root=tmp_path)
    assert res.watermark is None
    assert res.delta_rows == len(rows)
    assert res.decision == pipeline.FAIL
    assert res.systemic is not None and res.systemic.rule == "SYSTEMATIC_TEMPORAL_FAILURE"


def test_clean_gate_delta_systemic_still_fails(tmp_path):
    """不弱化反例：delta 内 60 行 VWAP 越带（field=amount 集中）→ 仍 FAIL。"""
    _prev_publish(tmp_path)
    rows = [_good(f"7{i:05d}.SZ", PART) for i in range(150)]
    rows += [_vwap_bad(f"9{i:05d}.SZ", NEW1) for i in range(60)]
    rows += [_good(f"8{i:05d}.SZ", NEW1) for i in range(300)]

    res = pipeline.run_clean_stage(_raw(rows), run_tag=TAG, root=tmp_path)
    assert res.delta_rows == 360
    assert res.metrics.error_count == 60
    assert res.decision == pipeline.FAIL, "delta 内系统性必须照旧阻断"
    assert res.systemic is not None
    assert res.systemic.rule == "SYSTEMATIC_FIELD_FAILURE"


def test_clean_gate_empty_delta_passes(tmp_path):
    """无新数据：delta 空 → PASS（不因历史残余 FAIL）。"""
    _prev_publish(tmp_path)
    rows = [_bad(f"9{i:05d}.SZ", PART) for i in range(150)]
    rows += [_good(f"8{i:05d}.SZ", PART) for i in range(100)]
    res = pipeline.run_clean_stage(_raw(rows), run_tag=TAG, root=tmp_path)
    assert res.watermark == "2026-09-17"
    assert res.delta_rows == 0
    assert res.decision == pipeline.PASS


# ── health：quality_backlog 披露 + delta completeness/quality ─────────────
def _staging_summary(root, *, delta_rows, clean_delta, quarantined_delta,
                     expected_delta, full_error_count=150, decision="PASS"):
    d = root / "staging" / DATASET / TAG
    d.mkdir(parents=True, exist_ok=True)
    n_full = 150 + 6000
    summary = {
        "dataset": DATASET, "run_tag": TAG, "scope": "full_table",
        "partition": NEW2.isoformat(), "dq_policy_version": rules.POLICY_VERSION,
        "decision": decision, "clean_rows": n_full - full_error_count,
        "quarantined_rows": full_error_count, "deduped_rows": 0,
        "watermark": PART.isoformat(), "gate_scope": "delta",
        "quality": {"fatal_count": 0, "error_count": full_error_count,
                    "warning_count": 0, "info_count": 0,
                    "quarantine_count": full_error_count,
                    "error_rate": full_error_count / n_full,
                    "systematic_issue": True,
                    "systemic_detail": "全表时间集中（历史残余）",
                    "unresolved_partition_error": False},
        "completeness": {"expected_count": n_full, "actual_count": n_full,
                         "coverage": 1.0},
        "rules": {rules.PRICE_NONPOSITIVE: full_error_count},
        "delta": {
            "rows": delta_rows, "clean_rows": clean_delta,
            "quarantined_rows": quarantined_delta, "deduped_rows": 0,
            "quality": {"fatal_count": 0, "error_count": 0, "warning_count": 0,
                        "info_count": 0, "quarantine_count": 0,
                        "error_rate": 0.0, "systematic_issue": False,
                        "systemic_detail": None,
                        "unresolved_partition_error": False},
            "rules": {},
        },
        "dry_run": False,
    }
    (d / "summary.json").write_text(json.dumps(summary, ensure_ascii=False),
                                    encoding="utf-8")
    raw = _raw([_good(f"8{i:05d}.SZ", NEW2) for i in range(50)])
    raw_path = root / "raw.parquet"
    raw.write_parquet(raw_path)
    return raw_path


def _run_health(root, raw_path, capsys):
    rc = health.main(["publish", "--root", str(root), "--run-tag", TAG,
                      "--partition", "latest", "--no-sample",
                      "--raw", str(raw_path)])
    return rc, json.loads((root / "health" / DATASET / f"{NEW2.isoformat()}.json")
                          .read_text(encoding="utf-8"))


def test_health_delta_scope_quality_and_backlog_disclosure(tmp_path, monkeypatch):
    raw_path = _staging_summary(tmp_path, delta_rows=6000, clean_delta=6000,
                                quarantined_delta=0, expected_delta=6000)
    frame = _raw([_good(f"8{i:05d}.SZ", NEW2) for i in range(50)])
    monkeypatch.setattr(health, "_read_canonical",
                        lambda partition, dataset=DATASET: (frame, 60150))
    monkeypatch.setattr(health, "_read_canonical_delta",
                        lambda watermark, dataset=DATASET, full_count=0: 6000)

    rc, doc = _run_health(tmp_path, raw_path, None)
    assert rc == 0, "delta 干净 → 非 FAIL（PASS/DEGRADED）"
    assert doc["health_status"] == pipeline.PASS
    assert doc["note"] is None
    assert doc["quality"]["error_count"] == 0, "health quality = delta 口径"
    assert doc["quality_backlog"]["scope"] == "full_table"
    assert doc["quality_backlog"]["error_count"] == 150
    assert doc["quality_backlog"]["error_rate"] == pytest.approx(150 / 6150)
    assert doc["quality_backlog"]["systemic_detail"]
    assert doc["quality_backlog"]["top_classes"][0]["rule_id"] == \
        rules.PRICE_NONPOSITIVE
    assert doc["completeness"]["status"] == "COMPLETE"


def test_health_backlog_does_not_affect_status(tmp_path, monkeypatch):
    """backlog 是披露项：改它（更坏）不影响 health_status。"""
    raw_path = _staging_summary(tmp_path, delta_rows=6000, clean_delta=6000,
                                quarantined_delta=0, expected_delta=6000,
                                full_error_count=150)
    frame = _raw([_good(f"8{i:05d}.SZ", NEW2) for i in range(50)])
    monkeypatch.setattr(health, "_read_canonical",
                        lambda partition, dataset=DATASET: (frame, 60150))
    monkeypatch.setattr(health, "_read_canonical_delta",
                        lambda watermark, dataset=DATASET, full_count=0: 6000)
    rc1, doc1 = _run_health(tmp_path, raw_path, None)

    # 把全表 backlog 改成更大（含 FATAL）后重发——delta 未变 → status 不变
    sp = tmp_path / "staging" / DATASET / TAG / "summary.json"
    s = json.loads(sp.read_text(encoding="utf-8"))
    s["quality"]["error_count"] = 999999
    s["quality"]["fatal_count"] = 3
    s["quality"]["systematic_issue"] = True
    s["rules"] = {rules.PRICE_NONPOSITIVE: 999999}
    sp.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    rc2, doc2 = _run_health(tmp_path, raw_path, None)
    assert (rc2, doc2["health_status"]) == (rc1, doc1["health_status"])
    assert doc2["quality_backlog"]["fatal_count"] == 3, "披露照实"


def test_health_no_new_data_passes_with_note(tmp_path):
    raw_path = _staging_summary(tmp_path, delta_rows=0, clean_delta=0,
                                quarantined_delta=0, expected_delta=0)
    rc, doc = _run_health(tmp_path, raw_path, None)
    assert rc == 0
    assert doc["health_status"] == pipeline.PASS
    assert doc["note"] == "no_new_data"
    assert doc["quality_backlog"]["error_count"] == 150, "backlog 照常披露"
    assert doc["completeness"]["status"] == "COMPLETE"


def test_health_delta_incomplete_breaks_final_gate(tmp_path, monkeypatch):
    """FINAL 门 delta 完整性腿：canonical delta 少行 → INCOMPLETE → FAIL。"""
    raw_path = _staging_summary(tmp_path, delta_rows=6000, clean_delta=6000,
                                quarantined_delta=0, expected_delta=6000)
    frame = _raw([_good(f"8{i:05d}.SZ", NEW2) for i in range(50)])
    monkeypatch.setattr(health, "_read_canonical",
                        lambda partition, dataset=DATASET: (frame, 5999))
    monkeypatch.setattr(health, "_read_canonical_delta",
                        lambda watermark, dataset=DATASET, full_count=0: 5999)
    rc, doc = _run_health(tmp_path, raw_path, None)
    assert rc == 1
    assert doc["health_status"] == "FAIL"
    assert doc["completeness"]["status"] == "INCOMPLETE"


def test_build_health_doc_requires_backlog_key_set(tmp_path):
    base = dict(
        dataset_id=DATASET, partition=NEW2.isoformat(), data_version="v1",
        dq_policy_version=rules.POLICY_VERSION, health_status="PASS",
        verification_state="VERIFIED",
        completeness={"status": "COMPLETE", "expected_count": 1,
                      "actual_count": 1, "coverage": 1.0},
        quality={"fatal_count": 0, "error_count": 0, "warning_count": 0,
                 "quarantine_count": 0, "error_rate": 0.0,
                 "systematic_issue": False, "systemic_detail": None},
        rules_counts={})
    with pytest.raises(ValueError, match="quality_backlog"):
        health.build_health_doc(**base, quality_backlog={"scope": "full_table"})
    with pytest.raises(ValueError, match="note"):
        health.build_health_doc(**base, note="")
    doc = health.build_health_doc(**base)
    assert set(doc) == set(health.TOP_KEYS)
    assert set(doc["quality_backlog"]) == set(health.BACKLOG_KEYS)
    assert doc["quality_backlog"]["scope"] == "full_table"
