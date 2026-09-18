"""聚合指标 + 系统性检测 + PRE-INGEST 分区门（Plan DQ-M1 T4）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-4-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §2（聚合指标 / systemic 三规则）/ §3.1（判定定义）/ §3.2（policy 字段）。

数据流（设计 §2）：``Record Issues → Aggregate Metrics → Systemic Detector →
Partition Gate``；本模块只做**聚合与判定**，不重跑校验、不修数据。

边界语义（§3.1，逐字）：
- ``error_rate <= pass.max_error_rate`` → PASS；``error_rate > fail.min_error_rate``
  → FAIL；带内（含恰等 fail 下界）→ DEGRADED；
- ``coverage >= pass.min_coverage`` → PASS；``coverage < 1 - fail.max_missing_coverage``
  → FAIL；带内（含恰等 fail 线）→ DEGRADED；
- ``quarantine_count > 0`` **不自动** DEGRADED（1/20000 孤立坏行 → PASS）。

systemic 三规则阈值一律取 policy ``systemic.*``；命中即 ``systematic_issue=true``，
由分区门判 FAIL（分布突变只 WARN/SUSPECT，不在此裁定——设计 §2 红线）。
"""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from data_quality import rules, validators
from data_quality.rules import DqPolicy, RuleResult

# ── 门判定枚举（与 health_status 的 PASS/DEGRADED/FAIL 同字面）────────────
PASS = "PASS"
DEGRADED = "DEGRADED"
FAIL = "FAIL"

# ── systemic 规则名（设计 §2 表格逐字）──────────────────────────────────
SYSTEMATIC_FIELD_FAILURE = "SYSTEMATIC_FIELD_FAILURE"
SYSTEMATIC_GROUP_FAILURE = "SYSTEMATIC_GROUP_FAILURE"
SYSTEMATIC_TEMPORAL_FAILURE = "SYSTEMATIC_TEMPORAL_FAILURE"


@dataclass(frozen=True)
class Metrics:
    """单分区聚合指标（设计 §2；所有计数含全部级别，供 health 直接引用）。

    - ``error_rate = error_count / expected_count``；
    - ``coverage = actual_count / expected_count``；
    - ``quarantine_count``：行级 ERROR/FATAL 的**去重 key 数**（repair 按 key 整组
      隔离；同键多行证据只算一组）；
    - ``unresolved_partition_error``：存在帧级（不可行级隔离）FATAL —— 只能整分区
      拒收，repair 无法靠 quarantine 解决；
    - ``rules``：rule_id → 命中数（**含 WARN/INFO**，health §6 rules 字段）；
    - ``errors_by_field/group/date``：仅 ERROR 且行级（systemic 的输入；field 经
      ``rules.RULE_FIELDS`` 归因，None 不计）。
    """

    expected_count: int
    actual_count: int
    fatal_count: int
    error_count: int
    warning_count: int
    info_count: int
    quarantine_count: int
    error_rate: float
    coverage: float
    unresolved_partition_error: bool
    rules: dict[str, int]
    errors_by_field: dict[str, int]
    errors_by_group: dict[str, int]
    errors_by_date: dict[str, int]


@dataclass(frozen=True)
class SystemicDetail:
    """系统性命中明细（systematic_issue=true 的证据）。"""

    rule: str        # SYSTEMATIC_FIELD_FAILURE / _GROUP_ / _TEMPORAL_
    subject: str     # 字段名 / 分组名 / 日期
    count: int       # 该维度的 error 命中数
    share: float     # 占比（字段/日期）或分组错误率（分组）
    detail: str      # 人类可读（进 health.systemic_detail）


def aggregate(
    results: list[RuleResult],
    expected_count: int,
    actual_count: int,
) -> Metrics:
    """把完整 ``results`` 列表（含 WARN/INFO）聚合成 ``Metrics``（纯函数）。

    ``expected_count`` 为分区应有行数（分母）；``actual_count`` 为实际行数。
    两者均须显式给出——coverage 无法从 results 推导（全干净分区的 results 为空），
    缺省猜测会制造 fail-open。
    """
    if not isinstance(expected_count, int) or isinstance(expected_count, bool) \
            or expected_count <= 0:
        raise ValueError(f"expected_count 必须为正整数：{expected_count!r}")
    if not isinstance(actual_count, int) or isinstance(actual_count, bool) \
            or actual_count < 0:
        raise ValueError(f"actual_count 必须为非负整数：{actual_count!r}")

    counts = {rules.FATAL: 0, rules.ERROR: 0, rules.WARN: 0, rules.INFO: 0}
    rule_counts: dict[str, int] = {}
    by_field: dict[str, int] = {}
    by_group: dict[str, int] = {}
    by_date: dict[str, int] = {}
    quarantine_keys: set[str] = set()
    unresolved = False

    for r in results:
        if r.level not in counts:
            raise ValueError(f"未知级别 {r.level!r}（rule_id={r.rule_id!r}）")
        counts[r.level] += 1
        rule_counts[r.rule_id] = rule_counts.get(r.rule_id, 0) + 1

        row_key = "|" in r.key
        if row_key and rules.SEVERITY[r.level] <= rules.SEVERITY[rules.ERROR]:
            quarantine_keys.add(r.key)
        if r.level == rules.FATAL and not row_key:
            unresolved = True
        if r.level == rules.ERROR and row_key:
            field = rules.RULE_FIELDS.get(r.rule_id)
            if field:
                by_field[field] = by_field.get(field, 0) + 1
            sym, _, day = r.key.partition("|")
            group = _group_of(sym)
            by_group[group] = by_group.get(group, 0) + 1
            by_date[day] = by_date.get(day, 0) + 1

    return Metrics(
        expected_count=expected_count,
        actual_count=actual_count,
        fatal_count=counts[rules.FATAL],
        error_count=counts[rules.ERROR],
        warning_count=counts[rules.WARN],
        info_count=counts[rules.INFO],
        quarantine_count=len(quarantine_keys),
        error_rate=counts[rules.ERROR] / expected_count,
        coverage=actual_count / expected_count,
        unresolved_partition_error=unresolved,
        rules=dict(sorted(rule_counts.items())),
        errors_by_field=dict(sorted(by_field.items())),
        errors_by_group=dict(sorted(by_group.items())),
        errors_by_date=dict(sorted(by_date.items())),
    )


