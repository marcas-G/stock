"""T8：daily 全史体检（只审不改）+ 存量打标（Plan DQ-M1）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-8-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §7（读取门三状态 + 过渡条款：存量无 health → LEGACY_UNVERIFIED/UNKNOWN）
      §8（全史体检只审不改：问题清单按日期/股票/数据集 + 影响分析；
          三状态 VERIFIED/LEGACY_UNVERIFIED/KNOWN_ISSUE；不首轮全史重跑）
      §10 M1 验收（daily 全史体检打标）。

红线（控制者裁定）：
- **只审不改**：禁止写 canonical/CH、禁止改任何 parquet/raw；只允许写
  ``data/health/**`` 的 verification 标记与 ``governance/evidence/**`` 报告；
- 存量分区（无 health 文件）→ ``verification_state=LEGACY_UNVERIFIED,
  health_status=UNKNOWN``；已由新链发布的 health 文件**不得覆盖**（不覆盖/合并）；
- 问题清单：按日期/字段/规则计数；影响分析骨架含高影响项（ADJ_NEGATIVE/
  退市 adj/circ_mv/跨频）；
- 分块全史扫描必须跨块正确（重叠一个交易日防 chunk 边界漏检）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import polars as pl
import pytest

from data_quality import health, historical_audit, rules

D = dt.date
DAY1, DAY2, DAY3 = D(2026, 9, 16), D(2026, 9, 17), D(2026, 9, 18)
DATASET = "ashare_daily"

_SCHEMA = {
    "code": pl.String, "trade_date": pl.Date,
    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Float64, "amount": pl.Float64,
    "adj_factor": pl.Float64, "fq_factor": pl.Float64,
    "div_cash": pl.Float64, "div_bonus": pl.Float64,
    "div_transfer": pl.Float64, "rights_num": pl.Float64,
    "float_shares": pl.Float64,
}


def _row(code="000001.SZ", day=DAY1, **over):
    r = {"code": code, "trade_date": day, "open": 10.0, "high": 10.2,
         "low": 9.8, "close": 10.0, "volume": 1000.0, "amount": 10200.0,
         "adj_factor": 1.0, "fq_factor": 1.0, "div_cash": 0.0, "div_bonus": 0.0,
         "div_transfer": 0.0, "rights_num": 0.0, "float_shares": 10000.0}
    r.update(over)
    return r


def _frame(rows):
    return pl.DataFrame(rows, schema=_SCHEMA)


def _raw_file(tmp_path, rows, name="daily_fact.parquet") -> Path:
    p = Path(tmp_path) / name
    _frame(rows).write_parquet(p)
    return p


def _issues(res) -> dict[tuple, int]:
    return {(r["trade_date"], r["rule_id"], r["level"], r["field"]): r["count"]
            for r in res.issue_counts.iter_rows(named=True)}


def _tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix():
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def _published_pass(root: Path, raw: Path, partition: str = DAY1) -> Path:
    return health.publish_health(
        root=root, dataset_id=DATASET, partition=partition.isoformat(),
        data_version="v20260919_01", dq_policy_version=rules.POLICY_VERSION,
        health_status="PASS", verification_state="VERIFIED",
        completeness={"status": "COMPLETE", "expected_count": 1,
                      "actual_count": 1, "coverage": 1.0},
        quality={"fatal_count": 0, "error_count": 0, "warning_count": 0,
                 "quarantine_count": 0, "error_rate": 0.0,
                 "systematic_issue": False, "systemic_detail": None},
        rules_counts={}, latest_trade_date=partition.isoformat(), raw_path=raw)


# ── Step 1：问题清单（按日期/字段/规则计数）──────────────────────────────
def test_issue_counts_by_date_field_rule(tmp_path):
    raw = _raw_file(tmp_path, [
        _row("000001.SZ", DAY1), _row("000002.SZ", DAY1), _row("000003.SZ", DAY1),
        _row("000004.SZ", DAY1, open=10.5, amount=10000.0),  # OHLC_INVALID（H<O）
        _row("000005.SZ", DAY1, adj_factor=-1.0),          # ADJ_NEGATIVE
        _row("000001.SZ", DAY2), _row("000002.SZ", DAY2),
        _row("000003.SZ", DAY2, amount=None),              # MISSING_VALUE
        _row("000004.SZ", DAY2, fq_factor=float("inf")),   # NONFINITE_VALUE
    ])
    res = historical_audit.audit_history(
        raw, root=tmp_path / "data", report_dir=tmp_path / "report",
        mark_legacy=True)

    issues = _issues(res)
    assert issues[(DAY1.isoformat(), rules.OHLC_INVALID, rules.ERROR,
                   historical_audit.MULTI_FIELD)] == 1
    assert issues[(DAY1.isoformat(), rules.ADJ_NEGATIVE, rules.ERROR,
                   "adj_factor")] == 1
    assert issues[(DAY2.isoformat(), rules.MISSING_VALUE, rules.WARN,
                   "amount")] == 1
    assert issues[(DAY2.isoformat(), rules.NONFINITE_VALUE, rules.ERROR,
                   "fq_factor")] == 1

    assert res.totals_by_rule[rules.OHLC_INVALID] == 1
    assert res.totals_by_rule[rules.MISSING_VALUE] == 1
    assert res.totals_by_field["adj_factor"] == 1
    assert res.totals_by_field["amount"] == 1
    assert res.partition_count == 2 and res.row_count == 9
    assert res.raw_sha256 == hashlib.sha256(raw.read_bytes()).hexdigest(), (
        "raw_lineage 必须实读 raw（不得硬编码/缺失）")

    p1 = res.summary["per_partition"][DAY1.isoformat()]
    p2 = res.summary["per_partition"][DAY2.isoformat()]
    assert (p1["rows"], p1["error_count"], p1["quarantine_count"]) == (5, 2, 2)
    assert (p2["rows"], p2["warning_count"], p2["error_count"]) == (4, 2, 1), (
        "DAY2 WARN = MISSING_VALUE(amount) + ADJ_FACTOR_CA_MISMATCH(000004 fq 变化)")
    assert res.summary["top_error_dates"][0]["trade_date"] in (
        DAY1.isoformat(), DAY2.isoformat())


# ── 禁止行为：只审不改（报告/health 标记之外零写入）─────────────────────
def test_forbidden_no_parquet_no_canonical_writes(tmp_path, monkeypatch):
    raw = _raw_file(tmp_path, [_row("000001.SZ", DAY1, adj_factor=-1.0),
                               _row("000002.SZ", DAY2)])
    raw_hash = hashlib.sha256(raw.read_bytes()).hexdigest()

    sandbox = tmp_path / "sandbox"
    (sandbox / "canonical").mkdir(parents=True)
    (sandbox / "canonical" / "daily.parquet").write_bytes(b"canonical-sentinel")
    (sandbox / "staging" / DATASET / "20260919").mkdir(parents=True)
    (sandbox / "staging" / DATASET / "20260919" / "daily_fact.parquet").write_bytes(
        b"staging-sentinel")
    before = _tree(sandbox)

    def _boom(*_a, **_k):
        raise AssertionError("全史体检不得写任何 parquet（只审不改）")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", _boom)

    res = historical_audit.audit_history(
        raw, root=sandbox, report_dir=tmp_path / "report", mark_legacy=True)

    after = _tree(sandbox)
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == raw_hash, (
        "审计不得改 raw parquet")
    for rel, sha in before.items():
        assert after.get(rel) == sha, f"审计不得改动既有文件：{rel}"
    new_files = set(after) - set(before)
    assert new_files and all(rel.startswith("health/") for rel in new_files), (
        f"审计只允许写 health 标记：{sorted(new_files)}")
    assert res.legacy["marked"] == 2


# ── 存量打标：LEGACY_UNVERIFIED/UNKNOWN + 不覆盖已发布 health + 合并幂等 ──
def test_legacy_marks_do_not_overwrite_published_health(tmp_path):
    rows = [_row("000001.SZ", DAY1), _row("000001.SZ", DAY2)]
    raw = _raw_file(tmp_path, rows)
    root = tmp_path / "data"
    published = _published_pass(root, raw, DAY1)
    published_bytes = published.read_bytes()

    res = historical_audit.audit_history(raw, root=root, mark_legacy=True)
    assert res.legacy["marked"] == 1 and res.legacy["existing"] == 1

    assert published.read_bytes() == published_bytes, (
        "新链已发布的 VERIFIED health 不得被存量打标覆盖")
    doc1 = json.loads(published.read_text(encoding="utf-8"))
    assert (doc1["health_status"], doc1["verification_state"]) == (
        "PASS", "VERIFIED")

    day2 = root / "health" / DATASET / f"{DAY2.isoformat()}.json"
    doc2 = json.loads(day2.read_text(encoding="utf-8"))
    assert (doc2["health_status"], doc2["verification_state"]) == (
        "UNKNOWN", "LEGACY_UNVERIFIED"), "存量无 health 分区按 §7 过渡条款打标"
    assert doc2["completeness"]["status"] == "UNKNOWN"
    assert doc2["partition"] == DAY2.isoformat()
    assert set(doc2) == set(health.TOP_KEYS)

    summary = json.loads((root / "health" / DATASET / "summary.json")
                         .read_text(encoding="utf-8"))
    entries = {e["partition"]: e for e in summary["partitions"]}
    assert (entries[DAY1.isoformat()]["health_status"],
            entries[DAY1.isoformat()]["verification_state"]) == ("PASS", "VERIFIED")
    assert (entries[DAY2.isoformat()]["health_status"],
            entries[DAY2.isoformat()]["verification_state"]) == (
        "UNKNOWN", "LEGACY_UNVERIFIED")

    # 幂等：重跑不再标、不再改任何文件（合并策略）
    snapshot = _tree(root)
    res2 = historical_audit.audit_history(raw, root=root, mark_legacy=True)
    assert res2.legacy == {"marked": 0, "existing": 2}
    assert _tree(root) == snapshot


# ── 影响分析骨架（高影响项从实测计数生成）───────────────────────────────
def test_impact_analysis_measured_and_has_high_impact_categories(tmp_path):
    delisted = pl.DataFrame({"code": ["000005.SZ"],
                             "last_trade_date": [D(2024, 3, 5)]})
    rows = [
        _row("000001.SZ", DAY1),
        _row("600003.SH", DAY1, adj_factor=-1.0),           # ADJ_NEGATIVE
        _row("000005.SZ", DAY1, adj_factor=None),           # 退市股 adj 缺失
        _row("600001.SH", DAY1, float_shares=None),         # circ_mv 代用证据
        _row("600002.SH", DAY1, float_shares=0.0),
    ]
    raw = _raw_file(tmp_path, rows)
    delisted.write_parquet(Path(raw).with_name("delisted_codes.parquet"))

    res = historical_audit.audit_history(
        raw, root=tmp_path / "data", report_dir=tmp_path / "report")

    imp = {c["id"]: c for c in res.impact["categories"]}
    assert imp["ADJ_NEGATIVE"]["rows"] == 1
    assert imp["ADJ_NEGATIVE"]["affected_partitions"] == 1
    assert imp["delisted_adj"]["delisted_codes"] == 1
    assert imp["delisted_adj"]["rows_adj_missing"] >= 1
    assert imp["circ_mv"]["float_shares_missing_rows"] == 1
    assert imp["circ_mv"]["float_shares_zero_rows"] == 1
    assert imp["cross_frequency"]["status"] == "deferred_to_M2"

    rep = tmp_path / "report"
    assert (rep / "issue_counts.csv").is_file()
    assert (rep / "summary.json").is_file()
    assert (rep / "run.json").is_file()
    md = (rep / "impact-analysis.md").read_text(encoding="utf-8")
    for anchor in ("ADJ_NEGATIVE", "circ_mv", "退市", "跨频"):
        assert anchor in md, f"影响分析骨架必须覆盖 {anchor}"


# ── 分块扫描跨块正确（chunk 边界不得漏检）───────────────────────────────
def test_chunk_overlap_detects_cross_date_rule_at_boundary(tmp_path):
    rows = [_row("600519.SH", DAY1, fq_factor=1.0),
            _row("600519.SH", DAY2, fq_factor=1.0),
            _row("600519.SH", DAY3, fq_factor=2.0)]   # 因子变化日无 CA 事件
    raw = _raw_file(tmp_path, rows)

    res = historical_audit.audit_history(
        raw, root=tmp_path / "data", mark_legacy=False, chunk_dates=2)

    issues = _issues(res)
    assert issues[(DAY3.isoformat(), rules.ADJ_FACTOR_CA_MISMATCH, rules.WARN,
                   "fq_factor")] == 1, (
        "第二块首日的变化必须借重叠日（DAY2）检出，不得因分块漏掉")
    assert res.partition_count == 3 and res.row_count == 3


def test_chunk_boundary_suspension_gap_uses_symbol_carry(tmp_path):
    """分块边界的停牌缺口：重叠日无票行情时，跨块 prev 必须回溯该票 carry 行。

    与全表一次扫描的 ``shift(1).over(symbol)`` 语义对齐——否则停牌跨块的票
    会在块首丢失一次因子变化检测（真跑实测少 205 次命中）。
    """
    days = [D(2026, 9, 16) + dt.timedelta(days=i) for i in range(5)]
    d1, d4 = days[0], days[3]
    rows = [_row("600519.SH", d1, fq_factor=1.0),
            _row("600519.SH", d4, fq_factor=2.0)]        # 中间 3 日停牌
    rows += [_row("000001.SZ", d, ) for d in days]        # 让 5 个交易日都存在
    raw = _raw_file(tmp_path, rows)

    res = historical_audit.audit_history(
        raw, root=tmp_path / "data", mark_legacy=False, chunk_dates=2)

    issues = _issues(res)
    assert issues[(d4.isoformat(), rules.ADJ_FACTOR_CA_MISMATCH, rules.WARN,
                   "fq_factor")] == 1, (
        "票在重叠日无行情时，块首变化仍必须借上一块 carry 行检出")
    assert res.partition_count == 5 and res.row_count == 7


# ── CLI ─────────────────────────────────────────────────────────────────
def test_cli_audit_writes_report_and_marks(tmp_path, capsys):
    raw = _raw_file(tmp_path, [_row("000001.SZ", DAY1), _row("000002.SZ", DAY2)])
    root, rep = tmp_path / "data", tmp_path / "report"
    rc = historical_audit.main(["audit", "--raw", str(raw), "--root", str(root),
                                "--report-dir", str(rep)])
    assert rc == 0
    assert (rep / "issue_counts.csv").is_file()
    assert (root / "health" / DATASET / f"{DAY2.isoformat()}.json").is_file()
    assert DAY2.isoformat() in capsys.readouterr().out

    root2, rep2 = tmp_path / "data2", tmp_path / "report2"
    rc2 = historical_audit.main(["audit", "--raw", str(raw), "--root", str(root2),
                                 "--report-dir", str(rep2), "--no-mark-legacy"])
    assert rc2 == 0 and not (root2 / "health").exists()

    rc3 = historical_audit.main(["audit", "--raw", str(tmp_path / "nope.parquet")])
    assert rc3 == 2


def test_refresh_summary_skips_corrupt_files_and_keeps_entries(tmp_path):
    """批量打标的 summary 重建：坏文件不阻断汇总，合法条目（含 VERIFIED）保留。"""
    doc = health.build_health_doc(
        dataset_id=DATASET, partition=DAY1.isoformat(), data_version="v1",
        dq_policy_version=rules.POLICY_VERSION, health_status="PASS",
        verification_state="VERIFIED",
        completeness={"status": "COMPLETE", "expected_count": 1,
                      "actual_count": 1, "coverage": 1.0},
        quality={"fatal_count": 0, "error_count": 0, "warning_count": 0,
                 "quarantine_count": 0, "error_rate": 0.0,
                 "systematic_issue": False, "systemic_detail": None},
        rules_counts={})
    p = health.write_health_artifact(tmp_path, doc)
    assert p == tmp_path / "health" / DATASET / f"{DAY1.isoformat()}.json"

    health_dir = p.parent
    (health_dir / "broken.json").write_text("{not json", encoding="utf-8")
    (health_dir / "no-partition.json").write_text('{"x": 1}', encoding="utf-8")
    health.refresh_dataset_summary(health_dir)
    summary = json.loads((health_dir / "summary.json").read_text(encoding="utf-8"))
    assert [e["partition"] for e in summary["partitions"]] == [DAY1.isoformat()]
    assert summary["dataset_id"] == DATASET
    assert summary["partitions"][0]["health_status"] == "PASS"


def test_audit_rejects_bad_range_and_missing_raw(tmp_path):
    raw = _raw_file(tmp_path, [_row("000001.SZ", DAY1)])
    with pytest.raises(ValueError):
        historical_audit.audit_history(raw, root=tmp_path / "d", start="2026-01-01",
                                       end="2025-01-01")
    with pytest.raises(ValueError):
        historical_audit.audit_history(tmp_path / "missing.parquet",
                                       root=tmp_path / "d")
