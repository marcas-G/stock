"""T6：post-ingest audit + FINAL 门 + health 发布（Plan DQ-M1）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-6-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §2（分布突变只 WARN/SUSPECT；升级 SYSTEMATIC_ISSUE 才 FAIL）
      §5（... → CANONICAL INGEST → POST-INGEST AUDIT → FINAL PARTITION GATE
           → HEALTH ARTIFACT → PUBLISH RESEARCH-READY）
      §6（Health Artifact 字段逐字，双枚举 health_status/verification_state）
      §7（读取门字段引用；因子/回测 summary 五字段）。

红线（控制者裁定）：
- completeness.status 独立（不得用 coverage 推）；
- 腾讯抽样是唯一外部依赖：fake transport + **断言请求 URL/参数**；偏差 → SUSPECT 不 FAIL；
- 漂移检测只产 WARN/SUSPECT；仅当（漂移 SUSPECT ∧ 跨源/单位/schema 证据）升级
  SYSTEMATIC_ISSUE → FINAL 门判 FAIL；
- FINAL 门复用 Task 4 判定（decide_pre_ingest）并与 PRE-INGEST 取最差；
- health JSON 键集与 §6 完全一致（缺 verification_state / completeness.status → 必红）；
- 发布原子写；raw_lineage.raw_sha256 取自 raw 文件（hashlib 复算）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import polars as pl
import pytest

from data_quality import audit, health, rules

D = dt.date
PART = "2026-09-18"
PREV = "2026-09-17"
TAG = "20260918"
DATASET = "ashare_daily"

SCHEMA = {"code": pl.String, "trade_date": pl.Date, "open": pl.Float64,
          "high": pl.Float64, "low": pl.Float64, "close": pl.Float64}


def _rows(n=100, day=PART, close=10.0, code_prefix="00000"):
    d = D.fromisoformat(day)
    return [{"code": f"{code_prefix}{i:02d}.SZ", "trade_date": d,
             "open": close, "high": close * 1.02, "low": close * 0.98,
             "close": close} for i in range(n)]


def _frame(rows):
    return pl.DataFrame(rows, schema=SCHEMA)


def _row(code="600519.SH", day=PART, **over):
    r = {"code": code, "trade_date": D.fromisoformat(day), "open": 10.0,
         "high": 10.2, "low": 9.8, "close": 10.0}
    r.update(over)
    return r


def _policy():
    return rules.load_policy()


def _fake_transport(payload: dict, calls: list):
    def transport(url, params):
        calls.append((url, dict(params)))
        return json.dumps(payload)

    return transport


def _tencent_payload(symbol="600519.SH", day=PART, close=10.0):
    code, suffix = symbol.split(".")
    market = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[suffix]
    dates = [d.isoformat() for d in
             [D.fromisoformat(day) - dt.timedelta(days=i) for i in range(5, 0, -1)]
             + [D.fromisoformat(day)]]
    # 真实腾讯响应：data 键 = <market><code>（如 sh600519）；列序 date,open,close,high,low,volume
    rows = [[d, "10.0", f"{close}", f"{close + 0.2}", "9.8", "1000"]
            for d in dates]
    return {"code": 0, "data": {f"{market}{code}": {"qfqday": rows}}}


# ── completeness.status 独立于 coverage ─────────────────────────────────
def test_completeness_status_independent_of_coverage():
    c = audit.check_completeness(expected_count=100, actual_count=99)
    assert (c.status, c.expected_count, c.actual_count) == (audit.INCOMPLETE, 100, 99)
    assert c.coverage == pytest.approx(0.99)

    full = audit.check_completeness(expected_count=100, actual_count=100)
    assert full.status == audit.COMPLETE and full.coverage == 1.0

    # coverage>1 不得被当成 COMPLETE（多出的行同样是完整性异常）
    over = audit.check_completeness(expected_count=100, actual_count=101)
    assert over.status == audit.INCOMPLETE and over.coverage == pytest.approx(1.01)

    # 无独立来源 → UNKNOWN（不拿 actual 自洽冒充 COMPLETE = fail-open）
    unknown = audit.check_completeness(expected_count=None, actual_count=101)
    assert unknown.status == audit.UNKNOWN
    assert unknown.expected_count is None and unknown.coverage is None


def test_audit_completeness_unknown_never_passes_final_gate():
    df = _frame(_rows(10))
    m = audit.audit_post_ingest(df, partition=PART, expected_count=None)
    assert m.completeness.status == audit.UNKNOWN
    assert audit.decide_final("PASS", m, _policy()) == audit.UNKNOWN


# ── 腾讯抽样：fake transport + 断言 URL/参数；偏差→SUSPECT 不 FAIL ────────
def test_tencent_sampling_fake_transport_asserts_url_and_params():
    calls: list = []
    tp = _fake_transport(_tencent_payload("600519.SH"), calls)

    df = audit.fetch_tencent_daily("600519.SH", "2026-09-01", PART, transport=tp)
    assert calls, "必须真的发起请求（fake transport 被调用）"
    url, params = calls[0]
    assert url == audit.TENCENT_DAILY_URL
    assert params["param"] == "sh600519,day,2026-09-01,2026-09-18,320,qfq", (
        "腾讯日线参数必须逐字（市场前缀/起止/条数/复权口径）")
    assert df.columns == ["code", "trade_date", "open", "high", "low", "close"]
    assert df["trade_date"].max() == D.fromisoformat(PART)
    assert df.height == 6
    assert df.filter(pl.col("trade_date") == D.fromisoformat(PART))["close"][0] == 10.0


def test_tencent_sample_deviation_is_suspect_not_fail():
    raw = _frame([_row()])
    policy = _policy()

    m = audit.audit_post_ingest(
        _frame(_rows(5)), partition=PART, expected_count=5,
        raw=raw, sample_symbols=["600519.SH"],
        transport=_fake_transport(_tencent_payload("600519.SH"), []), policy=policy)
    assert m.sample.status == audit.OK and m.sample.deviations == 0
    assert m.suspect is False

    m2 = audit.audit_post_ingest(
        _frame(_rows(5)), partition=PART, expected_count=5,
        raw=raw, sample_symbols=["600519.SH"],
        transport=_fake_transport(_tencent_payload("600519.SH", close=11.0), []),
        policy=policy)
    assert m2.sample.status == audit.SUSPECT and m2.sample.deviations == 1
    assert m2.suspect is True
    # 偏差只产 WARN；FINAL 门不得因此 FAIL（设计 §2：跨源偏差 ≠ 数据错误）
    assert all(r.level in (rules.WARN, rules.INFO) for r in m2.results)
    assert audit.decide_final("PASS", m2, policy) != audit.FAIL


# ── 漂移检测：只产 WARN/SUSPECT ──────────────────────────────────────────
def test_drift_detector_only_warn_suspect():
    baseline = _frame(_rows(100, day=PREV, close=10.0))
    same = _frame(_rows(100, day=PART, close=10.0))
    d = audit.detect_drift(same, baseline)
    assert d.status == audit.OK and d.unit_suspect is False
    assert d.ratio == pytest.approx(1.0)

    jumped = _frame(_rows(100, day=PART, close=13.5))          # +35% 均值
    d2 = audit.detect_drift(jumped, baseline)
    assert d2.status == audit.SUSPECT and d2.unit_suspect is False
    assert d2.ratio == pytest.approx(1.35)

    unit = _frame(_rows(100, day=PART, close=1000.0))          # 100× = 单位疑点
    d3 = audit.detect_drift(unit, baseline)
    assert d3.status == audit.SUSPECT and d3.unit_suspect is True


def test_audit_drift_alone_stays_warn_suspect():
    baseline = _frame(_rows(50, day=PREV, close=10.0))
    m = audit.audit_post_ingest(_frame(_rows(50, day=PART, close=10.0)),
                                partition=PART, expected_count=50,
                                baseline=baseline)
    assert m.drift.status == audit.OK

    m2 = audit.audit_post_ingest(_frame(_rows(50, day=PART, close=13.5)),
                                 partition=PART, expected_count=50,
                                 baseline=baseline)
    assert m2.drift.status == audit.SUSPECT
    assert m2.systemic is None, "分布突变单独不得升级 SYSTEMATIC_ISSUE"
    assert all(r.level == rules.WARN for r in m2.results)
    assert audit.decide_final("PASS", m2, _policy()) != audit.FAIL


def test_drift_plus_cross_source_evidence_escalates_systemic_fail():
    baseline = _frame(_rows(50, day=PREV, close=10.0))
    current = _frame(_rows(50, day=PART, close=13.5))
    raw = _frame([_row()])
    policy = _policy()

    m = audit.audit_post_ingest(
        current, partition=PART, expected_count=50, raw=raw, baseline=baseline,
        sample_symbols=["600519.SH"],
        transport=_fake_transport(_tencent_payload("600519.SH", close=11.0), []),
        policy=policy)
    assert m.drift.status == audit.SUSPECT and m.sample.status == audit.SUSPECT
    assert m.systemic is not None, "漂移 + 跨源不一致 = SYSTEMATIC_ISSUE（§2 升级条件）"
    assert "SYSTEMATIC" in m.systemic.rule
    assert audit.decide_final("PASS", m, policy) == audit.FAIL

    # schema metadata 变化同样是升级证据
    m2 = audit.audit_post_ingest(current, partition=PART, expected_count=50,
                                 baseline=baseline, schema_changed=True)
    assert m2.systemic is not None
    assert audit.decide_final("PASS", m2, policy) == audit.FAIL


# ── PK / reconcile / FINAL 门复用 Task 4 ─────────────────────────────────
def test_audit_pk_conflict_and_reconcile_drive_final_gate():
    codes = [f"00000{i}.SZ" for i in range(10)]
    rows = [{"code": c, "trade_date": D.fromisoformat(PART), "open": 10.0,
             "high": 10.1, "low": 9.9, "close": 10.0} for c in codes + codes]
    policy = _policy()

    m = audit.audit_post_ingest(_frame(rows), partition=PART,
                                expected_count=10, reconcile_expected=10)
    assert m.pk_duplicates == 10
    pk = [r for r in m.results if r.rule_id == rules.PK_CONFLICT]
    assert pk and all(r.level == rules.ERROR for r in pk)
    assert m.reconcile_ok is False and m.completeness.status == audit.INCOMPLETE
    assert audit.decide_final("PASS", m, policy) == audit.FAIL

    mismatch = audit.audit_post_ingest(_frame(_rows(9)), partition=PART,
                                       expected_count=10, reconcile_expected=10)
    assert mismatch.pk_duplicates == 0
    assert mismatch.completeness.status == audit.INCOMPLETE
    assert audit.decide_final("PASS", mismatch, policy) == audit.FAIL


def test_audit_full_table_reconcile_split_from_partition_completeness():
    """completeness=分区腿（canonical 帧），reconcile=独立全表计数腿（CLI 生产口径）。"""
    frame = _frame(_rows(10))
    m = audit.audit_post_ingest(frame, partition=PART, expected_count=10,
                                reconcile_expected=100, reconcile_actual=90)
    assert m.completeness.status == audit.COMPLETE, "分区完整性看 canonical 帧行数"
    assert m.completeness.actual_count == 10
    assert m.reconcile_ok is False
    assert "100" in m.reconcile_detail and "90" in m.reconcile_detail


def test_final_gate_reuses_pre_ingest_decision_and_worst_case():
    policy = _policy()
    clean = audit.audit_post_ingest(_frame(_rows(10)), partition=PART,
                                    expected_count=10)
    assert audit.decide_final("PASS", clean, policy) == audit.PASS
    assert audit.decide_final("DEGRADED", clean, policy) == audit.DEGRADED
    assert audit.decide_final("FAIL", clean, policy) == audit.FAIL

    # 1/5001 孤立 PK 冲突：error_rate ∈ (pass.max_error_rate, fail.min_error_rate]
    # → 复用 T4 判定 = DEGRADED（而 completeness 仍 COMPLETE）
    rows = _rows(5000) + [_rows(1)[0]]
    deg = audit.audit_post_ingest(_frame(rows), partition=PART,
                                  expected_count=5001)
    assert deg.completeness.status == audit.COMPLETE
    assert deg.pk_duplicates == 1
    assert audit.decide_final("PASS", deg, policy) == audit.DEGRADED


# ── health artifact：键集与 §6 完全一致 + 原子写 + raw sha ───────────────
SPEC_KEYS = {"dataset_id", "partition", "data_version", "dq_policy_version",
             "health_status", "verification_state", "completeness", "quality",
             "freshness", "rules", "validated_at", "raw_lineage"}
QUALITY_KEYS = {"fatal_count", "error_count", "warning_count",
                "quarantine_count", "error_rate", "systematic_issue",
                "systemic_detail"}


def _publish(tmp_path, **over):
    raw = tmp_path / "daily_fact.parquet"
    _frame(_rows(3)).write_parquet(raw)
    kw = dict(
        root=tmp_path, dataset_id=DATASET, partition=PART,
        data_version="v20260918_01", dq_policy_version=rules.POLICY_VERSION,
        health_status="PASS", verification_state="VERIFIED",
        completeness={"status": "COMPLETE", "expected_count": 3,
                      "actual_count": 3, "coverage": 1.0},
        quality={"fatal_count": 0, "error_count": 0, "warning_count": 0,
                 "quarantine_count": 0, "error_rate": 0.0,
                 "systematic_issue": False, "systemic_detail": None},
        rules_counts={"OHLC_INVALID": 0, "PK_CONFLICT": 0, "UNIT_SUSPECT": 0},
        latest_trade_date=PART, raw_path=raw, source_version="pan-2026-09-18",
        validated_at="2026-09-18T21:00:00+08:00")
    kw.update(over)
    return health.publish_health(**kw)


def test_health_doc_keys_exactly_match_spec_section6(tmp_path):
    p = _publish(tmp_path)
    assert p == tmp_path / "health" / DATASET / f"{PART}.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert set(doc) == SPEC_KEYS, "health 键集必须与 §6 完全一致（多/少都不行）"
    assert set(doc["completeness"]) == {"status", "expected_count",
                                        "actual_count", "coverage"}
    assert set(doc["quality"]) == QUALITY_KEYS
    assert set(doc["freshness"]) == {"latest_trade_date"}
    assert set(doc["raw_lineage"]) == {"source_version", "raw_sha256"}
    assert doc["health_status"] in health.HEALTH_STATUSES
    assert doc["verification_state"] in health.VERIFICATION_STATES
    assert doc["completeness"]["status"] in health.COMPLETENESS_STATUSES
    assert doc["dataset_id"] == DATASET and doc["partition"] == PART

    # 双枚举独立：LEGACY_UNVERIFIED 与 UNKNOWN 组合必须可表达，不得互相映射
    p2 = _publish(tmp_path, health_status="UNKNOWN",
                  verification_state="LEGACY_UNVERIFIED", partition="2026-09-17")
    doc2 = json.loads(p2.read_text(encoding="utf-8"))
    assert (doc2["health_status"], doc2["verification_state"]) == (
        "UNKNOWN", "LEGACY_UNVERIFIED")


def test_publish_health_rejects_unknown_enum_values(tmp_path):
    with pytest.raises(ValueError):
        _publish(tmp_path, health_status="OK")
    with pytest.raises(ValueError):
        _publish(tmp_path, verification_state="TRUSTED")
    with pytest.raises(ValueError):
        _publish(tmp_path, completeness={"status": "DONE", "expected_count": 1,
                                         "actual_count": 1, "coverage": 1.0})


def test_publish_health_atomic_write_and_raw_sha256(tmp_path, monkeypatch):
    raw = tmp_path / "daily_fact.parquet"
    raw.write_bytes(b"raw-bytes")
    p = health.publish_health(
        root=tmp_path, dataset_id=DATASET, partition=PART,
        data_version="v1", dq_policy_version=rules.POLICY_VERSION,
        health_status="PASS", verification_state="VERIFIED",
        completeness={"status": "COMPLETE", "expected_count": 1,
                      "actual_count": 1, "coverage": 1.0},
        quality={"fatal_count": 0, "error_count": 0, "warning_count": 0,
                 "quarantine_count": 0, "error_rate": 0.0,
                 "systematic_issue": False, "systemic_detail": None},
        rules_counts={}, latest_trade_date=PART, raw_path=raw)
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["raw_lineage"]["raw_sha256"] == hashlib.sha256(b"raw-bytes").hexdigest()
    assert doc["raw_lineage"]["source_version"] is None
    assert not list(p.parent.glob("*.tmp")), "发布必须原子（无 .tmp 残留）"

    # 半写不可见：replace 失败 → 已发布文件保持原样
    before = p.read_text(encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(health.os, "replace", boom)
    with pytest.raises(OSError):
        _publish(tmp_path)
    monkeypatch.undo()
    assert p.read_text(encoding="utf-8") == before, "失败不得破坏已发布 health"


def test_publish_health_cli_missing_staging_exits_2(tmp_path, capsys):
    rc = health.main(["publish", "--root", str(tmp_path), "--run-tag", TAG,
                      "--partition", PART])
    assert rc == 2
    assert "staging" in capsys.readouterr().err


# ── pan_update daily 链接线：audit/publish 必须在 ingest 之后 ─────────────
def test_pan_update_daily_chain_publishes_health_after_ingest():
    from pan_update import config, stages
    chain = stages.STAGE_CHAINS["daily"]
    names = [Path(c[1]).name for c in chain]
    assert names == ["import_daily.py", "pipeline.py", "ingest_daily.py",
                     "derive_stk_limit.py", "adj_backfill.py", "health.py"], (
        "post-ingest audit + health 发布必须在 ingest（canonical 写入）之后")
    venv = str(stages._VENV_PYTHON)
    tag = stages._DAILY_RUN_TAG
    assert chain[-1] == [venv, str(stages._TOOLS / "data_quality" / "health.py"),
                         "publish", "--partition", "latest", "--run-tag", tag]
    assert Path(chain[-1][1]).is_file()
