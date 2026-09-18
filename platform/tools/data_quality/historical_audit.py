"""daily 全史体检（只审不改）+ 存量打标（Plan DQ-M1 T8）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-8-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §7（读取门三状态 + 过渡条款：存量无 health 分区 →
          ``verification_state=LEGACY_UNVERIFIED, health_status=UNKNOWN``，读取默认拒）
      §8（全史体检**只审不改**：问题清单按日期/股票/数据集；影响分析；
          **不首轮全史重跑**——修复留 M3 定向）
      §10 M1（daily 全史体检打标）。

职责边界（只审不改）
--------------------
- **禁止写 canonical/CH、禁止改任何 parquet/raw**：本模块只读 raw parquet；
  全流程零 ``write_parquet``（测试以 monkeypatch 断言）；
- 唯一允许的写：``data/health/<dataset>/<partition>.json`` 存量标记
  （不覆盖已发布文件）+ ``governance/evidence/**`` 报告（由 CLI ``--report-dir`` 指定）；
- 逐分区跑 T2 行级校验器（与增量链同一规则目录），把结果归约为
  **按日期/字段/规则的问题计数**；不修改任何行（隔离/修复动作属增量链）。

分块扫描（内存安全）
--------------------
按 ``chunk_dates``（默认 250 个交易日）分块懒扫描 + 前一日重叠 + 每票上一块
末行 carry：跨日规则（TIME_ORDER / ADJ_FACTOR_CA_MISMATCH 的 prev 比较）在块
边界（含跨块停牌缺口）仍与全表一次扫描的 ``shift(1).over(symbol)`` 语义对齐；
carry/重叠行的结果只归入其所属块（不重复计数）。单块内存与最大年切片同量级。

存量打标（§7 过渡条款）
-----------------------
范围内每个交易日：无 health 文件 → 写 ``UNKNOWN/LEGACY_UNVERIFIED``（completeness
UNKNOWN、quality 为实测计数但不作 VERIFIED 断言）；已有文件（新链发布）→ 跳过，
最后 ``refresh_dataset_summary`` 合并汇总（不覆盖/幂等）。

CLI
---
``python platform/tools/data_quality/historical_audit.py audit \
    --raw <daily_fact.parquet> --root <data root> --report-dir <dir>``
退出码：0 完成；2 用法/输入错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import polars as pl

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:      # 脚本直启（CLI）时补 tools/ 再导入
    sys.path.insert(0, str(_TOOLS))

from data_quality import health, rules, validators  # noqa: E402

DATASET = "ashare_daily"
DEFAULT_CHUNK_DATES = 250
LEGACY_DATA_VERSION = "LEGACY"
SOURCE_VERSION = "historical-audit"
MULTI_FIELD = "(multi)"
FRAME_PARTITION = "(frame)"

ISSUE_CSV = "issue_counts.csv"
SUMMARY_JSON = "summary.json"
IMPACT_MD = "impact-analysis.md"
RUN_JSON = "run.json"

_SYMBOLS = validators._SYM_ALIASES
_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s")

_ISSUE_DTYPES = {"trade_date": pl.String, "rule_id": pl.String,
                 "level": pl.String, "field": pl.String, "count": pl.Int64}
_LEVEL_COUNTS = {rules.FATAL: "fatal_count", rules.ERROR: "error_count",
                 rules.WARN: "warning_count", rules.INFO: "info_count"}


@dataclass(frozen=True)
class HistoricalAuditResult:
    """全史体检结果（报告 + 打标账本）。"""

    dataset: str
    start: str | None
    end: str | None
    partition_count: int
    row_count: int
    issue_counts: pl.DataFrame
    totals_by_rule: dict[str, int]
    totals_by_field: dict[str, int]
    summary: dict[str, Any]
    impact: dict[str, Any]
    legacy: dict[str, int]
    raw_path: str
    raw_sha256: str
    report_dir: Path | None = None
    elapsed_s: float = 0.0

    @property
    def issues(self) -> int:
        return int(self.issue_counts["count"].sum()) if self.issue_counts.height else 0


def _field_of(rule_id: str, detail: str | None) -> str:
    """结果 → 归因字段：RULE_FIELDS 优先；MISSING/NONFINITE/ADJ 从 detail 点名列。"""
    field = rules.RULE_FIELDS.get(rule_id)
    if field:
        return field
    m = _FIELD_RE.match(detail or "")
    if m and rule_id in (rules.MISSING_VALUE, rules.NONFINITE_VALUE,
                         rules.ADJ_NEGATIVE, rules.PRICE_NONPOSITIVE):
        return m.group(1)
    return MULTI_FIELD


def _key_date(key: str) -> str | None:
    """行级 key ``symbol|date`` → date 字符串；帧级 key（无 ``|``）→ None。"""
    return key.rsplit("|", 1)[1] if "|" in key else None


def _to_date_expr(dtype: pl.DataType) -> pl.Expr | None:
    c = pl.col("trade_date")
    if dtype == pl.Date:
        return c
    if isinstance(dtype, pl.Datetime):
        return c.dt.date()
    if dtype == pl.String:
        return c.str.to_date(strict=False)
    if hasattr(dtype, "is_integer") and dtype.is_integer():
        return (c.cast(pl.String).str.zfill(8)
                .str.to_date(format="%Y%m%d", strict=False))
    return None


def _load_delisted(raw_path: Path) -> set[str] | None:
    p = raw_path.with_name("delisted_codes.parquet")
    if not p.is_file():
        return None
    try:
        return set(pl.read_parquet(p)["code"].cast(pl.String).to_list())
    except (OSError, pl.exceptions.PolarsError, KeyError):
        return None


def _sum(frame: pl.DataFrame, expr: pl.Expr) -> int:
    """布尔/数值表达式在帧上的非空求和。"""
    v = frame.select(expr.sum()).item()
    return int(v or 0)


def _probe_chunk(frame: pl.DataFrame, *, sym_col: str | None,
                 delisted: set[str] | None, probes: dict[str, int]) -> None:
    """实测影响分析输入（circ_mv 代用指标 / 退市 adj / amount 缺失）。"""
    cols = set(frame.columns)
    if frame.height == 0:
        return
    if "float_shares" in cols:
        fs = pl.col("float_shares").cast(pl.Float64, strict=False)
        probes["float_shares_missing_rows"] += _sum(frame, fs.is_null() | fs.is_nan())
        probes["float_shares_zero_rows"] += _sum(frame, fs == 0)
        probes["float_shares_negative_rows"] += _sum(frame, fs < 0)
    if "amount" in cols:
        a = pl.col("amount").cast(pl.Float64, strict=False)
        probes["amount_missing_rows"] += _sum(frame, a.is_null() | a.is_nan())
    if delisted and sym_col is not None:
        sym = pl.col(sym_col).cast(pl.String, strict=False)
        in_d = sym.is_in(sorted(delisted))
        sub = frame.filter(in_d)
        probes["delisted_rows"] += sub.height
        if "adj_factor" in cols and sub.height:
            af = pl.col("adj_factor").cast(pl.Float64, strict=False)
            probes["delisted_adj_missing_rows"] += _sum(
                sub, af.is_null() | af.is_nan())
            probes["delisted_adj_nonpositive_rows"] += _sum(sub, af <= 0)


def build_impact(*, totals_by_rule: dict[str, int], probes: dict[str, int],
                 affected_partitions: dict[str, int],
                 policy_version: str) -> dict[str, Any]:
    """影响分析骨架（§8）：高影响项 + 实测证据 + 下一步（M3 定向修复）。"""

    def rows(rule_id: str) -> int:
        return totals_by_rule.get(rule_id, 0)

    def parts(rule_id: str) -> int:
        return affected_partitions.get(rule_id, 0)

    categories = [
        {
            "id": "ADJ_NEGATIVE",
            "title": "复权因子负值（复权序列负价）",
            "rule_ids": [rules.ADJ_NEGATIVE],
            "rows": rows(rules.ADJ_NEGATIVE),
            "affected_partitions": parts(rules.ADJ_NEGATIVE),
            "impact": ("负因子整键在增量 clean 被行级隔离（不无痕改写），复权价序列"
                       "断裂会污染 lookback 类因子与回测；canonical 行数相对 raw 减少"),
            "next": "M3 定向往修：评估「置 NULL 不丢行」；核对 vendor 后复权价异常范围",
            "status": "measured",
        },
        {
            "id": "delisted_adj",
            "title": "退市股复权因子（adj sidecar 覆盖）",
            "rule_ids": [],
            "delisted_codes": probes.get("delisted_codes", 0),
            "rows_delisted": probes.get("delisted_rows", 0),
            "rows_adj_missing": probes.get("delisted_adj_missing_rows", 0),
            "rows_adj_nonpositive": probes.get("delisted_adj_nonpositive_rows", 0),
            "impact": ("退市股复权链断流 → 退市前区间无法复权，回测幸存者偏差修正"
                       "缺一段证据"),
            "next": "M3 定向修复：delisted_adj_factor sidecar 覆盖率与断点核查",
            "status": "measured" if probes.get("delisted_codes") else "no_sidecar",
        },
        {
            "id": "circ_mv",
            "title": "流通市值（circ_mv = close × float_shares）",
            "rule_ids": [],
            "float_shares_missing_rows": probes.get("float_shares_missing_rows", 0),
            "float_shares_zero_rows": probes.get("float_shares_zero_rows", 0),
            "float_shares_negative_rows": probes.get("float_shares_negative_rows", 0),
            "impact": ("canonical circ_mv 由 float_shares 派生：缺失 → NULL（不伪造），"
                       "零值 → 0 市值，均影响市值中性化/分组类因子"),
            "next": "M3 定向修复：CH 侧 circ_mv 与 float_shares 异常值核查",
            "status": "proxy_measured",
            "note": "M1 daily raw 无 circ_mv 列，以 float_shares 缺失/零值为代用指标",
        },
        {
            "id": "cross_frequency",
            "title": "跨频系统偏差（分钟 ↔ 日线）",
            "rule_ids": [rules.MINUTE_DAILY_MISMATCH],
            "rows": None,
            "impact": "分钟聚合与日线不一致的存量偏差影响分钟/日频混用研究",
            "next": "M2 三角验证（分钟聚合 vs 内建日线 vs 腾讯日线）时实测",
            "status": "deferred_to_M2",
        },
        {
            "id": "missing_value",
            "title": "核心字段缺失（尤以退市股 amount/adj 无源）",
            "rule_ids": [rules.MISSING_VALUE],
            "rows": rows(rules.MISSING_VALUE),
            "affected_partitions": parts(rules.MISSING_VALUE),
            "amount_missing_rows": probes.get("amount_missing_rows", 0),
            "impact": "WARN 保留不填充；下游特征需自行处理 NaN（Feature 层职责）",
            "next": "M3 calibration：按字段/证券状态分级，评估是否需来源补齐",
            "status": "measured",
        },
        {
            "id": "vwap_out_of_range",
            "title": "VWAP=amount/volume 越 [low,high]",
            "rule_ids": [rules.VWAP_OUT_OF_RANGE],
            "rows": rows(rules.VWAP_OUT_OF_RANGE),
            "affected_partitions": parts(rules.VWAP_OUT_OF_RANGE),
            "impact": "成交量额与价格区间矛盾 → 剔除该键（增量 clean 已隔离）",
            "next": "M3 定向核对：疑似单位/供应商口径问题（与 UNIT_SUSPECT 联动）",
            "status": "measured",
        },
        {
            "id": "adj_factor_ca_mismatch",
            "title": "复权因子变化日与 CA 事件不符",
            "rule_ids": [rules.ADJ_FACTOR_CA_MISMATCH],
            "rows": rows(rules.ADJ_FACTOR_CA_MISMATCH),
            "affected_partitions": parts(rules.ADJ_FACTOR_CA_MISMATCH),
            "impact": "因子事件日错配 → 复权口径漂移（WARN 保留，需 M3 核）",
            "next": "M3 定向核对 CA 事件源与 fq_factor 变化日",
            "status": "measured",
        },
    ]
    return {
        "dq_policy_version": policy_version,
        "scope": "daily 全史（raw 全表；每交易日为一个 partition）",
        "categories": categories,
        "limits": [
            "calendar/listing/limits 未接入（无权威来源）：TRADE_DATE_INVALID/"
            "LIST_* / LIMIT_BREACH 不计入本报告",
            "分区桶按交易日；跨日规则经「重叠一日 + 停牌 carry 行」扫描保证块边界"
            "不漏检（与全表一次扫描 shift 语义对齐）",
            "本报告只审不改：修复动作留 M3 定向（§8）",
        ],
    }


def mark_legacy_unknowns(
    *, days: list[dt.date], dataset: str, root: str | Path | None,
    policy: rules.DqPolicy, raw_sha256: str, day_counts: dict[str, dict],
    rules_by_day: dict[str, dict[str, int]],
    log: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """存量打标：无 health 的partition → UNKNOWN/LEGACY_UNVERIFIED（不覆盖）。

    返回 ``{"marked": n, "existing": m}``；有新增时重建数据集 summary（合并）。
    """
    log = log or (lambda line: None)
    base = Path(root) if root is not None else health._resolve_root(None)
    health_dir = base / "health" / dataset
    marked = existing = 0
    for day in days:
        iso = day.isoformat()
        if (health_dir / f"{iso}.json").is_file():
            existing += 1
            continue
        dayc = day_counts.get(iso, {})
        rows = int(dayc.get("rows", 0))
        errors = int(dayc.get("error_count", 0))
        doc = health.build_health_doc(
            dataset_id=dataset, partition=iso,
            data_version=LEGACY_DATA_VERSION,
            dq_policy_version=policy.dq_policy_version,
            health_status="UNKNOWN", verification_state="LEGACY_UNVERIFIED",
            completeness={"status": "UNKNOWN", "expected_count": None,
                          "actual_count": rows, "coverage": None},
            quality={
                "fatal_count": int(dayc.get("fatal_count", 0)),
                "error_count": errors,
                "warning_count": int(dayc.get("warning_count", 0)),
                "quarantine_count": int(dayc.get("quarantine_count", 0)),
                "error_rate": (errors / rows) if rows else 0.0,
                "systematic_issue": False,
                "systemic_detail": None,
            },
            rules_counts=rules_by_day.get(iso, {}),
            latest_trade_date=iso, source_version=SOURCE_VERSION,
            raw_sha256=raw_sha256)
        health.write_health_artifact(base, doc)
        marked += 1
    if marked:
        health.refresh_dataset_summary(health_dir)
        log(f"[historical-audit] 存量打标：marked={marked} existing={existing} "
            f"-> {health_dir}")
    return {"marked": marked, "existing": existing}


def audit_history(
    raw: str | Path,
    *,
    start: str | None = None,
    end: str | None = None,
    dataset: str = DATASET,
    calendar: Any = None,
    listing: Any = None,
    limits: Any = None,
    chunk_dates: int = DEFAULT_CHUNK_DATES,
    policy: rules.DqPolicy | None = None,
    root: str | Path | None = None,
    mark_legacy: bool = True,
    report_dir: str | Path | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> HistoricalAuditResult:
    """对 raw 全史逐分区体检（只读）并按需写存量标记 + 报告。

    ``start/end``（ISO 日期，含端点）限定扫描范围；缺省 = raw 全史。
    ``mark_legacy=False`` 只审不标；``dry_run=True`` 零落盘（报告也不写）。
    """
    t0 = time.monotonic()
    log = log or (lambda line: None)
    raw_path = Path(raw)
    if not raw_path.is_file():
        raise ValueError(f"raw parquet 不存在：{raw_path}")
    if chunk_dates < 1:
        raise ValueError(f"chunk_dates 必须 ≥ 1：{chunk_dates!r}")
    policy = policy or rules.load_policy()

    def _iso(text: str | None, what: str) -> dt.date | None:
        if text is None:
            return None
        try:
            return dt.date.fromisoformat(str(text))
        except ValueError as exc:
            raise ValueError(f"{what} 需为 ISO 日期（YYYY-MM-DD）：{text!r}") from exc

    start_d, end_d = _iso(start, "start"), _iso(end, "end")
    if start_d and end_d and start_d > end_d:
        raise ValueError(f"start({start_d}) > end({end_d})")

    lf = pl.scan_parquet(str(raw_path))
    schema = lf.collect_schema()
    if "trade_date" not in schema:
        raise ValueError("raw 缺少 trade_date 列（全史体检以交易日为分区）")
    date_expr = _to_date_expr(schema["trade_date"])
    if date_expr is None:
        raise ValueError(
            f"trade_date dtype={schema['trade_date']} 不支持（需 Date/Datetime/String/int）")

    days_df = (lf.select(date_expr.alias("_d")).filter(pl.col("_d").is_not_null())
               .group_by("_d").len().sort("_d").collect())
    if start_d is not None:
        days_df = days_df.filter(pl.col("_d") >= pl.lit(start_d))
    if end_d is not None:
        days_df = days_df.filter(pl.col("_d") <= pl.lit(end_d))
    if days_df.height == 0:
        raise ValueError("扫描范围内无交易日（检查 start/end 与 raw）")
    days: list[dt.date] = days_df["_d"].to_list()
    n_rows = int(days_df["len"].sum())

    day_counts: dict[str, dict[str, Any]] = {
        d.isoformat(): {"rows": n, "fatal_count": 0, "error_count": 0,
                        "warning_count": 0, "info_count": 0, "quarantine_count": 0}
        for d, n in zip(days, days_df["len"].to_list())}
    issue_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    day_quar: dict[str, set[str]] = {}
    rules_by_day: dict[str, dict[str, int]] = {}
    rules_with_days: dict[str, set[str]] = {}
    totals_by_field: dict[str, int] = {}
    probes: dict[str, int] = {
        "float_shares_missing_rows": 0, "float_shares_zero_rows": 0,
        "float_shares_negative_rows": 0, "amount_missing_rows": 0,
        "delisted_rows": 0, "delisted_adj_missing_rows": 0,
        "delisted_adj_nonpositive_rows": 0,
    }
    delisted = _load_delisted(raw_path)
    probes["delisted_codes"] = len(delisted) if delisted else 0

    raw_sha256 = health.sha256_file(raw_path)
    carry: pl.DataFrame | None = None
    for i in range(0, len(days), chunk_dates):
        chunk = days[i:i + chunk_dates]
        lo = days[i - 1] if i > 0 else chunk[0]      # 前一日重叠（跨日规则 prev）
        hi = chunk[-1]
        frame = lf.filter((date_expr >= pl.lit(lo)) & (date_expr <= pl.lit(hi))).collect()
        sym_col = next((c for c in _SYMBOLS if c in frame.columns), None)
        if carry is not None and carry.height and sym_col is not None:
            # 停牌缺口 carry：重叠日无该票行情时，用上一块末行维持
            # ``shift(1).over(symbol)`` 的全表语义（其结果归属旧日期，会被过滤）
            frame = pl.concat([carry, frame], how="vertical")
        if sym_col is not None and "trade_date" in frame.columns:
            frame = frame.sort([sym_col, "trade_date"], maintain_order=True)
        results = validators.validate_daily(frame, calendar, listing, limits)

        chunk_iso = {d.isoformat() for d in chunk}
        for r in results:
            d = _key_date(r.key)
            if d is None:
                d = FRAME_PARTITION
            elif _is_iso(d):
                if d not in chunk_iso:
                    continue
            else:
                d = FRAME_PARTITION

            key = (d, r.rule_id)
            entry = issue_by_key.setdefault(
                key, {"level": r.level, "field": _field_of(r.rule_id, r.detail),
                      "count": 0})
            entry["count"] += 1
            fields = rules_by_day.setdefault(d, {})
            fields[r.rule_id] = fields.get(r.rule_id, 0) + 1
            if d != FRAME_PARTITION:
                rules_with_days.setdefault(r.rule_id, set()).add(d)
            field = entry["field"]
            if field != MULTI_FIELD:
                totals_by_field[field] = totals_by_field.get(field, 0) + 1
            dc = day_counts.get(d)
            if dc is not None:
                dc[_LEVEL_COUNTS[r.level]] += 1
                if r.level == rules.ERROR and "|" in r.key:
                    day_quar.setdefault(d, set()).add(r.key)

        day_slice = frame.filter((date_expr >= pl.lit(chunk[0]))
                                 & (date_expr <= pl.lit(hi)))
        _probe_chunk(day_slice, sym_col=sym_col, delisted=delisted, probes=probes)
        if sym_col is not None and frame.height:
            carry = frame.unique(subset=[sym_col], keep="last")
        log(f"[historical-audit] chunk {i // chunk_dates + 1}/"
            f"{(len(days) + chunk_dates - 1) // chunk_dates}: "
            f"{chunk[0]}..{hi} rows={day_slice.height}")

    for iso, keys in day_quar.items():
        day_counts[iso]["quarantine_count"] = len(keys)

    issue_rows = []
    for (d, rule_id), entry in sorted(issue_by_key.items()):
        issue_rows.append({"trade_date": d, "rule_id": rule_id,
                           "level": entry["level"], "field": entry["field"],
                           "count": int(entry["count"])})
    issue_counts = (pl.DataFrame(issue_rows, schema=_ISSUE_DTYPES)
                    if issue_rows else pl.DataFrame(schema=_ISSUE_DTYPES))
    totals_by_rule: dict[str, int] = {}
    for (_d, rule_id), entry in issue_by_key.items():
        totals_by_rule[rule_id] = totals_by_rule.get(rule_id, 0) + entry["count"]
    totals_by_rule = dict(sorted(totals_by_rule.items()))
    totals_by_field = dict(sorted(totals_by_field.items()))

    per_partition = {}
    for iso, dc in day_counts.items():
        rows = int(dc["rows"])
        per_partition[iso] = {
            **dc,
            "error_rate": (dc["error_count"] / rows) if rows else 0.0,
        }
    top_error_dates = sorted(
        ({"trade_date": iso, "error_count": dc["error_count"],
          "error_rate": per_partition[iso]["error_rate"]}
         for iso, dc in day_counts.items() if iso != FRAME_PARTITION),
        key=lambda x: (-x["error_count"], x["trade_date"]))[:10]

    affected = {rid: len(ds) for rid, ds in rules_with_days.items()}
    impact = build_impact(totals_by_rule=totals_by_rule, probes=probes,
                          affected_partitions=affected,
                          policy_version=policy.dq_policy_version)

    legacy = {"marked": 0, "existing": 0}
    if mark_legacy and not dry_run:
        legacy = mark_legacy_unknowns(
            days=days, dataset=dataset, root=root, policy=policy,
            raw_sha256=raw_sha256, day_counts=day_counts,
            rules_by_day=rules_by_day, log=log)
    elif mark_legacy:
        legacy = {"marked": 0, "existing": 0, "would_mark": len(days)}

    summary = {
        "dataset": dataset,
        "scope": {"start": days[0].isoformat(), "end": days[-1].isoformat()},
        "partition_count": len(days),
        "row_count": n_rows,
        "raw": {"path": str(raw_path), "sha256": raw_sha256},
        "dq_policy_version": policy.dq_policy_version,
        "totals_by_rule": totals_by_rule,
        "totals_by_field": totals_by_field,
        "top_error_dates": top_error_dates,
        "per_partition": per_partition,
        "legacy": legacy,
        "probes": probes,
    }
    result = HistoricalAuditResult(
        dataset=dataset, start=days[0].isoformat(), end=days[-1].isoformat(),
        partition_count=len(days), row_count=n_rows,
        issue_counts=issue_counts, totals_by_rule=totals_by_rule,
        totals_by_field=totals_by_field, summary=summary, impact=impact,
        legacy=legacy, raw_path=str(raw_path), raw_sha256=raw_sha256,
        elapsed_s=time.monotonic() - t0)
    if report_dir is not None and not dry_run:
        out = write_report(result, report_dir)
        result = replace(result, report_dir=out["dir"])
    return result


def _is_iso(text: str | None) -> bool:
    if text is None:
        return False
    try:
        dt.date.fromisoformat(text)
        return True
    except ValueError:
        return False


def render_impact_md(result: HistoricalAuditResult) -> str:
    """影响分析骨架 → Markdown（实测证据 + 下一步）。"""
    lines = [
        "# daily 全史体检——影响分析",
        "",
        f"- 范围：{result.start} .. {result.end}（{result.partition_count} 个交易日，"
        f"{result.row_count:,} 行）",
        f"- raw sha256：`{result.raw_sha256}`",
        f"- dq_policy：`{result.impact.get('dq_policy_version')}`",
        f"- 问题总命中：{result.issues:,}（按日期/字段/规则明细见 `issue_counts.csv`）",
        "",
        "> 只审不改（§8）：本报告不触发任何重写/修复；修复动作留 M3 定向。",
        "",
        "## 高影响项",
        "",
    ]
    for c in result.impact["categories"]:
        lines.append(f"### {c['title']}（`{c['id']}`）")
        for k in ("rows", "affected_partitions", "delisted_codes",
                  "rows_delisted", "rows_adj_missing", "rows_adj_nonpositive",
                  "float_shares_missing_rows", "float_shares_zero_rows",
                  "float_shares_negative_rows", "amount_missing_rows"):
            if c.get(k) is not None:
                lines.append(f"- {k}: {c[k]}")
        lines.append(f"- 影响：{c.get('impact')}")
        lines.append(f"- 下一步：{c.get('next')}")
        lines.append(f"- 状态：`{c.get('status')}`"
                     + (f"（{c['note']}）" if c.get("note") else ""))
        lines.append("")
    lines += ["## 口径限制", ""]
    lines += [f"- {x}" for x in result.impact.get("limits", [])]
    lines.append("")
    return "\n".join(lines)


def write_report(result: HistoricalAuditResult,
                 report_dir: str | Path) -> dict[str, Path]:
    """落报告：issue_counts.csv / summary.json / run.json / impact-analysis.md。"""
    d = Path(report_dir)
    d.mkdir(parents=True, exist_ok=True)
    result.issue_counts.write_csv(d / ISSUE_CSV)
    (d / SUMMARY_JSON).write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    (d / RUN_JSON).write_text(
        json.dumps({
            "tool": "data_quality/historical_audit.py",
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "raw": {"path": result.raw_path, "sha256": result.raw_sha256},
            "scope": {k: result.summary["scope"][k] for k in ("start", "end")},
            "partition_count": result.partition_count,
            "row_count": result.row_count,
            "issue_hits": result.issues,
            "legacy": result.legacy,
            "elapsed_s": round(result.elapsed_s, 3),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (d / IMPACT_MD).write_text(render_impact_md(result), encoding="utf-8")
    return {"dir": d, "issues": d / ISSUE_CSV, "summary": d / SUMMARY_JSON,
            "run": d / RUN_JSON, "impact": d / IMPACT_MD}


def _default_root() -> Path:
    from data_quality.pipeline import _resolve_root as _pipeline_root

    return _pipeline_root(None)


def _default_raw() -> Path:
    from data_quality.pipeline import _resolve_raw as _pipeline_raw

    return _pipeline_raw(None)


def main(argv: list[str] | None = None) -> int:
    """CLI：``audit`` 全史体检（只审不改 + 存量打标 + 报告）。退出码 0/2。"""
    ap = argparse.ArgumentParser(prog="historical_audit", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    au = sub.add_parser("audit", help="daily 全史逐分区体检（只审不改）")
    au.add_argument("--raw", default=None,
                    help="raw parquet（缺省 data/fact/daily_fact/daily_fact.parquet）")
    au.add_argument("--root", default=None, help="data 根（缺省 factio DATA_ROOT）")
    au.add_argument("--dataset", default=DATASET)
    au.add_argument("--start", default=None, help="ISO 起始交易日（含）")
    au.add_argument("--end", default=None, help="ISO 结束交易日（含）")
    au.add_argument("--chunk-dates", type=int, default=DEFAULT_CHUNK_DATES,
                    help=f"每块交易日数（默认 {DEFAULT_CHUNK_DATES}）")
    au.add_argument("--report-dir", default=None,
                    help="报告落点（缺省不写报告，如 governance/evidence/...）")
    au.add_argument("--no-mark-legacy", action="store_true",
                    help="只审不标（默认写存量 UNKNOWN/LEGACY_UNVERIFIED 标记）")
    au.add_argument("--dry-run", action="store_true",
                    help="零落盘（不写标记、不写报告）")
    args = ap.parse_args(argv)
    if args.command != "audit":       # pragma: no cover - argparse required
        ap.error(f"未知子命令 {args.command!r}")

    raw = Path(args.raw) if args.raw else _default_raw()
    if not raw.is_file():
        print(f"错误：raw parquet 不存在：{raw}", file=sys.stderr)
        return 2
    try:
        res = audit_history(
            raw, start=args.start, end=args.end, dataset=args.dataset,
            chunk_dates=args.chunk_dates, root=args.root,
            mark_legacy=not args.no_mark_legacy, report_dir=args.report_dir,
            dry_run=args.dry_run, log=lambda line: print(line, flush=True))
    except ValueError as ex:
        print(f"错误：{ex}", file=sys.stderr)
        return 2
    print(f"[historical-audit] {res.start}..{res.end} partitions="
          f"{res.partition_count} rows={res.row_count} issue_hits={res.issues} "
          f"legacy(marked={res.legacy.get('marked')}, "
          f"existing={res.legacy.get('existing')}) "
          f"-> {res.report_dir or '(no report)'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
