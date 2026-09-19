"""Plan S Task 1：StrategyDoc——策略 YAML 文档契约（六层漏斗的声明面）。

**组合两份既有契约，不合并 schema**（StrategySpec 明令禁 execution 字段，
`extra=forbid`）：

    StrategyDoc
    ├── strategy: StrategySpec      L4 组合（M7 契约，原样）
    ├── signal_kind                 L4 信号来源种类（Plan CX-C4 T1：factor|composite）
    ├── execution: ExecutionSpec    L5 执行（M8 契约，原样）
    ├── date: DateRange             回测窗口（decision 过滤）
    ├── universe_override           L1 可选 codes 覆盖（null = 随因子）
    ├── regime: RegimeSpec          L2 门控语义（V1 仅 signal_gate）
    └── rules: RulesSpec            L5 路径依赖规则（V1：max_hold 放开；止损/止盈后置）

YAML 读入在 `spec_io.py`（与 `core/spec.py::load_spec` 同模式）；本模块保持
纯契约（无文件 I/O）。
"""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import (BaseModel, ConfigDict, StrictInt, field_validator,
                      model_validator)

from factorlab.core.execution.spec import ExecutionSpec
from factorlab.core.strategy.spec import StrategySpec


class DateRange(BaseModel):
    """回测窗口（闭区间）：start <= end（相等 = 单日窗口）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime.date
    end: datetime.date

    @model_validator(mode="after")
    def _ordered(self):
        if self.start > self.end:
            raise ValueError(
                f"date.start 必须 <= date.end（收到 {self.start} > {self.end}）")
        return self


class RegimeSpec(BaseModel):
    """L2 门控语义（D1 拍板）：V1 仅 `signal_gate`。

    语义 = 门控项在因子公式内、截面在全池计算，只决定"是否持有"——不是股票池
    条件（池条件会改变截面范围，两种语义不可混用）。G1（显式 regime 多输出）
    后置，见 Plan S Task 7 登记。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["signal_gate"] = "signal_gate"


class RulesSpec(BaseModel):
    """L5 路径依赖规则（有状态，唯一带状态的层）。

    V1：仅 `max_hold` 放开（研究侧近似，`research/tools/strategies/l5_rules.py`，
    调仓日粒度、非成交明细级）；`stop_loss` / `take_profit` 平台化后置（Plan S
    Task 7 触发条件）。后两者非 null 在 StrategyDoc 层显式 `NotImplementedError`
    ——不静默忽略。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    stop_loss: float | None = None
    take_profit: float | None = None
    max_hold: StrictInt | None = None

    @field_validator("stop_loss", "take_profit", mode="before")
    @classmethod
    def _ratio(cls, v, info):
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{info.field_name} 必须为 null 或数值（收到 {v!r}）")
        if v <= 0:
            raise ValueError(f"{info.field_name} 必须 > 0（收到 {v!r}）")
        return float(v)

    @field_validator("max_hold", mode="before")
    @classmethod
    def _max_hold(cls, v):
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"max_hold 必须为 null 或 strict int（收到 {v!r}）")
        if v < 1:
            raise ValueError(f"max_hold 必须 >= 1（收到 {v!r}）")
        return v


_V1_UNSUPPORTED_RULES = ("stop_loss", "take_profit")

# Plan CX-C4 T1（design §19.1）：signal 引用前缀 → kind。仅 composites/ 有语义；
# factor 信号保持既有裸名形态（不引入 factors/ 前缀，regex 契约不破）。
_SIGNAL_PREFIXES = {"composites": "composite"}


def parse_signal_ref(signal: str, signal_kind: str | None = None) -> tuple[str, str]:
    """YAML `signal` 引用 → `(kind, basename)`（Plan CX-C4 T1；design §19.1）。

    - `composites/<name>` 前缀自动识别 kind=composite；裸名缺省 factor；
    - 显式 `signal_kind`（factor|composite）覆盖缺省 kind——与前缀蕴含冲突 →
      ValueError（不静默取一方）；
    - 返回 basename：`StrategySpec.signal_name` 契约
      （^[A-Za-z_][A-Za-z0-9_]{0,63}$）不因前缀而破坏。
    """
    if not isinstance(signal, str) or not signal:
        raise ValueError(f"signal 必须为非空字符串（收到 {signal!r}）")
    if signal_kind is not None and not isinstance(signal_kind, str):
        raise ValueError(
            f"signal_kind 必须为 'factor' 或 'composite'（收到 {signal_kind!r}）")
    derived: str | None = None
    name = signal
    if "/" in signal:
        prefix, _, rest = signal.partition("/")
        if prefix not in _SIGNAL_PREFIXES:
            raise ValueError(
                f"signal {signal!r} 前缀 {prefix!r} 不支持（仅支持 "
                f"'composites/<name>' 前缀；factor 信号直接用裸名）")
        if not rest or "/" in rest:
            raise ValueError(
                f"signal {signal!r} 非法：前缀 'composites/' 后须为单个 name"
                f"（composites/<name>）")
        derived, name = _SIGNAL_PREFIXES[prefix], rest
    if signal_kind is not None and derived is not None and signal_kind != derived:
        raise ValueError(
            f"signal {signal!r} 前缀蕴含 kind={derived!r}，与显式 "
            f"signal_kind={signal_kind!r} 冲突——请统一二者（或删去 signal_kind）")
    return (signal_kind if signal_kind is not None else (derived or "factor")), name


class StrategyDoc(BaseModel):
    """策略文档（YAML 的唯一内存契约）：六层映射的聚合根。

    V1 跨字段约束：`rules` 非 null 一律 `NotImplementedError`（L5 复杂规则未落地，
    不静默忽略；Task 6 放开 max_hold）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: StrategySpec
    signal_kind: Literal["factor", "composite"] = "factor"
    execution: ExecutionSpec
    date: DateRange
    universe_override: list[str] | None = None
    regime: RegimeSpec = RegimeSpec()
    rules: RulesSpec = RulesSpec()

    @field_validator("universe_override", mode="before")
    @classmethod
    def _override_codes(cls, v):
        if v is None:
            return None
        if not isinstance(v, list):
            raise ValueError(
                f"universe_override 必须为 null 或 codes 列表（收到 {type(v).__name__}）")
        for c in v:
            if not isinstance(c, str) or not c:
                raise ValueError(
                    f"universe_override 元素必须为非空字符串（收到 {c!r}）")
        return v

    @model_validator(mode="after")
    def _v1_rules_boundary(self):
        present = [name for name in _V1_UNSUPPORTED_RULES
                   if getattr(self.rules, name) is not None]
        if present:
            raise NotImplementedError(
                f"V1 不支持策略路径依赖规则 {present}——M8 执行层无止损/止盈语义，"
                f"不允许静默忽略。max_hold 的 V1 近似在研究侧"
                f"（research/tools/strategies/l5_rules.py，调仓日粒度）；"
                f"stop_loss/take_profit 平台化见 Plan S Task 7 触发条件"
                f"（连续/日内触发语义 + 成交明细级回放，另立里程碑）")
        return self
