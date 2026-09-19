"""Plan S Task 1：`load_strategy_doc`——策略 YAML 读入（fail fast，含路径）。

模式与 `core/spec.py::load_spec` 相同（`yaml.safe_load` + pydantic 校验），
但 YAML 顶层是**文档级字段**：`name/signal/direction/portfolio.*` 映射为
`StrategySpec`，`execution.*` 映射为 `ExecutionSpec`（`timing` → 契约字段
`execution_timing`，其余原样透传——pydantic 既有校验全复用），`date/regime/
rules/universe_override` 为文档级声明。

错误纪律：一切解析/校验错误都带 spec 路径；未知键显式拒绝（extra=forbid
语义，不是静默丢弃）；`NotImplementedError`（V1 rules 边界）原样透传。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NoReturn

import yaml
from pydantic import ValidationError

from factorlab.core.domain.timing import ExecutionTiming
from factorlab.core.execution.spec import ExecutionSpec
from factorlab.core.strategy.doc import (DateRange, PortfolioSpec, RegimeSpec,
                                         RulesSpec, StrategyDoc, parse_signal_ref)
from factorlab.core.strategy.spec import StrategySpec, WeightingSpec

_TOP_LEVEL_KEYS = {"name", "signal", "signal_kind", "direction", "portfolio",
                   "execution", "rules", "regime", "date", "universe_override"}
_PORTFOLIO_KEYS = {"method", "top_k", "enter_k", "retain_k", "weighting",
                   "gross_exposure", "rebalance_frequency"}


def _fail(path: Any, message: str) -> NoReturn:
    raise ValueError(f"策略 spec {path}: {message}")


def _mapping(value: Any, what: str, path: Any) -> dict:
    if not isinstance(value, dict):
        _fail(path, f"{what} 必须为 mapping（收到 {type(value).__name__}）")
    return value


def _unknown_keys(mapping: dict, allowed: set[str], what: str, path: Any) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        _fail(path, f"{what} 含未知键 {unknown}（extra=forbid，允许 {sorted(allowed)}）")


def _required(mapping: dict, key: str, what: str, path: Any) -> Any:
    if key not in mapping:
        _fail(path, f"缺少必填字段 {what}.{key}")
    return mapping[key]


def _validated(model, value: Any, what: str, path: Any):
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise ValueError(f"策略 spec {path}: {what} 非法: {exc}") from exc


def _build_strategy(raw: dict, path: Any, signal_name: str) -> StrategySpec:
    portfolio = _mapping(_required(raw, "portfolio", "根", path),
                         "portfolio", path)
    _unknown_keys(portfolio, _PORTFOLIO_KEYS, "portfolio", path)
    weighting = portfolio.get("weighting", "equal_weight")
    if not isinstance(weighting, str):
        _fail(path, f"portfolio.weighting 必须为字符串（收到 {weighting!r}）")
    # C4b：portfolio 双形态（top_k / top_k_buffered）——选择参数校验单一来源
    # 仍是 SelectionSpec（PortfolioSpec.to_selection），本函数只做映射。
    pspec = _validated(PortfolioSpec, portfolio, "portfolio", path)
    return _validated(StrategySpec, {
        "name": _required(raw, "name", "根", path),
        "signal_name": signal_name,
        "direction": _required(raw, "direction", "根", path),
        "selection": pspec.to_selection(),
        "weighting": WeightingSpec(method=weighting),
        "gross_exposure": pspec.gross_exposure,
        "rebalance_frequency": pspec.rebalance_frequency,
    }, "strategy（L4）", path)


def _execution_timing(value: Any, path: Any) -> ExecutionTiming:
    """YAML 契约用大写枚举名（NEXT_OPEN / NEXT_WINDOW）；同时容忍 enum value 写法。"""
    if isinstance(value, ExecutionTiming):
        return value
    if isinstance(value, str):
        upper = value.upper()
        if upper in ExecutionTiming.__members__:
            return ExecutionTiming[upper]
        try:
            return ExecutionTiming(value)
        except ValueError:
            pass
    _fail(path, f"execution.timing 非法: {value!r}（允许 "
                f"{sorted(ExecutionTiming.__members__)}）")


def _build_execution(raw: dict, path: Any) -> ExecutionSpec:
    section = _mapping(_required(raw, "execution", "根", path), "execution", path)
    mapped = dict(section)
    if "timing" in mapped:
        if "execution_timing" in mapped:
            _fail(path, "execution.timing 与 execution.execution_timing 不能同时出现")
        mapped["execution_timing"] = _execution_timing(mapped.pop("timing"), path)
    return _validated(ExecutionSpec, mapped, "execution（L5）", path)


def strategy_doc_from_mapping(raw: Any, path: Any = "<mapping>") -> StrategyDoc:
    """dict（YAML safe_load 结果）→ StrategyDoc，未知键/缺字段/类型全部 fail fast。"""
    raw = _mapping(raw, "根", path)
    _unknown_keys(raw, _TOP_LEVEL_KEYS, "根", path)
    signal_ref = _required(raw, "signal", "根", path)
    try:
        signal_kind, signal_name = parse_signal_ref(signal_ref,
                                                    raw.get("signal_kind"))
    except ValueError as exc:
        _fail(path, str(exc))
    regime = _validated(RegimeSpec, raw.get("regime", {}), "regime", path)
    rules = _validated(RulesSpec, raw.get("rules", {}), "rules", path)
    date = _validated(DateRange, _required(raw, "date", "根", path), "date", path)
    try:
        return StrategyDoc(
            strategy=_build_strategy(raw, path, signal_name),
            signal_kind=signal_kind,
            execution=_build_execution(raw, path),
            date=date,
            universe_override=raw.get("universe_override"),
            regime=regime,
            rules=rules,
        )
    except ValidationError as exc:
        raise ValueError(f"策略 spec {path}: 文档级校验失败: {exc}") from exc


def load_strategy_doc(path: str | Path) -> StrategyDoc:
    """读取策略 YAML → StrategyDoc（读写错误/语法错误均带路径）。"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"策略 spec 文件不存在: {p}") from exc
    except OSError as exc:
        raise ValueError(f"策略 spec 读取失败: {p}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"策略 spec YAML 解析失败: {p}: {exc}") from exc
    if raw is None:
        raise ValueError(f"策略 spec 为空: {p}")
    return strategy_doc_from_mapping(raw, path=p)


def looks_like_strategy_doc(path: str | Path) -> bool:
    """形态识别（lint 分派）：顶层 mapping 且同时含 `signal` 与 `portfolio`。

    读取/解析失败返回 False——错误交原 spec 路径处理（这里不吞、不抢报）。
    """
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(raw, dict) and "signal" in raw and "portfolio" in raw
