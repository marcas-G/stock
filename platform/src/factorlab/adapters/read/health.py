"""读取门 require_dataset（Plan DQ-M1 T7，设计 §7）。

Readable = HealthValid AND FreshEnough AND Complete
---------------------------------------------------
- **只读 health JSON**（``data/health/<dataset>/<partition>.json``，T6 发布契约）：
  读取侧只验证证明有效，**不重复跑检查、不重算 OHLC**（设计 §0.3）；
- 默认 fail-closed：``accept_quality=("PASS",)``；
  ``DEGRADED`` 必须显式 opt-in 且自动写 Experiment Manifest（五字段）；
  ``FAIL`` 不可 opt-in；``UNKNOWN`` 仅 LEGACY 存量过渡（显式声明）；
- ``completeness.status`` 独立检查（不靠 coverage 推）；
- ``freshness`` 独立检查（``max_staleness`` 对 ``freshness.latest_trade_date``）；
- 拒绝文案含 dataset / partition / status / 指引。

``DatasetGate.summary_fields()`` 供 evaluate/backtest 产物追加五字段：
``dataset_version / quality_status / quarantined_rows / coverage /
cleaning_policy_version``（设计 §7）。
"""
from __future__ import annotations

import datetime
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DATASET = "ashare_daily"
DEFAULT_MAX_STALENESS = "1d"

HEALTH_STATUSES = ("PASS", "DEGRADED", "FAIL", "UNKNOWN")
VERIFICATION_STATES = ("VERIFIED", "LEGACY_UNVERIFIED", "KNOWN_ISSUE")
COMPLETENESS_STATUSES = ("COMPLETE", "INCOMPLETE", "UNKNOWN")

# FAIL 不可 opt-in（设计 §7 表：FAIL ❌ 不可 opt-in）
_NEVER_OPT_IN = ("FAIL",)
_LEGACY_GUIDANCE = (
    "存量无 health 分区按过渡条款视为 LEGACY_UNVERIFIED/UNKNOWN（设计 §8）："
    "默认拒绝；确需过渡使用请显式声明 accept_quality 含 UNKNOWN + override_reason")

_STALENESS_RE = re.compile(r"^(\d+)d$")


class DatasetQualityError(RuntimeError):
    """读取门拒绝（fail-closed）：数据未发布为 research-ready 或声明过期。"""


@dataclass(frozen=True)
class DatasetGate:
    """通过读取门的数据版本声明（产物记录的五个字段来源）。"""

    dataset: str
    partition: str
    health_status: str
    verification_state: str
    data_version: str
    dq_policy_version: str
    completeness_status: str
    quarantined_rows: int
    coverage: float | None
    max_staleness: str
    override_reason: str | None = None
    manifest_path: Path | None = None

    def summary_fields(self) -> dict[str, Any]:
        """设计 §7：factor/backtest summary 追加的五字段。"""
        return {
            "dataset_version": self.data_version,
            "quality_status": self.health_status,
            "quarantined_rows": self.quarantined_rows,
            "coverage": self.coverage,
            "cleaning_policy_version": self.dq_policy_version,
        }


def parse_max_staleness(text: str) -> datetime.timedelta:
    """``"1d"``/``"30d"``/``"0d"`` → timedelta；其它格式 ValueError。"""
    m = _STALENESS_RE.match(str(text).strip())
    if not m:
        raise ValueError(
            f"max_staleness 格式应为 <N>d（如 '1d'）：{text!r}")
    return datetime.timedelta(days=int(m.group(1)))


def _default_root() -> Path:
    from factorlab.core.factio import paths

    return Path(paths.DATA_ROOT)


def health_artifact_path(dataset: str, partition: str,
                         *, root: str | Path | None = None) -> Path:
    return (Path(root) if root is not None else _default_root()) / "health" \
        / dataset / f"{partition}.json"


def _reject(dataset: str, partition: str, status: str, why: str,
            *, guidance: str) -> "DatasetQualityError":
    return DatasetQualityError(
        f"读取门拒绝：dataset={dataset} partition={partition} status={status}"
        f"——{why}；指引：{guidance}")


