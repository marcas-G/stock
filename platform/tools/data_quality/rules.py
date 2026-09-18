"""dq_policy 与规则模型（Plan DQ-M1 T1）。

需求源：.superpowers/sdd/2026-09-18-data-quality-pipeline-m1/task-1-brief.md
设计：knowledge/design/platform/specs/2026-09-18-data-quality-pipeline-design.md
      §1（严重度）/ §3.2（policy 字段与初值）/ §4（规则目录 ①-⑦）/ §6（health 字段引用）。

Produces
--------
- ``POLICY_VERSION``：白名单 policy 版本（旧版本**拒绝不映射**，加载规则明确）；
- ``SEVERITY``：级别 → 排序秩（FATAL < ERROR < WARN < INFO）；
- 规则目录常量（§4 ①-⑦ daily 子集）+ ``RULE_LEVELS`` / ``RULE_FIELDS``；
- ``RuleResult(rule_id, level, key, detail)``（frozen）：
  * ``key``：行级问题 = ``"<symbol>|<trade_date>"``（repair 按此定位行）；
    整帧/schema 问题 = 字段名或分区标识（不可定位到行，由分区门处置）；
  * ``detail``：人类可读原因（不参与聚合；聚合按 rule_id / key）。
- ``load_policy(path) -> DqPolicy``：YAML 直读 + 字段名校验（缺字段 ValueError 点名）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# ── policy 版本 ───────────────────────────────────────────────────────────
POLICY_VERSION = "daily-v1"
DEFAULT_POLICY_PATH = Path(__file__).with_name(f"dq_policy.{POLICY_VERSION}.yaml")

# ── §1 严重度（排序秩 = 优先级，越小越严重）──────────────────────────────
FATAL = "FATAL"
ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"
SEVERITY: dict[str, int] = {FATAL: 0, ERROR: 1, WARN: 2, INFO: 3}

# ── §4 规则目录（daily 子集；tick 后置）──────────────────────────────────
# ① 主键与重复
DUP_IDENTICAL = "DUP_IDENTICAL"                # 完全相同行（确定性 dedup 可修）
PK_CONFLICT = "PK_CONFLICT"                    # 同 PK 不同 payload（一律 quarantine）
# ② 时间与交易日历
TIME_UNPARSEABLE = "TIME_UNPARSEABLE"
TRADE_DATE_INVALID = "TRADE_DATE_INVALID"
LIST_BEFORE = "LIST_BEFORE"
LIST_AFTER_DELIST = "LIST_AFTER_DELIST"
TIME_ORDER = "TIME_ORDER"                      # 组内时间倒序（保留 + flag）
# ③ 基础数值合法性
PRICE_NONPOSITIVE = "PRICE_NONPOSITIVE"
VOLUME_NEGATIVE = "VOLUME_NEGATIVE"
AMOUNT_NEGATIVE = "AMOUNT_NEGATIVE"
NONFINITE_VALUE = "NONFINITE_VALUE"            # inf/-inf
MISSING_VALUE = "MISSING_VALUE"                # NaN/NULL 只登记原因，绝不 fillna(0)
# ④ OHLC 与内部一致性
OHLC_INVALID = "OHLC_INVALID"
VWAP_OUT_OF_RANGE = "VWAP_OUT_OF_RANGE"
# ⑤ 收益跳变与复权/公司行为
ADJ_FACTOR_CA_MISMATCH = "ADJ_FACTOR_CA_MISMATCH"
ADJ_NEGATIVE = "ADJ_NEGATIVE"
# ⑥ 证券状态与市场规则一致性
LIMIT_BREACH = "LIMIT_BREACH"
LIMIT_FIRST_DAY_EXEMPT = "LIMIT_FIRST_DAY_EXEMPT"   # 首日上市例外（正常特殊状态）
# ⑦ 跨源/跨频（T6 腾讯抽样 / M2 分钟↔日线；本批只立常量）——
CROSS_SOURCE_DEVIATION = "CROSS_SOURCE_DEVIATION"
MINUTE_DAILY_MISMATCH = "MINUTE_DAILY_MISMATCH"
# §6 health 示例引用的单位疑点（分布突变只 WARN/SUSPECT，不 FAIL）
UNIT_SUSPECT = "UNIT_SUSPECT"
# 结构校验（schema 级 FATAL，validators 产出）
SCHEMA_MISSING_COLUMN = "SCHEMA_MISSING_COLUMN"
SCHEMA_DATE_DTYPE = "SCHEMA_DATE_DTYPE"

# rule_id → 默认级别（§1 逐条；validators 不应偏离）
RULE_LEVELS: dict[str, str] = {
    DUP_IDENTICAL: INFO,
    PK_CONFLICT: ERROR,
    TIME_UNPARSEABLE: ERROR,
    TRADE_DATE_INVALID: ERROR,
    LIST_BEFORE: ERROR,
    LIST_AFTER_DELIST: ERROR,
    TIME_ORDER: WARN,
    PRICE_NONPOSITIVE: ERROR,
    VOLUME_NEGATIVE: ERROR,
    AMOUNT_NEGATIVE: ERROR,
    NONFINITE_VALUE: ERROR,
    MISSING_VALUE: WARN,
    OHLC_INVALID: ERROR,
    VWAP_OUT_OF_RANGE: ERROR,
    ADJ_FACTOR_CA_MISMATCH: WARN,
    ADJ_NEGATIVE: ERROR,
    LIMIT_BREACH: WARN,
    LIMIT_FIRST_DAY_EXEMPT: INFO,
    CROSS_SOURCE_DEVIATION: WARN,
    MINUTE_DAILY_MISMATCH: WARN,
    UNIT_SUSPECT: WARN,
    SCHEMA_MISSING_COLUMN: FATAL,
    SCHEMA_DATE_DTYPE: FATAL,
}

# rule_id → 主要关联字段（§2 系统性检测「字段集中」的归因提示；None = 无单一字段）
RULE_FIELDS: dict[str, str | None] = {
    DUP_IDENTICAL: None,
    PK_CONFLICT: None,
    TIME_UNPARSEABLE: "trade_date",
    TRADE_DATE_INVALID: "trade_date",
    LIST_BEFORE: "trade_date",
    LIST_AFTER_DELIST: "trade_date",
    TIME_ORDER: "trade_date",
    PRICE_NONPOSITIVE: None,          # detail 内点名具体价格列
    VOLUME_NEGATIVE: "volume",
    AMOUNT_NEGATIVE: "amount",
    NONFINITE_VALUE: None,
    MISSING_VALUE: None,
    OHLC_INVALID: None,               # 多列（open/high/low/close）关系
    VWAP_OUT_OF_RANGE: "amount",      # VWAP = amount/volume
    ADJ_FACTOR_CA_MISMATCH: "fq_factor",
    ADJ_NEGATIVE: None,               # adj_factor / fq_factor 均可
    LIMIT_BREACH: None,
    LIMIT_FIRST_DAY_EXEMPT: None,
    CROSS_SOURCE_DEVIATION: None,
    MINUTE_DAILY_MISMATCH: None,
    UNIT_SUSPECT: None,
}


@dataclass(frozen=True)
class RuleResult:
    """单条规则命中：级别 + 规则 + 定位键 + 原因。"""

    rule_id: str
    level: str
    key: str
    detail: str = ""


@dataclass(frozen=True)
class DqPolicy:
    """dq_policy.daily-v1 的结构化视图（字段名与 spec §3.2 完全一致）。"""

    dq_policy_version: str
    partition_gate: dict[str, dict[str, float]]
    systemic: dict[str, float]


def sort_results(results: list[RuleResult]) -> list[RuleResult]:
    """稳定排序：level（严重度）→ rule_id → key（T2 输出契约，T4 聚合前序）。"""
    return sorted(results, key=lambda r: (SEVERITY[r.level], r.rule_id, r.key))


# ── policy 加载 ──────────────────────────────────────────────────────────
_MISSING = object()

# spec §3.2 要求「字段必须存在」的数值路径（dotted）
_NUMERIC_FIELDS: tuple[str, ...] = (
    "partition_gate.pass.max_error_rate",
    "partition_gate.pass.min_coverage",
    "partition_gate.fail.min_error_rate",
    "partition_gate.fail.max_missing_coverage",
    "systemic.field_share",
    "systemic.field_count",
    "systemic.group_ratio",
    "systemic.group_count",
    "systemic.time_share",
    "systemic.time_count",
)

_PASS_KEYS = ("max_error_rate", "min_coverage")
_FAIL_KEYS = ("min_error_rate", "max_missing_coverage")
_SYSTEMIC_KEYS = ("field_share", "field_count", "group_ratio", "group_count",
                  "time_share", "time_count")


def _dig(raw: Any, dotted: str) -> Any:
    node = raw
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> DqPolicy:
    """加载并校验 dq_policy YAML。

    - 缺字段/非数值 → ``ValueError``，消息逐个点名 dotted path；
    - ``dq_policy_version`` ≠ ``POLICY_VERSION`` → ``ValueError``（旧版拒绝，
      M1 不做兼容映射——加载规则必须显式）。
    """
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"dq_policy 必须是 YAML mapping：{p}")

    problems: list[str] = []
    if "dq_policy_version" not in raw:
        problems.append("dq_policy_version")
    for dotted in _NUMERIC_FIELDS:
        v = _dig(raw, dotted)
        if v is _MISSING:
            problems.append(dotted)
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            problems.append(f"{dotted}（值 {v!r} 非数值）")
    if problems:
        raise ValueError(f"dq_policy 缺少/非法字段（{p}）：{', '.join(problems)}")

    version = raw["dq_policy_version"]
    if version != POLICY_VERSION:
        raise ValueError(
            f"不支持的 dq_policy_version: {version!r}（{p}）；"
            f"本版本仅接受 {POLICY_VERSION!r}，旧版本不做兼容映射")

    gate_raw = raw["partition_gate"]
    systemic_raw = raw["systemic"]
    return DqPolicy(
        dq_policy_version=version,
        partition_gate={
            "pass": {k: gate_raw["pass"][k] for k in _PASS_KEYS},
            "fail": {k: gate_raw["fail"][k] for k in _FAIL_KEYS},
        },
        systemic={k: systemic_raw[k] for k in _SYSTEMIC_KEYS},
    )
