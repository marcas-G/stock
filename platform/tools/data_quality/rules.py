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

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from factorlab.core import scope as core_scope

# ── policy 版本 ───────────────────────────────────────────────────────────
# daily-v3（R37，2026-09-20 用户裁定）：新增 scope: 块（1996-01-01 起、排除 .BJ），
# 与 factorlab.core.scope 常量一致（加载校验不一致即 ValueError）。
# daily-v2（Plan DQ-M1.5 T2，2026-09-19 控制者裁定）：早市 VWAP 容差、单位约定
# 例外 registry、ADJ 字段级置 NULL。旧文件保留作历史，加载器拒绝旧版本。
POLICY_VERSION = "daily-v3"
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
# v2 字段级语义：adj_factor/fq_factor <= 0 → 该字段置 NULL + flag（行保留）
ADJ_NULLED = "ADJ_NULLED"
# v2 历史制度例外：登记（code 集合 × era）的早期单位约定行，VWAP 降级 WARN + flag
HISTORIC_UNIT_EXCEPTION = "HISTORIC_UNIT_EXCEPTION"
# v3（R37 T4）现代单位 bug 逐行修复：登记（逐行键 × 字段 × 单位因子）行 VWAP
# 降级 WARN + flag，repair 执行字段换算（volume ×factor / amount ÷factor），行保留
UNIT_SCALE_REPAIRED = "UNIT_SCALE_REPAIRED"
# ⑥ 证券状态与市场规则一致性
LIMIT_BREACH = "LIMIT_BREACH"
LIMIT_FIRST_DAY_EXEMPT = "LIMIT_FIRST_DAY_EXEMPT"   # 首日上市例外（正常特殊状态）
# ⑦ 跨源/跨频（T6 腾讯抽样 / M2 分钟↔日线；本批只立常量）——
CROSS_SOURCE_DEVIATION = "CROSS_SOURCE_DEVIATION"
MINUTE_DAILY_MISMATCH = "MINUTE_DAILY_MISMATCH"
# §6 health 示例引用的单位疑点（分布突变只 WARN/SUSPECT，不 FAIL）
UNIT_SUSPECT = "UNIT_SUSPECT"
# 通用分布漂移（F7：仅单位突变用 UNIT_SUSPECT；漂移本身独立命名）
DRIFT_SUSPECT = "DRIFT_SUSPECT"
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
    ADJ_NULLED: WARN,
    HISTORIC_UNIT_EXCEPTION: WARN,
    UNIT_SCALE_REPAIRED: WARN,
    LIMIT_BREACH: WARN,
    LIMIT_FIRST_DAY_EXEMPT: INFO,
    CROSS_SOURCE_DEVIATION: WARN,
    MINUTE_DAILY_MISMATCH: WARN,
    UNIT_SUSPECT: WARN,
    DRIFT_SUSPECT: WARN,
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
    ADJ_NULLED: None,                 # adj_factor / fq_factor 均可（detail 点名）
    HISTORIC_UNIT_EXCEPTION: "amount",   # VWAP = amount/volume（单位约定）
    UNIT_SCALE_REPAIRED: None,        # 字段级处置（field/factor 随 RuleResult 携带）
    LIMIT_BREACH: None,
    LIMIT_FIRST_DAY_EXEMPT: None,
    CROSS_SOURCE_DEVIATION: None,
    MINUTE_DAILY_MISMATCH: None,
    UNIT_SUSPECT: None,
    DRIFT_SUSPECT: None,
    SCHEMA_MISSING_COLUMN: None,      # key = 缺失列名
    SCHEMA_DATE_DTYPE: "trade_date",
}


@dataclass(frozen=True)
class RuleResult:
    """单条规则命中：级别 + 规则 + 定位键 + 原因（+ 可选字段级定位）。

    ``field``：字段级处置类规则指名列（ADJ_NULLED 置 NULL；UNIT_SCALE_REPAIRED
    换算的列）；行级规则为 None。
    ``factor``：UNIT_SCALE_REPAIRED 的单位因子（volume ×factor / amount ÷factor）。
    """

    rule_id: str
    level: str
    key: str
    detail: str = ""
    field: str | None = None
    factor: float | None = None


@dataclass(frozen=True)
class HistoricUnitException:
    """历史单位约定例外 registry 条目（daily-v2）：code 集合 × era × 单位因子。

    ``after``（ISO date，可缺省）：闭区间下界（``trade_date >= after``）；
    ``before``（ISO date）为**开区间上界**（``trade_date < before``）。
    ``match_tol``：``vwap / factor`` 相对 ``[low, high]`` 的放宽比例（用于把
    整数价取整行也纳入该代码的既有单位约定，同时排除真坏行）。
    """

    codes: tuple[str, ...]
    before: str
    factor: float
    match_tol: float
    after: str | None = None


