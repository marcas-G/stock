"""T7：读取门 require_dataset + 产物记录（Plan DQ-M1）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-7-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §7（Readable = HealthValid AND FreshEnough AND Complete；默认 fail-closed
          accept_quality=("PASS",)；DEGRADED 必须显式 opt-in + 写 Experiment
          Manifest 五字段；FAIL 不可 opt-in；UNKNOWN 仅 LEGACY 过渡；因子/回测
          summary 追加 dataset_version/quality_status/quarantined_rows/coverage/
          cleaning_policy_version）
      §6（双枚举 health_status / verification_state）+ §8（过渡条款）。

红线（控制者裁定）：
- 只读 health JSON：**不得重跑校验/重算 OHLC**（spy 断言 validators 未被调用）；
- completeness.status 独立检查（不得用 coverage 推）；max_staleness 独立；
- 固定测试根（tmp_path），不读生产 DATA_ROOT。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import polars as pl
import pytest

from factorlab.adapters.read.health import (DatasetGate, DatasetQualityError,
                                            parse_max_staleness, require_dataset)
from factorlab.app.context import RunContext
from factorlab.app.evaluate import evaluate_run, publish_run
from factorlab.core.engine.compute import FactorResult
from factorlab.core.spec import FactorSpec, UniverseSpec

D = dt.date
PART = "2026-09-18"
DATASET = "ashare_daily"


# ── health JSON fixture（§6 全字段）───────────────────────────────────────
def _doc(*, status="PASS", verification="VERIFIED", completeness="COMPLETE",
         latest=None, coverage=1.0, quality=None, partition=PART,
         data_version="v20260918_01", policy_version="daily-v1"):
    return {
        "dataset_id": DATASET,
        "partition": partition,
        "data_version": data_version,
        "dq_policy_version": policy_version,
        "health_status": status,
        "verification_state": verification,
        "completeness": {"status": completeness, "expected_count": 5231,
                         "actual_count": 5231, "coverage": coverage},
        "quality": quality or {"fatal_count": 0, "error_count": 1,
                               "warning_count": 4, "quarantine_count": 1,
                               "error_rate": 0.00019, "systematic_issue": False,
                               "systemic_detail": None},
        "freshness": {"latest_trade_date": latest or partition},
        "rules": {"OHLC_INVALID": 0, "PK_CONFLICT": 1},
        "validated_at": "2026-09-18T21:00:00+08:00",
        "raw_lineage": {"source_version": "pan-2026-09-18", "raw_sha256": "ab" * 32},
    }


def _write(root: Path, doc: dict, *, partition=None) -> Path:
    p = Path(root) / "health" / DATASET / f"{partition or doc['partition']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


# ── 拒绝矩阵 ─────────────────────────────────────────────────────────────
def test_pass_passes_and_returns_gate(tmp_path):
    _write(tmp_path, _doc())
    gate = require_dataset(DATASET, PART, root=tmp_path)
    assert isinstance(gate, DatasetGate)
    assert (gate.dataset, gate.partition) == (DATASET, PART)
    assert gate.health_status == "PASS"
    assert gate.verification_state == "VERIFIED"
    assert gate.summary_fields() == {
        "dataset_version": "v20260918_01",
        "quality_status": "PASS",
        "quarantined_rows": 1,
        "coverage": 1.0,
        "cleaning_policy_version": "daily-v1",
    }


def test_degraded_rejected_by_default_with_actionable_message(tmp_path):
    _write(tmp_path, _doc(status="DEGRADED"))
    with pytest.raises(DatasetQualityError) as ei:
        require_dataset(DATASET, PART, root=tmp_path)
    msg = str(ei.value)
    assert DATASET in msg and PART in msg and "DEGRADED" in msg
    assert "accept_quality" in msg and "opt-in" in msg


def test_degraded_opt_in_passes_and_writes_manifest_five_fields(tmp_path):
    _write(tmp_path, _doc(status="DEGRADED"))
    gate = require_dataset(DATASET, PART, root=tmp_path,
                           accept_quality=("PASS", "DEGRADED"),
                           override_reason="探索性研究放宽（非验收）")
    assert gate.health_status == "DEGRADED"

    manifest_path = tmp_path / "manifest" / DATASET / f"{PART}.json"
    assert gate.manifest_path == manifest_path and manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == {"dataset_quality", "quality_issues",
                             "affected_partitions", "dq_policy_version",
                             "override_reason"}, "Experiment Manifest 必须五字段"
    assert manifest["dataset_quality"] == "DEGRADED"
    assert manifest["quality_issues"] == _doc(status="DEGRADED")["quality"]
    assert manifest["affected_partitions"] == [PART]
    assert manifest["dq_policy_version"] == "daily-v1"
    assert manifest["override_reason"] == "探索性研究放宽（非验收）"


def test_degraded_opt_in_without_reason_stays_fail_closed(tmp_path):
    _write(tmp_path, _doc(status="DEGRADED"))
    with pytest.raises(DatasetQualityError):
        require_dataset(DATASET, PART, root=tmp_path,
                        accept_quality=("PASS", "DEGRADED"))
    with pytest.raises(ValueError):
        require_dataset(DATASET, PART, root=tmp_path, accept_quality=("PASS", "FAIL"),
                        override_reason="x")


def test_strict_pass_only_scenario_forbids_opt_in(tmp_path):
    """§7 场景强制（正式 OOS/验收/Replay/基准）→ PASS-only：strict 下 opt-in 非法。"""
    _write(tmp_path, _doc(status="DEGRADED"))
    with pytest.raises(ValueError, match="strict"):
        require_dataset(DATASET, PART, root=tmp_path, strict=True,
                        accept_quality=("PASS", "DEGRADED"),
                        override_reason="验收场景不允许")
    _write(tmp_path, _doc(), partition="2026-09-17")
    gate = require_dataset(DATASET, "2026-09-17", root=tmp_path, strict=True)
    assert gate.health_status == "PASS"


def test_fail_never_opt_in(tmp_path):
    _write(tmp_path, _doc(status="FAIL"))
    with pytest.raises(ValueError, match="FAIL"):
        require_dataset(DATASET, PART, root=tmp_path,
                        accept_quality=("PASS", "FAIL"), override_reason="x")
    with pytest.raises(DatasetQualityError):
        require_dataset(DATASET, PART, root=tmp_path,
                        accept_quality=("PASS", "DEGRADED", "UNKNOWN"),
                        override_reason="x")


def test_unknown_legacy_only(tmp_path):
    _write(tmp_path, _doc(status="UNKNOWN", verification="LEGACY_UNVERIFIED"))
    with pytest.raises(DatasetQualityError):
        require_dataset(DATASET, PART, root=tmp_path)
    gate = require_dataset(DATASET, PART, root=tmp_path,
                           accept_quality=("PASS", "UNKNOWN"),
                           override_reason="存量过渡（T8 打标前）")
    assert gate.health_status == "UNKNOWN"
    assert gate.manifest_path is not None and gate.manifest_path.is_file()

    # UNKNOWN 但非 LEGACY（VERIFIED）→ 语义不成立，仍拒
    _write(tmp_path, _doc(status="UNKNOWN", verification="VERIFIED"),
           partition="2026-09-17")
    with pytest.raises(DatasetQualityError):
        require_dataset(DATASET, "2026-09-17", root=tmp_path,
                        accept_quality=("PASS", "UNKNOWN"),
                        override_reason="x")


def test_missing_health_artifact_rejected_with_guidance(tmp_path):
    with pytest.raises(DatasetQualityError) as ei:
        require_dataset(DATASET, PART, root=tmp_path)
    msg = str(ei.value)
    assert DATASET in msg and PART in msg and "LEGACY_UNVERIFIED" in msg
    assert "health" in msg


# ── freshness / completeness 独立 ────────────────────────────────────────
def test_max_staleness_is_independent_check(tmp_path):
    _write(tmp_path, _doc(latest="2026-09-08"))     # 数据声明落后 10 天
    with pytest.raises(DatasetQualityError) as ei:
        require_dataset(DATASET, PART, root=tmp_path, max_staleness="1d")
    assert "stale" in str(ei.value).lower() or "陈旧" in str(ei.value)

    gate = require_dataset(DATASET, PART, root=tmp_path, max_staleness="30d")
    assert gate.health_status == "PASS"

    assert parse_max_staleness("3d") == dt.timedelta(days=3)
    with pytest.raises(ValueError):
        parse_max_staleness("1w")


def test_completeness_status_independent_of_coverage(tmp_path):
    # coverage≈1 但 status=INCOMPLETE → 拒（不得用 coverage 推）
    _write(tmp_path, _doc(completeness="INCOMPLETE", coverage=0.9999))
    with pytest.raises(DatasetQualityError) as ei:
        require_dataset(DATASET, PART, root=tmp_path)
    assert "INCOMPLETE" in str(ei.value) or "completeness" in str(ei.value)

    _write(tmp_path, _doc(completeness="UNKNOWN", coverage=0.9999),
           partition="2026-09-17")
    with pytest.raises(DatasetQualityError):
        require_dataset(DATASET, "2026-09-17", root=tmp_path)


# ── 禁止行为：门只读 health JSON，不重算 OHLC / 不重跑校验 ──────────────
def test_require_dataset_reads_health_json_only(tmp_path, monkeypatch):
    """禁止行为：读取门（含 factor run 入口）不得重跑校验/重算 OHLC。"""
    import sys
    tools = Path(__file__).resolve().parents[1] / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    import data_quality.validators as validators

    def boom(*_a, **_k):
        raise AssertionError("读取门不得重跑行级校验/重算 OHLC")

    monkeypatch.setattr(validators, "validate_daily", boom)

    as_of = _panel()["date"].max().isoformat()
    _write(tmp_path, _doc(partition=as_of), partition=as_of)
    gate = require_dataset(DATASET, as_of, root=tmp_path)
    assert gate.health_status == "PASS"

    outcome = evaluate_run(_result(), _spec(), RunContext(output_dir=tmp_path / "run"),
                           dataset=DATASET, dq_root=tmp_path)
    assert outcome.dataset_gate is not None
    assert outcome.dataset_gate.health_status == "PASS"


# ── 接线：evaluate_run 入口调用 + summary 五字段 + 默认拒绝 ──────────────
def _panel(n=200):
    rows = []
    for i in range(n):
        day = D(2024, 1, 2) + dt.timedelta(days=i // 20)
        rows.append({"date": day, "code": f"{i % 20:06d}",
                     "signal": 0.1 + 0.001 * i,
                     "forward_return_1d": 0.001 * (i % 7),
                     "forward_return_5d": 0.002 * (i % 7)})
    return pl.DataFrame(rows)


def _spec(name="dq_gate_probe"):
    return FactorSpec(name=name, category="custom", direction=1,
                      universe=UniverseSpec(codes=["000001.SZ"]),
                      formula="signal = close")


def _result(panel=None):
    return FactorResult(spec=_spec(), signal_artifact=None,
                        label_artifact=None, panel=panel or _panel())


def test_evaluate_run_gate_blocked_by_default_and_opt_in_records_summary(tmp_path):
    # health 落在 panel 最新日 2024-01-11 上（§6 doc 的 partition 用该日）
    as_of = _panel()["date"].max().isoformat()
    _write(tmp_path, _doc(partition=as_of, status="DEGRADED"), partition=as_of)
    spec = _spec()
    result = _result()
    ctx = RunContext(output_dir=tmp_path / "run")

    with pytest.raises(DatasetQualityError):
        evaluate_run(result, spec, ctx, dataset=DATASET, dq_root=tmp_path)

    outcome = evaluate_run(result, spec, ctx, dataset=DATASET, dq_root=tmp_path,
                           accept_quality=("PASS", "DEGRADED"),
                           override_reason="探索性研究")
    publish_run(result, outcome, ctx)
    assert result.summary["data_quality"] == {
        "dataset_version": "v20260918_01",
        "quality_status": "DEGRADED",
        "quarantined_rows": 1,
        "coverage": 1.0,
        "cleaning_policy_version": "daily-v1",
    }
    assert outcome.dataset_gate is not None


def test_run_backtest_gate_uses_decision_window_and_records_usage(tmp_path, monkeypatch):
    import factorlab.app.backtest.backtest as bt_mod
    from factorlab.core.domain.portfolio import (TargetPortfolio,
                                                 TargetPortfolioMeta)
    from factorlab.core.domain.timing import DEFAULT_EOD_SIGNAL_TIMING

    d1, d2 = D(2026, 9, 17), D(2026, 9, 18)
    frame = pl.DataFrame(
        {"decision_date": [d1, d2], "code": ["000001.SZ", "000001.SZ"],
         "target_weight": [1.0, 1.0]},
        schema={"decision_date": pl.Date, "code": pl.String,
                "target_weight": pl.Float64})
    target = TargetPortfolio(
        frame=frame, decision_dates=(d1, d2),
        meta=TargetPortfolioMeta(strategy_name="probe", source_signal_name="probe",
                                 source_timing=DEFAULT_EOD_SIGNAL_TIMING,
                                 gross_exposure=1.0))

    calls = []

    def fake_gate(dataset, as_of, **kw):
        calls.append({"dataset": dataset, "as_of": as_of, **kw})
        return DatasetGate(
            dataset=dataset, partition=as_of, health_status="DEGRADED",
            verification_state="VERIFIED", data_version="v1",
            dq_policy_version="daily-v1", completeness_status="COMPLETE",
            quarantined_rows=3, coverage=1.0, max_staleness=kw.get("max_staleness", "1d"),
            override_reason=kw.get("override_reason"),
            manifest_path=None)

    monkeypatch.setattr(bt_mod, "require_dataset", fake_gate)
    rd = _MemoryRead()
    with pytest.raises(Exception):
        bt_mod.run_backtest(target, _execution_spec(), rd, dataset=DATASET,
                            dq_root=tmp_path, accept_quality=("PASS", "DEGRADED"),
                            override_reason="探索性研究")
    assert calls and calls[0]["dataset"] == DATASET
    assert calls[0]["as_of"] == d2.isoformat(), "as_of 必须取决策窗口末端"

    usage = tmp_path / "manifest" / DATASET / f"{d2.isoformat()}.backtest.json"
    assert usage.is_file(), "回测产物必须记录五字段（usage sidecar）"
    rec = json.loads(usage.read_text(encoding="utf-8"))
    assert set(rec) == {"dataset_version", "quality_status", "quarantined_rows",
                        "coverage", "cleaning_policy_version"}
    assert rec["quality_status"] == "DEGRADED" and rec["quarantined_rows"] == 3


def _execution_spec():
    from factorlab.core.execution.spec import ExecutionSpec
    return ExecutionSpec.model_validate({"initial_cash": 1_000_000.0})


class _MemoryRead:
    backend = "memory"

    def query_df(self, sql, params=None, settings=None):
        return pl.DataFrame({"x": [1]})

    def query_rows(self, sql, params=None):
        return [(1,)]

    def command(self, sql, params=None):
        return None

    def tables(self):
        return set()

    def columns(self, table):
        return set()

    def close(self):
        pass
