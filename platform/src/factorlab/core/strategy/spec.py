"""M7-01：StrategySpec——策略领域契约（Portfolio Construction 输入）。

与 FactorSpec（因子研究）严格分离：StrategySpec 只描述"如何把已有
SignalArtifact 转化为目标组合"，**不包含 formula/process/target/forward 目标**。
Strategy Runtime 只消费 SignalArtifact（LabelArtifact/forward_return_*/legacy
panel 永不进入策略链）。
"""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import (BaseModel, ConfigDict, StrictInt, field_validator,
                      model_validator)

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _validate_name(value: str, field: str) -> str:
    if not isinstance(value, str) or not _NAME_RE.match(value):
        raise ValueError(
            f"{field} 必须匹配 ^[A-Za-z_][A-Za-z0-9_]{{0,63}}$（收到 {value!r}）")
    return value


def _validate_gross_exposure(value: float) -> float:
    if isinstance(value, bool):
        raise ValueError("gross_exposure 不能是 bool")
    if not isinstance(value, (int, float)):
        raise ValueError(f"gross_exposure 必须为数值（收到 {value!r}）")
    if not math.isfinite(value):
        raise ValueError("gross_exposure 必须 finite")
    if not 0 < value <= 1:
        raise ValueError(f"gross_exposure 必须 0 < x <= 1（收到 {value!r}）")
    return float(value)


def _validate_strict_int(value: int, field: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 必须为 strict int（bool 拒绝，收到 {value!r}）")
    if value < minimum:
        raise ValueError(f"{field} 必须 >= {minimum}（收到 {value!r}）")
    return value


class SelectionSpec(BaseModel):
    """证券选择契约（M7 v1：top_k；C4b：top_k_buffered）。

    - top_k：`k`（strict int >= 1）；每日独立选择 Top-K。
    - top_k_buffered：`enter_k`/`retain_k`（strict int >= 1，`retain_k >= enter_k`）
      ——顺序式构造（同一 run 内按 decision date 顺序维护前一 target 持仓）：
      当前持仓在当日 `retain_k` 名次内则保留（缓冲带）；空位从 `enter_k` 名次内
      候选按 (signal, code_asc) 确定性补入；目标仓位数 = enter_k（use_available
      时受当日可用数上限约束）。`k` 与 enter/retain 互斥（不静默忽略）。
    - tie_breaker：code_asc（cutoff 处相同 signal 按 code 升序——输入行序
      不影响 Top-K 结果）
    - null_policy：drop（signal null 不进入 candidate ranking）
    - on_insufficient：use_available（全部可用）/ all_cash（该日显式 0 仓位）
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["top_k", "top_k_buffered"] = "top_k"
    k: StrictInt | None = None            # strict：拒绝 "30"/1.5/True/False（bool subclass of int 显式拦截）
    enter_k: StrictInt | None = None      # C4b buffered：补入名次上限
    retain_k: StrictInt | None = None     # C4b buffered：保留名次上限（>= enter_k）
    tie_breaker: Literal["code_asc"] = "code_asc"
    null_policy: Literal["drop"] = "drop"
    on_insufficient: Literal["use_available", "all_cash"] = "use_available"

    @field_validator("k", "enter_k", "retain_k")
    @classmethod
    def _positive_int(cls, v: int | None, info) -> int | None:
        if v is None:
            return None
        return _validate_strict_int(v, info.field_name, minimum=1)

    @model_validator(mode="after")
    def _method_params(self) -> "SelectionSpec":
        if self.method == "top_k":
            if self.k is None:
                raise ValueError("selection.method=top_k 必须提供 k")
            if self.enter_k is not None or self.retain_k is not None:
                raise ValueError(
                    "selection.method=top_k 不接受 enter_k/retain_k"
                    "（buffered 参数）——请改用 method=top_k_buffered")
        else:
            if self.k is not None:
                raise ValueError(
                    "selection.method=top_k_buffered 不接受 k——请用 "
                    "enter_k/retain_k（k 与 buffered 参数互斥）")
            if self.enter_k is None or self.retain_k is None:
                raise ValueError(
                    "selection.method=top_k_buffered 必须提供 enter_k 和 retain_k")
            if self.retain_k < self.enter_k:
                raise ValueError(
                    f"selection.method=top_k_buffered 要求 retain_k >= enter_k"
                    f"（收到 enter_k={self.enter_k} > retain_k={self.retain_k}）")
        return self


class WeightingSpec(BaseModel):
    """权重契约（M7 v1：equal_weight；C4 §19.2：score_weighted；C4b：market_cap_weighted）。

    - equal_weight：gross_exposure / selected_count
    - score_weighted（long-only）：Top-K 后取有符号分 s = signal × direction，
      s' = max(s, 0)，w_i = gross_exposure × s'_i / Σs'；Σs'==0 → 当日 all-cash
      （显式，不 fallback 等权）。single-name cap 本期不做（V2 不引入）。
    - market_cap_weighted：Top-K 内 w_i ∝ PIT total_mv（按 (date, code) 对齐的
      市值面板，app 层从读句柄取），w_i = gross_exposure × mv_i / Σmv；long-only。
      缺 mv（无行/total_mv 非 finite 或 <= 0）→ 显式 ValueError（fail fast，
      不静默剔除/回退等权——缺市值不得伪装成零权重）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["equal_weight", "score_weighted", "market_cap_weighted"] = "equal_weight"


class StrategySpec(BaseModel):
    """策略契约 v1：把 SignalArtifact 转为 TargetPortfolio 的完整声明。

    - name：策略名（^[A-Za-z_][A-Za-z0-9_]{0,63}$）
    - signal_name：期望消费的 SignalArtifact.meta.name（M7-02 实际检查）
    - direction：±1（signal 越大越优 / 越小越优）
    - selection / weighting：选择与加权子契约
    - gross_exposure：0 < x <= 1（有限、非 bool；剩余为隐式现金）
    - rebalance_frequency：M7 v1 仅 daily（调仓日历语义属 M7-03）
    **禁止**：target/forward_return/label/commission/slippage 等字段
    （extra="forbid" fail fast）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    signal_name: str
    direction: Literal[1, -1]
    selection: SelectionSpec
    weighting: WeightingSpec
    gross_exposure: float = 1.0
    rebalance_frequency: Literal["daily", "weekly", "monthly"] = "daily"

    @field_validator("name")
    @classmethod
    def _name_valid(cls, v: str) -> str:
        return _validate_name(v, "name")

    @field_validator("signal_name")
    @classmethod
    def _signal_name_valid(cls, v: str) -> str:
        return _validate_name(v, "signal_name")

    @field_validator("direction", mode="before")
    @classmethod
    def _direction_valid(cls, v) -> int:
        if isinstance(v, bool):
            raise ValueError("direction 不能是 bool（True==1 的 int 子类陷阱）")
        return v

    @field_validator("gross_exposure", mode="before")
    @classmethod
    def _gross_valid(cls, v) -> float:
        return _validate_gross_exposure(v)
