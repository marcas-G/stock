"""clean staging + PRE-INGEST 分区门 + ingest 触发（Plan DQ-M1 T5）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-5-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §3（两阶段门）/ §5（FETCH → RAW STAGING → ... → CLEAN STAGING →
      PRE-INGEST PARTITION GATE → CANONICAL INGEST；落点
      ``data/staging/<dataset>/<partition>/`` + ``data/quarantine/``；flock + ``_SUCCESS``）。

``run_clean_stage``（单分区编排，纯函数 + 落盘副作用）
------------------------------------------------------
1. **校验前按 ``(symbol, trade_date)`` 稳定排序**（TIME_ORDER 依赖行序，控制者裁定）；
2. ``validate_daily`` → ``repair``（确定性修复 + 行级隔离）；
3. ``aggregate``（消费**完整** results，含 WARN/INFO）→ ``detect_systemic`` →
   ``decide_pre_ingest``；
4. 落盘（非 dry-run）：
   - quarantine：有隔离行才写（``write_quarantine``，含索引）；
   - clean staging：``rows.parquet`` + ``summary.json``；判定 ≠ FAIL 才落
     ``_SUCCESS``（FAIL 分区保留候选行但**无完成标记**，消费侧不可信）；
5. 判定 ≠ FAIL 且非 dry-run → 调用 ``ingest(result)``（阶段链里 ingest 是下一链步，
   由 CLI 退出码阻断；函数内回调供编排/测试用）。

安全语义：FAIL → 不落 staging 完成标记、不触发 ingest；``--dry-run`` 不落任何盘、
不触发 ingest。重写分区前先撤销旧 ``_SUCCESS``（撕裂防护，同 quarantine）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import polars as pl

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from data_quality import aggregate, repair, rules, validators  # noqa: E402
from data_quality.aggregate import Metrics, SystemicDetail  # noqa: E402
from data_quality.rules import DqPolicy  # noqa: E402

PASS = aggregate.PASS
DEGRADED = aggregate.DEGRADED
FAIL = aggregate.FAIL

DEFAULT_DATASET = "ashare_daily"


@dataclass(frozen=True)
class CleanStageResult:
    """单分区 clean 阶段结果（阶段链/编排/测试的返回契约）。"""

    dataset: str
    partition: str
    decision: str
    metrics: Metrics
    systemic: SystemicDetail | None
    clean_rows: int
    quarantined_rows: int
    staging_dir: Path | None
    quarantine_dir: Path | None
    dry_run: bool

    @property
    def ok(self) -> bool:
        """门通过（PASS/DEGRADED 可入 canonical；FAIL 阻断）。"""
        return self.decision != FAIL


def run_clean_stage(
    raw: pl.DataFrame,
    partition: str,
    *,
    dataset: str = DEFAULT_DATASET,
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
    """单分区：校验 → 修复隔离 → 聚合/门 → clean staging →（可选）ingest 回调。

    ``raw`` 必须是**该分区**的输入行（调用方负责按 partition 过滤）；``expected_count``
    缺省 = 实际行数（M1 自洽口径；completeness 的独立来源由 T6 post-ingest audit
    经 stock_basic 提供）。
    """
    policy = policy or rules.load_policy()
    log = log or (lambda line: None)
    actual = raw.height
    expected = actual if expected_count is None else expected_count

    df = _sort_rows(raw)
    results = validators.validate_daily(df, calendar, listing, limits)
    clean, quarantined, repair_log = repair.repair(df, results)
    metrics = aggregate.aggregate(results, expected, actual)
    systemic = aggregate.detect_systemic(metrics, df, policy)
    decision = aggregate.decide_pre_ingest(metrics, systemic, policy)
    log(f"[{dataset}] {partition}: decision={decision} "
        f"errors={metrics.error_count} quarantined={quarantined.height}")

    summary = _summary(dataset, partition, decision, metrics, systemic,
                       clean.height, quarantined.height, policy, dry_run)
    staging_dir: Path | None = None
    quarantine_dir: Path | None = None

    if not dry_run:
        base = _resolve_root(root)
        if quarantined.height:
            quarantine_dir = repair.write_quarantine(
                dataset, partition, quarantined,
                [e for e in repair_log if e.get("action") == "quarantine"],
                root=base)
        staging_dir = _write_staging(base, dataset, partition, clean, summary,
                                     mark=decision != FAIL)

    result = CleanStageResult(
        dataset=dataset, partition=str(partition), decision=decision,
        metrics=metrics, systemic=systemic, clean_rows=clean.height,
        quarantined_rows=quarantined.height, staging_dir=staging_dir,
        quarantine_dir=quarantine_dir, dry_run=dry_run)

    if not dry_run and result.ok and ingest is not None:
        ingest(result)
    return result


# ── 落盘 ─────────────────────────────────────────────────────────────────
def _write_staging(
    data_root: Path,
    dataset: str,
    partition: str,
    clean: pl.DataFrame,
    summary: dict,
    *,
    mark: bool,
) -> Path:
    """clean staging 分区：rows + summary 原子替换，``_SUCCESS`` 最后落。

    重写前先撤销旧完成标记（撕裂防护：半写分区不得被消费）。
    """
    d = Path(data_root) / "staging" / dataset / str(partition)
    d.mkdir(parents=True, exist_ok=True)
    marker = d / "_SUCCESS"
    if marker.exists():
        marker.unlink()

    tmp_rows = d / ".rows.parquet.tmp"
    clean.write_parquet(tmp_rows)
    os.replace(tmp_rows, d / "rows.parquet")

    tmp_sum = d / ".summary.json.tmp"
    tmp_sum.write_text(json.dumps(summary, ensure_ascii=False, indent=2,
                                  sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_sum, d / "summary.json")

    if mark:
        marker.touch()
    return d


def _summary(dataset: str, partition: str, decision: str, metrics: Metrics,
             systemic: SystemicDetail | None, clean_rows: int,
             quarantined_rows: int, policy: DqPolicy, dry_run: bool) -> dict:
    systematic = systemic is not None
    return {
        "dataset": dataset,
        "partition": str(partition),
        "dq_policy_version": policy.dq_policy_version,
        "decision": decision,
        "clean_rows": clean_rows,
        "quarantined_rows": quarantined_rows,
        "quality": {
            "fatal_count": metrics.fatal_count,
            "error_count": metrics.error_count,
            "warning_count": metrics.warning_count,
            "info_count": metrics.info_count,
            "quarantine_count": metrics.quarantine_count,
            "error_rate": metrics.error_rate,
            "systematic_issue": systematic,
            "systemic_detail": (systemic.detail if systematic else None),
            "unresolved_partition_error": metrics.unresolved_partition_error,
        },
        "completeness": {
            "expected_count": metrics.expected_count,
            "actual_count": metrics.actual_count,
            "coverage": metrics.coverage,
        },
        "rules": metrics.rules,
        "dry_run": dry_run,
    }


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


# ── CLI（阶段链入口：pipeline.py clean --partition latest）────────────────
def _partition_dates(raw: Path) -> list[dt.date]:
    lf = pl.scan_parquet(str(raw))
    col = (pl.col("trade_date").cast(pl.Date, strict=False).alias("_d"))
    return sorted(lf.select(col).unique().collect()["_d"].drop_nulls().to_list())


def _read_partition(raw: Path, day: dt.date) -> pl.DataFrame:
    lf = pl.scan_parquet(str(raw))
    return lf.filter(pl.col("trade_date").cast(pl.Date, strict=False) == day).collect()


def main(argv: list[str] | None = None) -> int:
    """CLI：clean 一个/全部 daily 分区；任一分区 FAIL → 退出码 1（阻断 ingest）。

    退出码：0 全部分区非 FAIL；1 存在 FAIL；2 用法/输入错误。
    """
    ap = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    clean = sub.add_parser("clean", help="raw parquet → clean staging + PRE-INGEST 门")
    clean.add_argument("--raw", default=None,
                       help="raw parquet（缺省 data/fact/daily_fact/daily_fact.parquet）")
    clean.add_argument("--root", default=None, help="data 根（缺省 factio DATA_ROOT）")
    clean.add_argument("--dataset", default=DEFAULT_DATASET)
    clean.add_argument("--partition", default="latest",
                       help="latest（默认）| all | YYYY-MM-DD")
    clean.add_argument("--dry-run", action="store_true",
                       help="只判定不落盘、不触发 ingest")
    args = ap.parse_args(argv)

    if args.command != "clean":         # pragma: no cover - argparse required
        ap.error(f"未知子命令 {args.command!r}")

    raw = _resolve_raw(args.raw)
    if not raw.is_file():
        print(f"错误：raw 文件不存在：{raw}", file=sys.stderr)
        return 2
    try:
        all_days = _partition_dates(raw)
    except (OSError, pl.exceptions.PolarsError) as ex:
        print(f"错误：读取 raw 失败：{ex}", file=sys.stderr)
        return 2
    if not all_days:
        print(f"错误：raw 无可解析 trade_date：{raw}", file=sys.stderr)
        return 2

    if args.partition == "latest":
        days = [all_days[-1]]
    elif args.partition == "all":
        days = list(all_days)
    else:
        try:
            want = dt.date.fromisoformat(args.partition)
        except ValueError:
            print(f"错误：--partition 需为 latest|all|YYYY-MM-DD："
                  f"{args.partition!r}", file=sys.stderr)
            return 2
        if want not in all_days:
            print(f"错误：raw 中无分区 {want.isoformat()}", file=sys.stderr)
            return 2
        days = [want]

    worst = 0
    for day in days:
        df = _read_partition(raw, day)
        res = run_clean_stage(df, day.isoformat(), dataset=args.dataset,
                              root=args.root, dry_run=args.dry_run,
                              log=lambda line: print(line, flush=True))
        print(f"[pipeline] {res.partition} decision={res.decision} "
              f"clean={res.clean_rows} quarantined={res.quarantined_rows}",
              flush=True)
        if res.decision == FAIL:
            worst = 1
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
