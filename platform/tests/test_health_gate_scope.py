"""R37 范围收窄（2026-09-20 用户裁定）：读取门范围外拒绝（addendum §3）。

规格：knowledge/design/platform/specs/2026-09-20-dq-scope-cut-addendum.md §3：
- `partition < scope.MIN_TRADE_DATE` → DatasetQualityError，文案显式：
  早于数据集范围（1996-01-01，用户裁定 2026-09-20），不属 UNKNOWN/LEGACY 过渡条款；
- **无论 health 文件是否存在**（范围外一律拒，不得走 MISSING/LEGACY 语义）；
- scope 内逻辑不变（PASS / DEGRADED opt-in / UNKNOWN LEGACY 显式过渡 / FAIL 拒绝）。

单一事实源：`factorlab.core.scope`（本文件 monkeypatch 常量证明门随源动，
门内硬编码 1996-01-01 必红）。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from factorlab.adapters.read.health import DatasetQualityError, require_dataset
from factorlab.core import scope

D = dt.date
DATASET = "ashare_daily"
PRE = "1995-12-29"
MIN = scope.MIN_TRADE_DATE.isoformat()


def _doc(partition: str, *, status="PASS") -> dict:
    return {
        "dataset_id": DATASET,
        "partition": partition,
        "data_version": "v20260920_01",
        "dq_policy_version": "daily-v3",
        "health_status": status,
        "verification_state": "VERIFIED",
        "completeness": {"status": "COMPLETE", "expected_count": 1,
                         "actual_count": 1, "coverage": 1.0},
        "quality": {"fatal_count": 0, "error_count": 0, "warning_count": 0,
                    "quarantine_count": 0, "error_rate": 0.0,
                    "systematic_issue": False, "systemic_detail": None},
        "freshness": {"latest_trade_date": partition},
        "rules": {},
        "validated_at": "2026-09-20T21:00:00+08:00",
        "raw_lineage": {"source_version": "pan-2026-09-20",
                        "raw_sha256": "ab" * 32},
    }


def _write(root: Path, partition: str, *, status="PASS") -> Path:
    p = Path(root) / "health" / DATASET / f"{partition}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_doc(partition, status=status), ensure_ascii=False),
                 encoding="utf-8")
    return p


def test_partition_before_min_date_rejected_even_with_pass_health(tmp_path):
    """1995-12-29 已有 PASS health 也必须拒——范围外不属研究数据集。"""
    _write(tmp_path, PRE)
    with pytest.raises(DatasetQualityError) as ei:
        require_dataset(DATASET, PRE, root=tmp_path)
    exc = ei.value
    assert exc.status == "OUT_OF_SCOPE"
    assert exc.dataset == DATASET and exc.partition == PRE
    msg = str(exc)
    assert "1996-01-01" in msg and "裁定" in msg
    assert date_text_in_guidance(exc) is True


def date_text_in_guidance(exc: DatasetQualityError) -> bool:
    guidance = exc.guidance or ""
    return "1996-01-01" in guidance and "裁定" in guidance


def test_partition_before_min_date_rejected_without_health_file(tmp_path):
    """无 health 文件同样 OUT_OF_SCOPE——不得落入 MISSING/LEGACY 过渡语义。"""
    with pytest.raises(DatasetQualityError) as ei:
        require_dataset(DATASET, PRE, root=tmp_path)
    exc = ei.value
    assert exc.status == "OUT_OF_SCOPE"
    assert "1996-01-01" in str(exc)
    assert "LEGACY_UNVERIFIED" not in str(exc)


def test_min_date_boundary_reuses_existing_semantics(tmp_path):
    """边界 1996-01-01 走既有逻辑：PASS 过门；缺 artifact = MISSING/LEGACY 原语义。"""
    _write(tmp_path, MIN)
    gate = require_dataset(DATASET, MIN, root=tmp_path)
    assert gate.health_status == "PASS"
    assert gate.dq_policy_version == "daily-v3"

    with pytest.raises(DatasetQualityError) as ei:
        require_dataset(DATASET, "1996-01-02", root=tmp_path)
    assert ei.value.status == "MISSING"
    assert "LEGACY_UNVERIFIED" in str(ei.value)


def test_scope_boundary_reads_core_scope_constant(tmp_path, monkeypatch):
    """单一事实源：门必须消费 factorlab.core.scope 常量（硬编码必红）。"""
    monkeypatch.setattr(scope, "MIN_TRADE_DATE", D(2000, 1, 1))
    with pytest.raises(DatasetQualityError) as ei:
        require_dataset(DATASET, "1999-12-31", root=tmp_path)
    assert ei.value.status == "OUT_OF_SCOPE"
    # 新边界生效：2000-01-01 不再是 OUT_OF_SCOPE（走 MISSING）
    with pytest.raises(DatasetQualityError) as ei2:
        require_dataset(DATASET, "2000-01-01", root=tmp_path)
    assert ei2.value.status == "MISSING"


def test_out_of_scope_rejection_writes_no_manifest(tmp_path):
    """范围外拒绝不得落任何 manifest（gate 未通过，无 opt-in 通道）。"""
    _write(tmp_path, PRE)
    with pytest.raises(DatasetQualityError):
        require_dataset(DATASET, PRE, root=tmp_path,
                        accept_quality=("PASS", "DEGRADED", "UNKNOWN"),
                        override_reason="无论如何都不许")
    assert not (tmp_path / "manifest").exists()