def _atomic_write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_quality_manifest(gate: DatasetGate, health_doc: dict, *,
                           root: str | Path | None = None,
                           manifest_path: str | Path | None = None,
                           override_reason: str | None) -> Path:
    """DEGRADED/UNKNOWN opt-in 的 Experiment Manifest（五字段，原子写）。"""
    base = Path(root) if root is not None else _default_root()
    path = (Path(manifest_path) if manifest_path is not None
            else base / "manifest" / gate.dataset / f"{gate.partition}.json")
    doc = {
        "dataset_quality": gate.health_status,
        "quality_issues": health_doc.get("quality"),
        "affected_partitions": [gate.partition],
        "dq_policy_version": gate.dq_policy_version,
        "override_reason": override_reason,
    }
    _atomic_write_json(path, doc)
    return path


def record_gate_usage(gate: DatasetGate, *, root: str | Path | None = None,
                      suffix: str = "usage") -> Path:
    """产物记录：五字段 usage sidecar（backtest 汇总处调用，设计 §7）。"""
    root_path = Path(root) if root is not None else _default_root()
    path = root_path / "manifest" / gate.dataset \
        / f"{gate.partition}.{suffix}.json"
    _atomic_write_json(path, gate.summary_fields())
    return path


def require_dataset(
    dataset: str = DEFAULT_DATASET,
    as_of: str | None = None,
    *,
    accept_quality: tuple[str, ...] = ("PASS",),
    max_staleness: str = DEFAULT_MAX_STALENESS,
    completeness_required: str = "COMPLETE",
    root: str | Path | None = None,
    override_reason: str | None = None,
    manifest_path: str | Path | None = None,
    strict: bool = False,
) -> DatasetGate:
    """读取门（fail-closed）：验证 ``data/health/<dataset>/<as_of>.json``。

    - ``accept_quality``：允许接受的 health_status（默认仅 PASS）；
      ``FAIL`` 任何情况都不可 opt-in；
    - ``strict=True``：正式 OOS/验收/Replay/上线复核/基准场景 → 仅 PASS
      （给非 PASS 的 opt-in 直接 ValueError）；
    - ``max_staleness``：``freshness.latest_trade_date`` 距 ``as_of`` 的容差；
    - ``completeness.status != COMPLETE`` → 拒绝（独立腿，不用 coverage 推）；
    - DEGRADED/UNKNOWN 显式 opt-in（须给 ``override_reason``）→ 自动写
      Experiment Manifest 五字段。
    """
    if as_of is None:
        raise ValueError(
            "require_dataset 必须显式给出 as_of（读取分区；不猜'最新'）")
    try:
        datetime.date.fromisoformat(str(as_of))
    except ValueError as exc:
        raise ValueError(f"as_of 需为 ISO 日期（YYYY-MM-DD）：{as_of!r}") from exc
    as_of = str(as_of)

    if not accept_quality:
        raise ValueError("accept_quality 不能为空（默认 ('PASS',)）")
    bad = [s for s in accept_quality if s not in HEALTH_STATUSES]
    if bad:
        raise ValueError(
            f"accept_quality 含未知状态 {bad}（可用 {HEALTH_STATUSES}）")
    if any(s in accept_quality for s in _NEVER_OPT_IN):
        raise ValueError(
            "FAIL 不可 opt-in（设计 §7：FAIL ❌ 不可 opt-in）——先修复数据重发 health")
    if strict and tuple(accept_quality) != ("PASS",):
        raise ValueError(
            f"strict（正式 OOS/验收/基准场景）只接受 PASS："
            f"accept_quality={tuple(accept_quality)} 非法")
    max_delta = parse_max_staleness(max_staleness)

    path = health_artifact_path(dataset, as_of, root=root)
    if not path.is_file():
        raise _reject(
            dataset, as_of, "MISSING",
            f"health artifact 不存在（{path}）——canonical 已写入 ≠ research-ready",
            guidance="先跑 post-ingest audit + health 发布（data_quality/health.py "
                     "publish）；" + _LEGACY_GUIDANCE)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise _reject(dataset, as_of, "MALFORMED",
                      f"health artifact 无法解析（{path}）：{exc}",
                      guidance="重发 health（data_quality/health.py publish）") from exc
    if not isinstance(doc, dict) or "health_status" not in doc:
        raise _reject(dataset, as_of, "MALFORMED",
                      f"health artifact 结构非法（{path}）",
                      guidance="重发 health（data_quality/health.py publish）")

    status = doc["health_status"]
    verification = doc.get("verification_state")
    if status not in HEALTH_STATUSES or verification not in VERIFICATION_STATES:
        raise _reject(dataset, as_of, str(status),
                      f"health/verification 枚举非法（health_status={status!r} / "
                      f"verification_state={verification!r}）",
                      guidance="重发 health（§6 双枚举契约）")
    if status == "FAIL":
        raise _reject(
            dataset, as_of, status, "FINAL 门判 FAIL（不可 opt-in）",
            guidance="修复数据并重跑 clean → ingest → health；不得绕过")
    if status not in accept_quality:
        raise _reject(
            dataset, as_of, status,
            f"不在 accept_quality={tuple(accept_quality)}（默认 fail-closed）",
            guidance="DEGRADED 需显式 opt-in（accept_quality 含 DEGRADED + "
                     "override_reason，自动写 manifest）；或修复数据重发 health")

    if status == "UNKNOWN":
        if verification != "LEGACY_UNVERIFIED":
            raise _reject(
                dataset, as_of, status,
                f"UNKNOWN 仅限 LEGACY 存量（verification_state={verification!r}）",
                guidance="重发 health 或按 §8 过渡条款处理")
        if override_reason is None:
            raise _reject(
                dataset, as_of, status, "UNKNOWN opt-in 缺少 override_reason",
                guidance="显式声明 accept_quality 含 UNKNOWN + override_reason"
                         "（过渡期条款；自动写 manifest）")

    completeness = doc.get("completeness") or {}
    c_status = completeness.get("status")
    if c_status not in COMPLETENESS_STATUSES:
        raise _reject(dataset, as_of, status,
                      f"completeness.status 非法（{c_status!r}）",
                      guidance="重发 health（§6 completeness.status 契约）")
    if c_status != completeness_required:
        raise _reject(
            dataset, as_of, status,
            f"completeness.status={c_status} ≠ {completeness_required}"
            f"（独立检查，不靠 coverage 推）",
            guidance="补齐/重导缺失分区后重发 health；完整性未证实不得 research-ready")

    freshness = doc.get("freshness") or {}
    latest = freshness.get("latest_trade_date")
    try:
        latest_d = datetime.date.fromisoformat(str(latest))
    except (TypeError, ValueError):
        raise _reject(dataset, as_of, status,
                      f"freshness.latest_trade_date 非法/缺失（{latest!r}）",
                      guidance="重发 health（§6 freshness 契约）") from None
    gap = datetime.date.fromisoformat(as_of) - latest_d
    if gap > max_delta:
        raise _reject(
            dataset, as_of, status,
            f"数据陈旧：freshness.latest_trade_date={latest_d} 距 as_of 为 "
            f"{gap.days}d > max_staleness={max_staleness}",
            guidance="刷新数据到 as_of 后重发 health；或不要求该新鲜度（放宽 "
                     "max_staleness 会被审计）")

    if status != "PASS" and override_reason is None:
        raise _reject(
            dataset, as_of, status,
            "非 PASS opt-in 缺少 override_reason（必须显式声明原因）",
            guidance="显式给出 override_reason（自动写 Experiment Manifest）")

    quality = doc.get("quality") or {}
    gate = DatasetGate(
        dataset=dataset, partition=as_of, health_status=status,
        verification_state=verification, data_version=doc.get("data_version"),
        dq_policy_version=doc.get("dq_policy_version"),
        completeness_status=c_status,
        quarantined_rows=int(quality.get("quarantine_count", 0)),
        coverage=completeness.get("coverage"),
        max_staleness=max_staleness, override_reason=override_reason)
    # 非 PASS 的 opt-in 自动写 Experiment Manifest；PASS 显式给路径时也落
    if status != "PASS" or manifest_path is not None:
        manifest = write_quality_manifest(
            gate, doc, root=root, manifest_path=manifest_path,
            override_reason=override_reason)
        gate = DatasetGate(**{**gate.__dict__, "manifest_path": manifest})
    return gate