@dataclass(frozen=True)
class UnitScaleRepair:
    """逐行单位 bug 修复登记（daily-v3，R37 T4）。

    ``keys``：行键 ``<code>|<YYYY-MM-DD>``（与 validators 的 ``_key`` 同式），
    **逐行 RCA 证据**（bars_1m 三角 / vendor 源）锁定；只允许 scope 内行。
    ``field`` ∈ {volume, amount}；``factor`` = 原值/真值倍率（volume 手→股 =
    100，amount 金额偏大 = 100）——修正 VWAP = 原 VWAP / factor；换算方向：
    volume ×factor、amount ÷factor。
    ``match_tol``：换算后 VWAP 对 [low, high] 的放宽比例（同 registry guard）——
    不落带的行照旧 ERROR quarantine（不洗白）。
    """

    field: str
    factor: float
    match_tol: float
    keys: tuple[str, ...]


@dataclass(frozen=True)
class DqPolicy:
    """dq_policy.daily-v3 的结构化视图（daily 面字段为 v2/v3 扩展）。"""

    dq_policy_version: str
    partition_gate: dict[str, dict[str, float]]
    systemic: dict[str, float]
    vwap_default_tol: float
    vwap_pre_1995_tol: float
    vwap_pre_1995_cutoff: str
    field_invalidity_null: tuple[str, ...]
    historic_unit_exceptions: tuple[HistoricUnitException, ...]
    unit_scale_repairs: tuple[UnitScaleRepair, ...] = ()


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
    "vwap.default_tol",
    "vwap.pre_1995_tol",
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


