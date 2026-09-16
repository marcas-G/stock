"""Plan S Task 1：StrategyDoc——策略 YAML 文档契约（六层漏斗的声明面）。

**组合两份既有契约，不合并 schema**（StrategySpec 明令禁 execution 字段，
`extra=forbid`）：

    StrategyDoc
    ├── strategy: StrategySpec      L4 组合（M7 契约，原样）
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


class StrategyDoc(BaseModel):
    """策略文档（YAML 的唯一内存契约）：六层映射的聚合根。

    V1 跨字段约束：`rules` 非 null 一律 `NotImplementedError`（L5 复杂规则未落地，
    不静默忽略；Task 6 放开 max_hold）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: StrategySpec
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
