"""确定性修复 + 行级隔离（Plan DQ-M1 T3）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-3-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §1（FATAL/ERROR → quarantine；WARN/INFO 保留 + flag）
      §5（确定性修复清单**穷尽**：完全相同行去重；时间解析/时区规范化；
          schema/类型强制；其余一律 quarantine）
      §6（quarantine 索引字段 rule_id/reason/count）。

语义红线
--------
- ``PK_CONFLICT`` 等 ERROR → **该 key 的全部行**进 quarantine，禁止任何"选一条/
  优先级"逻辑；只有**全列完全一致**的行才允许 dedup。
- 隔离行保持原始形态（证据），规范化只作用于 clean。
- 不含任何统计美化：不 winsorize、不 fillna(0)、不佳值修补。

``repair(df, results) -> (clean_df, quarantined_df, repair_log)``
    repair_log：有序 dict 列表，action ∈ {dedup_identical, normalize_time,
    coerce_dtypes, quarantine}；quarantine 条目携带 rule_id/reason/count/keys，
    可直接喂给 ``write_quarantine`` 的 index。

``write_quarantine(dataset, partition, rows, index, *, root=None) -> Path``
    落 ``<root>/quarantine/<dataset>/<partition>/``：rows.parquet + index.json
    + ``_SUCCESS``（两文件原子替换后最后落标记）；幂等重写。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import polars as pl

from data_quality import rules, validators
from data_quality.rules import RuleResult

# schema/类型强制的期望类型（设计 §5 白名单；trade_date 由时间规范化处理）
_EXPECTED_DTYPES: dict[str, pl.DataType] = {
    "symbol": pl.String, "code": pl.String, "ts_code": pl.String,
    "trade_date": pl.Date,
    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
    "pre_close": pl.Float64, "change": pl.Float64, "pct_chg": pl.Float64,
    "volume": pl.Float64, "vol": pl.Float64, "amount": pl.Float64,
    "adj_factor": pl.Float64, "fq_factor": pl.Float64, "fq_deduct": pl.Float64,
    "float_shares": pl.Float64, "total_shares": pl.Float64,
    "div_cash": pl.Float64, "div_bonus": pl.Float64, "div_transfer": pl.Float64,
    "rights_num": pl.Float64, "rights_price": pl.Float64,
}


def repair(
    df: pl.DataFrame,
    results: list[RuleResult] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, list[dict[str, Any]]]:
    """执行确定性修复并隔离 ERROR/FATAL 命中的行（纯函数：不改输入）。"""
    results = list(results or [])
    log: list[dict[str, Any]] = []

    # ── 1) 完全相同行 dedup（全列一致才删；keep first + 保持原顺序）────────
    work = _with_key(df)
    has_key = "_key" in work.columns
    before = work.height
    if has_key:
        before_counts = work.group_by("_key").len().rename({"len": "_n0"})
    deduped = work.unique(keep="first", maintain_order=True)
    removed = before - deduped.height
    if removed:
        keys: list[str] = []
        if has_key:
            after_counts = (deduped.group_by("_key").len().rename({"len": "_n1"}))
            merged = (before_counts.join(after_counts, on="_key", how="left")
                      .with_columns(pl.col("_n1").fill_null(0)))
            keys = sorted(merged.filter(pl.col("_n0") > pl.col("_n1"))["_key"].to_list())
        log.append({"action": "dedup_identical", "count": removed, "keys": keys})
    work = deduped

    # ── 2) ERROR/FATAL 命中 key → 全部行 quarantine ──────────────────────
    blocking = [r for r in results
                if rules.SEVERITY[r.level] <= rules.SEVERITY[rules.ERROR]]
    if has_key and blocking:
        by_rule: dict[str, list[RuleResult]] = {}
        for r in blocking:
            if r.key:
                by_rule.setdefault(r.rule_id, []).append(r)

        cond: pl.Expr | None = None
        keys_by_rule: dict[str, list[str]] = {}
        for rid in sorted(by_rule):
            keys = sorted({r.key for r in by_rule[rid]})
            if not keys:
                continue
            keys_by_rule[rid] = keys
            m = pl.col("_key").is_in(keys).fill_null(False)
            cond = m if cond is None else (cond | m)
        if cond is not None:
            q_frame = work.filter(cond)
            clean = work.filter(~cond)
            for rid, keys in keys_by_rule.items():
                n = q_frame.filter(pl.col("_key").is_in(keys)).height
                if n:
                    log.append({"action": "quarantine", "rule_id": rid,
                                "reason": by_rule[rid][0].detail, "count": n,
                                "keys": keys})
        else:
            clean, q_frame = work, work.head(0)
    else:
        clean, q_frame = work, work.head(0)

    if "_key" in clean.columns:
        clean = clean.drop("_key")
    if "_key" in q_frame.columns:
        q_frame = q_frame.drop("_key")

    # ── 3) 时间解析/时区规范化（幂等）───────────────────────────────────
    clean, n_time = _normalize_time(clean)
    if n_time:
        log.append({"action": "normalize_time", "column": "trade_date",
                    "count": n_time})

    # ── 4) schema/类型强制（幂等；非法值归 NULL，绝不填 0）──────────────
    clean, coerced = _coerce_dtypes(clean)
    if coerced:
        log.append({"action": "coerce_dtypes", "count": len(coerced),
                    "columns": coerced})

    return clean, q_frame, log


def write_quarantine(
    dataset: str,
    partition: str,
    rows: pl.DataFrame,
    index: list[dict[str, Any]] | None = None,
    *,
    root: str | Path | None = None,
) -> Path:
    """原子落盘 quarantine 分区 + 索引（rule_id/reason/count）+ ``_SUCCESS``。

    ``root`` 缺省 = 工作区 data 根（``factorlab.core.factio.paths.DATA_ROOT``，
    测试传 tmp_path）；返回分区目录。
    """
    from factorlab.core.factio import paths

    base = (Path(root) if root is not None else paths.DATA_ROOT) / "quarantine"
    d = base / dataset / str(partition)
    d.mkdir(parents=True, exist_ok=True)

    tmp_rows = d / ".rows.parquet.tmp"
    rows.write_parquet(tmp_rows)
    os.replace(tmp_rows, d / "rows.parquet")

    entries = [{"rule_id": e.get("rule_id"), "reason": e.get("reason"),
                "count": e.get("count")} for e in (index or [])]
    doc = {"dataset": dataset, "partition": str(partition),
           "row_count": int(rows.height), "entries": entries}
    tmp_idx = d / ".index.json.tmp"
    tmp_idx.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    os.replace(tmp_idx, d / "index.json")

    (d / "_SUCCESS").touch()          # 完成标记最后落（半写分区不可信）
    return d


# ── helpers ──────────────────────────────────────────────────────────────
def _with_key(df: pl.DataFrame) -> pl.DataFrame:
    """加 ``_key = symbol|date``（与 validators 同式；不可定位时原帧返回）。"""
    sym_col = next((c for c in validators._SYM_ALIASES if c in df.columns), None)
    if sym_col is None or "trade_date" not in df.columns:
        return df
    date_expr = validators._date_expr(df.schema["trade_date"])
    if date_expr is None:
        return df
    return df.with_columns(
        (pl.col(sym_col).cast(pl.String, strict=False) + pl.lit("|")
         + pl.coalesce([date_expr.cast(pl.String),
                        pl.col("trade_date").cast(pl.String)])).alias("_key"))


def _normalize_time(df: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """trade_date → Date（时区统一 Asia/Shanghai，交易所本地日）。"""
    if "trade_date" not in df.columns:
        return df, 0
    dtype = df.schema["trade_date"]
    if dtype == pl.Date:
        return df, 0
    if isinstance(dtype, pl.Datetime):
        expr = pl.col("trade_date")
        if dtype.time_zone:
            expr = expr.dt.convert_time_zone("Asia/Shanghai")
        expr = expr.dt.date()
    elif dtype == pl.String:
        s = pl.col("trade_date").str.strip_chars()
        expr = pl.coalesce([
            s.str.to_date(format="%Y%m%d", strict=False),
            s.str.to_date(format="%Y-%m-%d", strict=False),
            s.str.to_date(format="%Y-%m-%d %H:%M:%S", strict=False),
        ])
    elif hasattr(dtype, "is_integer") and dtype.is_integer():
        s = pl.col("trade_date").cast(pl.String)
        expr = pl.coalesce([
            s.str.zfill(8).str.to_date(format="%Y%m%d", strict=False),
            s.str.to_date(format="%Y-%m-%d", strict=False),
        ])
    else:
        return df, 0
    n = int(df.select(expr.is_not_null().sum()).item() or 0)
    return df.with_columns(expr.alias("trade_date")), n


def _coerce_dtypes(df: pl.DataFrame) -> tuple[pl.DataFrame, list[str]]:
    """白名单列 → 期望类型（strict=False：不可转换值归 NULL，不做任何填补）。"""
    exprs, cols = [], []
    for c, dtype in _EXPECTED_DTYPES.items():
        if c == "trade_date" or c not in df.columns:
            continue
        if df.schema[c] == dtype:
            continue
        exprs.append(pl.col(c).cast(dtype, strict=False).alias(c))
        cols.append(c)
    if exprs:
        df = df.with_columns(exprs)
    return df, cols
