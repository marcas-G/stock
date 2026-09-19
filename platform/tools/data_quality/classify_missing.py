"""1.25M 命中 root-cause 分群（Plan DQ-M1.5 T3，只读）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m15/task-3-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §4③（核心字段 NaN 只登记原因不填充）/ §8（全史体检只审不改；定向修复留 M3）。

分群口径（规则可配置）
----------------------
对每个 ``(raw 行, 缺失字段)`` 命中按优先级判定：
1. ``LEGACY_SCHEMA``：``trade_date < field_first_date``（该字段数据集内首次可用日）；
2. ``TRUE_ERROR``：代码不在退市目录（无已知源解释）；
3. ``EXPECTED_MISSING``：``volume == 0``（停牌/无成交语义，无成交额）；
4. ``SOURCE_LIMITATION``：退市代码有成交但字段整列为空（vendor 导出缺列）。

R32 事实（本模块运行时会复算并写入报告）：
- 1,251,010 行三字段同时为 NULL（amount/float_shares/adj_factor），全部属于退市代码；
- 315 个退市码**全史**缺列（1,246,656 行），13 个退市码**部分**缺列（4,354 行）；
- 其中 volume==0 的 104,304 行是停牌 carry 行；volume>0 的 1,146,706 行是源缺口。

CLI
---
``python platform/tools/data_quality/classify_missing.py \
    --raw data/fact/daily_fact/daily_fact.parquet \
    --delisted data/fact/daily_fact/delisted_codes.parquet \
    --report-dir governance/evidence/verification/R33``

只读：不写任何 parquet/canonical；仅 ``--report-dir`` 下报告证据。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import polars as pl  # noqa: E402

from data_quality import health  # noqa: E402

FIELDS: tuple[str, ...] = ("amount", "float_shares", "adj_factor")

EXPECTED_MISSING = "EXPECTED_MISSING"
SOURCE_LIMITATION = "SOURCE_LIMITATION"
TRUE_ERROR = "TRUE_ERROR"
LEGACY_SCHEMA = "LEGACY_SCHEMA"
CLASSES = (EXPECTED_MISSING, SOURCE_LIMITATION, TRUE_ERROR, LEGACY_SCHEMA)
SPORADIC_GAP = "SPORADIC_GAP"
CLASS_PRIORITY: tuple[str, ...] = (
    TRUE_ERROR, LEGACY_SCHEMA, SOURCE_LIMITATION, EXPECTED_MISSING)
TAIL_MAX_GAP_DAYS = 5


@dataclass(frozen=True)
class ClassRules:
    """分群规则（可配置）。"""

    expected_when_volume_zero: bool = True
    legacy_field_first_dates: Mapping[str, dt.date] = field(default_factory=dict)


def market_of_code(code: str) -> str:
    """代码后缀 → 市场（SH/SZ/BJ）。"""
    suffix = code.rsplit(".", 1)[-1].upper() if "." in code else ""
    return suffix or "UNKNOWN"


def classify_missing_row(*, field: str, trade_date: dt.date,
                         volume: float | None, delisted: bool,
                         rules: ClassRules) -> tuple[str, str]:
    """单 (行, 字段) 命中 → (类, 原因)。"""
    first = rules.legacy_field_first_dates.get(field)
    if first is not None and trade_date < first:
        return LEGACY_SCHEMA, f"{field} 早于字段可用日 {first}（schema 未引入）"
    if not delisted:
        return TRUE_ERROR, f"非退市代码 {field} 缺失（无已知源解释）"
    if rules.expected_when_volume_zero and (volume is None or volume == 0):
        return EXPECTED_MISSING, "停牌/无成交（volume=0），成交额语义缺失属预期"
    return SOURCE_LIMITATION, "退市代码有成交但该字段整列缺失（vendor 导出缺列）"


def class_counts(hits: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """命中列表 → 类计数（仅含出现过的类，未出现的类不补 0）。"""
    out: dict[str, int] = {}
    for h in hits:
        out[h["klass"]] = out.get(h["klass"], 0) + 1
    return out


def aggregate_by(hits: Iterable[Mapping[str, Any]], *,
                 by: str) -> dict[Any, dict[str, int]]:
    """按维度聚合：``{维度值: {类: 计数}}``。"""
    out: dict[Any, dict[str, int]] = {}
    for h in hits:
        key = h.get(by)
        bucket = out.setdefault(key, {k: 0 for k in CLASSES})
        bucket[h["klass"]] = bucket.get(h["klass"], 0) + 1
    return out


def split_full_partial(
        per_code: Mapping[str, tuple[int, int]]) -> dict[str, int]:
    """``{code: (n_total, n_missing)}`` → 全史缺列/部分缺列代码数。"""
    full = sum(1 for _t, m in per_code.values() if m > 0 and m == _t)
    partial = sum(1 for t, m in per_code.values() if 0 < m < t)
    return {"full": full, "partial": partial}


def terminal_tail_dates(dates: Iterable[dt.date], *,
                        max_gap_days: int = TAIL_MAX_GAP_DAYS) -> set[dt.date]:
    """代码缺列日期序列 → 抵达最新日期的末端连续段（跳空 ≤max_gap_days）。

    退市尾段（2026 退市前连续缺列）与历史孤立单日由此区分（F6）。
    """
    ds = sorted(set(dates))
    if not ds:
        return set()
    tail = [ds[-1]]
    for prev in reversed(ds[:-1]):
        if (tail[0] - prev).days <= max_gap_days:
            tail.insert(0, prev)
        else:
            break
    return set(tail)


def is_sporadic_gap(row: Mapping[str, Any], *,
                    terminal_tail: set[dt.date], partial_code: bool) -> bool:
    """残余类判据：部分缺列码、有成交、且不在退市尾段 → ``SPORADIC_GAP``。"""
    if not partial_code:
        return False
    volume = row.get("volume")
    if volume is None or volume <= 0:
        return False
    return row["trade_date"] not in terminal_tail


def resolve_row_class(classes: Mapping[str, str]) -> str:
    """行级类 = 字段类中按 :data:`CLASS_PRIORITY` 最高优先者。"""
    for klass in CLASS_PRIORITY:
        if klass in classes.values():
            return klass
    raise ValueError(f"空字段类集合: {classes}")


def read_delisted(path: str | Path) -> set[str]:
    return set(pl.read_parquet(str(path))["code"].to_list())


def scan_missing(raw: str | Path, delisted: set[str],
                 *, fields: Sequence[str] = FIELDS) -> pl.DataFrame:
    """只读取三字段任一缺失的 raw 行（read-only）。"""
    lf = pl.scan_parquet(str(raw))
    cond = None
    for c in fields:
        t = pl.col(c).is_null()
        cond = t if cond is None else (cond | t)
    return (lf.select(["trade_date", "code", "volume", "close", *fields])
            .filter(cond).collect(engine="streaming"))


def scan_per_code(raw: str | Path) -> dict[str, tuple[int, int]]:
    """``{code: (n_total, n_missing_amount)}``（整表 code/amount 两列 pass）。"""
    agg = (pl.scan_parquet(str(raw)).select(["code", "amount"])
           .group_by("code")
           .agg(pl.len().alias("n_total"),
                pl.col("amount").is_null().sum().alias("n_miss"))
           .collect(engine="streaming"))
    return {r["code"]: (int(r["n_total"]), int(r["n_miss"]))
            for r in agg.iter_rows(named=True)}


def field_first_dates(raw: str | Path,
                      fields: Sequence[str] = FIELDS) -> dict[str, dt.date]:
    """各字段在数据集内首次非空日期（LEGACY_SCHEMA 判据/证据）。"""
    lf = pl.scan_parquet(str(raw))
    got = lf.select([
        pl.col("trade_date").filter(pl.col(c).is_not_null()).min().alias(c)
        for c in fields
    ]).collect(engine="streaming").row(0, named=True)
    return {c: v for c, v in got.items() if v is not None}


def iter_row_hits(df: pl.DataFrame, delisted: set[str],
                  rules: ClassRules,
                  terminal_tails: Mapping[str, set[dt.date]] | None = None,
                  partial_codes: set[str] | None = None,
                  ) -> Iterable[dict[str, Any]]:
    """raw 缺失行 → 逐行命中（惰性，避免 3.75M 物化）。

    每个条目含行级 ``klass``（字段类最高优先）+ ``classes``（字段→类）+
    ``sporadic_gap``（残余类标注）。命中级展开由调用方按字段累计。
    """
    tails = terminal_tails or {}
    partials = partial_codes or set()
    for row in df.iter_rows(named=True):
        code = row["code"]
        trade_date = row["trade_date"]
        is_delisted = code in delisted
        classes: dict[str, str] = {}
        reasons: dict[str, str] = {}
        for field in FIELDS:
            if row.get(field) is not None:
                continue
            klass, reason = classify_missing_row(
                field=field, trade_date=trade_date, volume=row.get("volume"),
                delisted=is_delisted, rules=rules)
            classes[field] = klass
            reasons[field] = reason
        if not classes:
            continue
        yield {"code": code, "trade_date": trade_date, "year": trade_date.year,
               "market": market_of_code(code), "delisted": is_delisted,
               "volume": row.get("volume"), "classes": classes,
               "reasons": reasons, "klass": resolve_row_class(classes),
               "sporadic_gap": is_sporadic_gap(
                   row, terminal_tail=tails.get(code, set()),
                   partial_code=code in partials)}


def _md_table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(lines)


def _dual_table(title: str, dim: str, by_rows: dict[Any, dict[str, int]],
                by_hits: dict[Any, dict[str, int]]) -> list[str]:
    """行级/命中双口径表（F7：与 JSON 两个键一致）。"""
    keys = sorted(k for k in by_hits if k is not None)
    hit_totals = {k: sum(by_hits.get(k, {}).values()) for k in keys}
    row_totals = {k: sum(by_rows.get(k, {}).values()) for k in keys}
    return [
        f"## 按{dim}（行级）", "",
        _md_table([dim, *CLASSES, "行级合计"],
                  [[k, *[f"{by_rows.get(k, {}).get(c, 0):,}" for c in CLASSES],
                    f"{row_totals[k]:,}"] for k in keys]),
        "",
        f"## 按{dim}（命中 行×字段）", "",
        _md_table([dim, *CLASSES, "命中合计"],
                  [[k, *[f"{by_hits.get(k, {}).get(c, 0):,}" for c in CLASSES],
                    f"{hit_totals[k]:,}"] for k in keys]),
        "",
    ]


def build_report(*, counts: dict[str, int], by_field: dict[str, dict[str, int]],
                 by_year: dict[Any, dict[str, int]],
                 by_market: dict[Any, dict[str, int]],
                 by_year_rows: dict[Any, dict[str, int]],
                 by_market_rows: dict[Any, dict[str, int]],
                 by_delist: dict[Any, dict[str, int]],
                 code_split: dict[str, int],
                 row_total: int,
                 code_rows: dict[str, int],
                 sporadic: dict[str, Any],
                 first_dates: dict[str, dt.date],
                 extras: dict[str, Any],
                 raw: str, delisted_path: str) -> str:
    """生成分群报告（纯字符串组装）。"""
    total_hits = sum(counts.values())
    lines = [
        "# 1.25M 命中 root-cause 分群（Plan DQ-M1.5 T3，只读）",
        "",
        f"- 输入：`{raw}` ｜ 退市目录：`{delisted_path}`",
        f"- 唯一命中行：**{row_total:,}**（amount/float_shares/adj_factor 三字段同缺）；"
        f"命中（行×字段）：**{total_hits:,}**",
        f"- 代码分流：全史缺列 {code_split['full']} 码（{code_rows['full_rows']:,} 行，"
        f"其中有成交 {code_rows['full_traded']:,} 行）｜ 部分缺列 "
        f"{code_split['partial']} 码（{code_rows['partial_rows']:,} 行，"
        f"其中有成交 {code_rows['partial_traded']:,} 行）",
        f"- 字段首次可用日（LEGACY_SCHEMA 判据）："
        + "，".join(f"{k}={v}" for k, v in sorted(first_dates.items())),
        "",
        "## 四分类计数（行×字段）",
        "",
        _md_table(["类", "命中数", "占比", "备注"], [
            [EXPECTED_MISSING, f"{counts.get(EXPECTED_MISSING, 0):,}",
             f"{counts.get(EXPECTED_MISSING, 0) / total_hits * 100:.3f}%",
             "volume=0 停牌 carry 行"],
            [SOURCE_LIMITATION, f"{counts.get(SOURCE_LIMITATION, 0):,}",
             f"{counts.get(SOURCE_LIMITATION, 0) / total_hits * 100:.3f}%",
             f"退市码源缺列；其中残余 {SPORADIC_GAP}(待查) "
             f"{sporadic['hits']:,}（行级 {sporadic['rows']:,}）"],
            [TRUE_ERROR, f"{counts.get(TRUE_ERROR, 0):,}",
             f"{counts.get(TRUE_ERROR, 0) / total_hits * 100:.3f}%",
             "**在市码 0 命中**；退市码残余归 SPORADIC_GAP(待查)"],
            [LEGACY_SCHEMA, f"{counts.get(LEGACY_SCHEMA, 0):,}",
             f"{counts.get(LEGACY_SCHEMA, 0) / total_hits * 100:.3f}%",
             "字段全史可用（>= 1990-12-19）"],
        ]),
        "",
        "## 残余类 SPORADIC_GAP（待查；为 SOURCE_LIMITATION 的子标注，"
        "不改变四分类总数）",
        "",
        _md_table(["代码", "行数", "日期", "备注"],
                  [[c, n, ", ".join(str(d) for d in dates), "历史孤立单日缺列（非退市尾段）"]
                   for c, (n, dates) in sorted(sporadic["by_code"].items())]),
        "",
        "## 按字段",
        "",
        _md_table(["字段", *CLASSES],
                  [[f, *[f"{by_field.get(f, {}).get(k, 0):,}" for k in CLASSES]]
                   for f in FIELDS]),
        "",
        *_dual_table("年份", "年份", by_year_rows, by_year),
        *_dual_table("市场", "市场", by_market_rows, by_market),
        "## 按退市状态（行级）",
        "",
        _md_table(["退市", *CLASSES],
                  [["True(退市)" if d else "False(在市)",
                    *[f"{by_delist.get(d, {}).get(k, 0):,}" for k in CLASSES]]
                   for d in sorted(k for k in by_delist if k is not None)]),
        "",
        "## 补充异常（非缺失，供 Task 2/健康交叉核对）",
        "",
        _md_table(["项", "行数", "备注"], [
            ["float_shares == 0", f"{extras['float_shares_zero']:,}",
             "circ_mv=0；代码 " + ",".join(extras["float_shares_zero_codes"])],
            ["adj_factor <= 0", f"{extras['adj_nonpositive']:,}",
             "其中 <0 为 ADJ_NEGATIVE（Task 2 字段级语义冻结范围）"],
            ["amount > 0 行中 volume 缺失", f"{extras['volume_null']:,}",
             "volume 全表无 NULL（本项应为 0）"],
        ]),
        "",
        "## 影响范围（消费方，引用现状）",
        "",
        "- 因子 spec：`amount` 36 条（reversal_20d 27 / intraday 5 / momentum_20d 2 / "
        "misc 1 / liquidity 1）；`circ_mv` 10 条（crash_bottom_leader 7 / size 2 / "
        "reversal_20d 1）；`float_shares`/`adj_factor` 无 spec 直引（经复权视图与 "
        "daily_basic 派生）。",
        "- 平台读路径：`adapters/read/adjust.py`（adj_factor 复权视图）、"
        "`adapters/catalog.py`、`research/data_bars.py`、`core/engine/forward.py`、"
        "`adapters/ic_kernel.py`、`core/execution/valuation.py`（circ_mv 市值）、"
        "`tools/universe_stages/**`、`tools/1m_features`（日级注入 amount/volume）、"
        "`tools/ch_ingest/ingest_daily.py`（canonical 三表派生）。",
        "- 语义：缺失保持 NULL（不填充）；退市股金额/复权链断流影响退市前区间复权与"
        "幸存者偏差修正、流通市值类因子（circ_mv 为 0 或 NULL）。",
        "",
        "## 修复候选（本阶段不修数据）",
        "",
        _md_table(["类", "可否 backfill", "源/手段", "成本", "优先级"], [
            [EXPECTED_MISSING, "无需（语义为无成交）",
             "保持 NULL + 显式 flag（停牌行）", "低", "P3"],
            [SOURCE_LIMITATION, "部分可",
             "退市码 amount：网易/腾讯历史 CSV（含成交额）核对后回填**（待验证："
             "源对退市码 amount/adj 的历史覆盖未实测）**；"
             "adj：扩展现有 delisted_adj_factor sidecar 全史；"
             "float_shares：需股本结构源（成本最高）", "中-高",
             "P1（adj/amount，待验证）/P2（float_shares）"],
            [f"P0 近端（部分码 {SPORADIC_GAP}/退市尾段）", "是",
             "2026 退市尾段+孤立单日明细见 "
             "`missing-classification-p0-recent.csv` / "
             "`missing-classification-sporadic-gap.csv`，走每日增量源回填",
             "低", "P0"],
            [TRUE_ERROR, "直接可（若有）", "增量源刷新即可（在市码 0 命中）", "低", "P0"],
            [LEGACY_SCHEMA, "不适用", "字段全史可用，无命中", "-", "-"],
        ]),
        "",
        "> 本报告只审不改：未写 canonical/CH，未改任何 raw/fact parquet。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="1.25M 命中 root-cause 分群（只读）")
    parser.add_argument("--raw", default="data/fact/daily_fact/daily_fact.parquet")
    parser.add_argument("--delisted",
                        default="data/fact/daily_fact/delisted_codes.parquet")
    parser.add_argument("--report-dir", default="governance/evidence/verification/R33")
    parser.add_argument("--rules", default=None,
                        help="可选规则覆盖（YAML/JSON：expected_when_volume_zero / "
                             "legacy_field_first_dates）")
    args = parser.parse_args(argv)

    delisted = read_delisted(args.delisted)
    first_dates = field_first_dates(args.raw)
    rules = ClassRules(legacy_field_first_dates=first_dates)
    if args.rules:
        import yaml
        raw_rules = yaml.safe_load(Path(args.rules).read_text(encoding="utf-8")) or {}
        legacy = {k: dt.date.fromisoformat(str(v)) for k, v in
                  (raw_rules.get("legacy_field_first_dates") or {}).items()}
        rules = ClassRules(
            expected_when_volume_zero=bool(
                raw_rules.get("expected_when_volume_zero",
                              rules.expected_when_volume_zero)),
            legacy_field_first_dates=legacy or dict(rules.legacy_field_first_dates))
    df = scan_missing(args.raw, delisted)
    per_code = scan_per_code(args.raw)

    code_split = split_full_partial(per_code)
    partial_codes = {c for c, (t, m) in per_code.items() if 0 < m < t}
    missing_by_code = df.group_by("code").len().rename({"len": "n_miss"})
    traded = df.filter(pl.col("volume") > 0).group_by("code").len().rename(
        {"len": "n_traded"})
    code_rows = {"full_rows": 0, "full_traded": 0,
                 "partial_rows": 0, "partial_traded": 0}
    for r in missing_by_code.join(traded, on="code", how="left").iter_rows(named=True):
        n_total, n_miss = per_code.get(r["code"], (0, 0))
        n_traded = int(r["n_traded"] or 0)
        if n_miss == n_total:
            code_rows["full_rows"] += int(r["n_miss"])
            code_rows["full_traded"] += n_traded
        else:
            code_rows["partial_rows"] += int(r["n_miss"])
            code_rows["partial_traded"] += n_traded

    terminal_tails: dict[str, set[dt.date]] = {}
    for code, grp in df.filter(pl.col("code").is_in(partial_codes)).group_by("code"):
        terminal_tails[code[0]] = terminal_tail_dates(grp["trade_date"].to_list())

    counts = {k: 0 for k in CLASSES}
    by_field: dict[str, dict[str, int]] = {f: {k: 0 for k in CLASSES} for f in FIELDS}
    by_year: dict[Any, dict[str, int]] = {}
    by_market: dict[Any, dict[str, int]] = {}
    by_delist: dict[Any, dict[str, int]] = {}
    by_year_rows: dict[Any, dict[str, int]] = {}
    by_market_rows: dict[Any, dict[str, int]] = {}
    sporadic_rows: list[dict[str, Any]] = []
    p0_rows: list[dict[str, Any]] = []
    for h in iter_row_hits(df, delisted, rules,
                           terminal_tails=terminal_tails,
                           partial_codes=partial_codes):
        row_bucket = by_delist.setdefault(h["delisted"], {k: 0 for k in CLASSES})
        row_bucket[h["klass"]] += 1
        for dim, by in (("year", by_year_rows), ("market", by_market_rows)):
            bucket = by.setdefault(h[dim], {k: 0 for k in CLASSES})
            bucket[h["klass"]] += 1
        if h["code"] in partial_codes and h["volume"] and h["volume"] > 0:
            p0_rows.append({"code": h["code"], "trade_date": h["trade_date"],
                            "volume": h["volume"], "market": h["market"],
                            "year": h["year"],
                            "segment": SPORADIC_GAP if h["sporadic_gap"] else "TAIL"})
        if h["sporadic_gap"]:
            sporadic_rows.append({"code": h["code"],
                                  "trade_date": h["trade_date"],
                                  "volume": h["volume"]})
        for field, klass in h["classes"].items():
            counts[klass] = counts.get(klass, 0) + 1
            by_field[field][klass] += 1
            for key, by in ((h["year"], by_year), (h["market"], by_market)):
                by.setdefault(key, {k: 0 for k in CLASSES})[klass] += 1

    sporadic_by_code: dict[str, list[dt.date]] = {}
    for r in sporadic_rows:
        sporadic_by_code.setdefault(r["code"], []).append(r["trade_date"])
    sporadic = {
        "rows": len(sporadic_rows),
        "hits": len(sporadic_rows) * len(FIELDS),
        "by_code": {c: (len(ds), sorted(ds)) for c, ds in sporadic_by_code.items()},
    }

    lf = pl.scan_parquet(str(args.raw))
    extras = {
        "float_shares_zero": int(lf.filter(
            pl.col("float_shares") == 0).select(pl.len()).collect().item()),
        "float_shares_zero_codes": sorted(set(
            lf.filter(pl.col("float_shares") == 0).select("code").collect()
            ["code"].to_list())),
        "adj_nonpositive": int(lf.filter(
            (pl.col("adj_factor") <= 0)).select(pl.len()).collect().item()),
        "volume_null": int(lf.select(
            pl.col("volume").is_null().sum()).collect().item()),
    }

    out_dir = Path(args.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = build_report(counts=counts, by_field=by_field, by_year=by_year,
                      by_market=by_market, by_year_rows=by_year_rows,
                      by_market_rows=by_market_rows, by_delist=by_delist,
                      code_split=code_split, row_total=df.height,
                      code_rows=code_rows, sporadic=sporadic,
                      first_dates=first_dates, extras=extras,
                      raw=args.raw, delisted_path=args.delisted)
    (out_dir / "missing-classification.md").write_text(md, encoding="utf-8")

    pl.DataFrame(p0_rows).sort(["code", "trade_date"]).write_csv(
        out_dir / "missing-classification-p0-recent.csv")
    pl.DataFrame(sporadic_rows).sort(["code", "trade_date"]).write_csv(
        out_dir / "missing-classification-sporadic-gap.csv")

    payload = {
        "raw": {"path": str(args.raw), "sha256": health.sha256_file(args.raw)},
        "delisted": args.delisted,
        "row_total": df.height, "total_hits": sum(counts.values()),
        "classes": counts, "by_field": by_field,
        "by_year": by_year, "by_year_rows": by_year_rows,
        "by_market": by_market, "by_market_rows": by_market_rows,
        "by_delist": by_delist,
        "code_split": code_split, "code_rows": code_rows,
        "sporadic_gap": sporadic,
        "rules": {"expected_when_volume_zero": rules.expected_when_volume_zero,
                  "legacy_field_first_dates": {
                      k: str(v) for k, v in rules.legacy_field_first_dates.items()}},
        "extras": extras}
    (out_dir / "missing-classification.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8")

    print(f"[classify-missing] rows={df.height} hits={sum(counts.values())} "
          f"{counts} sporadic={sporadic['rows']} "
          f"full={code_split['full']} partial={code_split['partial']} "
          f"-> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
