"""M8-01B / M8-05A：ExecutionSpec——Execution Runtime 配置契约。

- initial_cash：账户初始现金（positive finite float，bool/str 拒绝）
- cost_model：嵌套 ExecutionCostSpec（M8-05A）——**默认 zero-cost**；
  zero default 不代表真实 A 股成本为零——生产 backtest 必须显式配置
  （后续 M8-06 Gate 再 enforce，当前不 enforce nonzero）
- **不拥有 per-security quantity rules**（M8-01B：SecurityQuantityRule 是
  唯一数量权威——全局 lot_size 已移除，传入 lot_size 即 extra=forbid fail）
- **成本参数必须嵌套在 cost_model**（root 直接传 commission/slippage/
  stamp_tax 等即 extra=forbid fail——避免第二套扁平 cost API）
- **不重复时间语义**（最早执行时点复用 M6 SignalTiming 的
  ExecutionTiming——不建立第二套 timing configuration）
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import (BaseModel, ConfigDict, Field, field_validator,
                      model_validator)

from factorlab.core.domain.timing import ExecutionTiming


class ExecutionCostSpec(BaseModel):
    """v1 交易成本模型配置（security-agnostic / time-invariant / 显式保守）。

    - commission_rate：券商佣金比例（0 <= r < 1；0 时 minimum_commission 不生效）
    - minimum_commission：比例佣金被启用后的下限（finite >= 0）
    - stamp_tax_sell_rate：印花税（仅 SELL；0 <= r < 1）
    - transfer_fee_rate：过户费（BUY+SELL 均按名义金额；0 <= r < 1；
      v1 不做 SH/SZ/时代条件化——需历史费率版本化时升 CostModel v2）
    - slippage_bps：确定性价格滑点（bps，10_000 bps = 100%；finite >= 0；
      修改 execution_price，不是 fee）
    - 默认全零 = zero-cost model；真实费率 broker-dependent / 历史变化 /
      market-rule dependent——平台不未经版本化声称"真实 A 股成本"
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    commission_rate: float = 0.0
    minimum_commission: float = 0.0
    stamp_tax_sell_rate: float = 0.0
    transfer_fee_rate: float = 0.0
    slippage_bps: float = 0.0

    @field_validator("commission_rate", "stamp_tax_sell_rate",
                     "transfer_fee_rate", mode="before")
    @classmethod
    def _proportional_rate(cls, v, info) -> float:
        if isinstance(v, bool):
            raise ValueError(f"{info.field_name} 不能是 bool")
        if not isinstance(v, (int, float)):
            raise ValueError(
                f"{info.field_name} 必须为数值（string 不自动 cast，收到 {v!r}）")
        if not math.isfinite(v):
            raise ValueError(f"{info.field_name} 必须 finite")
        if not 0 <= v < 1:
            raise ValueError(
                f"{info.field_name} 必须 0 <= r < 1（收到 {v!r}）")
        return float(v)

    @field_validator("minimum_commission", "slippage_bps", mode="before")
    @classmethod
    def _nonnegative(cls, v, info) -> float:
        if isinstance(v, bool):
            raise ValueError(f"{info.field_name} 不能是 bool")
        if not isinstance(v, (int, float)):
            raise ValueError(
                f"{info.field_name} 必须为数值（string 不自动 cast，收到 {v!r}）")
        if not math.isfinite(v):
            raise ValueError(f"{info.field_name} 必须 finite")
        if v < 0:
            raise ValueError(f"{info.field_name} 必须 >= 0（收到 {v!r}）")
        return float(v)


class SliceSpec(BaseModel):
    """分钟窗口子切片（R22）：[start, end] 闭区间 + 目标量权重。

    - minute_index 语义同 data contract（0=09:25 开盘集合竞价、239=15:00）
    - weight 必须 finite > 0（SliceSpec 自身只管单点合法；权重和 = 1 /
      切片顺序 / 重叠 / 落在窗口内由 MinuteWindowSpec 跨字段校验）
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int
    end: int
    weight: float

    @field_validator("start", "end", mode="before")
    @classmethod
    def _int_index(cls, v, info) -> int:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(
                f"{info.field_name} 必须为 int（bool/str/float 拒绝，收到 {v!r}）")
        if not 0 <= v <= 239:
            raise ValueError(
                f"{info.field_name} 必须 0 <= {info.field_name} <= 239"
                f"（收到 {v!r}）")
        return v

    @field_validator("weight", mode="before")
    @classmethod
    def _weight(cls, v) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"weight 必须为数值（收到 {v!r}）")
        if not math.isfinite(v):
            raise ValueError(f"weight 必须 finite（收到 {v!r}）")
        if v <= 0:
            raise ValueError(f"weight 必须 > 0（收到 {v!r}）")
        return float(v)

    @model_validator(mode="after")
    def _bounds(self):
        if self.end < self.start:
            raise ValueError(
                f"SliceSpec end 必须 >= start（收到 start={self.start}, "
                f"end={self.end}）")
        return self


class TriggerSpec(BaseModel):
    """价格触发配置（R22 V1：limit / vwap_offset）。

    - mode="limit"：静态限价，基准 ref ∈ {pre_close, window_open, window_vwap}
    - mode="vwap_offset"：limit 随窗口累计 VWAP 滚动（ref 忽略）
    - offset_bps：买 limit = ref×(1+offset/1e4)；卖 limit = ref×(1-offset/1e4)
      ——负值 = 买更低价 / 卖更高价；finite（bool 拒绝）
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["limit", "vwap_offset"]
    ref: Literal["pre_close", "window_open", "window_vwap"] = "pre_close"
    offset_bps: float = 0.0

    @field_validator("offset_bps", mode="before")
    @classmethod
    def _offset(cls, v) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"offset_bps 必须为数值（收到 {v!r}）")
        if not math.isfinite(v):
            raise ValueError(f"offset_bps 必须 finite（收到 {v!r}）")
        return float(v)


