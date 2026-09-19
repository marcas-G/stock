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

import polars as pl

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:      # 脚本直启（阶段链）时补 tools/ 再导入
    sys.path.insert(0, str(_TOOLS))

from data_quality import audit, rules  # noqa: E402

HEALTH_STATUSES = ("PASS", "DEGRADED", "FAIL", "UNKNOWN")
VERIFICATION_STATES = ("VERIFIED", "LEGACY_UNVERIFIED", "KNOWN_ISSUE")
COMPLETENESS_STATUSES = ("COMPLETE", "INCOMPLETE", "UNKNOWN")

# spec §6 冻结键集（逐字段；M1.5 增 repair_policy_version —— 修复语义版本留痕）
TOP_KEYS = ("dataset_id", "partition", "data_version", "dq_policy_version",
            "repair_policy_version",
            "health_status", "verification_state", "completeness", "quality",
            "freshness", "rules", "validated_at", "raw_lineage")
COMPLETENESS_KEYS = ("status", "expected_count", "actual_count", "coverage")
QUALITY_KEYS = ("fatal_count", "error_count", "warning_count",
                "quarantine_count", "error_rate", "systematic_issue",
                "systemic_detail")
FRESHNESS_KEYS = ("latest_trade_date",)
RAW_LINEAGE_KEYS = ("source_version", "raw_sha256")

DEFAULT_DATASET = "ashare_daily"


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
                     latest_trade_date: str | None = None,
                     validated_at: str | None = None,
                     source_version: str | None = None,
                     raw_sha256: str | None = None) -> dict:
    """构造 §6 文档（键集/枚举校验；多键少键都 ValueError）。

    ``repair_policy_version``：修复/清洗语义版本留痕（M1.5 T2）；缺省 =
    ``dq_policy_version``（同一版本线），显式空串拒绝。
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


def quality_block(base_quality: dict, metrics: audit.AuditMetrics,
                  final_decision: str) -> dict:
    """health quality 块 = PRE 清洗计数（全表口径）+ post-ingest 审计结果。

    审计发现的 ERROR（PK 等）计入 error_count；WARN/SUSPECT 计入 warning_count；
    ``error_rate`` 以 audit.completeness 的独立分母计算（无分母沿用 base）。
    """
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


# ── CLI（阶段链末步）────────────────────────────────────────────────────
def _read_canonical(partition: str,
                    dataset: str = DEFAULT_DATASET) -> tuple[pl.DataFrame, int]:
    """读 canonical 分区帧 + 全表计数（生产 ch / 历史 duckdb）。

    返回 ``(partition_frame, full_table_count)``：分区帧供 PK/抽样/漂移，
    全表计数供 reconcile（clean staging 全表口径）；不整表拉取（内存安全）。
    表名 = dataset 去掉 ``ashare_`` 前缀。
    """
    table = dataset.split("_", 1)[-1] if dataset.startswith("ashare_") else dataset
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


def main(argv: list[str] | None = None) -> int:
    """``publish``：staging summary + canonical → audit → FINAL 门 → health 发布。

    退出码：0 非 FAIL；1 FINAL FAIL；2 用法/输入错误。
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
    args = ap.parse_args(argv)
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

    # F5：health quality/completeness 统一 **full_table** 口径——
    # expected = raw 全表行数；actual = clean 行数；差额须被确定性清洗账
    # （quarantine + dedup）解释；error_rate 分母同为 raw 全表。分区帧仍用于
    # PK/漂移/抽样，canonical 全表 count 用于 reconcile（clean vs canonical）。
    raw_full = int(pl.scan_parquet(str(raw_path)).select(pl.len())
                   .collect().item())
    clean_rows = int(summary["clean_rows"])
    explained = (int(summary.get("quarantined_rows", 0))
                 + int(summary.get("deduped_rows", 0)))
    canonical, canonical_full = _read_canonical(partition, args.dataset)
    metrics = audit.audit_post_ingest(
        canonical, partition=partition, expected_count=raw_full,
        actual_count=clean_rows, explained_drops=explained,
        reconcile_expected=clean_rows, reconcile_actual=canonical_full,
        raw=raw_part, baseline=baseline, sample_symbols=symbols,
        transport=None if args.no_sample else audit.http_get_text)
    final = audit.decide_final(summary["decision"], metrics, policy)
    quality = quality_block(summary.get("quality", {}), metrics, final)
    counts = merge_rules(summary.get("rules", {}),
                         _rule_counts(metrics.results))
    path = publish_health(
        dataset_id=args.dataset, partition=partition,
        data_version=f"v{run_tag}_01", dq_policy_version=policy.dq_policy_version,
        repair_policy_version=policy.dq_policy_version,
        health_status=final, verification_state="VERIFIED",
        completeness={"status": metrics.completeness.status,
                      "expected_count": metrics.completeness.expected_count,
                      "actual_count": metrics.completeness.actual_count,
                      "coverage": metrics.completeness.coverage},
        quality=quality, rules_counts=counts, latest_trade_date=partition,
        raw_path=raw_path, source_version=run_tag, root=root,
        dry_run=args.dry_run)
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