def _iso_date(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        dt.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> DqPolicy:
    """加载并校验 dq_policy YAML（daily-v3 结构）。

    - 缺字段/非数值/非法结构 → ``ValueError``，消息逐个点名 dotted path；
    - ``dq_policy_version`` ≠ ``POLICY_VERSION`` → ``ValueError``（旧版拒绝，
      不做兼容映射——加载规则必须显式）；
    - v2 扩展：``vwap``（早市容差与截止日）、``field_invalidity``（字段级置
      NULL 白名单）、``historic_unit_exceptions``（code 集合 × era registry，
      条目逐字段校验，不允许空 codes/非法日期/非数值因子）。
    - v3 扩展：``scope``（min_trade_date / exclude_code_suffixes）必须与
      ``factorlab.core.scope`` 常量一致——不一致即拒绝（口径不许漂移）；
      ``unit_scale_repairs``（可缺省）逐行单位修复登记：字段/因子/键逐项校验，
      键必须 ``<code>|<ISO date>`` 且 scope 内、全局不重复（R37 T4）。
    """
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"dq_policy 必须是 YAML mapping：{p}")

    problems: list[str] = []
    version = raw.get("dq_policy_version")
    if version is None:
        problems.append("dq_policy_version")
    elif version != POLICY_VERSION:
        raise ValueError(
            f"不支持的 dq_policy_version: {version!r}（{p}）；"
            f"本版本仅接受 {POLICY_VERSION!r}，旧版本不做兼容映射")
    for dotted in _NUMERIC_FIELDS:
        v = _dig(raw, dotted)
        if v is _MISSING:
            problems.append(dotted)
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            problems.append(f"{dotted}（值 {v!r} 非数值）")

    cutoff = _dig(raw, "vwap.pre_1995_cutoff")
    if cutoff is _MISSING:
        problems.append("vwap.pre_1995_cutoff")
    elif not _iso_date(cutoff):
        problems.append(f"vwap.pre_1995_cutoff（值 {cutoff!r} 非 ISO 日期）")

    null_fields = _dig(raw, "field_invalidity.null_on_nonpositive")
    if null_fields is _MISSING:
        problems.append("field_invalidity.null_on_nonpositive")
    elif (not isinstance(null_fields, list) or not null_fields
          or any(not isinstance(c, str) or not c for c in null_fields)):
        problems.append(
            "field_invalidity.null_on_nonpositive（须为非空字符串列表）")

    registry = _dig(raw, "historic_unit_exceptions")
    if registry is _MISSING:
        problems.append("historic_unit_exceptions")
    elif not isinstance(registry, list):
        problems.append("historic_unit_exceptions（须为列表，可为空）")
    else:
        for i, entry in enumerate(registry):
            base = f"historic_unit_exceptions[{i}]"
            if not isinstance(entry, dict):
                problems.append(f"{base}（须为 mapping）")
                continue
            codes = entry.get("codes")
            if (not isinstance(codes, list) or not codes
                    or any(not isinstance(c, str) or not c for c in codes)):
                problems.append(f"{base}.codes（须为非空字符串列表）")
            after = entry.get("after")
            if after is not None:
                if not _iso_date(after):
                    problems.append(f"{base}.after（须为 ISO 日期）")
                elif _iso_date(entry.get("before")) \
                        and after >= str(entry["before"]):
                    problems.append(
                        f"{base}.after（{after!r} 必须 < before {entry['before']!r}）")
            if not _iso_date(entry.get("before")):
                problems.append(f"{base}.before（须为 ISO 日期）")
            for key in ("factor", "match_tol"):
                v = entry.get(key)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    problems.append(f"{base}.{key}（值 {v!r} 非数值）")

    # v3 逐行单位修复登记（可缺省；条目逐字段校验，不允许静默无效条目）
    repairs_raw = raw.get("unit_scale_repairs", [])
    if not isinstance(repairs_raw, list):
        problems.append("unit_scale_repairs（须为列表，可缺省）")
        repairs_raw = []
    else:
        seen_keys: dict[str, int] = {}
        for i, entry in enumerate(repairs_raw):
            base = f"unit_scale_repairs[{i}]"
            if not isinstance(entry, dict):
                problems.append(f"{base}（须为 mapping）")
                continue
            field = entry.get("field")
            if field not in ("volume", "amount"):
                problems.append(
                    f"{base}.field（须为 volume|amount；收到 {field!r}）")
            factor = entry.get("factor")
            if (isinstance(factor, bool) or not isinstance(factor, (int, float))
                    or factor <= 0 or factor == 1.0):
                problems.append(
                    f"{base}.factor（须为正数值且 ≠1.0；收到 {factor!r}）")
            tol = entry.get("match_tol")
            if isinstance(tol, bool) or not isinstance(tol, (int, float)) or tol < 0:
                problems.append(f"{base}.match_tol（须为非负数值；收到 {tol!r}）")
            keys = entry.get("keys")
            if (not isinstance(keys, list) or not keys
                    or any(not isinstance(k, str) or not k for k in keys)):
                problems.append(f"{base}.keys（须为非空字符串列表）")
                continue
            for j, k in enumerate(keys):
                code, sep, day = k.partition("|")
                if not sep or not code or not _iso_date(day):
                    problems.append(
                        f"{base}.keys[{j}]（须为 '<code>|<ISO date>'；收到 {k!r}）")
                    continue
                if (not core_scope.is_in_scope_code(code)
                        or not core_scope.is_in_scope_date(
                            dt.date.fromisoformat(day))):
                    problems.append(
                        f"{base}.keys[{j}]（scope 外行不可登记——范围外免修；"
                        f"收到 {k!r}）")
                    continue
                if k in seen_keys:
                    problems.append(
                        f"{base}.keys[{j}]（键重复，已登记于 "
                        f"unit_scale_repairs[{seen_keys[k]}]；收到 {k!r}）")
                else:
                    seen_keys[k] = i

    scope_raw = raw.get("scope")
    if not isinstance(scope_raw, dict):
        problems.append("scope（须为 mapping：min_trade_date / exclude_code_suffixes）")
    else:
        md = scope_raw.get("min_trade_date")
        if md != core_scope.MIN_TRADE_DATE_ISO:
            problems.append(
                f"scope.min_trade_date（须为 {core_scope.MIN_TRADE_DATE_ISO!r}，"
                f"与 factorlab.core.scope 一致；收到 {md!r}）")
        suffixes = scope_raw.get("exclude_code_suffixes")
        if suffixes != list(core_scope.EXCLUDED_CODE_SUFFIXES):
            problems.append(
                f"scope.exclude_code_suffixes（须为 "
                f"{list(core_scope.EXCLUDED_CODE_SUFFIXES)!r}，与 factorlab.core.scope "
                f"一致；收到 {suffixes!r}）")

    if problems:
        raise ValueError(f"dq_policy 缺少/非法字段（{p}）：{', '.join(problems)}")

    version = raw["dq_policy_version"]
    if version != POLICY_VERSION:
        raise ValueError(
            f"不支持的 dq_policy_version: {version!r}（{p}）；"
            f"本版本仅接受 {POLICY_VERSION!r}，旧版本不做兼容映射")

    gate_raw = raw["partition_gate"]
    systemic_raw = raw["systemic"]
    vwap_raw = raw["vwap"]
    exceptions = tuple(
        HistoricUnitException(
            codes=tuple(entry["codes"]), before=str(entry["before"]),
            factor=float(entry["factor"]), match_tol=float(entry["match_tol"]),
            after=str(entry["after"]) if entry.get("after") is not None else None)
        for entry in registry)
    unit_scale_repairs = tuple(
        UnitScaleRepair(field=str(entry["field"]), factor=float(entry["factor"]),
                        match_tol=float(entry["match_tol"]),
                        keys=tuple(str(k) for k in entry["keys"]))
        for entry in repairs_raw)
    return DqPolicy(
        dq_policy_version=version,
        partition_gate={
            "pass": {k: gate_raw["pass"][k] for k in _PASS_KEYS},
            "fail": {k: gate_raw["fail"][k] for k in _FAIL_KEYS},
        },
        systemic={k: systemic_raw[k] for k in _SYSTEMIC_KEYS},
        vwap_default_tol=float(vwap_raw["default_tol"]),
        vwap_pre_1995_tol=float(vwap_raw["pre_1995_tol"]),
        vwap_pre_1995_cutoff=str(vwap_raw["pre_1995_cutoff"]),
        field_invalidity_null=tuple(null_fields),
        historic_unit_exceptions=exceptions,
        unit_scale_repairs=unit_scale_repairs,
    )


@lru_cache(maxsize=1)
def default_policy() -> DqPolicy:
    """惰性缓存的 shipped policy（validators 缺省口径，只读一次 YAML）。"""
    return load_policy(DEFAULT_POLICY_PATH)
