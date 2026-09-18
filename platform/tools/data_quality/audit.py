"""post-ingest audit（Plan DQ-M1 T6）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-6-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §2（系统性检测；分布突变只 WARN/SUSPECT，升级证据齐才 SYSTEMATIC_ISSUE）
      §4⑦（腾讯行情抽样：daily raw OHLC 对拍；系统性同向偏差优先于单票异常）
      §5（CANONICAL INGEST → POST-INGEST AUDIT → FINAL PARTITION GATE）
      §6（health 字段引用）。

职责边界（只审不改、只读不重算）：
- 输入是 **canonical 已写入的结果**（+ raw/staging 侧独立分母与抽样源），
  审计**不重跑行级校验**（validators 只在 clean 阶段跑一次）；
- completeness 分母独立给出（``expected_count``，None = UNKNOWN，**绝不用
  actual 自洽冒充 COMPLETE**）；PK/重复用 ``rules.PK_CONFLICT`` 常量；
  reconcile 与独立期望行数比对（clean staging / raw − quarantine 口径）；
- 腾讯抽样是**唯一外部依赖**：``transport(url, params) -> str`` 可注入
  （测试 fake + 断言 URL/参数）；偏差只产 WARN/SUSPECT，绝不 FAIL；接口
  失败/限流同样降级为 SUSPECT（plan 风险表）；
- 漂移检测（当前分区 vs 基线）只产 WARN/SUSPECT；仅当**同时**有升级证据
  （跨源不一致 / 单位突变 / schema metadata 变化）才升级 SYSTEMATIC_ISSUE，
  由 ``decide_final`` 判 FAIL。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Sequence

import polars as pl

from data_quality import aggregate, rules, validators
from data_quality.aggregate import SystemicDetail
from data_quality.rules import RuleResult

# ── 枚举（completeness.status 与 spec §6 同字面）────────────────────────
COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
UNKNOWN = "UNKNOWN"

# ── 审计检查状态（WARN/SUSPECT 语义，绝不直接 FAIL）─────────────────────
OK = "OK"
SUSPECT = "SUSPECT"
SKIPPED = "SKIPPED"

# ── 抽样结果类型（F2：只有 DEVIATION 是「跨源不一致」升级证据）──────────
DEVIATION = "DEVIATION"          # 有真实偏差行（deviations > 0）
NO_OVERLAP = "NO_OVERLAP"        # 本地/远端无可比对交集（不是数据错误）
FETCH_FAILED = "FETCH_FAILED"    # 腾讯接口失败/限流（降级 SUSPECT，不升级）

PASS = aggregate.PASS
DEGRADED = aggregate.DEGRADED
FAIL = aggregate.FAIL

# ── 腾讯日线接口（唯一外部依赖；§4⑦）───────────────────────────────────
TENCENT_DAILY_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_COUNT = 320
TENCENT_TIMEOUT_S = 10.0
# F1：对拍口径必须与本地 raw **同口径**——raw 是不复权交易所价，腾讯必须取
# 不复权（响应键 "day"）；qfq 会在除权/送转日返回复权序列 → 必然误报。
TENCENT_ADJUST = ""
_TENCENT_MARKET = {"SH": "sh", "SZ": "sz", "BJ": "bj"}

# ── 阈值（初值；calibration 只改值不改结构）────────────────────────────
SAMPLE_TOL = 0.01          # OHLC 相对偏差 > 1% → SUSPECT
DRIFT_TOL = 0.30           # 分区均值漂移 > 30% → SUSPECT
UNIT_RATIO = 50.0          # 均值 ≥50×/≤1/50 → 单位突变证据
SAMPLE_WINDOW_DAYS = 30    # 抽样窗口：partition - 30d .. partition

# 升级后的 systemic 规则名（§2「升级 SYSTEMATIC_ISSUE」）
SYSTEMATIC_CROSS_EVIDENCE = "SYSTEMATIC_CROSS_EVIDENCE"

Transport = Callable[[str, dict], str]


# ── 数据结构 ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Completeness:
    """完整性（§6 completeness 块；``status`` 独立于 coverage）。"""

    status: str
    expected_count: int | None
    actual_count: int
    coverage: float | None


@dataclass(frozen=True)
class SampleCheck:
    """腾讯抽样对拍结果（偏差 → SUSPECT，不 FAIL）。

    ``kind``：OK / DEVIATION / NO_OVERLAP / FETCH_FAILED / SKIPPED——只有
    DEVIATION 是 SYSTEMATIC_ISSUE 的「跨源不一致」升级证据（F2）。
    """

    status: str
    checked: int
    deviations: int
    max_deviation: float
    detail: str
    kind: str = OK


@dataclass(frozen=True)
class DriftCheck:
    """分布漂移（只产 SUSPECT；``unit_suspect`` 是升级证据之一）。"""

    status: str
    subject: str
    ratio: float
    unit_suspect: bool
    detail: str


@dataclass(frozen=True)
class AuditMetrics:
    """post-ingest 审计汇总（FINAL 门 / health 的输入）。"""

    partition: str
    completeness: Completeness
    pk_duplicates: int
    reconcile_ok: bool
    reconcile_detail: str
    sample: SampleCheck
    drift: DriftCheck
    systemic: SystemicDetail | None
    suspect: bool
    results: tuple[RuleResult, ...]


# ── completeness ─────────────────────────────────────────────────────────
def check_completeness(expected_count: int | None,
                       actual_count: int,
                       explained_drops: int = 0) -> Completeness:
    """独立完整性判定：``expected_count`` 缺省 = UNKNOWN（不得用 actual 自洽）。

    ``status = COMPLETE`` 当且仅当 expected 已知且
    ``actual + explained_drops == expected``——``explained_drops`` 是**确定性
    清洗账**（quarantine + dedup 删除行数；full_table 口径专用）。未解释差额
    （少或多）一律 INCOMPLETE（不是"超量就通过"，也不是"有 quarantine 就不完整"）。
    """
    if not isinstance(actual_count, int) or isinstance(actual_count, bool) \
            or actual_count < 0:
        raise ValueError(f"actual_count 必须为非负整数：{actual_count!r}")
    if not isinstance(explained_drops, int) or isinstance(explained_drops, bool) \
            or explained_drops < 0:
        raise ValueError(f"explained_drops 必须为非负整数：{explained_drops!r}")
    if expected_count is None:
        return Completeness(UNKNOWN, None, actual_count, None)
    if not isinstance(expected_count, int) or isinstance(expected_count, bool) \
            or expected_count <= 0:
        raise ValueError(f"expected_count 必须为正整数或 None：{expected_count!r}")
    coverage = actual_count / expected_count
    status = (COMPLETE if actual_count + explained_drops == expected_count
              else INCOMPLETE)
    return Completeness(status, expected_count, actual_count, coverage)


# ── PK / reconcile ───────────────────────────────────────────────────────
def check_pk(frame: pl.DataFrame) -> tuple[int, list[RuleResult]]:
    """canonical PK 唯一性（复用 rules.PK_CONFLICT 常量；重复键 → 每键一条 ERROR）。

    返回 (重复键数, results)。缺 symbol/trade_date 列 → (0, [])（结构问题由
    clean 阶段 schema 门负责，audit 不重复行级校验）。
    """
    sym_col = next((c for c in validators._SYM_ALIASES if c in frame.columns), None)
    if sym_col is None or "trade_date" not in frame.columns or frame.height == 0:
        return 0, []
    grouped = (frame.group_by([sym_col, "trade_date"]).len()
               .filter(pl.col("len") > 1))
    out: list[RuleResult] = []
    for row in grouped.iter_rows(named=True):
        key = f"{row[sym_col]}|{row['trade_date']}"
        out.append(RuleResult(
            rules.PK_CONFLICT, rules.ERROR, key,
            f"canonical 主键重复：{key} 出现 {row['len']} 行（ingest 后不应发生）"))
    return grouped.height, out


def check_reconcile(expected: int | None, actual: int) -> tuple[bool, str]:
    """对账：独立期望行数（clean staging / raw − quarantine）vs canonical 实际。"""
    if expected is None:
        return True, "无对账源（未给独立期望行数）"
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
        raise ValueError(f"对账期望行数必须为非负整数或 None：{expected!r}")
    ok = expected == actual
    detail = ("一致" if ok else
              f"不一致：canonical={actual}，独立期望={expected}（差 {actual - expected:+,}）")
    return ok, detail


# ── 腾讯抽样（fake transport 可注入）────────────────────────────────────
def tencent_param(symbol: str, start: str, end: str,
                  count: int = TENCENT_COUNT, adjust: str = TENCENT_ADJUST) -> str:
    """腾讯日线 param：``<market><code>,day,<start>,<end>,<count>[,<adjust>]``。

    ``adjust=""``（默认，F1）= 不复权——与本地 raw（交易所价）同口径；
    传入 ``"qfq"`` 才请求前复权（响应键变 ``qfqday``）。
    """
    code, _, suffix = symbol.partition(".")
    market = _TENCENT_MARKET.get(suffix.upper())
    if market is None or not code:
        raise ValueError(
            f"腾讯抽样不支持 symbol={symbol!r}（需 <code>.<SH|SZ|BJ> canonical 形态）")
    param = f"{market}{code},day,{start},{end},{count}"
    return f"{param},{adjust}" if adjust else param


def sample_window(partition: str) -> tuple[str, str]:
    """抽样窗口 = [partition - 30d, partition]（腾讯接口起止为闭区间）。"""
    import datetime as _dt

    end = _dt.date.fromisoformat(partition)
    start = end - _dt.timedelta(days=SAMPLE_WINDOW_DAYS)
    return start.isoformat(), end.isoformat()


def http_get_text(url: str, params: dict) -> str:
    """默认 HTTP transport（唯一外部依赖；测试注入 fake 断言 URL/参数）。"""
    full = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(full, timeout=TENCENT_TIMEOUT_S) as resp:  # noqa: S310
        return resp.read().decode("utf-8", errors="replace")


def fetch_tencent_daily(symbol: str, start: str, end: str, *,
                        transport: Transport | None = None,
                        count: int = TENCENT_COUNT,
                        adjust: str = TENCENT_ADJUST) -> pl.DataFrame:
    """拉取腾讯日线（默认**不复权**，F1）→ [code, trade_date, open, high, low, close]。

    JSON 行序：``data.<code>.day`` = [[date, open, close, high, low, volume], ...]
    （腾讯接口列序为 open/close/high/low/volume——按接口语义解析，不猜）；
    复权口径请求时响应键为 ``qfqday``（parser 两键兼容）。
    """
    tp = transport or http_get_text
    param = tencent_param(symbol, start, end, count, adjust)
    text = tp(TENCENT_DAILY_URL, {"param": param})
    try:
        payload = json.loads(text)
        # 真实响应键 = <market><code>（如 sh600519，2026-09-19 实测）
        node = payload["data"][param.split(",", 1)[0]]
        rows = (node.get("qfqday") if adjust else node.get("day")) \
            or node.get("day") or node["qfqday"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"腾讯日线响应无法解析（symbol={symbol}）：{exc}") from exc
    parsed = []
    for r in rows:
        day, o, c, h, low = r[0], r[1], r[2], r[3], r[4]
        parsed.append({"code": symbol, "trade_date": day,
                       "open": float(o), "high": float(h),
                       "low": float(low), "close": float(c)})
    df = pl.DataFrame(parsed, schema={
        "code": pl.String, "trade_date": pl.String, "open": pl.Float64,
        "high": pl.Float64, "low": pl.Float64, "close": pl.Float64})
    return df.with_columns(pl.col("trade_date").str.to_date(strict=False))


def compare_ohlc(local: pl.DataFrame, remote: pl.DataFrame, *,
                 partition: str, tol: float = SAMPLE_TOL,
                 ) -> tuple[SampleCheck, list[RuleResult]]:
    """本地 raw OHLC vs 腾讯抽样（逐 (code, date) 最大相对偏差 > tol → 偏差行）。

    返回 ``(SampleCheck, [CROSS_SOURCE_DEVIATION WARN 结果])``。
    """
    sym_col = next((c for c in validators._SYM_ALIASES if c in local.columns), None)
    if sym_col is None or local.height == 0 or remote.height == 0:
        return (SampleCheck(SUSPECT, 0, 0, 0.0, "抽样无可比对行（本地/远端为空）",
                            kind=NO_OVERLAP), [])
    day = pl.lit(partition).str.to_date(strict=False)
    left = (local.filter(pl.col("trade_date") == day)
            .select([pl.col(sym_col).cast(pl.String).alias("code"),
                     pl.col("trade_date"), "open", "high", "low", "close"]))
    joined = left.join(remote, on=["code", "trade_date"], how="inner",
                       suffix="_remote")
    if joined.height == 0:
        return (SampleCheck(SUSPECT, 0, 0, 0.0,
                            f"抽样无交集：本地 {left.height} 行 / 远端 {remote.height} 行",
                            kind=NO_OVERLAP), [])
    worst = 0.0
    deviations = 0
    results: list[RuleResult] = []
    for row in joined.iter_rows(named=True):
        dev = 0.0
        field = ""
        for col in ("open", "high", "low", "close"):
            a, b = row[col], row[f"{col}_remote"]
            if a is None or b is None:
                continue
            rel = abs(a - b) / abs(b) if b else abs(a - b)
            if rel > dev:
                dev, field = rel, col
        worst = max(worst, dev)
        if dev > tol:
            deviations += 1
            key = f"{row['code']}|{row['trade_date']}"
            results.append(RuleResult(
                rules.CROSS_SOURCE_DEVIATION, rules.WARN, key,
                f"腾讯抽样偏差：{field} 本地={row[field]!r} 腾讯={row[f'{field}_remote']!r}"
                f"（相对偏差 {dev:.4f} > {tol}）"))
    status = SUSPECT if deviations else OK
    kind = DEVIATION if deviations else OK
    detail = (f"抽样 {joined.height} 行（{joined['code'].n_unique()} 只），"
              f"偏差 {deviations} 行，最大相对偏差 {worst:.4%}")
    check = SampleCheck(status, joined.height, deviations, worst, detail, kind)
    return check, results


# ── 漂移 ─────────────────────────────────────────────────────────────────
def detect_drift(current: pl.DataFrame, baseline: pl.DataFrame, *,
                 tol: float = DRIFT_TOL) -> DriftCheck:
    """当前分区 vs 基线均值（close）漂移：只产 OK/SUSPECT（绝不 FAIL）。"""
    if baseline.height == 0 or current.height == 0:
        return DriftCheck(OK, "close_mean", 1.0, False, "无基线/当前分区为空，跳过漂移")
    base = float(baseline["close"].mean())
    cur = float(current["close"].mean())
    if base == 0:
        return DriftCheck(SUSPECT, "close_mean", float("inf"), True,
                          "基线 close 均值 = 0，无法比较（单位疑点）")
    ratio = cur / base
    unit = ratio >= UNIT_RATIO or ratio <= 1 / UNIT_RATIO
    status = SUSPECT if abs(ratio - 1.0) > tol else OK
    detail = (f"close 均值 {base:.4g} → {cur:.4g}（ratio={ratio:.4g}）"
              + ("；疑似单位变化" if unit else ""))
    return DriftCheck(status, "close_mean", ratio, unit, detail)


# ── 升级检测（§2）与 FINAL 门 ───────────────────────────────────────────
def detect_audit_systemic(drift: DriftCheck, sample: SampleCheck, *,
                          schema_changed: bool = False) -> SystemicDetail | None:
    """分布漂移 + 明确升级证据 → SYSTEMATIC_ISSUE（§2：统计不负责裁定）。

    升级证据：单位变化 / 跨源不一致 / schema metadata 变化。单独漂移不升级。
    """
    if drift.status != SUSPECT:
        return None
    evidence = []
    if drift.unit_suspect:
        evidence.append("单位变化")
    if sample.kind == DEVIATION:
        # F2：只有真实偏差（deviations > 0）才是「跨源不一致」证据；
        # FETCH_FAILED / NO_OVERLAP 只是审计能力受限，绝不升级 SYSTEMATIC_ISSUE。
        evidence.append("跨源不一致")
    if schema_changed:
        evidence.append("schema metadata 变化")
    if not evidence:
        return None
    count = sample.deviations if sample.kind == DEVIATION else 0
    return SystemicDetail(
        rule=SYSTEMATIC_CROSS_EVIDENCE, subject=drift.subject,
        count=count, share=drift.ratio,
        detail=(f"分布漂移（{drift.detail}）+ {'、'.join(evidence)}"
                f" → 升级 SYSTEMATIC_ISSUE（设计 §2）"))


_DECISION_RANK = {PASS: 0, DEGRADED: 1, UNKNOWN: 2, FAIL: 3}


def decide_final(pre_decision: str, audit_metrics: AuditMetrics,
                 policy) -> str:
    """FINAL 分区门（§3.1 判定复用 + §2 升级 + 完整性独立腿）。

    - 质量腿：audit results → ``aggregate.aggregate`` → ``decide_pre_ingest``
      （含 ``systemic`` —— 升级命中 → FAIL）；
    - 完整性腿：completeness.status INCOMPLETE → FAIL；UNKNOWN → 至少 UNKNOWN；
    - 最终 = pre_decision 与后审计判定的**最差**（清洗后不可能比清洗前更好）。
    """
    if pre_decision not in _DECISION_RANK:
        raise ValueError(f"未知 pre_decision：{pre_decision!r}")
    actual = audit_metrics.completeness.actual_count
    expected = audit_metrics.completeness.expected_count or max(actual, 1)
    metrics = aggregate.aggregate(list(audit_metrics.results), expected, actual)
    post = aggregate.decide_pre_ingest(metrics, audit_metrics.systemic, policy)
    final = pre_decision if _DECISION_RANK[pre_decision] >= _DECISION_RANK[post] \
        else post
    status = audit_metrics.completeness.status
    if status == INCOMPLETE:
        return FAIL
    if status == UNKNOWN and final != FAIL:
        return UNKNOWN
    return final


# ── post-ingest 审计编排 ─────────────────────────────────────────────────
def audit_post_ingest(
    canonical: pl.DataFrame,
    *,
    partition: str,
    expected_count: int | None,
    actual_count: int | None = None,
    explained_drops: int = 0,
    reconcile_expected: int | None = None,
    reconcile_actual: int | None = None,
    raw: pl.DataFrame | None = None,
    baseline: pl.DataFrame | None = None,
    sample_symbols: Sequence[str] | None = None,
    transport: Transport | None = None,
    schema_changed: bool = False,
) -> AuditMetrics:
    """对 canonical 写入结果做 post-ingest 审计（只读；不重跑行级校验）。

    - ``expected_count``：独立完整性分母（None = UNKNOWN）；``actual_count``
      缺省 = ``canonical.height``（分区帧）；全表口径可显式传入；
      ``explained_drops`` = 确定性清洗账（quarantine + dedup 行数；full_table）；
    - ``reconcile_expected`` / ``reconcile_actual``：独立对账（clean staging
      全表口径 vs canonical 全表计数；actual 缺省 = actual_count）；
    - ``raw``：抽样对拍源（raw/clean 行）；``sample_symbols`` + ``transport``
      齐备才抽样（fake transport 断言 URL/参数；接口失败降级 SUSPECT）；
    - ``baseline``：漂移基线（上一窗口行）。
    """
    actual = actual_count if actual_count is not None else canonical.height
    completeness = check_completeness(expected_count, actual,
                                      explained_drops=explained_drops)
    pk_duplicates, pk_results = check_pk(canonical)
    reconcile_ok, reconcile_detail = check_reconcile(
        reconcile_expected,
        reconcile_actual if reconcile_actual is not None else actual)

    results: list[RuleResult] = list(pk_results)
    sample = SampleCheck(SKIPPED, 0, 0, 0.0, "未配置抽样源（raw/symbols/transport）",
                         kind=SKIPPED)
    if raw is not None and sample_symbols and transport is not None:
        start, end = sample_window(partition)
        frames = []
        failures = []
        for symbol in sample_symbols:
            try:
                frames.append(fetch_tencent_daily(symbol, start, end, transport=transport))
            except (ValueError, OSError) as exc:
                failures.append(f"{symbol}: {exc}")
        if frames:
            remote = pl.concat(frames)
            sample, sample_results = compare_ohlc(raw, remote, partition=partition)
            results.extend(sample_results)
        else:
            sample = SampleCheck(
                SUSPECT, 0, 0, 0.0,
                f"腾讯抽样全部失败（降级 SUSPECT 不 FAIL）：{failures}",
                kind=FETCH_FAILED)

    drift = detect_drift(canonical, baseline) if baseline is not None else \
        DriftCheck(OK, "close_mean", 1.0, False, "无基线，跳过漂移")
    if drift.status == SUSPECT:
        rule_id = rules.UNIT_SUSPECT if drift.unit_suspect else rules.DRIFT_SUSPECT
        results.append(RuleResult(rule_id, rules.WARN, f"{partition}", drift.detail))

    systemic = detect_audit_systemic(drift, sample, schema_changed=schema_changed)
    suspect = (sample.status == SUSPECT or drift.status == SUSPECT
               or any(r.level == rules.WARN for r in results))
    return AuditMetrics(
        partition=partition, completeness=completeness,
        pk_duplicates=pk_duplicates, reconcile_ok=reconcile_ok,
        reconcile_detail=reconcile_detail, sample=sample, drift=drift,
        systemic=systemic, suspect=suspect, results=tuple(results))