def detect_systemic(
    metrics: Metrics,
    rows: pl.DataFrame | None,
    policy: DqPolicy,
) -> SystemicDetail | None:
    """systemic 三规则（§2 表）：字段集中 → 分组集中 → 时间集中，首中即返回。

    ``rows`` = 该分区输入行（含随后被隔离的行），仅用于**分组错误率的分母**；
    ``None``/无 symbol 列 → 跳过分组规则（不猜、不误报）。
    """
    s = policy.systemic
    if metrics.error_count <= 0:
        return None

    # ① 字段集中：单一字段占全部 error 的比例（严格大于阈值）
    if metrics.errors_by_field:
        field, cnt = max(metrics.errors_by_field.items(),
                         key=lambda kv: (kv[1], kv[0]))
        share = cnt / metrics.error_count
        if share > s["field_share"] and cnt > s["field_count"]:
            return SystemicDetail(
                rule=SYSTEMATIC_FIELD_FAILURE, subject=field, count=cnt, share=share,
                detail=(f"字段集中：单一字段 {field} 占全部 error {share:.2%}"
                        f"（>{s['field_share']:.0%}）且 count {cnt}"
                        f" > {int(s['field_count'])}"))

    # ② 分组集中：group_rate > market_rate × K 且 group_count > N_group
    group_rows = _rows_by_group(rows)
    if group_rows and metrics.errors_by_group:
        total_rows = sum(group_rows.values())
        market_rate = metrics.error_count / total_rows
        hit: tuple[str, int, float, float] | None = None
        for group, cnt in sorted(metrics.errors_by_group.items()):
            n = group_rows.get(group, 0)
            rate = cnt / n if n else float("inf")
            if cnt > s["group_count"] and rate > market_rate * s["group_ratio"]:
                if hit is None or (cnt, group) > (hit[1], hit[0]):
                    hit = (group, cnt, rate, market_rate)
        if hit is not None:
            group, cnt, rate, market = hit
            return SystemicDetail(
                rule=SYSTEMATIC_GROUP_FAILURE, subject=group, count=cnt, share=rate,
                detail=(f"分组集中：{group} 错误率 {rate:.4g} > 全市场 {market:.4g}"
                        f" × {s['group_ratio']:g} 且 count {cnt}"
                        f" > {int(s['group_count'])}"))

    # ③ 时间集中：单日占错误比例（严格大于阈值）
    if metrics.errors_by_date:
        day, cnt = max(metrics.errors_by_date.items(), key=lambda kv: (kv[1], kv[0]))
        share = cnt / metrics.error_count
        if share > s["time_share"] and cnt > s["time_count"]:
            return SystemicDetail(
                rule=SYSTEMATIC_TEMPORAL_FAILURE, subject=day, count=cnt, share=share,
                detail=(f"时间集中：{day} 占全部 error {share:.2%}"
                        f"（>{s['time_share']:.0%}）且 count {cnt}"
                        f" > {int(s['time_count'])}"))

    return None


def decide_pre_ingest(
    metrics: Metrics,
    systemic: SystemicDetail | None,
    policy: DqPolicy,
) -> str:
    """PRE-INGEST 分区门（§3.1；返回 PASS/DEGRADED/FAIL）。

    判定顺序 = 先 FAIL 条件（任一命中即拒收），再 PASS 条件（全部满足），
    其余落 DEGRADED。与 §3.1 的集合定义等价：
    ``FAIL = fatal>0 ∪ rate 超 fail 线 ∪ coverage 低 fail 线 ∪ systemic ∪ unresolved``；
    ``PASS = ¬FAIL ∩ fatal==0 ∩ rate ≤ pass 线 ∩ coverage ≥ pass 线``。
    """
    gate = policy.partition_gate
    if metrics.fatal_count > 0:
        return FAIL
    if metrics.error_rate > gate["fail"]["min_error_rate"]:
        return FAIL
    if metrics.coverage < 1 - gate["fail"]["max_missing_coverage"]:
        return FAIL
    if systemic is not None:
        return FAIL
    if metrics.unresolved_partition_error:
        return FAIL
    if (metrics.error_rate <= gate["pass"]["max_error_rate"]
            and metrics.coverage >= gate["pass"]["min_coverage"]):
        return PASS
    return DEGRADED


# ── helpers ──────────────────────────────────────────────────────────────
def _group_of(symbol: str) -> str:
    """分组归因：交易所后缀（600519.SH → SH）；无后缀 → UNKNOWN。"""
    return symbol.rsplit(".", 1)[-1].upper() if "." in symbol else "UNKNOWN"


def _rows_by_group(rows: pl.DataFrame | None) -> dict[str, int] | None:
    """输入行 → 分组行数（分组规则的分母）；不可归因 → None（跳过该规则）。"""
    if rows is None or not isinstance(rows, pl.DataFrame) or rows.height == 0:
        return None
    sym_col = next((c for c in validators._SYM_ALIASES if c in rows.columns), None)
    if sym_col is None:
        return None
    counts: dict[str, int] = {}
    for sym in rows[sym_col].cast(pl.String, strict=False).to_list():
        group = _group_of(sym or "")
        counts[group] = counts.get(group, 0) + 1
    return counts
