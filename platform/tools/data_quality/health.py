"""Health Artifact 发布（Plan DQ-M1 T6）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-6-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §5（... → FINAL PARTITION GATE → HEALTH ARTIFACT → PUBLISH RESEARCH-READY；
           落点 ``data/health/<dataset>/<partition>.json``）
      §6（Health Artifact 字段逐字；health_status / verification_state **双枚举
           不同维度，不得互相映射**）。

契约：
- ``publish_health`` 产物键集与 §6 完全一致（多/少都算违约；测试逐键断言）；
- **scope=full_table（F5）**：``quality.*`` 与 ``completeness.*`` 为清洗链全表口径——
  分母 = raw 全表行数、actual = clean 行数、``coverage=actual/expected``、
  ``error_rate=error_count/expected``；``status=COMPLETE`` 当且仅当差额被
  quarantine+dedup 清洗账完全解释；分区级 PK/抽样/漂移仍按 partition 审查；
- ``health_status ∈ {PASS, DEGRADED, FAIL, UNKNOWN}``；
  ``verification_state ∈ {VERIFIED, LEGACY_UNVERIFIED, KNOWN_ISSUE}``；
  ``completeness.status ∈ {COMPLETE, INCOMPLETE, UNKNOWN}``；
- 原子发布：同目录临时文件 + ``os.replace``；失败不破坏已发布文件；
- ``raw_lineage.raw_sha256`` 取自 raw 文件（``sha256_file``）；
- CLI（阶段链末步）：``health.py publish --partition latest --run-tag <tag>``，
  staging summary 缺失 → 退出码 2；FINAL FAIL → 退出码 1（阶段失败上抛）。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

import polars as pl

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:      # 脚本直启（阶段链）时补 tools/ 再导入
    sys.path.insert(0, str(_TOOLS))

from factorlab.core import scope as core_scope  # noqa: E402

from data_quality import audit, rules  # noqa: E402

if TYPE_CHECKING:                    # pragma: no cover - 仅类型
    from data_quality.pipeline import ScopedLedger

PASS = audit.PASS
DEGRADED = audit.DEGRADED
FAIL = audit.FAIL


HEALTH_STATUSES = ("PASS", "DEGRADED", "FAIL", "UNKNOWN")
VERIFICATION_STATES = ("VERIFIED", "LEGACY_UNVERIFIED", "KNOWN_ISSUE")
COMPLETENESS_STATUSES = ("COMPLETE", "INCOMPLETE", "UNKNOWN")

# spec §6 冻结键集（逐字段；M1.5 增 repair_policy_version；M1.5c 增 quality_backlog/note）
TOP_KEYS = ("dataset_id", "partition", "data_version", "dq_policy_version",
            "repair_policy_version", "quality_backlog", "note",
            "health_status", "verification_state", "completeness", "quality",
            "freshness", "rules", "validated_at", "raw_lineage")
COMPLETENESS_KEYS = ("status", "expected_count", "actual_count", "coverage")
QUALITY_KEYS = ("fatal_count", "error_count", "warning_count",
                "quarantine_count", "error_rate", "systematic_issue",
                "systemic_detail")
FRESHNESS_KEYS = ("latest_trade_date",)
RAW_LINEAGE_KEYS = ("source_version", "raw_sha256")
# M1.5c：全表口径披露块（不参与门判定）
BACKLOG_KEYS = ("scope", "expected_count", "actual_count", "fatal_count",
                "error_count", "warning_count", "quarantine_count",
                "error_rate", "systemic_detail", "top_classes")
NOTE_NO_NEW_DATA = "no_new_data"

DEFAULT_DATASET = "ashare_daily"
# R37 T2：publish-history 的 quality_backlog scope 标签（scoped 全范围披露）
HISTORY_BACKLOG_SCOPE = "scoped_full_table"


def _default_backlog() -> dict:
    """无全表账本可披露时（LEGACY 打标/旧件）的空 backlog（键集不破）。"""
    return {"scope": "full_table", "expected_count": None, "actual_count": None,
            "fatal_count": 0, "error_count": 0, "warning_count": 0,
            "quarantine_count": 0, "error_rate": None, "systemic_detail": None,
            "top_classes": []}


def last_published_trade_date(root: str | Path, dataset: str = DEFAULT_DATASET,
                              ) -> str | None:
    """上次链发布水位：health/<dataset>/*.json 中 ``verification_state=VERIFIED``
    档案的 ``freshness.latest_trade_date`` 最大值（无 → None；坏文件跳过）。

    设计 §3.3：门判 `delta = trade_date > 该水位`；LEGACY 打标不算发布；
    已链发布的档案（含 FAIL）都推进水位，避免失败重跑死锁在历史残余上。
    """
    d = Path(root) / "health" / dataset
    best: str | None = None
    if not d.is_dir():
        return None
    for p in sorted(d.glob("*.json")):
        if p.name == "summary.json":
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            if doc.get("verification_state") != "VERIFIED":
                continue
            latest = (doc.get("freshness") or {}).get("latest_trade_date")
            if not isinstance(latest, str) or not latest:
                continue
        except (ValueError, KeyError, TypeError, OSError):
            continue
        if best is None or latest > best:
            best = latest
    return best


def _default_run_tag() -> str:
    return datetime.date.today().strftime("%Y%m%d")


def _resolve_root(root: str | Path | None) -> Path:
    if root is not None:
        return Path(root)
    from data_quality.pipeline import _resolve_root as _pipeline_root

    return _pipeline_root(None)


def sha256_file(path: str | Path) -> str:
    """流式 sha256（raw 文件可能 GB 级）。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_health_doc(*, dataset_id: str, partition: str, data_version: str,
                     dq_policy_version: str, health_status: str,
                     verification_state: str, completeness: dict,
                     quality: dict, rules_counts: dict,
                     repair_policy_version: str | None = None,
                     quality_backlog: dict | None = None,
                     note: str | None = None,
                     latest_trade_date: str | None = None,
                     validated_at: str | None = None,
                     source_version: str | None = None,
                     raw_sha256: str | None = None) -> dict:
    """构造 §6 文档（键集/枚举校验；多键少键都 ValueError）。

    ``repair_policy_version``：修复/清洗语义版本留痕（M1.5 T2）；缺省 =
    ``dq_policy_version``（同一版本线），显式空串拒绝。
    ``quality_backlog``：全表口径披露（M1.5c §3.3；**不参与门判定**），缺省 = 空块；
    ``note``：`no_new_data` 等注记（None 或非空字符串）。
    """
    if health_status not in HEALTH_STATUSES:
        raise ValueError(
            f"health_status 必须 ∈ {HEALTH_STATUSES}（收到 {health_status!r}）")
    if verification_state not in VERIFICATION_STATES:
        raise ValueError(
            f"verification_state 必须 ∈ {VERIFICATION_STATES}（收到 {verification_state!r}）")
    if repair_policy_version is None:
        repair_policy_version = dq_policy_version
    if not isinstance(repair_policy_version, str) or not repair_policy_version:
        raise ValueError(
            f"repair_policy_version 必须为非空字符串（收到 {repair_policy_version!r}）")
    if quality_backlog is None:
        quality_backlog = _default_backlog()
    if set(quality_backlog) != set(BACKLOG_KEYS):
        raise ValueError(
            f"quality_backlog 键集必须为 {BACKLOG_KEYS}"
            f"（收到 {sorted(quality_backlog)}）")
    if note is not None and (not isinstance(note, str) or not note):
        raise ValueError(f"note 必须为 None 或非空字符串（收到 {note!r}）")
    if set(completeness) != set(COMPLETENESS_KEYS):
        raise ValueError(
            f"completeness 键集必须为 {COMPLETENESS_KEYS}（收到 {sorted(completeness)}）")
    if completeness["status"] not in COMPLETENESS_STATUSES:
        raise ValueError(
            f"completeness.status 必须 ∈ {COMPLETENESS_STATUSES}"
            f"（收到 {completeness['status']!r}）")
    if set(quality) != set(QUALITY_KEYS):
        raise ValueError(
            f"quality 键集必须为 {QUALITY_KEYS}（收到 {sorted(quality)}）")
    if not isinstance(partition, str) or not partition:
        raise ValueError(f"partition 必须为非空字符串：{partition!r}")

    doc = {
        "dataset_id": dataset_id,
        "partition": partition,
        "data_version": data_version,
        "dq_policy_version": dq_policy_version,
        "repair_policy_version": repair_policy_version,
        "quality_backlog": {k: quality_backlog[k] for k in BACKLOG_KEYS},
        "note": note,
        "health_status": health_status,
        "verification_state": verification_state,
        "completeness": {k: completeness[k] for k in COMPLETENESS_KEYS},
        "quality": {k: quality[k] for k in QUALITY_KEYS},
        "freshness": {"latest_trade_date": latest_trade_date or partition},
        "rules": {str(k): int(v) for k, v in rules_counts.items()},
        "validated_at": validated_at or datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "raw_lineage": {"source_version": source_version,
                        "raw_sha256": raw_sha256},
    }
    if set(doc) != set(TOP_KEYS):     # pragma: no cover - 防御性（构建即键集）
        raise AssertionError(f"health 键集漂移：{sorted(doc)}")
    return doc


def _atomic_write_json(path: Path, doc: dict) -> None:
    """同目录临时文件 + os.replace（失败不留半截，不破坏旧文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def publish_health(*, dataset_id: str, partition: str, data_version: str,
                   dq_policy_version: str, health_status: str,
                   verification_state: str, completeness: dict, quality: dict,
                   rules_counts: dict, repair_policy_version: str | None = None,
                   quality_backlog: dict | None = None, note: str | None = None,
                   latest_trade_date: str | None = None,
                   raw_path: str | Path | None = None,
                   raw_sha256: str | None = None,
                   source_version: str | None = None,
                   validated_at: str | None = None,
                   root: str | Path | None = None,
                   dry_run: bool = False) -> Path:
    """发布 ``data/health/<dataset>/<partition>.json``（原子；返回路径）。

    ``raw_path`` 给出且未显式给 ``raw_sha256`` → 从 raw 文件复算
    （``raw_lineage.raw_sha256``）；另原子更新数据集汇总 ``summary.json``。
    """
    if raw_sha256 is None and raw_path is not None:
        raw_sha256 = sha256_file(raw_path)
    doc = build_health_doc(
        dataset_id=dataset_id, partition=partition, data_version=data_version,
        dq_policy_version=dq_policy_version, health_status=health_status,
        verification_state=verification_state, completeness=completeness,
        quality=quality, rules_counts=rules_counts,
        repair_policy_version=repair_policy_version,
        quality_backlog=quality_backlog, note=note,
        latest_trade_date=latest_trade_date, validated_at=validated_at,
        source_version=source_version, raw_sha256=raw_sha256)
    path = _resolve_root(root) / "health" / dataset_id / f"{partition}.json"
    if dry_run:
        return path
    _atomic_write_json(path, doc)
    _update_dataset_summary(path.parent, doc)
    return path


def write_health_artifact(root: str | Path | None, doc: dict) -> Path:
    """原子写单份 health artifact（**不更新**数据集 summary；批量打标路径）。

    T8 存量打标用：全史逐分区写 ``LEGACY_UNVERIFIED/UNKNOWN`` 时若逐份走
    ``publish_health`` 的 summary 重写会 O(n²)；批量写完后由
    ``refresh_dataset_summary`` 一次性重建。
    """
    path = _resolve_root(root) / "health" / doc["dataset_id"] / f"{doc['partition']}.json"
    _atomic_write_json(path, doc)
    return path


def refresh_dataset_summary(health_dir: str | Path) -> Path:
    """扫描 health 目录重建 ``summary.json``（保留全部已发布条目，原子写）。

    ``health_dir`` 下每份 ``<partition>.json`` 一条（``summary.json`` 自身与
    无法解析/缺键的文件跳过——坏文件不阻断汇总）。同一目录内条目以文件为准，
    因此不会覆盖/丢失既有 VERIFIED 记录。
    """
    d = Path(health_dir)
    entries: dict[str, dict] = {}
    dataset: str | None = None
    for p in sorted(d.glob("*.json")):
        if p.name == "summary.json":
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            key = doc["partition"]
        except (ValueError, KeyError, TypeError):
            continue
        dataset = dataset or doc.get("dataset_id")
        entries[str(key)] = {
            "partition": str(key),
            "health_status": doc.get("health_status"),
            "verification_state": doc.get("verification_state"),
            "validated_at": doc.get("validated_at"),
        }
    summary_path = d / "summary.json"
    if dataset is None and summary_path.is_file():
        try:
            dataset = json.loads(summary_path.read_text(encoding="utf-8")).get("dataset_id")
        except ValueError:
            pass
    summary = {"dataset_id": dataset,
               "partitions": [entries[k] for k in sorted(entries)]}
    _atomic_write_json(summary_path, summary)
    return summary_path


def _update_dataset_summary(health_dir: Path, doc: dict) -> None:
    """数据集汇总（spec §5「+数据集汇总」）：各分区状态列表（旧文件损坏 → 重建）。"""
    summary_path = health_dir / "summary.json"
    entries: dict[str, dict] = {}
    if summary_path.is_file():
        try:
            old = json.loads(summary_path.read_text(encoding="utf-8"))
            entries = {e["partition"]: e for e in old.get("partitions", [])}
        except (ValueError, KeyError):
            entries = {}
    entries[doc["partition"]] = {
        "partition": doc["partition"],
        "health_status": doc["health_status"],
        "verification_state": doc["verification_state"],
        "validated_at": doc["validated_at"],
    }
    summary = {"dataset_id": doc["dataset_id"],
               "partitions": [entries[k] for k in sorted(entries)]}
    _atomic_write_json(summary_path, summary)


# ── history summary / quality 块组装 ─────────────────────────────────────
def merge_rules(*counts_maps: dict) -> dict:
    """rule_id → 命中数合并（staging summary rules + 审计 results）。"""
    out: dict[str, int] = {}
    for counts in counts_maps:
        for k, v in (counts or {}).items():
            out[str(k)] = out.get(str(k), 0) + int(v)
    return dict(sorted(out.items()))


def _backlog_block(summary: dict, top_n: int = 10) -> dict:
    """全表口径披露块（M1.5c §3.3；**不参与门判定**）。

    来源 = clean staging summary 的 full_table 计数/规则（backlog 在 clean 阶段
    已算好；health 只组装，不重跑校验）；top_classes = 命中数 top-N 规则类。
    """
    q = summary.get("quality") or {}
    top = sorted((summary.get("rules") or {}).items(),
                 key=lambda kv: (-int(kv[1]), str(kv[0])))[:top_n]
    return {
        "scope": "full_table",
        "expected_count": (summary.get("completeness") or {}).get("expected_count"),
        "actual_count": summary.get("clean_rows"),
        "fatal_count": int(q.get("fatal_count", 0)),
        "error_count": int(q.get("error_count", 0)),
        "warning_count": int(q.get("warning_count", 0)),
        "quarantine_count": int(q.get("quarantine_count", 0)),
        "error_rate": q.get("error_rate"),
        "systemic_detail": q.get("systemic_detail"),
        "top_classes": [{"rule_id": str(k), "count": int(v)} for k, v in top],
    }


def quality_block(base_quality: dict, metrics: audit.AuditMetrics | None,
                  final_decision: str) -> dict:
    """health quality 块 = delta 口径 PRE 清洗计数 + post-ingest 审计结果。

    审计发现的 ERROR（PK 等）计入 error_count；WARN/SUSPECT 计入 warning_count；
    ``error_rate`` 以 audit.completeness 的独立分母计算（无分母沿用 base）。
    ``metrics=None``（无新数据路径）→ 只披露 base（delta）计数。
    """
    if metrics is None:
        return {
            "fatal_count": int(base_quality.get("fatal_count", 0)),
            "error_count": int(base_quality.get("error_count", 0)),
            "warning_count": int(base_quality.get("warning_count", 0)),
            "quarantine_count": int(base_quality.get("quarantine_count", 0)),
            "error_rate": float(base_quality.get("error_rate", 0.0)),
            "systematic_issue": bool(base_quality.get("systematic_issue")),
            "systemic_detail": base_quality.get("systemic_detail"),
        }
    extra_error = sum(1 for r in metrics.results if r.level == rules.ERROR)
    extra_warn = sum(1 for r in metrics.results if r.level == rules.WARN)
    error_count = int(base_quality.get("error_count", 0)) + extra_error
    warning_count = int(base_quality.get("warning_count", 0)) + extra_warn
    expected = metrics.completeness.expected_count
    if expected:
        error_rate = error_count / expected
    else:
        error_rate = float(base_quality.get("error_rate", 0.0))
    systematic = metrics.systemic is not None or bool(
        base_quality.get("systematic_issue"))
    detail = None
    if metrics.systemic is not None:
        detail = metrics.systemic.detail
    elif base_quality.get("systemic_detail"):
        detail = base_quality["systemic_detail"]
    return {
        "fatal_count": int(base_quality.get("fatal_count", 0)),
        "error_count": error_count,
        "warning_count": warning_count,
        "quarantine_count": int(base_quality.get("quarantine_count", 0)),
        "error_rate": error_rate,
        "systematic_issue": systematic,
        "systemic_detail": detail,
    }


# ── publish-history（R37 T2：scoped 账本 + 逐分区批量发布）────────────────
def derive_partition_status(*, partition_quarantine: int,
                            partition_expected: int,
                            partition_has_error: bool, policy) -> str:
    """publish-history 分区状态推导（R37 规格增补 §2；语义冻结）。

    - 分区级 audit 有 ERROR（PK 重复等）→ ``FAIL``；
    - 分区内 scope 内 ``quarantine > 0``，或
      ``error_rate = quarantine / expected ∈ (pass.max_error_rate,
      fail.min_error_rate]`` → ``DEGRADED``；
    - 其余 → ``PASS``。

    全表计数/规则 top-N 只进 ``quality_backlog`` 披露，**不参与**本判定。
    """
    if partition_expected < 0 or partition_quarantine < 0:
        raise ValueError(
            f"quarantine/expected 必须非负：quarantine={partition_quarantine!r} "
            f"expected={partition_expected!r}")
    if partition_has_error:
        return FAIL
    rate = (partition_quarantine / partition_expected
            if partition_expected > 0
            else (1.0 if partition_quarantine else 0.0))
    gate = policy.partition_gate
    if partition_quarantine > 0 or (
            gate["pass"]["max_error_rate"] < rate
            <= gate["fail"]["min_error_rate"]):
        return DEGRADED
    return PASS


def _history_backlog(ledger: "ScopedLedger") -> dict:
    """全范围 scoped 账本 → health ``quality_backlog`` 披露块（键集同契约）。"""
    top = sorted((ledger.rules or {}).items(),
                 key=lambda kv: (-int(kv[1]), str(kv[0])))[:10]
    return {
        "scope": HISTORY_BACKLOG_SCOPE,
        "expected_count": ledger.expected_count,
        "actual_count": ledger.clean_count,
        "fatal_count": ledger.fatal_count,
        "error_count": ledger.error_count,
        "warning_count": ledger.warning_count,
        "quarantine_count": ledger.quarantine_count,
        "error_rate": ledger.error_rate,
        "systemic_detail": (ledger.systemic.detail if ledger.systemic else None),
        "top_classes": [{"rule_id": str(k), "count": int(v)} for k, v in top],
    }


def _iso_date(value: str, what: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} 需为 ISO 日期（YYYY-MM-DD）：{value!r}") from exc


def _normalize_dates(dates: Iterable) -> list[datetime.date]:
    out: list[datetime.date] = []
    for d in dates:
        if isinstance(d, datetime.date):
            out.append(d)
            continue
        out.append(_iso_date(d, "dates 元素"))
    return out


def _is_published_pass(path: Path, policy_version: str) -> bool:
    """已发布且可续跑：PASS + VERIFIED + 当前政策版本（旧政策不算续跑）。"""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (doc.get("health_status") == PASS
            and doc.get("verification_state") == "VERIFIED"
            and doc.get("dq_policy_version") == policy_version)


def _load_scoped_ledger(*, policy=None, log: Callable[[str], None] | None = None,
                        ) -> "ScopedLedger":
    """生产装载：读 raw 全表 → ``pipeline.build_scoped_ledger``（只读、不落盘）。

    publish-history 开工前跑一次；`systemic` 非 None → 整批拒绝（§2）。
    """
    from data_quality import pipeline as dq_pipeline

    policy = policy or rules.load_policy()
    log = log or (lambda line: None)
    raw_path = dq_pipeline._resolve_raw(None)
    log(f"[publish-history] scoped 全范围账本：{raw_path}")
    raw = pl.read_parquet(raw_path)
    return dq_pipeline.build_scoped_ledger(
        raw, policy=policy, raw_sha256=sha256_file(raw_path), log=log)


def _list_canonical_partitions(dataset: str = DEFAULT_DATASET, *,
                               date_from: str, date_to: str) -> list[str]:
    """canonical 中 [date_from, date_to] 的交易日清单（DISTINCT；升序）。"""
    table = _table_name(dataset)
    from factorlab.app.bootstrap import open_read

    rd = open_read()
    try:
        if table not in rd.tables():
            raise ValueError(
                f"canonical 表 {table!r} 不存在（backend={rd.backend}）——"
                f"publish-history 需要已灌入的 canonical")
        if rd.backend == "ch":
            pred = (f"trade_date >= toDate('{date_from}') AND "
                    f"trade_date <= toDate('{date_to}')")
        else:
            pred = (f"trade_date >= DATE '{date_from}' AND "
                    f"trade_date <= DATE '{date_to}'")
        frame = rd.query_df(
            f"SELECT DISTINCT trade_date FROM {table} WHERE {pred} "
            f"ORDER BY trade_date")
        days = frame["trade_date"].to_list()
    finally:
        rd.close()
    return sorted(d.isoformat() if hasattr(d, "isoformat") else str(d)
                  for d in days)


def _read_partition_scoped(partition: str,
                           dataset: str = DEFAULT_DATASET) -> pl.DataFrame:
    """读 canonical 单分区 + scope 过滤（§2：读该分区 canonical（scoped））。

    只拉分区帧（不整表 count），列与现有 ``_read_canonical`` 同口径：
    ``code/trade_date/open/high/low/close``。
    """
    table = _table_name(dataset)
    from factorlab.app.bootstrap import open_read

    rd = open_read()
    try:
        if table not in rd.tables():
            raise ValueError(
                f"canonical 表 {table!r} 不存在（backend={rd.backend}）——"
                f"publish-history 需要已灌入的 canonical")
        pred = (f"trade_date = toDate('{partition}')" if rd.backend == "ch"
                else f"trade_date = DATE '{partition}'")
        frame = rd.query_df(
            f"SELECT ts_code AS code, trade_date, open, high, low, close "
            f"FROM {table} WHERE {pred}")
    finally:
        rd.close()
    return core_scope.filter_frame(frame)


def _history_doc(*, partition: str, frame: pl.DataFrame,
                 ledger: "ScopedLedger", policy, dataset_id: str,
                 run_tag: str) -> tuple[dict, str]:
    """单分区 canonical（scoped）审计 → health doc + 冻结推导状态。"""
    scoped = core_scope.filter_frame(frame)
    unit = ledger.partition(partition)
    expected = unit.expected if unit is not None else scoped.height
    actual = scoped.height
    explained = ((unit.quarantined_rows + unit.deduped_rows)
                 if unit is not None else 0)
    metrics = audit.audit_post_ingest(
        scoped, partition=partition,
        expected_count=expected if expected > 0 else None,
        actual_count=actual, explained_drops=explained)
    has_error = any(r.level in (rules.FATAL, rules.ERROR)
                    for r in metrics.results)
    quarantine = unit.quarantined_keys if unit is not None else 0
    status = derive_partition_status(
        partition_quarantine=quarantine, partition_expected=expected,
        partition_has_error=has_error, policy=policy)

    audit_level = {rules.FATAL: 0, rules.ERROR: 0, rules.WARN: 0}
    for r in metrics.results:
        if r.level in audit_level:
            audit_level[r.level] += 1
    error_count = (unit.error_count if unit is not None else 0) \
        + audit_level[rules.ERROR]
    quality = {
        "fatal_count": (unit.fatal_count if unit is not None else 0)
        + audit_level[rules.FATAL],
        "error_count": error_count,
        "warning_count": (unit.warning_count if unit is not None else 0)
        + audit_level[rules.WARN],
        "quarantine_count": quarantine,
        "error_rate": error_count / expected if expected else 0.0,
        "systematic_issue": False,     # 系统性已在开工前整批裁定（§2）
        "systemic_detail": None,
    }
    completeness = metrics.completeness
    doc = build_health_doc(
        dataset_id=dataset_id, partition=partition,
        data_version=f"v{run_tag}_01",
        dq_policy_version=policy.dq_policy_version,
        repair_policy_version=policy.dq_policy_version,
        health_status=status, verification_state="VERIFIED",
        completeness={
            "status": completeness.status,
            "expected_count": completeness.expected_count,
            "actual_count": completeness.actual_count,
            "coverage": completeness.coverage},
        quality=quality, rules_counts=_rule_counts(metrics.results),
        latest_trade_date=partition, quality_backlog=_history_backlog(ledger),
        source_version=str(run_tag), raw_sha256=ledger.raw_sha256)
    return doc, status


def publish_history(
    *,
    dataset_id: str = DEFAULT_DATASET,
    date_from: str,
    date_to: str | None = None,
    run_tag: str,
    root: str | Path | None = None,
    resume: bool = True,
    force: bool = False,
    reader: Callable[[str], pl.DataFrame] | None = None,
    dates: Iterable | None = None,
    ledger: "ScopedLedger | None" = None,
    log: Callable[[str], None] | None = None,
) -> dict:
    """scope 全范围账本 + 逐分区 canonical 审计 → health 批量发布（R37 §2）。

    语义（冻结）：

    - 开工前在 scoped 全范围上跑一次 ``aggregate.detect_systemic``；未过 →
      ``status="rejected_systemic"``，**零落盘**（fail fast，不留半成品）；
    - 逐分区（[date_from, date_to] 内 canonical 交易日）：读 canonical（scoped）
      → 分区级 audit（PK/重复）→ 冻结推导
      （``derive_partition_status``）→ 原子写
      ``data/health/<dataset>/<partition>.json``；
    - 幂等可续：已 PASS（且政策版本一致）分区跳过；``force=True`` 重发；
    - 全表计数只进 ``quality_backlog`` 披露；``summary.json`` 末尾一次性重建；
    - 范围外分区（<1996-01-01 或超出 [date_from, date_to]）**不写**。

    返回报告：``status``（ok / rejected_systemic / no_dates）与
    published/skipped/passed/degraded/failed 计数。
    ``reader`` / ``dates`` / ``ledger`` 为测试/装配注入面（生产留 None）。
    """
    if not isinstance(run_tag, str) or not run_tag.strip():
        raise ValueError(f"run_tag 必须为非空字符串：{run_tag!r}")
    log = log or (lambda line: None)
    start = _iso_date(date_from, "date_from")
    end = (_iso_date(date_to, "date_to") if date_to is not None
           else dt.date.today())
    if end < start:
        raise ValueError(f"date_to({end}) < date_from({start})")
    policy = rules.load_policy()
    base = _resolve_root(root)
    health_dir = base / "health" / dataset_id
    effective_from = max(start, core_scope.MIN_TRADE_DATE)
    if effective_from > start:
        log(f"[publish-history] --from {start} 早于数据集范围 "
            f"{core_scope.MIN_TRADE_DATE_ISO} → 从 {effective_from} 起")

    if dates is None:
        picked = _normalize_dates(_list_canonical_partitions(
            dataset_id, date_from=effective_from.isoformat(),
            date_to=end.isoformat()))
    else:
        picked = _normalize_dates(dates)
    picked = sorted(d for d in picked if effective_from <= d <= end)

    report = {
        "dataset_id": dataset_id,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "run_tag": str(run_tag),
        "status": "ok",
        "total": len(picked),
        "published": 0,
        "skipped": 0,
        "passed": 0,
        "degraded": 0,
        "failed": 0,
        "systemic_detail": None,
    }
    if not picked:
        report["status"] = "no_dates"
        log(f"[publish-history] 范围内无分区日期（{effective_from}..{end}）")
        return report

    if ledger is None:
        ledger = _load_scoped_ledger(policy=policy, log=log)
    if ledger.systemic is not None:
        report["status"] = "rejected_systemic"
        report["systemic_detail"] = ledger.systemic.detail
        log(f"[publish-history] 系统性检查未过 → 整批拒绝："
            f"{ledger.systemic.detail}")
        return report

    read = reader or (lambda partition: _read_partition_scoped(partition,
                                                               dataset_id))
    skip = resume and not force
    total = len(picked)
    for i, partition in enumerate(picked, start=1):
        iso = partition.isoformat()
        path = health_dir / f"{iso}.json"
        if skip and _is_published_pass(path, policy.dq_policy_version):
            report["skipped"] += 1
        else:
            doc, status = _history_doc(
                partition=iso, frame=read(iso), ledger=ledger, policy=policy,
                dataset_id=dataset_id, run_tag=run_tag)
            write_health_artifact(base, doc)
            report["published"] += 1
            if status == PASS:
                report["passed"] += 1
            elif status == DEGRADED:
                report["degraded"] += 1
            else:
                report["failed"] += 1
        if i % 100 == 0 or i == total:
            log(f"[publish-history] {i}/{total} published={report['published']} "
                f"skipped={report['skipped']} degraded={report['degraded']} "
                f"failed={report['failed']}")
    if report["published"]:
        refresh_dataset_summary(health_dir)
    return report


# ── CLI（阶段链末步）────────────────────────────────────────────────────
def _table_name(dataset: str) -> str:
    return dataset.split("_", 1)[-1] if dataset.startswith("ashare_") else dataset


def _read_canonical(partition: str,
                    dataset: str = DEFAULT_DATASET) -> tuple[pl.DataFrame, int]:
    """读 canonical 分区帧 + 全表计数（生产 ch / 历史 duckdb）。

    返回 ``(partition_frame, full_table_count)``：分区帧供 PK/抽样/漂移，
    全表计数供 reconcile（clean staging 全表口径）；不整表拉取（内存安全）。
    表名 = dataset 去掉 ``ashare_`` 前缀。
    """
    table = _table_name(dataset)
    from factorlab.app.bootstrap import open_read

    rd = open_read()
    try:
        if table not in rd.tables():
            raise ValueError(
                f"canonical 表 {table!r} 不存在（backend={rd.backend}）——health "
                f"是 post-ingest 审计，必须先完成 ingest")
        pred = (f"trade_date = toDate('{partition}')" if rd.backend == "ch"
                else f"trade_date = DATE '{partition}'")
        frame = rd.query_df(
            f"SELECT ts_code AS code, trade_date, open, high, low, close "
            f"FROM {table} WHERE {pred}")
        full = int(rd.query_rows(f"SELECT count() FROM {table}")[0][0])
    finally:
        rd.close()
    return frame, full


def _read_canonical_delta(watermark: str | None, dataset: str = DEFAULT_DATASET,
                          full_count: int | None = None) -> int:
    """canonical 中 ``trade_date > watermark`` 的行数（M1.5c §3.3 delta 腿）。

    ``watermark=None``（首跑/旧件）→ 返回全表计数 ``full_count``（delta=全量）。
    """
    if watermark is None:
        if full_count is None:
            raise ValueError("watermark=None 时必须给 full_count（全表计数）")
        return int(full_count)
    table = _table_name(dataset)
    from factorlab.app.bootstrap import open_read

    rd = open_read()
    try:
        pred = (f"trade_date > toDate('{watermark}')" if rd.backend == "ch"
                else f"trade_date > DATE '{watermark}'")
        return int(rd.query_rows(
            f"SELECT count() FROM {table} WHERE {pred}")[0][0])
    finally:
        rd.close()


def _day_literal(dtype: pl.DataType, text: str) -> pl.Expr:
    """按 raw trade_date dtype 生成字面量（Date 生产口径；String 兼容旧件）。"""
    if dtype == pl.Date:
        return pl.lit(str(text)).str.to_date(strict=False)
    if dtype == pl.String:
        return pl.lit(str(text))
    raise ValueError(
        f"raw trade_date dtype={dtype} 不支持（需 Date/String）——"
        f"health audit 不做类型猜测")


def _scan_raw(raw_path, *, day: str | None = None,
              start: str | None = None, end_exclusive: str | None = None
              ) -> pl.DataFrame:
    """懒扫描 + 过滤后 collect（raw 436MB 级——**不整表拉取**）。"""
    lf = pl.scan_parquet(str(raw_path))
    dtype = lf.collect_schema()["trade_date"]
    expr: pl.Expr | None = None
    if day is not None:
        expr = pl.col("trade_date") == _day_literal(dtype, day)
    if start is not None:
        lo = pl.col("trade_date") >= _day_literal(dtype, start)
        expr = lo if expr is None else (expr & lo)
    if end_exclusive is not None:
        hi = pl.col("trade_date") < _day_literal(dtype, end_exclusive)
        expr = hi if expr is None else (expr & hi)
    if expr is not None:
        lf = lf.filter(expr)
    return lf.collect()


def _publish_history_cli(args) -> int:
    """``publish-history`` 子命令入口（退出码 0/1/2）。"""
    try:
        report = publish_history(
            dataset_id=args.dataset, date_from=args.date_from,
            date_to=args.date_to, run_tag=args.run_tag, root=args.root,
            force=args.force, log=lambda line: print(line, flush=True))
    except ValueError as ex:
        print(f"错误：{ex}", file=sys.stderr)
        return 2
    if report["status"] == "no_dates":
        print(f"错误：范围内无分区日期（{report['date_from']} .. "
              f"{report['date_to']}）", file=sys.stderr)
        return 2
    if report["status"] == "rejected_systemic":
        print(f"错误：系统性检查未过，整批拒绝发布："
              f"{report['systemic_detail']}", file=sys.stderr)
        return 1
    print(f"[health] publish-history {report['date_from']}..{report['date_to']} "
          f"published={report['published']} skipped={report['skipped']} "
          f"passed={report['passed']} degraded={report['degraded']} "
          f"failed={report['failed']}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    """``publish``：日更链末步；``publish-history``：R37 批量历史发布。

    退出码（publish）：0 非 FAIL；1 FINAL FAIL；2 用法/输入错误。
    退出码（publish-history）：0 完成；1 系统性拒绝；2 无在范围日期/用法错误。
    """
    ap = argparse.ArgumentParser(prog="health", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    pub = sub.add_parser("publish", help="post-ingest audit + FINAL 门 + 发布 health")
    pub.add_argument("--root", default=None, help="data 根（缺省 factio DATA_ROOT）")
    pub.add_argument("--dataset", default=DEFAULT_DATASET)
    pub.add_argument("--run-tag", default=None, help="clean staging 目录标记")
    pub.add_argument("--partition", default="latest",
                     help="latest（用 staging summary）| YYYY-MM-DD")
    pub.add_argument("--raw", default=None, help="raw parquet（缺省 daily_fact）")
    pub.add_argument("--sample", type=int, default=3, help="腾讯抽样 symbol 数")
    pub.add_argument("--no-sample", action="store_true", help="跳过腾讯抽样")
    pub.add_argument("--dry-run", action="store_true")
    hist = sub.add_parser(
        "publish-history",
        help="scoped 全范围账本 + 逐分区 audit + health 批量历史发布")
    hist.add_argument("--from", dest="date_from", required=True,
                      help="ISO 起始交易日（含）")
    hist.add_argument("--to", dest="date_to", default=None,
                      help="ISO 结束交易日（含；缺省 = --from）")
    hist.add_argument("--run-tag", required=True,
                      help="发布标记（进 data_version/source_version）")
    hist.add_argument("--root", default=None, help="data 根（缺省 factio DATA_ROOT）")
    hist.add_argument("--dataset", default=DEFAULT_DATASET)
    hist.add_argument("--force", action="store_true",
                      help="重发已 PASS 分区（默认幂等跳过）")
    args = ap.parse_args(argv)
    if args.command == "publish-history":
        return _publish_history_cli(args)
    if args.command != "publish":     # pragma: no cover - argparse required
        ap.error(f"未知子命令 {args.command!r}")

    root = _resolve_root(args.root)
    run_tag = args.run_tag or _default_run_tag()
    summary_path = root / "staging" / args.dataset / run_tag / "summary.json"
    if not summary_path.is_file():
        print(f"错误：clean staging summary 不存在：{summary_path}"
              f"（先跑 data_quality/pipeline.py clean）", file=sys.stderr)
        return 2
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    partition = summary["partition"] if args.partition == "latest" else args.partition
    try:
        datetime.date.fromisoformat(partition)
    except ValueError:
        print(f"错误：partition 非法：{partition!r}", file=sys.stderr)
        return 2

    from data_quality import rules as dq_rules

    policy = dq_rules.load_policy()
    raw_path = args.raw
    if raw_path is None:
        from data_quality.pipeline import _resolve_raw

        raw_path = _resolve_raw(None)
    raw_part = _scan_raw(raw_path, day=partition)      # 不整表 collect
    start, _ = audit.sample_window(partition)
    baseline = _scan_raw(raw_path, start=start, end_exclusive=partition)
    symbols = []
    if not args.no_sample and raw_part.height:
        sym_col = audit.validators._SYM_ALIASES
        col = next(c for c in sym_col if c in raw_part.columns)
        symbols = sorted(raw_part[col].unique().to_list())[:max(args.sample, 0)]

    # M1.5c §3.3：门（FINAL）判 **delta**（trade_date > 上次链发布水位）；全表口径
    # 只作 `quality_backlog` 披露（不参与判定）。旧 staging summary（无 delta 键）
    # 退化为全表口径（向后兼容；watermark=None 即 delta=全量）。
    delta = summary.get("delta")
    watermark = summary.get("watermark")
    if delta is None:
        raw_full = int(pl.scan_parquet(str(raw_path)).select(pl.len())
                       .collect().item())
        delta = {"rows": raw_full, "clean_rows": int(summary["clean_rows"]),
                 "quarantined_rows": int(summary.get("quarantined_rows", 0)),
                 "deduped_rows": int(summary.get("deduped_rows", 0)),
                 "quality": summary.get("quality", {}),
                 "rules": summary.get("rules", {})}
        watermark = None

    metrics: audit.AuditMetrics | None
    if int(delta["rows"]) == 0:
        # 无新数据：PASS + note（backlog 照常披露；不跑 post-ingest 审计）
        final = audit.PASS
        note = NOTE_NO_NEW_DATA
        metrics = None
        completeness_doc = {"status": "COMPLETE", "expected_count": 0,
                            "actual_count": 0, "coverage": 1.0}
        quality = quality_block(delta.get("quality", {}), None, final)
        counts = {str(k): int(v) for k, v in delta.get("rules", {}).items()}
    else:
        canonical, canonical_full = _read_canonical(partition, args.dataset)
        delta_canonical = _read_canonical_delta(watermark, args.dataset,
                                                canonical_full)
        metrics = audit.audit_post_ingest(
            canonical, partition=partition,
            expected_count=int(delta["rows"]), actual_count=delta_canonical,
            explained_drops=(int(delta.get("quarantined_rows", 0))
                             + int(delta.get("deduped_rows", 0))),
            reconcile_expected=int(delta["clean_rows"]),
            reconcile_actual=delta_canonical,
            raw=raw_part, baseline=baseline, sample_symbols=symbols,
            transport=None if args.no_sample else audit.http_get_text)
        final = audit.decide_final(summary["decision"], metrics, policy)
        note = None
        completeness_doc = {"status": metrics.completeness.status,
                            "expected_count": metrics.completeness.expected_count,
                            "actual_count": metrics.completeness.actual_count,
                            "coverage": metrics.completeness.coverage}
        quality = quality_block(delta.get("quality", {}), metrics, final)
        counts = merge_rules(delta.get("rules", {}),
                             _rule_counts(metrics.results))

    backlog = _backlog_block(summary)
    path = publish_health(
        dataset_id=args.dataset, partition=partition,
        data_version=f"v{run_tag}_01", dq_policy_version=policy.dq_policy_version,
        repair_policy_version=policy.dq_policy_version,
        health_status=final, verification_state="VERIFIED",
        completeness=completeness_doc,
        quality=quality, rules_counts=counts, latest_trade_date=partition,
        quality_backlog=backlog, note=note,
        raw_path=raw_path, source_version=run_tag, root=root,
        dry_run=args.dry_run)
    if metrics is None:
        print(f"[health] {partition} health_status={final} note={note} "
              f"backlog_error={backlog['error_count']} -> {path}", flush=True)
    else:
        print(f"[health] {partition} health_status={final} "
              f"completeness={metrics.completeness.status} "
              f"pk_duplicates={metrics.pk_duplicates} "
          f"sample={metrics.sample.status} drift={metrics.drift.status} "
          f"-> {path}", flush=True)
    return 1 if final == audit.FAIL else 0


def _rule_counts(results) -> dict:
    out: dict[str, int] = {}
    for r in results:
        out[r.rule_id] = out.get(r.rule_id, 0) + 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