class MinuteWindowSpec(BaseModel):
    """分钟执行窗口配置（R22）：窗口 + 价格口径 + 分批 + 参与率 + 触发 + 兜底。

    - start/end：minute_index 闭区间（0=09:25、239=15:00；data contract 为准）
    - price_basis：成交价口径（vwap = Σamount/Σvolume；其余按分钟 bar 口径）
    - slices：分批（缺省 = 单切片 [start,end] 权重 1）；按 start 升序、互不
      重叠、落在窗口内、权重和 = 1（容差 1e-9）、每片 weight > 0
    - participation：单分钟成交 ≤ participation × minute.volume（0<r<=1）
    - trigger：价格触发；None = 必成交（窗口内按参与率成交）
    - fallback：窗口结束仍未成完 → none=不成交；close=最后窗口 close 兜底
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int
    end: int
    price_basis: Literal["vwap", "open", "close", "twap", "mid"] = "vwap"
    slices: list[SliceSpec] | None = None
    participation: float = 0.10
    trigger: TriggerSpec | None = None
    fallback: Literal["none", "close"] = "none"

    @field_validator("start", "end", mode="before")
    @classmethod
    def _int_index(cls, v, info) -> int:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(
                f"{info.field_name} 必须为 int（bool/str/float 拒绝，收到 {v!r}）")
        if not 0 <= v <= 239:
            raise ValueError(
                f"{info.field_name} 必须 0 <= {info.field_name} <= 239"
                f"（收到 {v!r}）")
        return v

    @field_validator("participation", mode="before")
    @classmethod
    def _participation(cls, v) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"participation 必须为数值（收到 {v!r}）")
        if not math.isfinite(v):
            raise ValueError(f"participation 必须 finite（收到 {v!r}）")
        if not 0 < v <= 1:
            raise ValueError(
                f"participation 必须 0 < r <= 1（收到 {v!r}）")
        return float(v)

    @model_validator(mode="after")
    def _cross_fields(self):
        if self.end < self.start:
            raise ValueError(
                f"MinuteWindowSpec end 必须 >= start（收到 start={self.start}, "
                f"end={self.end}）")
        if self.slices is None:
            return self
        if not self.slices:
            raise ValueError("slices 若提供必须非空（空列表 → 用 None 表达单切片）")
        prev = None
        for s in self.slices:
            if s.start < self.start or s.end > self.end:
                raise ValueError(
                    f"slice [{s.start}, {s.end}] 必须落在窗口 "
                    f"[{self.start}, {self.end}] 内（收到越界切片）")
            if prev is not None:
                if s.start <= prev.start:
                    raise ValueError(
                        f"slices 必须按 start 严格升序（收到 {prev.start} → "
                        f"{s.start}）")
                if s.start <= prev.end:
                    raise ValueError(
                        f"slices 不得重叠（[{prev.start}, {prev.end}] 与 "
                        f"[{s.start}, {s.end}] 重叠）")
            prev = s
        total = sum(s.weight for s in self.slices)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"slices 权重和必须 = 1（容差 1e-9，收到 {total!r}）")
        return self


class ExecutionSpec(BaseModel):
    """执行配置（long-only A 股、嵌套成本模型、无全局数量规则）。

    - execution_timing：默认 NEXT_OPEN（向后兼容）；NEXT_WINDOW 必须提供
      minute_window（分钟窗口执行配置）；NEXT_OPEN/NEXT_CLOSE 禁止携带窗口
      ——timing 语义不复制（复用 M6 ExecutionTiming）
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_cash: float = 1_000_000.0
    # default_factory：每个 ExecutionSpec 实例独立 cost_model（frozen 对象
    # 虽不可变，仍避免 pydantic 默认值实例共享）
    cost_model: ExecutionCostSpec = Field(default_factory=ExecutionCostSpec)
    execution_timing: ExecutionTiming = ExecutionTiming.NEXT_OPEN
    minute_window: MinuteWindowSpec | None = None

    @field_validator("initial_cash", mode="before")
    @classmethod
    def _cash_valid(cls, v) -> float:
        if isinstance(v, bool):
            raise ValueError("initial_cash 不能是 bool")
        if not isinstance(v, (int, float)):
            raise ValueError(
                f"initial_cash 必须为数值（string 不自动 cast，收到 {v!r}）")
        if not math.isfinite(v):
            raise ValueError("initial_cash 必须 finite")
        if v <= 0:
            raise ValueError(f"initial_cash 必须 > 0（收到 {v!r}）")
        return float(v)

    @model_validator(mode="after")
    def _timing_window_consistency(self):
        if self.execution_timing is ExecutionTiming.NEXT_WINDOW:
            if self.minute_window is None:
                raise ValueError(
                    "execution_timing=NEXT_WINDOW 必须提供 minute_window"
                    "（窗口执行配置缺失，禁止隐式默认窗口）")
        elif self.minute_window is not None:
            raise ValueError(
                f"execution_timing={self.execution_timing.value} 禁止携带 "
                f"minute_window（仅 NEXT_WINDOW 使用分钟窗口）")
        return self
