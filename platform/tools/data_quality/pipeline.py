"""clean staging（全表） + PRE-INGEST 分区门 + ingest 触发（Plan DQ-M1 T5 / 修复轮 1 I1）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-5-brief.md
       + 修复轮 1 控制者裁定 I1/I3/M5。
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §3（两阶段门）/ §5（FETCH → RAW STAGING → ... → CLEAN STAGING →
      PRE-INGEST PARTITION GATE → CANONICAL INGEST）。

``run_clean_stage``（全表清洗编排，纯函数 + 落盘副作用）
--------------------------------------------------------
1. **校验前按 ``(symbol, trade_date)`` 稳定排序**（TIME_ORDER 依赖行序）；
2. 对**完整 daily_fact**（不按日切）``validate_daily`` → ``repair``（确定性修复 +
   行级隔离）——被隔离/dedup 的行**不产出**到 clean staging；
3. ``aggregate``（消费完整 results，含 WARN/INFO；quality 计数 = 清洗范围全量）→
   ``detect_systemic``（时间集中按范围内 ``trade_date`` 分布）→
   ``decide_pre_ingest``；
4. 落盘（非 dry-run）：
   - quarantine：有隔离行才写 ``data/quarantine/<dataset>/<run_tag>/``；**无隔离行
     时清理旧目录**（M5：防上一轮陈旧证据误导）；
   - clean staging：``data/staging/<dataset>/<run_tag>/daily_fact.parquet`` +
     ``summary.json``（``scope=full_table``、health ``partition`` = 范围内最新交易日）；
     判定 ≠ FAIL 才落 ``_SUCCESS``（FAIL 保留候选行但无完成标记，消费侧不可信）；
5. 判定 ≠ FAIL 且非 dry-run → 调用 ``ingest(result)``（canonical ingest；
   pan_update 链中 ingest 是下一链步并消费 ``staging_path``）。

安全语义：FAIL → 不落 staging 完成标记、不触发 ingest；``--dry-run`` 不落任何盘、
不触发 ingest。重写分区前先撤销旧 ``_SUCCESS``（撕裂防护，同 quarantine）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import polars as pl

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from data_quality import aggregate, health as dq_health, repair, rules, validators  # noqa: E402
from data_quality.aggregate import Metrics, SystemicDetail  # noqa: E402
from data_quality.rules import DqPolicy  # noqa: E402

PASS = aggregate.PASS
DEGRADED = aggregate.DEGRADED
FAIL = aggregate.FAIL

DEFAULT_DATASET = "ashare_daily"
SCOPE_FULL_TABLE = "full_table"
STAGED_FACT_NAME = "daily_fact.parquet"


@dataclass(frozen=True)
class CleanStageResult:
    """全表 clean 阶段结果（阶段链/编排/测试的返回契约）。

    M1.5c：``decision`` = **delta**（本轮更新增量）门判定；``metrics``/``systemic``
    同为 delta 口径；全表口径以 ``watermark``（水位）与 staging summary 的
    full_table 计数披露（health `quality_backlog` 源）。
    """

    dataset: str
    scope: str                 # 恒 "full_table"（清洗范围）
    run_tag: str               # staging/quarantine 目录粒度（日期戳）
    partition: str             # health partition = 清洗范围内最新交易日
    decision: str
    metrics: Metrics
    systemic: SystemicDetail | None
    clean_rows: int
    quarantined_rows: int
    staging_dir: Path | None
    staging_path: Path | None  # <staging_dir>/daily_fact.parquet（喂 ingest --source）
    quarantine_dir: Path | None
    dry_run: bool
    watermark: str | None = None   # 上次链发布 freshness（None = 首跑，delta=全量）
    delta_rows: int = 0            # 门作用域行数（trade_date > watermark）

    @property
    def ok(self) -> bool:
        """门通过（PASS/DEGRADED 可入 canonical；FAIL 阻断）。"""
        return self.decision != FAIL


def run_clean_stage(
    raw: pl.DataFrame,
    *,
    run_tag: str,
    dataset: str = DEFAULT_DATASET,
    partition: str = "latest",
    expected_count: int | None = None,
    calendar: Any = None,
    listing: Any = None,
    limits: Any = None,
    policy: DqPolicy | None = None,
    root: str | Path | None = None,
    dry_run: bool = False,
    ingest: Callable[[CleanStageResult], Any] | None = None,
    log: Callable[[str], None] | None = None,
) -> CleanStageResult:
    """全表：校验 → 修复隔离 → 聚合/门 → clean staging →（可选）ingest 回调。

    ``raw`` = **完整 daily_fact**（不是单日切片）；``expected_count`` 缺省 = 实际
    行数（M1 自洽口径；completeness 的独立来源由 T6 post-ingest audit 提供）。
    ``partition``：health 标记的交易日；``"latest"`` = 范围内最新交易日，或显式
    ISO 日期（必须在清洗范围内，否则 ValueError）。
    """
    policy = policy or rules.load_policy()
    log = log or (lambda line: None)
    base = _resolve_root(root)
    watermark = dq_health.last_published_trade_date(base, dataset)
    actual = raw.height
    expected = actual if expected_count is None else expected_count

    df = _sort_rows(raw)
    results = validators.validate_daily(df, calendar, listing, limits, policy)
    clean, quarantined, repair_log = repair.repair(df, results)
    # F5：full_table 完整性账本——raw = clean + quarantine + dedup（删除行数）
    deduped = sum(int(e.get("count", 0)) for e in repair_log
                  if e.get("action") == "dedup_identical")
    # ── M1.5c §3.3：门（PRE-INGEST）判 delta；全表口径只作 backlog 披露 ─────
    wd = dt.date.fromisoformat(watermark) if watermark else None
    delta_df, delta_results = _delta_scope(df, results, wd)
    delta_rows = delta_df.height if wd is not None else actual
    delta_expected = delta_rows if expected_count is None else expected_count
    if delta_rows == 0:
        metrics = _empty_metrics()          # 无新数据：0 行门（error_rate 0/coverage 1）
        systemic = None
    else:
        metrics = aggregate.aggregate(delta_results, delta_expected, delta_rows)
        systemic = aggregate.detect_systemic(metrics, delta_df, policy)
    decision = aggregate.decide_pre_ingest(metrics, systemic, policy)
    full_metrics = aggregate.aggregate(results, expected, actual)
    full_systemic = aggregate.detect_systemic(full_metrics, df, policy)
    delta_clean = _delta_count(clean, wd)
    delta_quarantined = _delta_count(quarantined, wd)
    delta_deduped = _delta_dedup_count(repair_log, wd)
    health_partition = _health_partition(clean, partition)
    date_range = _date_range(clean)

    log(f"[{dataset}] run_tag={run_tag} scope={SCOPE_FULL_TABLE} "
        f"gate_scope=delta watermark={watermark} delta_rows={delta_rows} "
        f"partition={health_partition}: decision={decision} "
        f"rows={actual} clean={clean.height} quarantined={quarantined.height} "
        f"(full_table error={full_metrics.error_count})")

    summary = _summary(
        dataset, run_tag, health_partition, decision, full_metrics,
        full_systemic, clean.height, quarantined.height, deduped,
        policy, dry_run, date_range, watermark=watermark,
        delta={"rows": delta_rows, "clean_rows": delta_clean,
               "quarantined_rows": delta_quarantined,
               "deduped_rows": delta_deduped,
               "quality": _quality_block(metrics, systemic),
               "rules": metrics.rules})
    staging_dir: Path | None = None
    staging_path: Path | None = None
    quarantine_dir: Path | None = None

    if not dry_run:
        if quarantined.height:
            quarantine_dir = repair.write_quarantine(
                dataset, run_tag, quarantined,
                [e for e in repair_log if e.get("action") == "quarantine"],
                root=base)
        else:
            _clear_quarantine(base, dataset, run_tag)     # M5：清理陈旧证据
        staging_dir, staging_path = _write_staging(
            base, dataset, run_tag, clean, summary, mark=decision != FAIL)

    result = CleanStageResult(
        dataset=dataset, scope=SCOPE_FULL_TABLE, run_tag=str(run_tag),
        partition=health_partition, decision=decision, metrics=metrics,
        systemic=systemic, clean_rows=clean.height,
        quarantined_rows=quarantined.height, staging_dir=staging_dir,
        staging_path=staging_path, quarantine_dir=quarantine_dir, dry_run=dry_run,
        watermark=watermark, delta_rows=delta_rows)

    if not dry_run and result.ok and ingest is not None:
        ingest(result)
    return result


# ── 落盘 ─────────────────────────────────────────────────────────────────
def _write_staging(
    data_root: Path,
    dataset: str,
    run_tag: str,
    clean: pl.DataFrame,
    summary: dict,
    *,
    mark: bool,
) -> tuple[Path, Path]:
    """clean staging 分区：daily_fact + summary 原子替换，``_SUCCESS`` 最后落。

    重写前先撤销旧完成标记（撕裂防护：半写分区不得被消费）。返回 (目录, 数据文件)。
    """
    d = Path(data_root) / "staging" / dataset / str(run_tag)
    d.mkdir(parents=True, exist_ok=True)
    marker = d / "_SUCCESS"
    if marker.exists():
        marker.unlink()

    fact = d / STAGED_FACT_NAME
    tmp_rows = d / f".{STAGED_FACT_NAME}.tmp"
    clean.write_parquet(tmp_rows)
    os.replace(tmp_rows, fact)

    tmp_sum = d / ".summary.json.tmp"
    tmp_sum.write_text(json.dumps(summary, ensure_ascii=False, indent=2,
                                  sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_sum, d / "summary.json")

    if mark:
        marker.touch()
    return d, fact


def _clear_quarantine(data_root: Path, dataset: str, run_tag: str) -> None:
    """无隔离行时的陈旧证据清理（M5）：整目录删除（标记/行/索引一起）。"""
    d = Path(data_root) / "quarantine" / dataset / str(run_tag)
    if d.exists():
        shutil.rmtree(d)


def _summary(dataset: str, run_tag: str, partition: str, decision: str,
             metrics: Metrics, systemic: SystemicDetail | None, clean_rows: int,
             quarantined_rows: int, deduped_rows: int, policy: DqPolicy,
             dry_run: bool, date_range: dict | None, *,
             watermark: str | None = None, delta: dict | None = None) -> dict:
    return {
        "dataset": dataset,
        "run_tag": str(run_tag),
        "scope": SCOPE_FULL_TABLE,          # 清洗范围（backlog 披露口径）
        "gate_scope": "delta",              # M1.5c：门作用域
        "watermark": watermark,             # 上次链发布 freshness（null=首跑）
        "partition": partition,
        "dq_policy_version": policy.dq_policy_version,
        "decision": decision,
        "clean_rows": clean_rows,
        "quarantined_rows": quarantined_rows,
        "deduped_rows": deduped_rows,
        "quality": _quality_block(metrics, systemic),   # full_table（披露）
        "completeness": {
            "expected_count": metrics.expected_count,
            "actual_count": metrics.actual_count,
            "coverage": metrics.coverage,
        },
        "delta": delta or {},               # 门作用域计数/质量/规则
        "date_range": date_range,
        "rules": metrics.rules,             # full_table（top_classes 源）
        "dry_run": dry_run,
    }


def _quality_block(metrics: Metrics, systemic: SystemicDetail | None) -> dict:
    return {
        "fatal_count": metrics.fatal_count,
        "error_count": metrics.error_count,
        "warning_count": metrics.warning_count,
        "info_count": metrics.info_count,
        "quarantine_count": metrics.quarantine_count,
        "error_rate": metrics.error_rate,
        "systematic_issue": systemic is not None,
        "systemic_detail": (systemic.detail if systemic else None),
        "unresolved_partition_error": metrics.unresolved_partition_error,
    }


# ── M1.5c：delta 作用域 helpers ───────────────────────────────────────────
def _empty_metrics() -> Metrics:
    """delta 空（无新数据）的零计数指标：error_rate 0 / coverage 1 → PASS 腿。"""
    return Metrics(
        expected_count=0, actual_count=0, fatal_count=0, error_count=0,
        warning_count=0, info_count=0, quarantine_count=0, error_rate=0.0,
        coverage=1.0, unresolved_partition_error=False, rules={},
        errors_by_field={}, errors_by_group={}, errors_by_date={})


def _delta_scope(df: pl.DataFrame, results: list,
                 wd: dt.date | None) -> tuple[pl.DataFrame, list]:
    """把校验结果缩到 delta（``trade_date > wd``）；``wd=None`` → 全量。

    行级结果按 ``symbol|date`` 键过滤；帧级（无 ``|``，schema FATAL）与日期不可
    解析的结果一律保留（不可定位到旧时代 → 保守计入）。
    """
    if wd is None:
        return df, results
    date_expr = validators._date_expr(df.schema["trade_date"])
    sym_col = next((c for c in validators._SYM_ALIASES if c in df.columns), None)
    if date_expr is None or sym_col is None:
        return df, results
    delta_df = df.filter(date_expr > pl.lit(wd))
    keys = set(delta_df.select(
        (pl.col(sym_col).cast(pl.String, strict=False) + pl.lit("|")
         + date_expr.cast(pl.String)).alias("_k"))["_k"].to_list())
    out = []
    for r in results:
        if "|" not in r.key or r.key in keys:
            out.append(r)
            continue
        _, _, day = r.key.partition("|")
        try:
            key_day = dt.date.fromisoformat(day)
        except ValueError:
            out.append(r)                      # 不可解析日期 → 保守计入
            continue
        if key_day > wd:
            out.append(r)
    return delta_df, out


def _delta_count(frame: pl.DataFrame, wd: dt.date | None) -> int:
    """frame（clean/quarantine）中 ``trade_date > wd`` 的行数；None → 全量。

    quarantine 行保持原始形态（trade_date 可能是 String）→ 经 validators 的
    日期表达式归一；不可解析 → 保守计全量。
    """
    if wd is None or frame.height == 0 or "trade_date" not in frame.columns:
        return frame.height
    expr = validators._date_expr(frame.schema["trade_date"])
    if expr is None:
        return frame.height
    return frame.filter(expr > pl.lit(wd)).height


def _delta_dedup_count(repair_log: list[dict], wd: dt.date | None) -> int:
    if wd is None:
        return sum(int(e.get("count", 0)) for e in repair_log
                   if e.get("action") == "dedup_identical")
    n = 0
    for e in repair_log:
        if e.get("action") != "dedup_identical":
            continue
        for key in e.get("keys", []):
            _, _, day = key.partition("|")
            try:
                if dt.date.fromisoformat(day) > wd:
                    n += 1
            except ValueError:
                n += 1
    return n


def _health_partition(clean: pl.DataFrame, partition: str) -> str:
    """health partition：``latest`` = 清洗范围内最新交易日；显式日期必须在范围内。"""
    days = _clean_days(clean)
    if partition == "latest":
        return days[-1].isoformat() if days else "unknown"
    want = dt.date.fromisoformat(partition)
    if days and want not in days:
        raise ValueError(
            f"partition {partition} 不在清洗范围（{days[0]}..{days[-1]}）")
    return want.isoformat()


def _clean_days(clean: pl.DataFrame) -> list[dt.date]:
    if clean.height == 0 or "trade_date" not in clean.columns:
        return []
    return sorted(clean["trade_date"].drop_nulls().unique().to_list())


def _date_range(clean: pl.DataFrame) -> dict | None:
    days = _clean_days(clean)
    if not days:
        return None
    return {"min": days[0].isoformat(), "max": days[-1].isoformat()}


def _resolve_root(root: str | Path | None) -> Path:
    if root is not None:
        return Path(root)
    from _env import ensure_platform
    ensure_platform()
    from factorlab.core.factio import paths
    return Path(paths.DATA_ROOT)


def _resolve_raw(raw: str | Path | None) -> Path:
    if raw is not None:
        return Path(raw)
    from _env import ensure_platform
    ensure_platform()
    from factorlab.core.factio import paths
    return Path(paths.daily_fact_path())


def _sort_rows(df: pl.DataFrame) -> pl.DataFrame:
    """校验前稳定排序（按 symbol → trade_date；TIME_ORDER 依赖行序）。"""
    sym_col = next((c for c in validators._SYM_ALIASES if c in df.columns), None)
    if sym_col is None or "trade_date" not in df.columns:
        return df
    return df.sort([sym_col, "trade_date"], maintain_order=True)


# ── CLI（阶段链入口：pipeline.py clean --partition latest --run-tag <tag>）──
def _default_run_tag() -> str:
    return dt.date.today().strftime("%Y%m%d")


def main(argv: list[str] | None = None) -> int:
    """CLI：对完整 daily_fact 做 clean（全表）；FAIL → 退出码 1（阻断 ingest）。

    退出码：0 非 FAIL；1 PRE-INGEST FAIL；2 用法/输入错误。
    """
    ap = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    clean = sub.add_parser("clean", help="完整 daily_fact → clean staging + PRE-INGEST 门")
    clean.add_argument("--raw", default=None,
                       help="raw parquet（缺省 data/fact/daily_fact/daily_fact.parquet）")
    clean.add_argument("--root", default=None, help="data 根（缺省 factio DATA_ROOT）")
    clean.add_argument("--dataset", default=DEFAULT_DATASET)
    clean.add_argument("--run-tag", default=None,
                       help=f"staging/quarantine 目录粒度（缺省今日 YYYYMMDD）")
    clean.add_argument("--partition", default="latest",
                       help="health 标记交易日：latest（默认）| YYYY-MM-DD")
    clean.add_argument("--dry-run", action="store_true",
                       help="只判定不落盘、不触发 ingest")
    args = ap.parse_args(argv)

    if args.command != "clean":         # pragma: no cover - argparse required
        ap.error(f"未知子命令 {args.command!r}")
    if args.partition != "latest":
        try:
            dt.date.fromisoformat(args.partition)
        except ValueError:
            print(f"错误：--partition 需为 latest|YYYY-MM-DD：{args.partition!r}",
                  file=sys.stderr)
            return 2

    raw = _resolve_raw(args.raw)
    if not raw.is_file():
        print(f"错误：raw 文件不存在：{raw}", file=sys.stderr)
        return 2
    try:
        df = pl.read_parquet(raw)
    except (OSError, pl.exceptions.PolarsError) as ex:
        print(f"错误：读取 raw 失败：{ex}", file=sys.stderr)
        return 2

    try:
        res = run_clean_stage(
            df, run_tag=args.run_tag or _default_run_tag(), dataset=args.dataset,
            partition=args.partition, root=args.root, dry_run=args.dry_run,
            log=lambda line: print(line, flush=True))
    except ValueError as ex:
        print(f"错误：{ex}", file=sys.stderr)
        return 2
    print(f"[pipeline] {res.partition} decision={res.decision} "
          f"clean={res.clean_rows} quarantined={res.quarantined_rows} "
          f"staging={res.staging_path}", flush=True)
    return 1 if res.decision == FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
