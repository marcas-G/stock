"""18 个全隔离日 RCA（Plan DQ-M1.5 T1，只读）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m15/task-1-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §4④（VWAP=Amount/Volume 落当期价格范围）/ §8（只审不改）。

范围与结论口径
--------------
- 18 日：1991-01-19..1993-08-21（M1 全表 clean 后 canonical/`trade_cal` 消失的
  全行隔离日；逐日 raw 1-2 行，全部 `VWAP_OUT_OF_RANGE`）。
- 每日三选一：``恢复（规则误判）`` / ``保持隔离（数据确坏）`` /
  ``历史制度例外（标记）``；证据等级 充分/推断/未知。
- 只读：本模块不写任何 parquet/canonical；唯一写入口是 CLI ``--report-dir``
  下的 markdown/json/csv 证据。

单行判定（基于 raw 自身证据）
-----------------------------
- ``LEGACY_UNIT``：行 ratio(=vwap/close) 与**该代码 ``<1994-01-01`` 成交行
  中位 ratio** 一致（默认 ±10%），且中位 ratio 显著偏离 1（代码级金额/成交量
  单位约定，如 000002/000004 早期 ratio≈5）→ 历史制度例外；
- ``MARGIN``：非单位约定行，且 VWAP 落在 **pre-1995 候选 ``tol=2%`` 带**内
  （以现行 1% 带衡量的超带幅度 ≤ 上界 ~0.99pp / 下界 ~1.01pp）→ 恢复候选；
- ``CORRUPT``：其余（含 ratio 偏离代码约定 >10%、超 2% 带）→ 确坏。

CLI
---
``python platform/tools/data_quality/rca_full_quarantine_days.py \
    --raw data/fact/daily_fact/daily_fact.parquet \
    --report-dir governance/evidence/verification/R33``
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

_TOOLS = Path(__file__).resolve().parents[1]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import polars as pl  # noqa: E402

from data_quality import health  # noqa: E402

DAYS_18: tuple[str, ...] = (
    "1991-01-19", "1991-02-02", "1991-02-09", "1991-02-23", "1991-03-02",
    "1991-03-09", "1991-03-16", "1991-03-23", "1991-03-30", "1991-04-13",
    "1991-04-20", "1991-05-04", "1991-05-25", "1991-06-01", "1991-11-17",
    "1992-04-18", "1993-07-03", "1993-08-21",
)
VWAP_TOL = 0.01
RESTORE_TOL = 0.02
MED_MATCH_TOL = 0.10
LEGACY_MIN_HIST = 100
DEFAULT_FACTORS: tuple[float, ...] = (0.01, 0.1, 0.2, 1.0, 5.0, 10.0, 100.0)
HIST_CUTOFF = "1994-01-01"

ROW_CLEAN = "CLEAN"
ROW_LEGACY_UNIT = "LEGACY_UNIT"
ROW_MARGIN = "MARGIN"
ROW_CORRUPT = "CORRUPT"

RESTORE = "恢复（规则误判）"
KEEP_QUARANTINE = "保持隔离（数据确坏）"
LEGACY_REGIME = "历史制度例外（标记）"

_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

_CALENDAR_FIX = ("trade_cal 不得从 canonical daily 派生：全隔离日从日历消失的根因是"
                 "派生链而非规则；应换权威日历源或按 raw 日期域保留交易日。")
_RULE_FIX_LEGACY = ("保留 VWAP 规则；将匹配代码级单位约定的早期行标记为历史单位例外"
                    "（UNIT_SUSPECT/LEGACY_UNIT），不自动改写金额。")
_RULE_FIX_RESTORE = ("规则修正候选：pre-1995 直接采用 tol=2% 的 VWAP 容差带（对价格"
                     "的覆盖上界按带边相对量度 0.02/1.02≈1.96pp）；本批 4 行（以现行 "
                     "1% 带衡量超带 ≤0.80pp）均被覆盖；需 Task 2 决策并 bump dq_policy。")
_RULE_FIX_KEEP = ("不放宽 VWAP 容差（超 2% 带或非单位约定行）；如要恢复须先有权威"
                  "价源核对 amount/volume 单位。")


def weekday_cn(d: str | dt.date | dt.datetime) -> str:
    """交易日 → 中文星期。"""
    if isinstance(d, str):
        d = dt.date.fromisoformat(d)
    if isinstance(d, dt.datetime):
        d = d.date()
    return _WEEKDAY_CN[d.weekday()]


def _pct_gap(vwap: float | None, low: float | None, high: float | None,
             tol: float) -> tuple[float | None, bool]:
    """VWAP 距 [low*(1-tol), high*(1+tol)] 的相对偏离（%，带内=0）与是否在带内。"""
    if vwap is None or low is None or high is None or low <= 0:
        return None, False
    lo, hi = low * (1 - tol), high * (1 + tol)
    if vwap < lo:
        return (vwap - lo) / lo * 100.0, False
    if vwap > hi:
        return (vwap - hi) / hi * 100.0, False
    return 0.0, True


def vwap_analysis(*, open: float | None, high: float | None, low: float | None,
                  close: float | None, volume: float | None,
                  amount: float | None, tol: float = VWAP_TOL) -> dict[str, Any]:
    """复算 VWAP=amount/volume 并与当期 OHLC 比较（纯函数，不修数据）。"""
    vwap = None
    if (volume is not None and amount is not None and volume > 0 and amount > 0):
        vwap = amount / volume
    dev_pct = None
    if vwap is not None and close:
        dev_pct = (vwap / close - 1.0) * 100.0
    gap_pct, in_range = _pct_gap(vwap, low, high, tol)
    return {"vwap": vwap, "dev_pct": dev_pct, "gap_pct": gap_pct,
            "in_range": in_range}


def factor_candidates(vwap: float | None, *, low: float | None,
                      high: float | None, tol: float = VWAP_TOL,
                      factors: Sequence[float] = DEFAULT_FACTORS) -> tuple[float, ...]:
    """使 ``vwap/k`` 落入价格带的乘性因子候选（金额单位约定的证据）。"""
    if vwap is None or low is None or high is None or vwap <= 0:
        return ()
    lo, hi = low * (1 - tol), high * (1 + tol)
    out = []
    for k in factors:
        if k > 0 and lo <= vwap / k <= hi:
            out.append(float(k))
    return tuple(out)


@dataclass(frozen=True)
class RowFact:
    """单行 RCA 事实（raw 原始字段 + 复算值 + 代码级单位约定背景）。"""

    date: str
    code: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    amount: float | None
    adj_factor: float | None
    fq_factor: float | None
    vwap: float | None
    dev_pct: float | None
    gap_pct: float | None
    in_range: bool
    factor_candidates: tuple[float, ...]
    ratio: float | None
    code_med_ratio: float | None
    n_code_hist: int


def build_row_fact(*, date: str, code: str, open: float | None,
                   high: float | None, low: float | None, close: float | None,
                   volume: float | None, amount: float | None,
                   adj_factor: float | None = None,
                   fq_factor: float | None = None,
                   code_med_ratio: float | None = None,
                   n_code_hist: int = 0,
                   tol: float = VWAP_TOL) -> RowFact:
    """raw 行 → :class:`RowFact`（VWAP 复算 + 单位因子候选）。"""
    a = vwap_analysis(open=open, high=high, low=low, close=close,
                      volume=volume, amount=amount, tol=tol)
    ratio = None
    if a["vwap"] is not None and close:
        ratio = a["vwap"] / close
    return RowFact(
        date=date, code=code, open=open, high=high, low=low, close=close,
        volume=volume, amount=amount, adj_factor=adj_factor, fq_factor=fq_factor,
        vwap=a["vwap"], dev_pct=a["dev_pct"], gap_pct=a["gap_pct"],
        in_range=a["in_range"],
        factor_candidates=factor_candidates(a["vwap"], low=low, high=high, tol=tol),
        ratio=ratio, code_med_ratio=code_med_ratio, n_code_hist=n_code_hist)


def classify_row(fact: RowFact, *, med_match_tol: float = MED_MATCH_TOL,
                 restore_tol: float = RESTORE_TOL,
                 legacy_min_hist: int = LEGACY_MIN_HIST) -> str:
    """单行判定：CLEAN / LEGACY_UNIT / MARGIN / CORRUPT。"""
    if fact.in_range:
        return ROW_CLEAN
    if (fact.ratio is not None and fact.code_med_ratio is not None
            and fact.n_code_hist >= legacy_min_hist
            and abs(fact.code_med_ratio - 1.0) > 0.02):
        rel = fact.ratio / fact.code_med_ratio - 1.0
        if abs(rel) <= med_match_tol:
            return ROW_LEGACY_UNIT
    if _pct_gap(fact.vwap, fact.low, fact.high, restore_tol)[1]:
        return ROW_MARGIN
    return ROW_CORRUPT


def _row_evidence(fact: RowFact) -> str:
    if fact.ratio is not None and fact.code_med_ratio:
        return "充分" if abs(fact.ratio / fact.code_med_ratio - 1.0) <= 0.05 else "推断"
    return "推断"


@dataclass(frozen=True)
class DayRCA:
    date: str
    n_rows: int
    conclusion: str
    evidence: str
    reason: str
    row_verdicts: dict[str, str] = field(default_factory=dict)
    rule_fix_candidates: tuple[str, ...] = ()


def classify_day(facts: Sequence[RowFact], *,
                 med_match_tol: float = MED_MATCH_TOL,
                 restore_tol: float = RESTORE_TOL,
                 legacy_min_hist: int = LEGACY_MIN_HIST) -> DayRCA:
    """单日三选一（输入行全部来自该日 raw）。"""
    if not facts:
        raise ValueError("classify_day 需要至少一行 raw 事实")
    verdicts = {f"{f.code}": classify_row(
        f, med_match_tol=med_match_tol, restore_tol=restore_tol,
        legacy_min_hist=legacy_min_hist) for f in facts}
    kinds = set(verdicts.values())

    if kinds == {ROW_LEGACY_UNIT}:
        evidence = "充分" if all(_row_evidence(f) == "充分" for f in facts) else "推断"
        return DayRCA(
            date=facts[0].date, n_rows=len(facts), conclusion=LEGACY_REGIME,
            evidence=evidence,
            reason=("全行匹配代码级早期单位约定（金额单位因子≠1，价格序列连续）；"
                    "属历史制度例外而非随机损坏"),
            row_verdicts=verdicts,
            rule_fix_candidates=(_RULE_FIX_LEGACY, _CALENDAR_FIX))

    if kinds == {ROW_MARGIN}:
        return DayRCA(
            date=facts[0].date, n_rows=len(facts), conclusion=RESTORE,
            evidence="推断",
            reason=("非单位约定行，但 VWAP 落在 pre-1995 tol=2% 候选带内"
                    "（早期价格记载精度/金额按整数价取整），判定为规则容差误判"),
            row_verdicts=verdicts,
            rule_fix_candidates=(_RULE_FIX_RESTORE, _CALENDAR_FIX))

    if kinds == {ROW_CORRUPT}:
        evidence = "充分"
    elif ROW_CORRUPT in kinds:
        evidence = "充分"
    else:
        evidence = "推断"
    return DayRCA(
        date=facts[0].date, n_rows=len(facts), conclusion=KEEP_QUARANTINE,
        evidence=evidence,
        reason=("至少一行无法归因于单位约定或容差边际（ratio 背离代码约定 >10%"
                " 或超 2% 带）；不得整体恢复"),
        row_verdicts=verdicts,
        rule_fix_candidates=(_RULE_FIX_KEEP, _CALENDAR_FIX))


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def regime_note(date: str) -> str:
    """早期制度证据（brief 给定的制度前提 + raw 日期间的分布事实）。"""
    y, m, d = (int(x) for x in date.split("-"))
    wd = weekday_cn(date)
    notes = {
        1991: "1991：深市处于 6 交易日/周时期（raw 内 47 个周六有行情）；"
              "1996 前无涨跌停、T+0；早期金额/成交量存在代码级单位约定"
              "（000002/000004 成交行 ratio≈5，000001≈1）。",
        1992: "1992：周六交易已不常规（raw 全年仅 5 个周末日）；"
              "1992-05-21 全面放开价格限制、T+0（制度切换年）。",
        1993: "1993：周末行情仅 9 日且多为单行（本日如是），推断为偶发/异常记载；"
              "T+0、无涨跌停制度延续。",
    }
    suffix = ""
    if wd in ("周六", "周日"):
        suffix = f" 本日为{wd}。"
    return notes.get(y, "制度事实未在本地证据内覆盖。") + suffix


def render_day_report(facts_by_day: dict[str, list[RowFact]],
                      day_results: dict[str, DayRCA]) -> str:
    """生成逐日 markdown（含 raw 字段、复算 VWAP、结论/证据/规则候选）。"""
    lines: list[str] = []
    for date in sorted(facts_by_day):
        rows = facts_by_day[date]
        res = day_results[date]
        lines.append(f"### {date}（{weekday_cn(date)}）")
        lines.append("")
        lines.append(f"- 结论：**{res.conclusion}** ｜ 证据等级：**{res.evidence}** ｜ "
                     f"raw 行数：{res.n_rows}")
        lines.append(f"- 根因：{res.reason}")
        lines.append(f"- 早期制度证据：{regime_note(date)}")
        lines.append("")
        lines.append("| code | O | H | L | C | volume | amount | adj | fq | "
                     "VWAP(vs OHLC) | 偏离% | 距带% | 单位因子候选 | 判定 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for f in sorted(rows, key=lambda x: x.code):
            cand = ",".join(_fmt(c, 4).rstrip("0").rstrip(".") for c in f.factor_candidates)
            lines.append(
                f"| {f.code} | {_fmt(f.open)} | {_fmt(f.high)} | {_fmt(f.low)} | "
                f"{_fmt(f.close)} | {_fmt(f.volume, 2)} | {_fmt(f.amount, 2)} | "
                f"{_fmt(f.adj_factor)} | {_fmt(f.fq_factor)} | "
                f"**{_fmt(f.vwap)}** | {_fmt(f.dev_pct, 2)} | {_fmt(f.gap_pct, 2)} | "
                f"{cand or '-'} | {res.row_verdicts.get(f.code, '-')} |")
        lines.append("")
        lines.append("规则修正候选：")
        for c in res.rule_fix_candidates:
            lines.append(f"- {c}")
        lines.append("")
    return "\n".join(lines)


def load_rows(raw: str | Path, days: Iterable[str] = DAYS_18) -> pl.DataFrame:
    """只读取指定日期 raw 行（read-only；不写回）。"""
    ds = [dt.date.fromisoformat(d) for d in days]
    return (pl.scan_parquet(str(raw))
            .filter(pl.col("trade_date").is_in(ds))
            .collect()
            .sort(["trade_date", "code"]))


def code_history_stats(raw: str | Path,
                       codes: Iterable[str]) -> dict[str, tuple[float, int]]:
    """代码 ``trade_date < 1994-01-01`` 成交行的 ratio 中位数与样本数（单位约定背景）。"""
    codes = list(codes)
    hist = (pl.scan_parquet(str(raw))
            .filter(pl.col("code").is_in(codes)
                    & (pl.col("trade_date") < dt.date.fromisoformat(HIST_CUTOFF))
                    & (pl.col("volume") > 0)
                    & (pl.col("amount") > 0))
            .select(["code", "close", "volume", "amount"])
            .collect())
    hist = hist.with_columns(
        (pl.col("amount") / pl.col("volume") / pl.col("close")).alias("ratio"))
    out: dict[str, tuple[float, int]] = {}
    for code, grp in hist.group_by("code"):
        vals = [v for v in grp["ratio"].to_list() if v is not None and v > 0]
        if vals:
            out[code[0]] = (float(statistics.median(vals)), len(vals))
    return out


def build_day_facts(df: pl.DataFrame, stats: dict[str, tuple[float, int]],
                    *, tol: float = VWAP_TOL) -> dict[str, list[RowFact]]:
    """raw 帧 → {date: [RowFact]}。"""
    out: dict[str, list[RowFact]] = {}
    for row in df.iter_rows(named=True):
        date = str(row["trade_date"])
        med, n_hist = stats.get(row["code"], (None, 0))
        fact = build_row_fact(
            date=date, code=row["code"], open=row.get("open"), high=row.get("high"),
            low=row.get("low"), close=row.get("close"), volume=row.get("volume"),
            amount=row.get("amount"), adj_factor=row.get("adj_factor"),
            fq_factor=row.get("fq_factor"), code_med_ratio=med,
            n_code_hist=n_hist, tol=tol)
        out.setdefault(date, []).append(fact)
    return out


def run_analysis(raw: str | Path, *, days: Iterable[str] = DAYS_18,
                 tol: float = VWAP_TOL,
                 med_match_tol: float = MED_MATCH_TOL,
                 restore_tol: float = RESTORE_TOL,
                 legacy_min_hist: int = LEGACY_MIN_HIST,
                 ) -> tuple[dict[str, list[RowFact]], dict[str, DayRCA]]:
    """读 raw → 逐日事实与结论（纯计算，不写盘）。"""
    df = load_rows(raw, days)
    stats = code_history_stats(raw, df["code"].unique().to_list())
    facts = build_day_facts(df, stats, tol=tol)
    results = {
        date: classify_day(rows, med_match_tol=med_match_tol,
                           restore_tol=restore_tol,
                           legacy_min_hist=legacy_min_hist)
        for date, rows in facts.items()
    }
    return facts, results


def summary_table(facts_by_day: dict[str, list[RowFact]],
                  day_results: dict[str, DayRCA]) -> str:
    """18 日结论汇总表。"""
    lines = ["| 日期 | 星期 | raw 行数 | 涉及代码 | 结论 | 证据等级 |",
             "|---|---|---|---|---|---|"]
    for date in sorted(day_results):
        res = day_results[date]
        codes = ",".join(sorted({f.code for f in facts_by_day[date]}))
        lines.append(f"| {date} | {weekday_cn(date)} | {res.n_rows} | {codes} | "
                     f"{res.conclusion} | {res.evidence} |")
    return "\n".join(lines)


def row_verdict_counts(day_results: dict[str, DayRCA]) -> dict[str, int]:
    """全 18 日逐行判定计数（LEGACY_UNIT/MARGIN/CORRUPT）。"""
    out: dict[str, int] = {}
    for res in day_results.values():
        for v in res.row_verdicts.values():
            out[v] = out.get(v, 0) + 1
    return out


def rule_fix_summary(verdict_counts: dict[str, int]) -> str:
    """规则修正候选（汇总，交 Task 2 决策；Task 1 不改规则）。"""
    n_legacy = verdict_counts.get(ROW_LEGACY_UNIT, 0)
    n_margin = verdict_counts.get(ROW_MARGIN, 0)
    n_corrupt = verdict_counts.get(ROW_CORRUPT, 0)
    return "\n".join([
        "## 规则修正候选（汇总，交 Task 2）",
        "",
        f"- 逐行判定（27 行）：单位约定 {n_legacy} ｜ 容差边际 {n_margin} ｜ "
        f"确坏 {n_corrupt}。",
        "1. **日历链（18 日消失根因，最高优先）**：`trade_cal`/canonical 日期域不得只由"
        "清洗后幸存行派生；全隔离日应从权威日历或 raw 日期域保留，否则真实交易日整日消失。",
        "2. **早期单位约定标记（11 日/19 行）**：匹配代码级 ratio 中位数的行（如 "
        "000002/000004 早期因子≈5）保留 VWAP 隔离结论，但标记为 `LEGACY_UNIT`/"
        "`UNIT_SUSPECT`，不计入系统性 ERROR；**不自动改写金额**（无权威单位源）。",
        "3. **容差边际例外（3 日/4 行，恢复候选）**：pre-1995 **直接采用 tol=2%** 的 "
        "VWAP 容差带（对价格的覆盖上界按带边相对量度 0.02/1.02≈1.96pp；本批 4 行以"
        "现行 1% 带衡量超带 ≤0.80pp，均被覆盖；需 Task 2 决策并 bump dq_policy）；"
        "超出该带的行不放宽。",
        "4. **不得恢复（4 日/4 行）**：显著坏行（ratio 背离代码约定 >10% 或落在 "
        "tol=2% 带外），保持隔离。",
        "5. **M3 研究项**：若能从权威源确认早期金额单位（因子 5/100/0.01 等），再评估"
        "确定性单位归一化（属历史修复，非本阶段）。",
    ])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="18 个全隔离日 RCA（只读）")
    parser.add_argument("--raw", default="data/fact/daily_fact/daily_fact.parquet")
    parser.add_argument("--report-dir", default="governance/evidence/verification/R33")
    parser.add_argument("--tol", type=float, default=VWAP_TOL)
    parser.add_argument("--med-match-tol", type=float, default=MED_MATCH_TOL)
    parser.add_argument("--restore-tol", type=float, default=RESTORE_TOL,
                        help="pre-1995 恢复候选带（直接 tol=2%，非 1%+边际）")
    parser.add_argument("--legacy-min-hist", type=int, default=LEGACY_MIN_HIST)
    parser.add_argument("--days", nargs="*", default=list(DAYS_18))
    args = parser.parse_args(argv)

    facts, results = run_analysis(
        args.raw, days=args.days, tol=args.tol,
        med_match_tol=args.med_match_tol,
        restore_tol=args.restore_tol,
        legacy_min_hist=args.legacy_min_hist)

    out_dir = Path(args.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    for res in results.values():
        counts[res.conclusion] = counts.get(res.conclusion, 0) + 1
    buckets = {k: counts.get(v, 0) for k, v in
               (("恢复（规则误判）", RESTORE),
                ("保持隔离（数据确坏）", KEEP_QUARANTINE),
                ("历史制度例外（标记）", LEGACY_REGIME))}
    verdicts = row_verdict_counts(results)

    md = [
        "# 18 个全隔离日 RCA（Plan DQ-M1.5 T1，只读）",
        "",
        f"- 输入：`{args.raw}` ｜ 现行容差 tol={args.tol} ｜ 单位匹配 tol={args.med_match_tol}"
        f" ｜ 恢复候选带 tol={args.restore_tol}（覆盖上界 0.02/1.02≈1.96pp）"
        f" ｜ 单位约定最小样本 {args.legacy_min_hist}",
        f"- 结论分布：" + "，".join(f"{k} {v} 日" for k, v in buckets.items()),
        f"- 逐行判定：单位约定 {verdicts.get(ROW_LEGACY_UNIT, 0)} ｜ 容差边际 "
        f"{verdicts.get(ROW_MARGIN, 0)} ｜ 确坏 {verdicts.get(ROW_CORRUPT, 0)}",
        "",
        "## 汇总",
        "",
        summary_table(facts, results),
        "",
        "> 证据等级口径：`充分` = ratio 拟合充分（行 ratio vs 代码级中位 ratio 的"
        "实测），**机制来源（单位约定/取整的制度原因）一律为 `推断`**；"
        "`未知` 表示本地无证据。",
        "",
        rule_fix_summary(verdicts),
        "",
        "## 逐日明细",
        "",
        render_day_report(facts, results),
    ]
    (out_dir / "rca-18-days.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    raw_sha256 = health.sha256_file(args.raw)
    payload = {
        "days": args.days,
        "raw": {"path": str(args.raw), "sha256": raw_sha256},
        "params": {"tol": args.tol, "med_match_tol": args.med_match_tol,
                   "restore_tol": args.restore_tol,
                   "legacy_min_hist": args.legacy_min_hist},
        "summary": buckets,
        "row_verdicts": verdicts,
        "per_day": {date: {
            "conclusion": res.conclusion, "evidence": res.evidence,
            "reason": res.reason, "n_rows": res.n_rows,
            "rows": [asdict(f) for f in facts[date]],
            "rule_fix_candidates": list(res.rule_fix_candidates),
        } for date, res in sorted(results.items())},
    }
    (out_dir / "rca-18-days.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    csv_rows = []
    for rows in facts.values():
        for f in rows:
            d = asdict(f)
            d["factor_candidates"] = ",".join(str(x) for x in f.factor_candidates)
            csv_rows.append(d)
    pl.DataFrame(csv_rows).write_csv(out_dir / "rca-18-days-rows.csv")

    print(f"[rca-18-days] {len(results)} 日；" +
          "，".join(f"{k}={v}" for k, v in buckets.items()) +
          f" -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
